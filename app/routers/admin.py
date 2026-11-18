"""
Router administrativo para ParkSmart (Etapa 4: panel del admin_global).

Rutas:
- GET  /admin                               → Dashboard: indicadores globales y parqueaderos pendientes
- POST /admin/parqueaderos/{id}/aprobar     → Aprobar un parqueadero pendiente
- POST /admin/parqueaderos/{id}/rechazar    → Rechazar un parqueadero pendiente
- GET  /admin/reservas                      → Gestión de reservas
- POST /admin/reservas/{id}/estado          → Confirmar o cancelar una reserva
- GET  /admin/usuarios                      → Gestión de usuarios (todos los roles)
- GET  /admin/usuarios/crear                 → Formulario para crear un propietario
- POST /admin/usuarios/crear                 → Crear la cuenta de un propietario
- POST /admin/usuarios/{id}/suspender       → Suspender una cuenta (activo = False)
- POST /admin/usuarios/{id}/activar         → Reactivar una cuenta
- POST /admin/usuarios/{id}/contrasena      → Asignar una contraseña nueva
- POST /admin/usuarios/{id}/cedula          → Completar la cédula de un propietario
- GET  /admin/estadisticas                  → Estadísticas con Chart.js
- GET  /api/estadisticas/...                → Datos de los gráficos (JSON)

Definiciones de las cifras (iguales a las del panel del propietario):
- Hora de Colombia (America/Bogota) para días y meses.
- Ingresos = pagos confirmados - reembolsos del período (no totales de reservas).
"""

from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import requiere_rol
from app.models import (
    Espacio, EstadoPago, EstadoParqueadero, EstadoReserva, MotivoCancelacion, Pago, Parqueadero,
    Reserva, RolUsuario, TipoPago, Usuario, Vehiculo,
)
from app.auth import hash_password
from app.routers.auth import mensaje_error_registro
from app.schemas import ContrasenaAsignada, PropietarioCrear, normalizar_cedula
from app.services.metricas import rango_dia, resumen_global
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio

# Configurar templates
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["pesos"] = lambda valor: "$" + f"{valor or 0:,.0f}".replace(",", ".")
templates.env.filters["local"] = lambda instante, formato: (
    instante.astimezone(ZONA_NEGOCIO).strftime(formato) if instante else ""
)

# dependencies=[...] aplica requiere_rol a TODAS las rutas de este router,
# incluidas las APIs de estadísticas: solo el admin_global entra aquí.
router = APIRouter(
    prefix="",
    tags=["admin"],
    dependencies=[Depends(requiere_rol(RolUsuario.ADMIN_GLOBAL))]
)

# Hora local de una columna TIMESTAMPTZ, calculada en PostgreSQL
ZONA_SQL = "America/Bogota"
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

# A dónde puede volver una acción del admin (evita redirecciones abiertas)
DESTINOS_PERMITIDOS = {"/admin", "/admin/parqueaderos", "/admin/usuarios"}


def avisar(request: Request, tipo: str, texto: str):
    """Guarda un mensaje para mostrarlo en la próxima página."""
    request.session.setdefault("avisos", []).append({"tipo": tipo, "texto": texto})


def volver(destino: Optional[str], por_defecto: str = "/admin"):
    """Redirección 303 a una página del admin de la lista permitida."""
    return RedirectResponse(url=destino if destino in DESTINOS_PERMITIDOS else por_defecto, status_code=303)


# ============================================
# DASHBOARD
# ============================================

@router.get("/admin", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db)
):
    """Indicadores globales y parqueaderos que esperan aprobación."""
    pendientes = db.query(Parqueadero).filter(
        Parqueadero.estado == EstadoParqueadero.PENDIENTE
    ).order_by(Parqueadero.fecha_creacion).all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "titulo": "Dashboard - ParkSmart",
            "resumen": resumen_global(db, ahora_negocio()),
            "pendientes": pendientes,
            "avisos": request.session.pop("avisos", []),
        }
    )


# ============================================
# APROBAR / RECHAZAR PARQUEADEROS
# ============================================

def cambiar_estado_pendiente(db: Session, parqueadero_id: int, nuevo: EstadoParqueadero) -> Parqueadero:
    """
    Pasa un parqueadero PENDIENTE a activo o rechazado.

    Solo se decide sobre pendientes: activar o suspender uno ya decidido se
    hace desde "Editar", a propósito, para que un clic accidental en la
    lista no cambie un parqueadero que está operando.
    """
    parqueadero = db.get(Parqueadero, parqueadero_id)
    if parqueadero is None:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")
    if parqueadero.estado != EstadoParqueadero.PENDIENTE:
        raise HTTPException(status_code=400, detail="Solo se pueden aprobar o rechazar parqueaderos pendientes")
    parqueadero.estado = nuevo
    db.commit()
    return parqueadero


@router.post("/admin/parqueaderos/{parqueadero_id}/aprobar")
async def aprobar_parqueadero(
    request: Request,
    parqueadero_id: int,
    volver_a: str = Form("/admin"),
    db: Session = Depends(get_db)
):
    """Aprueba un parqueadero: queda activo, aparece en el catálogo y recibe vehículos."""
    parqueadero = cambiar_estado_pendiente(db, parqueadero_id, EstadoParqueadero.ACTIVO)
    avisar(request, "exito", f"«{parqueadero.nombre}» aprobado: ya aparece en el catálogo.")
    return volver(volver_a)


@router.post("/admin/parqueaderos/{parqueadero_id}/rechazar")
async def rechazar_parqueadero(
    request: Request,
    parqueadero_id: int,
    volver_a: str = Form("/admin"),
    db: Session = Depends(get_db)
):
    """Rechaza un parqueadero pendiente: no aparece en el catálogo."""
    parqueadero = cambiar_estado_pendiente(db, parqueadero_id, EstadoParqueadero.RECHAZADO)
    avisar(request, "advertencia", f"«{parqueadero.nombre}» rechazado.")
    return volver(volver_a)


# ============================================
# GESTIÓN DE RESERVAS
# ============================================

@router.get("/admin/reservas", response_class=HTMLResponse)
async def admin_reservas(
    request: Request,
    estado: Optional[str] = None,
    fecha: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Muestra la gestión de reservas con filtros (fecha en hora de Colombia).
    """
    query = db.query(Reserva)

    if estado:
        query = query.filter(Reserva.estado == estado)

    if fecha:
        try:
            dia = datetime.strptime(fecha, "%Y-%m-%d")
            desde, hasta = rango_dia(dia.replace(tzinfo=ZONA_NEGOCIO))
            query = query.filter(Reserva.inicio >= desde, Reserva.inicio < hasta)
        except ValueError:
            pass

    reservas = query.order_by(Reserva.inicio.desc()).all()

    reservas_enriquecidas = [
        {
            "reserva": r,
            "espacio": r.espacio,
            "parqueadero": r.espacio.parqueadero if r.espacio else None,
            "vehiculo": r.vehiculo,
            "usuario": r.usuario,
        }
        for r in reservas
    ]

    return templates.TemplateResponse(
        "admin/reservas.html",
        {
            "request": request,
            "titulo": "Gestión de Reservas - ParkSmart",
            "reservas": reservas_enriquecidas,
            "filtro_estado": estado,
            "filtro_fecha": fecha,
            "estados": [e.value for e in EstadoReserva],
        }
    )


# Cambios que el admin puede hacer a mano. "en_curso", "excedida" y
# "completada" los ponen la entrada, el protocolo de retraso y la salida
# (con su cobro): hacerlos a mano dejaría reservas sin estancia ni cuenta.
TRANSICIONES_ADMIN = {
    EstadoReserva.PENDIENTE: {EstadoReserva.CONFIRMADA, EstadoReserva.CANCELADA},
    EstadoReserva.CONFIRMADA: {EstadoReserva.CANCELADA},
}


@router.post("/admin/reservas/{reserva_id}/estado")
async def cambiar_estado_reserva(
    request: Request,
    reserva_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Confirma una reserva pendiente o cancela una pendiente/confirmada.

    Cancelar NO toca el estado de la plaza: la reserva es agenda; si hay un
    vehículo dentro, la plaza sigue ocupada.
    """
    reserva = db.query(Reserva).filter(Reserva.id == reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")

    try:
        nuevo = EstadoReserva(estado)
    except ValueError:
        raise HTTPException(status_code=400, detail="Estado no válido")
    if nuevo not in TRANSICIONES_ADMIN.get(reserva.estado, set()):
        raise HTTPException(
            status_code=400,
            detail=f"No se puede pasar una reserva de '{reserva.estado.value}' a '{nuevo.value}' desde el admin",
        )

    reserva.estado = nuevo
    if nuevo == EstadoReserva.CANCELADA:
        reserva.motivo_cancelacion = MotivoCancelacion.ADMIN
        reserva.cancelada_en = ahora_negocio()
    else:
        reserva.pendiente_expira_en = None

    try:
        db.commit()
    except IntegrityError:
        # Por ejemplo, confirmar una pendiente cuyo horario ya ocupó otra reserva
        db.rollback()
        raise HTTPException(status_code=400, detail="El horario de la reserva ya no está disponible")

    return RedirectResponse(url="/admin/reservas", status_code=302)


# ============================================
# GESTIÓN DE USUARIOS
# ============================================

@router.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_usuarios(
    request: Request,
    rol: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Usuarios de todos los roles, con su estado y un resumen de su actividad.

    q = buscar por nombre: coincidencia parcial sin distinguir mayúsculas
    (ILIKE). Se busca en TODOS los usuarios de la base, no en una página.
    """
    query = db.query(Usuario)
    if rol in {r.value for r in RolUsuario}:
        query = query.filter(Usuario.rol == rol)
    q = (q or "").strip()
    if q:
        # % y _ son comodines de LIKE: se escapan para buscarlos como texto
        texto = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(Usuario.nombre.ilike(f"%{texto}%", escape="\\"))
    usuarios = query.order_by(Usuario.fecha_registro.desc()).all()

    ids = [u.id for u in usuarios]
    reservas = dict(db.query(Reserva.usuario_id, func.count(Reserva.id))
                    .filter(Reserva.usuario_id.in_(ids)).group_by(Reserva.usuario_id).all()) if ids else {}
    vehiculos = dict(db.query(Vehiculo.usuario_id, func.count(Vehiculo.id))
                     .filter(Vehiculo.usuario_id.in_(ids)).group_by(Vehiculo.usuario_id).all()) if ids else {}
    parqueaderos = dict(db.query(Parqueadero.propietario_id, func.count(Parqueadero.id))
                        .filter(Parqueadero.propietario_id.in_(ids)).group_by(Parqueadero.propietario_id).all()) if ids else {}

    usuarios_enriquecidos = [
        {
            "usuario": u,
            "total_reservas": reservas.get(u.id, 0),
            "total_vehiculos": vehiculos.get(u.id, 0),
            "total_parqueaderos": parqueaderos.get(u.id, 0),
        }
        for u in usuarios
    ]

    return templates.TemplateResponse(
        "admin/usuarios.html",
        {
            "request": request,
            "titulo": "Gestión de Usuarios - ParkSmart",
            "usuarios": usuarios_enriquecidos,
            "filtro_rol": rol,
            "busqueda": q,
            "roles": [r.value for r in RolUsuario],
            "admin_id": request.session.get("usuario_id"),
            "avisos": request.session.pop("avisos", []),
        }
    )


def formulario_propietario(request: Request, valores: Optional[dict] = None, error: Optional[str] = None):
    """Muestra el formulario de nueva cuenta de propietario (400 si hay error)."""
    return templates.TemplateResponse(
        "admin/propietario_form.html",
        {
            "request": request,
            "titulo": "Crear propietario - ParkSmart",
            "valores": valores or {},
            "error": error,
        },
        status_code=400 if error else 200,
    )


@router.get("/admin/usuarios/crear", response_class=HTMLResponse)
async def nuevo_propietario(request: Request):
    """Formulario para crear la cuenta de un propietario de parqueadero."""
    return formulario_propietario(request)


@router.post("/admin/usuarios/crear")
async def crear_propietario(
    request: Request,
    nombre: str = Form(""),
    correo: str = Form(""),
    telefono: str = Form(""),
    cedula: str = Form(""),
    password: str = Form(""),
    confirmar_password: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Crea la cuenta de un propietario. Solo el admin_global llega aquí
    (el router entero exige ese rol).

    - El rol lo fija el backend: siempre 'propietario'. Si el formulario
      envía otro campo 'rol', se ignora (no está en los parámetros).
    - La contraseña inicial la define el admin y se guarda con hash; el
      admin se la entrega al propietario por un canal privado.
    """
    valores = {"nombre": nombre, "correo": correo, "telefono": telefono, "cedula": cedula}
    try:
        datos = PropietarioCrear(
            nombre=nombre, correo=correo, telefono=telefono, cedula=cedula,
            password=password, confirmar_password=confirmar_password,
        )
    except ValidationError as error:
        return formulario_propietario(request, valores, mensaje_error_registro(error))

    if db.query(Usuario).filter(Usuario.correo == datos.correo).first():
        return formulario_propietario(request, valores, "Este correo ya está registrado.")

    propietario = Usuario(
        nombre=datos.nombre,
        correo=datos.correo,
        password=hash_password(datos.password),
        telefono=datos.telefono,
        cedula=datos.cedula,
        rol=RolUsuario.PROPIETARIO,
    )
    db.add(propietario)
    try:
        db.commit()
    except IntegrityError:
        # Otro admin creó el mismo correo un instante antes (UNIQUE en la base)
        db.rollback()
        return formulario_propietario(request, valores, "Este correo ya está registrado.")

    avisar(request, "exito", f"Cuenta de propietario creada para {propietario.nombre} ({propietario.correo}).")
    return volver("/admin/usuarios")


def cambiar_activo(request: Request, db: Session, usuario_id: int, activo: bool) -> Usuario:
    """
    Suspende o reactiva una cuenta.

    - No se puede suspender a un admin_global (ni a uno mismo): evita que la
      plataforma quede sin administradores.
    - Suspender no borra nada: el usuario no puede iniciar sesión y su
      sesión abierta se cierra en la próxima petición (get_usuario_actual).
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not activo and (usuario.rol == RolUsuario.ADMIN_GLOBAL or usuario.id == request.session.get("usuario_id")):
        raise HTTPException(status_code=400, detail="No se puede suspender a un administrador global")
    usuario.activo = activo
    db.commit()
    return usuario


@router.post("/admin/usuarios/{usuario_id}/suspender")
async def suspender_usuario(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    """Suspende una cuenta (activo = False)."""
    usuario = cambiar_activo(request, db, usuario_id, False)
    avisar(request, "advertencia", f"Cuenta de {usuario.nombre} suspendida.")
    return volver("/admin/usuarios")


@router.post("/admin/usuarios/{usuario_id}/activar")
async def activar_usuario(request: Request, usuario_id: int, db: Session = Depends(get_db)):
    """Reactiva una cuenta suspendida."""
    usuario = cambiar_activo(request, db, usuario_id, True)
    avisar(request, "exito", f"Cuenta de {usuario.nombre} activada.")
    return volver("/admin/usuarios")


def lista_usuarios(q: str = "", rol: str = ""):
    """
    Vuelve a Gestión de usuarios conservando la búsqueda y el filtro.

    El destino siempre es /admin/usuarios (solo cambian los parámetros),
    así que no hay riesgo de redirección abierta.
    """
    parametros = {clave: valor for clave, valor in (("q", q.strip()), ("rol", rol.strip())) if valor}
    url = "/admin/usuarios" + (f"?{urlencode(parametros)}" if parametros else "")
    return RedirectResponse(url=url, status_code=303)


@router.post("/admin/usuarios/{usuario_id}/contrasena")
async def asignar_contrasena(
    request: Request,
    usuario_id: int,
    password: str = Form(""),
    confirmar_password: str = Form(""),
    q: str = Form(""),
    rol: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    El admin_global asigna una contraseña nueva a un usuario.

    - Solo llega aquí un admin_global (el router entero lo exige).
    - No puede cambiar la de OTRO admin_global (evita que un admin tome la
      cuenta de otro); la propia sí.
    - Reglas: las de cualquier cuenta (ContrasenaAsignada). Se guarda con el
      mismo hash del login (hash_password). La contraseña nunca se muestra
      ni se escribe en mensajes o registros.
    - El resultado se muestra en la misma vista (Gestión de usuarios).
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if usuario.rol == RolUsuario.ADMIN_GLOBAL and usuario.id != request.session.get("usuario_id"):
        raise HTTPException(status_code=400, detail="No se puede cambiar la contraseña de otro administrador global")

    try:
        ContrasenaAsignada(password=password, confirmar_password=confirmar_password)
    except ValidationError as error:
        primer_error = error.errors()[0]
        if primer_error["loc"] and primer_error["loc"][0] == "password":
            mensaje = "La contraseña debe tener al menos 6 caracteres."
        else:
            mensaje = primer_error["msg"].removeprefix("Value error, ")
        avisar(request, "error", f"No se cambió la contraseña de {usuario.nombre}: {mensaje}")
        return lista_usuarios(q, rol)

    usuario.password = hash_password(password)
    db.commit()
    avisar(request, "exito", f"Contraseña actualizada para {usuario.nombre} ({usuario.correo}).")
    return lista_usuarios(q, rol)


@router.post("/admin/usuarios/{usuario_id}/cedula")
async def completar_cedula(
    request: Request,
    usuario_id: int,
    cedula: str = Form(""),
    q: str = Form(""),
    rol: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Completa o corrige la cédula de un propietario (por ejemplo, de los
    creados antes de que la cédula fuera obligatoria).
    """
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if usuario.rol != RolUsuario.PROPIETARIO:
        raise HTTPException(status_code=400, detail="La cédula solo se registra en cuentas de propietario")
    try:
        usuario.cedula = normalizar_cedula(cedula)
    except ValueError as error:
        avisar(request, "error", f"{usuario.nombre}: {error}")
        return lista_usuarios(q, rol)
    db.commit()
    avisar(request, "exito", f"Cédula guardada para {usuario.nombre}.")
    return lista_usuarios(q, rol)


# ============================================
# ESTADÍSTICAS
# ============================================

@router.get("/admin/estadisticas", response_class=HTMLResponse)
async def estadisticas(
    request: Request,
    db: Session = Depends(get_db)
):
    """Muestra las estadísticas del sistema."""
    return templates.TemplateResponse(
        "admin/estadisticas.html",
        {
            "request": request,
            "titulo": "Estadísticas - ParkSmart"
        }
    )


# API endpoints para Chart.js (formato {labels, data})

@router.get("/api/estadisticas/resumen")
async def api_resumen(db: Session = Depends(get_db)):
    """Indicadores globales de la plataforma."""
    return resumen_global(db, ahora_negocio())


@router.get("/api/estadisticas/reservas-por-dia")
async def api_reservas_por_dia(db: Session = Depends(get_db)):
    """Reservas (no canceladas) que empiezan en cada uno de los últimos 7 días, hora de Colombia."""
    hoy, _ = rango_dia(ahora_negocio())
    desde = hoy - timedelta(days=6)
    dia_local = func.date(func.timezone(ZONA_SQL, Reserva.inicio))
    conteos = dict(db.query(dia_local, func.count(Reserva.id)).filter(
        Reserva.inicio >= desde,
        Reserva.inicio < hoy + timedelta(days=1),
        Reserva.estado != EstadoReserva.CANCELADA,
    ).group_by(dia_local).all())

    dias = [(desde + timedelta(days=i)).date() for i in range(7)]
    return {
        "labels": [d.strftime("%d/%m") for d in dias],
        "data": [conteos.get(d, 0) for d in dias],
    }


@router.get("/api/estadisticas/parqueaderos-populares")
async def api_parqueaderos_populares(db: Session = Depends(get_db)):
    """Los 5 parqueaderos con más reservas (sin contar canceladas)."""
    resultados = db.query(
        Parqueadero.nombre,
        func.count(Reserva.id).label('total')
    ).join(
        Espacio, Reserva.espacio_id == Espacio.id
    ).join(
        Parqueadero, Espacio.parqueadero_id == Parqueadero.id
    ).filter(
        Reserva.estado != EstadoReserva.CANCELADA
    ).group_by(
        Parqueadero.id
    ).order_by(
        func.count(Reserva.id).desc()
    ).limit(5).all()

    return {
        "labels": [r.nombre for r in resultados],
        "data": [r.total for r in resultados]
    }


@router.get("/api/estadisticas/horarios-demanda")
async def api_horarios_demanda(db: Session = Depends(get_db)):
    """Horas de inicio (hora de Colombia) con más reservas: las 8 más pedidas, en orden de hora."""
    hora_local = func.extract("hour", func.timezone(ZONA_SQL, Reserva.inicio))
    resultados = db.query(hora_local.label("hora"), func.count(Reserva.id).label("total")).filter(
        Reserva.estado != EstadoReserva.CANCELADA
    ).group_by(hora_local).order_by(func.count(Reserva.id).desc()).limit(8).all()
    resultados = sorted(resultados, key=lambda r: r.hora)

    return {
        "labels": [f"{int(r.hora):02d}:00" for r in resultados],
        "data": [r.total for r in resultados]
    }


@router.get("/api/estadisticas/ingresos-por-mes")
async def api_ingresos_por_mes(db: Session = Depends(get_db)):
    """
    Ingresos netos (pagos - reembolsos) de los últimos 6 meses, hora de
    Colombia. Cada mes lleva su año: noviembre de 2025 y de 2026 no se mezclan.
    """
    ahora = ahora_negocio()
    meses = []
    anio, mes = ahora.year, ahora.month
    for _ in range(6):
        meses.append((anio, mes))
        anio, mes = (anio, mes - 1) if mes > 1 else (anio - 1, 12)
    meses.reverse()
    desde = datetime(meses[0][0], meses[0][1], 1, tzinfo=ZONA_NEGOCIO)

    mes_local = func.date_trunc("month", func.timezone(ZONA_SQL, Pago.confirmado_en))
    signo = case((Pago.tipo == TipoPago.REEMBOLSO, -Pago.monto), else_=Pago.monto)
    filas = db.query(mes_local, func.sum(signo)).filter(
        Pago.estado == EstadoPago.PAGADO,
        Pago.confirmado_en >= desde,
    ).group_by(mes_local).all()
    totales = {(m.year, m.month): float(total or 0) for m, total in filas}

    return {
        "labels": [f"{MESES[m - 1]} {a}" for a, m in meses],
        "data": [totales.get((a, m), 0.0) for a, m in meses]
    }


@router.get("/api/estadisticas/reservas-por-estado")
async def api_reservas_por_estado(db: Session = Depends(get_db)):
    """Cantidad de reservas en cada estado."""
    resultados = db.query(
        Reserva.estado,
        func.count(Reserva.id).label('total')
    ).group_by(
        Reserva.estado
    ).all()

    return {
        "labels": [r.estado.value.replace("_", " ") for r in resultados],
        "data": [r.total for r in resultados]
    }


@router.get("/api/estadisticas/tipos-vehiculos")
async def api_tipos_vehiculos(db: Session = Depends(get_db)):
    """Reservas por tipo de vehículo (carro, moto, bus)."""
    resultados = db.query(
        Vehiculo.tipo,
        func.count(Reserva.id).label('total')
    ).join(
        Reserva, Vehiculo.id == Reserva.vehiculo_id
    ).group_by(
        Vehiculo.tipo
    ).all()

    return {
        "labels": [r.tipo.value for r in resultados],
        "data": [r.total for r in resultados]
    }
