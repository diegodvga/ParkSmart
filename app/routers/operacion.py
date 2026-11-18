"""
Router de operación para ParkSmart (Módulo 2: entradas y salidas).

Lo usan el propietario (en SUS parqueaderos) y el operario (donde está
asignado). Rutas:
- GET  /operacion                                              → Parqueaderos que puedo operar
- GET  /operacion/parqueaderos/{id}                            → Panel de la puerta
- POST /operacion/parqueaderos/{id}/ingreso-directo            → Ingreso sin reserva (walk-in)
- POST /operacion/parqueaderos/{id}/reservas/{rid}/entrada     → Entrada con reserva
- POST /operacion/parqueaderos/{id}/estancias/{eid}/salida     → Salida y cobro
- GET  /operacion/parqueaderos/{id}/estancias/{eid}/cuenta     → Desglose del cobro y pagos
- POST /operacion/parqueaderos/{id}/cuentas/{cid}/pagos        → Registrar un pago (total o parcial)
- POST /operacion/parqueaderos/{id}/reservas/{rid}/pago-anticipado → Pago anticipado de una reserva
- GET  /operacion/parqueaderos/{id}/comprobantes               → Historial de facturas

Los mensajes para el operario viajan en la sesión ("avisos") y se muestran
después de redirigir: así recargar la página no repite el registro.
"""

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import obtener_parqueadero_operable, requiere_rol
from app.models import (
    Comprobante,
    Cuenta,
    MetodoPago,
    Espacio,
    EstadoEspacio,
    EstadoIncidencia,
    EstadoPagoCuenta,
    EstadoParqueadero,
    Incidencia,
    EstadoReserva,
    Estancia,
    Parqueadero,
    ParqueaderoOperario,
    Reserva,
    RolUsuario,
    TipoVehiculo,
    Usuario,
)
from app.schemas import IngresoDirecto
from app.services.ocupacion import (
    OperacionError,
    hora_texto,
    orden_codigo,
    registrar_entrada_reserva,
    registrar_ingreso_directo,
    registrar_salida,
)
from app.services.pagos import facturas_de_cuenta, registrar_pago, registrar_pago_anticipado, saldo
from app.services.reservas import ahora_negocio, instante_local
from app.services.retrasos import procesar_retrasos

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["hora_local"] = lambda instante: hora_texto(instante) if instante else ""
# 12500 → "$12.500" (separador de miles colombiano)
templates.env.filters["pesos"] = lambda valor: "$" + f"{valor or 0:,.0f}".replace(",", ".")

router = APIRouter(prefix="/operacion", tags=["operacion"])

# Solo propietarios y operarios; cuál parqueadero se revisa en cada ruta
operador = requiere_rol(RolUsuario.PROPIETARIO, RolUsuario.OPERARIO)

MENSAJES_INGRESO = {
    "placa": "La placa debe tener entre 4 y 10 letras o números.",
    "tipo": "Elige el tipo de vehículo: carro, moto o bus.",
    "nombre": "El nombre puede tener como máximo 100 caracteres.",
    "telefono": "El teléfono solo puede tener números, espacios, guiones y un + inicial (7 a 20 caracteres).",
}


def avisar(request: Request, tipo: str, texto: str):
    """Guarda un mensaje para mostrarlo en la próxima página (exito, advertencia o error)."""
    request.session.setdefault("avisos", []).append({"tipo": tipo, "texto": texto})


def ir_al_panel(parqueadero_id: int):
    """Redirección 303 (POST → GET) al panel del parqueadero."""
    return RedirectResponse(url=f"/operacion/parqueaderos/{parqueadero_id}", status_code=303)


def exigir_activo(parqueadero: Parqueadero):
    """Solo un parqueadero aprobado (activo) recibe vehículos."""
    if parqueadero.estado != EstadoParqueadero.ACTIVO:
        raise OperacionError("El parqueadero no está activo: no puede registrar entradas.")


# ============================================
# LISTA Y PANEL
# ============================================

@router.get("", response_class=HTMLResponse)
async def mis_parqueaderos_operables(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """Parqueaderos que el usuario puede operar."""
    if usuario.rol == RolUsuario.PROPIETARIO:
        parqueaderos = db.query(Parqueadero).filter(Parqueadero.propietario_id == usuario.id)
    else:
        parqueaderos = db.query(Parqueadero).join(
            ParqueaderoOperario, ParqueaderoOperario.parqueadero_id == Parqueadero.id
        ).filter(
            ParqueaderoOperario.usuario_id == usuario.id,
            ParqueaderoOperario.activo.is_(True),
        )
    return templates.TemplateResponse(
        "operacion/lista.html",
        {
            "request": request,
            "titulo": "Operación - ParkSmart",
            "parqueaderos": parqueaderos.order_by(Parqueadero.nombre).all(),
        },
    )


@router.get("/parqueaderos/{parqueadero_id}", response_class=HTMLResponse)
async def panel_operacion(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """
    Panel de la puerta: estado de cada plaza, vehículos dentro, reservas
    del día y el formulario de ingreso directo.
    """
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    ahora = ahora_negocio()

    # Respaldo de la tarea programada: si pg_cron se retrasa o falla, abrir
    # el panel también aplica el protocolo de retraso (es idempotente).
    procesar_retrasos(db, ahora, parqueadero.id)

    alertas = db.query(Incidencia).filter(
        Incidencia.parqueadero_id == parqueadero.id,
        Incidencia.estado == EstadoIncidencia.ABIERTA,
    ).order_by(Incidencia.creado_en.desc()).all()

    espacios = sorted(
        db.query(Espacio).filter(Espacio.parqueadero_id == parqueadero.id).all(),
        key=orden_codigo,
    )
    dentro = db.query(Estancia).filter(
        Estancia.parqueadero_id == parqueadero.id,
        Estancia.salida_en.is_(None),
    ).order_by(Estancia.entrada_en).all()
    por_espacio = {e.espacio_id: e for e in dentro}

    # Reservas de hoy (hora de Colombia) que siguen en la agenda
    inicio_dia = instante_local(ahora.date(), "00:00")
    reservas_hoy = db.query(Reserva).join(Espacio).filter(
        Espacio.parqueadero_id == parqueadero.id,
        Reserva.estado.in_([EstadoReserva.CONFIRMADA, EstadoReserva.EN_CURSO, EstadoReserva.PENDIENTE]),
        Reserva.inicio < inicio_dia + timedelta(days=1),
        Reserva.fin > inicio_dia,
    ).order_by(Reserva.inicio).all()

    plazas = []
    for espacio in espacios:
        estancia = por_espacio.get(espacio.id)
        if espacio.estado == EstadoEspacio.MANTENIMIENTO:
            situacion = "mantenimiento"
        elif estancia is None:
            situacion = "libre"
        elif estancia.reserva_id:
            situacion = "reserva"
        else:
            situacion = "directo"
        plazas.append({"espacio": espacio, "situacion": situacion, "estancia": estancia})

    # Reservas que esperan el pago anticipado (aún vigentes)
    pendientes_pago = db.query(Reserva).join(Espacio).filter(
        Espacio.parqueadero_id == parqueadero.id,
        Reserva.estado == EstadoReserva.PENDIENTE,
        Reserva.fin > ahora,
    ).order_by(Reserva.inicio).all()

    # Salidas ya liquidadas que todavía deben dinero
    cuentas_con_saldo = db.query(Cuenta).filter(
        Cuenta.parqueadero_id == parqueadero.id,
        Cuenta.liquidada_en.is_not(None),
        Cuenta.estado_pago.in_([EstadoPagoCuenta.PENDIENTE, EstadoPagoCuenta.PARCIAL]),
    ).order_by(Cuenta.liquidada_en.desc()).all()

    avisos = request.session.pop("avisos", [])
    return templates.TemplateResponse(
        "operacion/panel.html",
        {
            "request": request,
            "titulo": f"Operación · {parqueadero.nombre} - ParkSmart",
            "parqueadero": parqueadero,
            "plazas": plazas,
            "dentro": dentro,
            "reservas_hoy": reservas_hoy,
            "avisos": avisos,
            "alertas": alertas,
            "pendientes_pago": pendientes_pago,
            "cuentas_con_saldo": [(c, saldo(c)) for c in cuentas_con_saldo],
            "metodos": METODOS_CAJA,
            "nueva_clave": lambda: uuid.uuid4().hex,
            "tipos": [t for t in TipoVehiculo if parqueadero.tarifa_para(t) is not None],
            "ahora": ahora,
        },
    )


# ============================================
# INGRESO DIRECTO, ENTRADA CON RESERVA Y SALIDA
# ============================================

@router.post("/parqueaderos/{parqueadero_id}/ingreso-directo")
async def ingreso_directo(
    request: Request,
    parqueadero_id: int,
    placa: str = Form(""),
    tipo: str = Form(""),
    nombre: str = Form(""),
    telefono: str = Form(""),
    salida_estimada: str = Form(""),
    espacio_id: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """
    Registra un vehículo sin reserva y le asigna una plaza: la que elija el
    operario (espacio_id) o, si no elige, la primera libre del tipo.

    salida_estimada es opcional ("HH:MM", hoy): si llega, la plaza elegida
    debe quedar libre con el margen antes de su próxima reserva.
    """
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    try:
        datos = IngresoDirecto(placa=placa, tipo=tipo, nombre=nombre, telefono=telefono)
    except ValidationError as error:
        campo = error.errors()[0]["loc"][0] if error.errors()[0]["loc"] else None
        avisar(request, "error", MENSAJES_INGRESO.get(campo, "Revisa los datos del vehículo."))
        return ir_al_panel(parqueadero.id)

    ahora = ahora_negocio()
    try:
        exigir_activo(parqueadero)
        salida = None
        if salida_estimada.strip():
            try:
                salida = instante_local(ahora.date(), salida_estimada.strip())
            except ValueError:
                raise OperacionError("La salida estimada debe tener el formato HH:MM.")
        estancia, advertencia = registrar_ingreso_directo(
            db, parqueadero, usuario,
            placa=datos.placa,
            tipo=TipoVehiculo(datos.tipo),
            ahora=ahora,
            nombre=datos.nombre,
            telefono=datos.telefono,
            salida_estimada=salida,
            espacio_id=int(espacio_id) if espacio_id.strip().isdigit() else None,
        )
    except OperacionError as error:
        avisar(request, "error", str(error))
        return ir_al_panel(parqueadero.id)

    avisar(request, "exito", f"{estancia.placa} entró a la plaza {estancia.espacio.numero}.")
    if advertencia:
        avisar(request, "advertencia", advertencia)
    return ir_al_panel(parqueadero.id)


@router.post("/parqueaderos/{parqueadero_id}/reservas/{reserva_id}/entrada")
async def entrada_con_reserva(
    request: Request,
    parqueadero_id: int,
    reserva_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """Registra la llegada de un cliente con reserva (repetirla no duplica)."""
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    try:
        exigir_activo(parqueadero)
        estancia, repetida = registrar_entrada_reserva(db, parqueadero, usuario, reserva_id, ahora_negocio())
    except OperacionError as error:
        avisar(request, "error", str(error))
        return ir_al_panel(parqueadero.id)

    if repetida:
        avisar(request, "advertencia", f"La entrada de {estancia.placa} ya estaba registrada.")
    else:
        avisar(request, "exito", f"{estancia.placa} entró a la plaza {estancia.espacio.numero} (con reserva).")
    return ir_al_panel(parqueadero.id)


@router.post("/parqueaderos/{parqueadero_id}/estancias/{estancia_id}/salida")
async def salida(
    request: Request,
    parqueadero_id: int,
    estancia_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """Registra la salida, libera la plaza y muestra el cobro."""
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    try:
        registrar_salida(db, parqueadero, usuario, estancia_id, ahora_negocio())
    except OperacionError as error:
        avisar(request, "error", str(error))
        return ir_al_panel(parqueadero.id)
    return RedirectResponse(
        url=f"/operacion/parqueaderos/{parqueadero.id}/estancias/{estancia_id}/cuenta",
        status_code=303,
    )


@router.get("/parqueaderos/{parqueadero_id}/estancias/{estancia_id}/cuenta", response_class=HTMLResponse)
async def ver_cuenta(
    request: Request,
    parqueadero_id: int,
    estancia_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """Desglose del cobro de una estancia ya cerrada."""
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    cuenta = db.query(Cuenta).join(Estancia, Cuenta.estancia_id == Estancia.id).filter(
        Estancia.id == estancia_id,
        Estancia.parqueadero_id == parqueadero.id,
    ).first()
    if cuenta is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")

    return templates.TemplateResponse(
        "operacion/cuenta.html",
        {
            "request": request,
            "titulo": "Cobro - ParkSmart",
            "parqueadero": parqueadero,
            "cuenta": cuenta,
            "estancia": cuenta.estancia,
            "saldo": saldo(cuenta),
            "pagos": cuenta.pagos,
            "facturas": facturas_de_cuenta(db, cuenta.id),
            "metodos": METODOS_CAJA,
            # Una clave nueva por cada vez que se muestra el formulario: si se
            # envía dos veces (doble clic), el segundo envío no cobra otra vez
            "clave_pago": uuid.uuid4().hex,
            "avisos": request.session.pop("avisos", []),
        },
    )


# ============================================
# PAGOS Y COMPROBANTES (Módulo 4)
# ============================================

# Métodos que registra la caja (la pasarela llega por su propio flujo)
METODOS_CAJA = [MetodoPago.EFECTIVO, MetodoPago.TARJETA, MetodoPago.TRANSFERENCIA]


def leer_monto(texto: str) -> Decimal:
    """
    Convierte el monto del formulario a Decimal.

    Acepta "3000", "1500.50" (punto decimal, lo que envía <input type=number>)
    y "3.000" (punto de miles, como se escribe en Colombia).
    """
    texto = (texto or "").strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", texto):
        raise OperacionError("Escribe el monto en pesos, por ejemplo 5000.")
    return Decimal(texto)


def leer_metodo(metodo: str) -> MetodoPago:
    """Valida el método de pago del formulario."""
    try:
        valor = MetodoPago(metodo)
    except ValueError:
        raise OperacionError("Elige un método de pago válido.")
    if valor not in METODOS_CAJA:
        raise OperacionError("Elige un método de pago válido.")
    return valor


@router.post("/parqueaderos/{parqueadero_id}/cuentas/{cuenta_id}/pagos")
async def pagar_cuenta(
    request: Request,
    parqueadero_id: int,
    cuenta_id: int,
    metodo: str = Form(""),
    monto: str = Form(""),
    clave_idempotencia: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """
    Registra un pago (total o parcial) de una cuenta del parqueadero.

    La cuenta se busca DENTRO del parqueadero autorizado: cambiar el id en
    la URL no permite cobrar cuentas de otro propietario.
    """
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    cuenta = db.query(Cuenta).filter(
        Cuenta.id == cuenta_id,
        Cuenta.parqueadero_id == parqueadero.id,
    ).first()
    if cuenta is None or cuenta.estancia_id is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    destino = f"/operacion/parqueaderos/{parqueadero.id}/estancias/{cuenta.estancia_id}/cuenta"

    try:
        if not clave_idempotencia.strip() or len(clave_idempotencia) > 64:
            raise OperacionError("Recarga la página e intenta de nuevo.")
        _, comprobante = registrar_pago(
            db, cuenta, leer_metodo(metodo), leer_monto(monto), clave_idempotencia.strip(), usuario, ahora_negocio()
        )
    except OperacionError as error:
        db.rollback()
        avisar(request, "error", str(error))
        return RedirectResponse(url=destino, status_code=303)

    avisar(request, "exito", "Pago registrado.")
    if comprobante:
        avisar(request, "exito", f"Factura N.º {comprobante.numero} emitida.")
    return RedirectResponse(url=destino, status_code=303)


@router.post("/parqueaderos/{parqueadero_id}/reservas/{reserva_id}/pago-anticipado")
async def pago_anticipado(
    request: Request,
    parqueadero_id: int,
    reserva_id: int,
    metodo: str = Form(""),
    clave_idempotencia: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """Registra el pago anticipado del total de una reserva pendiente y la confirma."""
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    reserva = db.query(Reserva).join(Espacio).filter(
        Reserva.id == reserva_id,
        Espacio.parqueadero_id == parqueadero.id,
    ).first()
    if reserva is None:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    try:
        if not clave_idempotencia.strip() or len(clave_idempotencia) > 64:
            raise OperacionError("Recarga la página e intenta de nuevo.")
        pago = registrar_pago_anticipado(
            db, reserva, leer_metodo(metodo), clave_idempotencia.strip(), usuario, ahora_negocio()
        )
    except OperacionError as error:
        db.rollback()
        avisar(request, "error", str(error))
        return ir_al_panel(parqueadero.id)

    avisar(request, "exito", f"Pago anticipado de {templates.env.filters['pesos'](pago.monto)} registrado: "
                             "reserva confirmada y factura emitida.")
    return ir_al_panel(parqueadero.id)


@router.get("/parqueaderos/{parqueadero_id}/comprobantes", response_class=HTMLResponse)
async def historial_comprobantes(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(operador),
):
    """Historial de facturas del parqueadero, de la más reciente a la más antigua."""
    parqueadero = obtener_parqueadero_operable(db, parqueadero_id, usuario)
    comprobantes = db.query(Comprobante).filter(
        Comprobante.parqueadero_id == parqueadero.id
    ).order_by(Comprobante.numero.desc()).all()
    return templates.TemplateResponse(
        "operacion/comprobantes.html",
        {
            "request": request,
            "titulo": f"Facturas · {parqueadero.nombre} - ParkSmart",
            "parqueadero": parqueadero,
            "comprobantes": comprobantes,
        },
    )
