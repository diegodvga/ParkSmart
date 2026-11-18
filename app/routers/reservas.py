"""
Router de reservas para ParkSmart.
Rutas (solo clientes):
- GET  /parqueaderos/{id}/reservar         → Formulario con el selector de plazas
- GET  /parqueaderos/{id}/reservar/plazas  → Cuadrícula de plazas para un horario (HTML parcial)
- POST /parqueaderos/{id}/reservar         → Crear la reserva
- GET  /reservas/{id}/editar               → Formulario para modificar una reserva
- POST /reservas/{id}/editar               → Guardar los cambios
- GET  /reservas/nueva/{espacio_id}        → Enlace antiguo: redirige al formulario con la plaza elegida
- POST /reservas/nueva/{espacio_id}        → Enlace antiguo: crea la reserva en esa plaza
- GET  /reservas/{id}/pagar                → Pasarela de pago simulada (tarjeta o transferencia)
- POST /reservas/{id}/pagar                → Pagar: si se aprueba, confirma la reserva y emite la factura
- GET  /mis-reservas                       → Lista de reservas del usuario
- POST /reservas/{id}/cancelar             → Cancelar una reserva
- POST /reservas/{id}/extender             → Extender el fin de una reserva
"""

import uuid
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import requiere_rol
from app.models import (
    Comprobante, Cuenta, Espacio, EstadoIncidencia, EstadoPago, EstadoParqueadero, EstadoReserva, Incidencia,
    MetodoPago, MotivoCancelacion, Notificacion, Parqueadero, Reserva, RolUsuario, TipoIncidencia, TipoPago,
    Vehiculo,
)
from app.services.ocupacion import OperacionError
from app.services.pagos import pagar_reserva_en_linea, reembolso_por_cancelacion, saldo
from app.services.pasarela import BANCOS, respuesta_tarjeta, respuesta_transferencia
from app.services.reservas import (
    FORMA_DIGITAL, LEYENDA_RESERVA, ZONA_NEGOCIO, ReservaError, ahora_negocio, guardar_reserva, horario,
    instante_local, plazas_para_reserva,
)
from app.services.retrasos import extender_reserva

# Configurar templates
templates = Jinja2Templates(directory="app/templates")
# Muestra un instante en hora de Colombia: {{ r.inicio|local('%H:%M') }}
templates.env.filters["local"] = lambda instante, formato: instante.astimezone(ZONA_NEGOCIO).strftime(formato)

# dependencies=[...] aplica requiere_rol a TODAS las rutas de este router:
# solo los clientes hacen y ven sus reservas.
router = APIRouter(
    prefix="",
    tags=["reservas"],
    dependencies=[Depends(requiere_rol(RolUsuario.CLIENTE))]
)


def obtener_parqueadero_reservable(db: Session, parqueadero_id: int) -> Parqueadero:
    """Solo se reserva en parqueaderos ACTIVOS (lo demás no está en el catálogo)."""
    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id,
        Parqueadero.estado == EstadoParqueadero.ACTIVO,
    ).first()
    if parqueadero is None:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")
    return parqueadero


def obtener_reserva_propia(db: Session, reserva_id: int, usuario_id: int) -> Reserva:
    """Una reserva del cliente en sesión; la de otro responde 404 (no se revela que existe)."""
    reserva = db.query(Reserva).filter(Reserva.id == reserva_id, Reserva.usuario_id == usuario_id).first()
    if reserva is None:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    return reserva


def a_entero(valor) -> Optional[int]:
    """Convierte un parámetro del formulario a entero (None si no lo es)."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def tarjetas_de_plazas(db, parqueadero, vehiculos, valores, reserva=None):
    """
    Calcula la cuadrícula para lo que el cliente tiene escrito: su vehículo
    (define el tipo de plaza), la fecha y las horas (definen la agenda).
    """
    vehiculo = next((v for v in vehiculos if v.id == a_entero(valores.get("vehiculo_id"))), None)
    inicio, fin = horario(valores.get("fecha"), valores.get("hora_entrada"), valores.get("hora_salida"))
    tarjetas = plazas_para_reserva(
        db, parqueadero, vehiculo.tipo if vehiculo else None, inicio, fin,
        seleccionada_id=a_entero(valores.get("espacio_id")),
        excluir_reserva_id=reserva.id if reserva else None,
    )
    if vehiculo is None:
        aviso = "Elige tu vehículo para ver las plazas compatibles."
    elif inicio is None:
        aviso = "Elige la fecha y las horas de entrada y salida para ver qué plazas están libres en ese horario."
    elif not any(t["seleccionable"] for t in tarjetas):
        aviso = "No hay plazas libres para tu vehículo en ese horario. Prueba con otro horario."
    else:
        aviso = (f"Plazas libres de {valores['hora_entrada']} a {valores['hora_salida']}, "
                 f"con {parqueadero.margen_minutos} minutos de margen entre reservas. Elige una.")
    return tarjetas, aviso


def pagina_reserva(request, db, parqueadero, usuario_id, valores, reserva=None, error=None):
    """Formulario de reserva (crear o editar) con la cuadrícula ya calculada."""
    vehiculos = db.query(Vehiculo).filter(Vehiculo.usuario_id == usuario_id).all()
    if not valores.get("vehiculo_id") and vehiculos:
        valores["vehiculo_id"] = vehiculos[0].id
    tarjetas, aviso = tarjetas_de_plazas(db, parqueadero, vehiculos, valores, reserva)
    if reserva is not None:
        accion = f"/reservas/{reserva.id}/editar"
    else:
        accion = f"/parqueaderos/{parqueadero.id}/reservar"
    return templates.TemplateResponse(
        "reservar.html",
        {
            "request": request,
            "titulo": ("Modificar reserva" if reserva else "Reservar") + " - ParkSmart",
            "parqueadero": parqueadero,
            "vehiculos": vehiculos,
            "valores": valores,
            "reserva": reserva,
            "accion": accion,
            "plazas": tarjetas,
            "leyenda": LEYENDA_RESERVA,
            "aviso_plazas": aviso,
            "error": error,
            "hoy": ahora_negocio().date().isoformat(),
        },
        status_code=400 if error else 200,
    )


def valores_de_formulario(vehiculo_id, fecha, hora_entrada, hora_salida, espacio_id, forma_pago=None) -> dict:
    """Lo que escribió el cliente, para volver a mostrarlo si algo falla."""
    return {
        "vehiculo_id": a_entero(vehiculo_id),
        "fecha": (fecha or "").strip(),
        "hora_entrada": (hora_entrada or "").strip(),
        "hora_salida": (hora_salida or "").strip(),
        "espacio_id": a_entero(espacio_id),
        # None = no llegó el campo (enlace antiguo); "" = llegó vacío
        "forma_pago": forma_pago.strip() if forma_pago is not None else None,
    }


def guardar_o_mostrar_error(request, db, parqueadero, usuario_id, valores, reserva=None):
    """
    Guarda la reserva o vuelve a mostrar el formulario con el error.

    Si falla (por ejemplo, la plaza se ocupó mientras el cliente decidía),
    se conservan fecha, horario y vehículo, se recalcula la cuadrícula y la
    plaza elegida queda resaltada solo si todavía se puede elegir.
    """
    forma_pago = valores.get("forma_pago") if reserva is None else None
    try:
        guardada = guardar_reserva(
            db, usuario_id, parqueadero, valores["espacio_id"], valores["vehiculo_id"],
            valores["fecha"], valores["hora_entrada"], valores["hora_salida"], reserva,
            forma_pago=forma_pago,
        )
    except ReservaError as error:
        return pagina_reserva(request, db, parqueadero, usuario_id, valores, reserva, str(error))
    if forma_pago == FORMA_DIGITAL:
        # Pago con tarjeta o transferencia: directo a la pasarela simulada
        return RedirectResponse(url=f"/reservas/{guardada.id}/pagar", status_code=303)
    return RedirectResponse(url="/mis-reservas", status_code=302)


# ============================================
# RUTAS
# ============================================

@router.get("/parqueaderos/{parqueadero_id}/reservar", response_class=HTMLResponse)
async def formulario_reservar(
    request: Request,
    parqueadero_id: int,
    vehiculo_id: Optional[str] = None,
    fecha: Optional[str] = None,
    hora_entrada: Optional[str] = None,
    hora_salida: Optional[str] = None,
    espacio_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Formulario de reserva con el selector de plazas del parqueadero."""
    parqueadero = obtener_parqueadero_reservable(db, parqueadero_id)
    valores = valores_de_formulario(vehiculo_id, fecha or ahora_negocio().date().isoformat(),
                                    hora_entrada, hora_salida, espacio_id)
    return pagina_reserva(request, db, parqueadero, request.session.get("usuario_id"), valores)


@router.get("/parqueaderos/{parqueadero_id}/reservar/plazas", response_class=HTMLResponse)
async def cuadricula_plazas(
    request: Request,
    parqueadero_id: int,
    vehiculo_id: Optional[str] = None,
    fecha: Optional[str] = None,
    hora_entrada: Optional[str] = None,
    hora_salida: Optional[str] = None,
    espacio_id: Optional[str] = None,
    reserva_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Solo la cuadrícula, para que la página la actualice sin recargar cuando
    el cliente cambia la fecha, las horas o el vehículo.
    """
    parqueadero = obtener_parqueadero_reservable(db, parqueadero_id)
    usuario_id = request.session.get("usuario_id")
    reserva = None
    if a_entero(reserva_id) is not None:
        # Solo se excluye una reserva PROPIA (no se puede alterar la agenda ajena)
        reserva = db.query(Reserva).filter(
            Reserva.id == a_entero(reserva_id), Reserva.usuario_id == usuario_id
        ).first()
    vehiculos = db.query(Vehiculo).filter(Vehiculo.usuario_id == usuario_id).all()
    valores = valores_de_formulario(vehiculo_id, fecha, hora_entrada, hora_salida, espacio_id)
    tarjetas, aviso = tarjetas_de_plazas(db, parqueadero, vehiculos, valores, reserva)
    return templates.TemplateResponse(
        "componentes/plazas_reserva.html",
        {"request": request, "plazas": tarjetas, "leyenda": LEYENDA_RESERVA, "aviso_plazas": aviso},
    )


@router.post("/parqueaderos/{parqueadero_id}/reservar")
async def reservar(
    request: Request,
    parqueadero_id: int,
    vehiculo_id: str = Form(""),
    fecha: str = Form(""),
    hora_entrada: str = Form(""),
    hora_salida: str = Form(""),
    espacio_id: str = Form(""),
    forma_pago: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Crea la reserva en la plaza elegida, revalidando todo en el servidor."""
    parqueadero = obtener_parqueadero_reservable(db, parqueadero_id)
    valores = valores_de_formulario(vehiculo_id, fecha, hora_entrada, hora_salida, espacio_id, forma_pago)
    return guardar_o_mostrar_error(request, db, parqueadero, request.session.get("usuario_id"), valores)


@router.get("/reservas/{reserva_id}/editar", response_class=HTMLResponse)
async def formulario_editar(
    request: Request,
    reserva_id: int,
    db: Session = Depends(get_db)
):
    """Formulario para modificar una reserva propia, con su plaza actual elegida."""
    usuario_id = request.session.get("usuario_id")
    reserva = obtener_reserva_propia(db, reserva_id, usuario_id)
    valores = {
        "vehiculo_id": reserva.vehiculo_id,
        "fecha": reserva.inicio.astimezone(ZONA_NEGOCIO).date().isoformat(),
        "hora_entrada": reserva.inicio.astimezone(ZONA_NEGOCIO).strftime("%H:%M"),
        "hora_salida": reserva.fin.astimezone(ZONA_NEGOCIO).strftime("%H:%M"),
        "espacio_id": reserva.espacio_id,
    }
    return pagina_reserva(request, db, reserva.espacio.parqueadero, usuario_id, valores, reserva)


@router.post("/reservas/{reserva_id}/editar")
async def editar_reserva(
    request: Request,
    reserva_id: int,
    vehiculo_id: str = Form(""),
    fecha: str = Form(""),
    hora_entrada: str = Form(""),
    hora_salida: str = Form(""),
    espacio_id: str = Form(""),
    db: Session = Depends(get_db)
):
    """Guarda los cambios de una reserva propia (mismas validaciones que al crear)."""
    usuario_id = request.session.get("usuario_id")
    reserva = obtener_reserva_propia(db, reserva_id, usuario_id)
    valores = valores_de_formulario(vehiculo_id, fecha, hora_entrada, hora_salida, espacio_id)
    return guardar_o_mostrar_error(request, db, reserva.espacio.parqueadero, usuario_id, valores, reserva)


def espacio_reservable(db: Session, espacio_id: int) -> Espacio:
    """Plaza de un parqueadero activo (para los enlaces antiguos por plaza)."""
    espacio = db.query(Espacio).join(Parqueadero).filter(
        Espacio.id == espacio_id,
        Parqueadero.estado == EstadoParqueadero.ACTIVO,
    ).first()
    if espacio is None:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")
    return espacio


@router.get("/reservas/nueva/{espacio_id}")
async def enlace_antiguo_reserva(espacio_id: int, db: Session = Depends(get_db)):
    """Enlace antiguo por plaza: lleva al formulario nuevo con esa plaza preelegida."""
    espacio = espacio_reservable(db, espacio_id)
    return RedirectResponse(
        url=f"/parqueaderos/{espacio.parqueadero_id}/reservar?{urlencode({'espacio_id': espacio.id})}",
        status_code=303,
    )


@router.post("/reservas/nueva/{espacio_id}")
async def crear_reserva(
    request: Request,
    espacio_id: int,
    vehiculo_id: str = Form(""),
    fecha: str = Form(""),
    hora_entrada: str = Form(""),
    hora_salida: str = Form(""),
    db: Session = Depends(get_db)
):
    """Enlace antiguo: crea la reserva en la plaza de la URL (mismas validaciones)."""
    espacio = espacio_reservable(db, espacio_id)
    valores = valores_de_formulario(vehiculo_id, fecha, hora_entrada, hora_salida, espacio.id)
    return guardar_o_mostrar_error(request, db, espacio.parqueadero, request.session.get("usuario_id"), valores)


@router.get("/mis-reservas", response_class=HTMLResponse)
async def listar_reservas(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra la lista de reservas del usuario autenticado.
    """
    return pagina_mis_reservas(request, db, request.session.get("usuario_id"))


def pagina_mis_reservas(request, db, usuario_id, error=None, status_code=200):
    """
    Arma la página "Mis reservas": reservas del cliente y sus avisos
    (recordatorios, reasignaciones, incidencias). Se reutiliza para mostrar
    un error, por ejemplo una extensión rechazada.
    """
    reservas = db.query(Reserva).filter(
        Reserva.usuario_id == usuario_id
    ).order_by(Reserva.inicio.desc()).all()

    # Enriquecer con datos relacionados
    reservas_enriquecidas = []
    for r in reservas:
        espacio = db.query(Espacio).filter(Espacio.id == r.espacio_id).first()
        parqueadero = db.query(Parqueadero).filter(Parqueadero.id == espacio.parqueadero_id).first() if espacio else None
        vehiculo = db.query(Vehiculo).filter(Vehiculo.id == r.vehiculo_id).first()

        facturas = db.query(Comprobante).join(Cuenta, Comprobante.cuenta_id == Cuenta.id).filter(
            Cuenta.reserva_id == r.id
        ).order_by(Comprobante.numero).all()
        cuenta = db.query(Cuenta).filter(Cuenta.reserva_id == r.id).first()
        pagada = cuenta is not None and any(
            p.estado == EstadoPago.PAGADO and p.tipo != TipoPago.REEMBOLSO for p in cuenta.pagos)

        reservas_enriquecidas.append({
            "reserva": r,
            "espacio": espacio,
            "parqueadero": parqueadero,
            "vehiculo": vehiculo,
            "facturas": facturas,
            "pagada": pagada,
        })

    avisos = db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario_id
    ).order_by(Notificacion.creado_en.desc()).limit(10).all()

    return templates.TemplateResponse(
        "mis_reservas.html",
        {
            "request": request,
            "titulo": "Mis Reservas - ParkSmart",
            "reservas": reservas_enriquecidas,
            "avisos": avisos,
            "error": error,
            "ahora": ahora_negocio(),
        },
        status_code=status_code,
    )


# ============================================
# PASARELA DE PAGO SIMULADA
# ============================================

def monto_por_pagar(db: Session, reserva: Reserva) -> Optional[float]:
    """Lo que falta pagar de la reserva (None si no se puede pagar en línea)."""
    if reserva.estado not in (EstadoReserva.PENDIENTE, EstadoReserva.CONFIRMADA):
        return None
    cuenta = db.query(Cuenta).filter(Cuenta.reserva_id == reserva.id).first()
    if cuenta is None or not any(p.estado == EstadoPago.PAGADO for p in cuenta.pagos):
        return reserva.total
    pendiente = float(saldo(cuenta))
    return pendiente if pendiente > 0 else None


def pagina_pago(request, db, reserva, error=None, valores=None, status_code=200):
    """Pantalla de la pasarela simulada. Nunca vuelve a mostrar el número de tarjeta."""
    return templates.TemplateResponse(
        "pagar.html",
        {
            "request": request,
            "titulo": "Pagar reserva - ParkSmart",
            "reserva": reserva,
            "parqueadero": reserva.espacio.parqueadero,
            "monto": monto_por_pagar(db, reserva),
            "bancos": BANCOS,
            "valores": valores or {},
            "error": error,
            # Una clave nueva cada vez que se muestra: un doble clic no cobra dos veces
            "clave_pago": uuid.uuid4().hex,
        },
        status_code=status_code,
    )


@router.get("/reservas/{reserva_id}/pagar", response_class=HTMLResponse)
async def formulario_pago(
    request: Request,
    reserva_id: int,
    db: Session = Depends(get_db)
):
    """Pasarela simulada para pagar una reserva propia con tarjeta o transferencia."""
    reserva = obtener_reserva_propia(db, reserva_id, request.session.get("usuario_id"))
    if monto_por_pagar(db, reserva) is None:
        return RedirectResponse(url="/mis-reservas", status_code=303)
    return pagina_pago(request, db, reserva)


@router.post("/reservas/{reserva_id}/pagar")
async def pagar_reserva(
    request: Request,
    reserva_id: int,
    metodo: str = Form(""),
    titular: str = Form(""),
    numero_tarjeta: str = Form(""),
    vencimiento: str = Form(""),
    cvv: str = Form(""),
    banco: str = Form(""),
    documento: str = Form(""),
    clave_idempotencia: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Procesa el pago simulado. Si el banco lo aprueba, la reserva queda
    confirmada y se muestra su factura; si lo rechaza, la reserva sigue
    pendiente y el cliente puede intentar de nuevo.
    """
    reserva = obtener_reserva_propia(db, reserva_id, request.session.get("usuario_id"))
    valores = {"metodo": metodo, "titular": titular.strip(), "banco": banco}   # sin datos de tarjeta
    ahora = ahora_negocio()
    try:
        clave = clave_idempotencia.strip()
        if not clave or len(clave) > 64:
            raise OperacionError("Recarga la página e intenta de nuevo.")
        if metodo == MetodoPago.TARJETA.value:
            aprobado = respuesta_tarjeta(titular, numero_tarjeta, vencimiento, cvv, ahora)
        elif metodo == MetodoPago.TRANSFERENCIA.value:
            aprobado = respuesta_transferencia(banco, documento)
        else:
            raise OperacionError("Elige tarjeta o transferencia.")
        pago, factura = pagar_reserva_en_linea(db, reserva, MetodoPago(metodo), aprobado, clave, ahora)
    except OperacionError as error:
        db.rollback()
        db.refresh(reserva)
        return pagina_pago(request, db, reserva, str(error), valores, status_code=400)

    if pago.estado != EstadoPago.PAGADO or factura is None:
        db.refresh(reserva)
        return pagina_pago(
            request, db, reserva,
            "El banco rechazó el pago. No se te cobró nada: prueba con otra tarjeta u otro medio.",
            valores, status_code=400,
        )
    return RedirectResponse(url=f"/comprobantes/{factura.id}?pagado=1", status_code=303)


@router.post("/reservas/{reserva_id}/extender")
async def extender(
    request: Request,
    reserva_id: int,
    nueva_hora_fin: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Extiende una reserva propia hasta nueva_hora_fin ("HH:MM", mismo día
    del fin actual en hora de Colombia).

    Se aprueba sola si respeta la siguiente reserva de la plaza y su margen;
    el tiempo agregado se cobra a la tarifa congelada de la reserva.
    """
    usuario_id = request.session.get("usuario_id")
    reserva = db.query(Reserva).filter(
        Reserva.id == reserva_id,
        Reserva.usuario_id == usuario_id
    ).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")

    try:
        dia_fin = reserva.fin.astimezone(ZONA_NEGOCIO).date()
        nuevo_fin = instante_local(dia_fin, nueva_hora_fin.strip())
    except ValueError:
        return pagina_mis_reservas(request, db, usuario_id, "La hora debe tener el formato HH:MM.", 400)

    try:
        extender_reserva(db, reserva, nuevo_fin, ahora_negocio())
    except OperacionError as error:
        db.rollback()
        return pagina_mis_reservas(request, db, usuario_id, str(error), 400)

    return RedirectResponse(url="/mis-reservas", status_code=302)


@router.post("/reservas/{reserva_id}/cancelar")
async def cancelar_reserva(
    request: Request,
    reserva_id: int,
    db: Session = Depends(get_db)
):
    """
    Cancela una reserva (solo si está pendiente o confirmada).
    """
    usuario_id = request.session.get("usuario_id")

    reserva = db.query(Reserva).filter(
        Reserva.id == reserva_id,
        Reserva.usuario_id == usuario_id
    ).first()

    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrado")

    # Solo se pueden cancelar reservas pendientes o confirmadas
    if reserva.estado not in [EstadoReserva.PENDIENTE.value, EstadoReserva.CONFIRMADA.value]:
        raise HTTPException(status_code=400, detail="No se puede cancelar esta reserva")

    ahora = ahora_negocio()
    sin_alternativa = db.query(Incidencia.id).filter(
        Incidencia.reserva_id == reserva.id,
        Incidencia.tipo == TipoIncidencia.SIN_ALTERNATIVA,
        Incidencia.estado == EstadoIncidencia.ABIERTA,
    ).first() is not None

    # Reembolso según la política (antes de cerrar la incidencia, porque
    # "sin plaza alternativa" da derecho al 100 %)
    reembolso_por_cancelacion(db, reserva, ahora)

    # Al quedar cancelada, deja de ocupar la agenda (el EXCLUDE ignora las
    # canceladas). No se toca espacio.estado: reservar nunca lo marcó ocupado.
    reserva.estado = EstadoReserva.CANCELADA.value
    reserva.motivo_cancelacion = (
        MotivoCancelacion.SIN_ALTERNATIVA if sin_alternativa else MotivoCancelacion.CLIENTE
    )
    reserva.cancelada_en = ahora
    # Las alertas de esta reserva ya no aplican
    db.query(Incidencia).filter(
        Incidencia.reserva_id == reserva.id,
        Incidencia.estado == EstadoIncidencia.ABIERTA,
    ).update(
        {Incidencia.estado: EstadoIncidencia.RESUELTA, Incidencia.resuelto_en: ahora},
        synchronize_session=False,
    )

    db.commit()

    return RedirectResponse(
        url="/mis-reservas",
        status_code=302
    )
