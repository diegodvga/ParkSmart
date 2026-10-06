"""
Router administrativo para ParkSmart.

Rutas:
- GET  /admin                      → Dashboard con estadísticas
- GET  /admin/reservas             → Gestión de reservas
- POST /admin/reservas/{id}/estado → Cambiar estado de reserva
- GET  /admin/usuarios              → Gestión de usuarios
- GET  /admin/estadisticas         → Estadísticas con Chart.js
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import datetime, date, timedelta
from typing import Optional

from app.database import get_db
from app.models import (
    Usuario, Parqueadero, Espacio, Vehiculo, Reserva,
    RolUsuario, EstadoReserva, EstadoParqueadero, EstadoEspacio
)

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["admin"]
)


def requerir_admin(request: Request):
    """
    Verifica que el usuario sea administrador.
    """
    rol = request.session.get("usuario_rol")
    if rol != RolUsuario.ADMINISTRADOR.value:
        raise HTTPException(status_code=403, detail="Acceso denegado")


# ============================================
# DASHBOARD
# ============================================

@router.get("/admin", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra el dashboard administrativo con estadísticas.
    """
    requerir_admin(request)

    # Estadísticas generales
    total_usuarios = db.query(Usuario).filter(
        Usuario.rol == RolUsuario.USUARIO.value
    ).count()

    total_parqueaderos = db.query(Parqueadero).filter(
        Parqueadero.estado == EstadoParqueadero.ACTIVO.value
    ).count()

    total_espacios = db.query(Espacio).count()

    reservas_hoy = db.query(Reserva).filter(
        func.date(Reserva.fecha) == date.today()
    ).count()

    total_reservas = db.query(Reserva).count()

    # Ingresos totales (reservas completadas y confirmadas)
    ingresos = db.query(func.sum(Reserva.total)).filter(
        Reserva.estado.in_([EstadoReserva.CONFIRMADA.value, EstadoReserva.COMPLETADA.value])
    ).scalar() or 0

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "titulo": "Dashboard - ParkSmart",
            "total_usuarios": total_usuarios,
            "total_parqueaderos": total_parqueaderos,
            "total_espacios": total_espacios,
            "reservas_hoy": reservas_hoy,
            "total_reservas": total_reservas,
            "ingresos": ingresos
        }
    )


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
    Muestra la gestión de reservas con filtros.
    """
    requerir_admin(request)

    query = db.query(Reserva)

    # Filtro por estado
    if estado:
        query = query.filter(Reserva.estado == estado)

    # Filtro por fecha
    if fecha:
        try:
            fecha_filtro = datetime.strptime(fecha, "%Y-%m-%d").date()
            query = query.filter(func.date(Reserva.fecha) == fecha_filtro)
        except ValueError:
            pass

    reservas = query.order_by(Reserva.fecha.desc()).all()

    # Enriquecer con datos relacionados
    reservas_enriquecidas = []
    for r in reservas:
        espacio = db.query(Espacio).filter(Espacio.id == r.espacio_id).first()
        parqueadero = db.query(Parqueadero).filter(Parqueadero.id == espacio.parqueadero_id).first() if espacio else None
        vehiculo = db.query(Vehiculo).filter(Vehiculo.id == r.vehiculo_id).first()
        usuario = db.query(Usuario).filter(Usuario.id == r.usuario_id).first()

        reservas_enriquecidas.append({
            "reserva": r,
            "espacio": espacio,
            "parqueadero": parqueadero,
            "vehiculo": vehiculo,
            "usuario": usuario
        })

    return templates.TemplateResponse(
        "admin/reservas.html",
        {
            "request": request,
            "titulo": "Gestión de Reservas - ParkSmart",
            "reservas": reservas_enriquecidas,
            "filtro_estado": estado,
            "filtro_fecha": fecha
        }
    )


@router.post("/admin/reservas/{reserva_id}/estado")
async def cambiar_estado_reserva(
    request: Request,
    reserva_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Cambia el estado de una reserva.
    """
    requerir_admin(request)

    reserva = db.query(Reserva).filter(Reserva.id == reserva_id).first()

    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrado")

    # Validar que el estado sea válido
    estados_validos = [e.value for e in EstadoReserva]
    if estado not in estados_validos:
        raise HTTPException(status_code=400, detail="Estado no válido")

    reserva.estado = estado

    # Si se cancela, liberar el espacio
    if estado == EstadoReserva.CANCELADA.value:
        espacio = db.query(Espacio).filter(Espacio.id == reserva.espacio_id).first()
        if espacio:
            espacio.estado = EstadoEspacio.DISPONIBLE.value

    db.commit()

    return RedirectResponse(
        url="/admin/reservas",
        status_code=302
    )


# ============================================
# GESTIÓN DE USUARIOS
# ============================================

@router.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_usuarios(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra la gestión de usuarios.
    """
    requerir_admin(request)

    usuarios = db.query(Usuario).filter(
        Usuario.rol == RolUsuario.USUARIO.value
    ).all()

    # Enriquecer con conteo de reservas y vehículos
    usuarios_enriquecidos = []
    for u in usuarios:
        total_reservas = db.query(Reserva).filter(Reserva.usuario_id == u.id).count()
        total_vehiculos = db.query(Vehiculo).filter(Vehiculo.usuario_id == u.id).count()

        usuarios_enriquecidos.append({
            "usuario": u,
            "total_reservas": total_reservas,
            "total_vehiculos": total_vehiculos
        })

    return templates.TemplateResponse(
        "admin/usuarios.html",
        {
            "request": request,
            "titulo": "Gestión de Usuarios - ParkSmart",
            "usuarios": usuarios_enriquecidos
        }
    )


# ============================================
# ESTADÍSTICAS
# ============================================

@router.get("/admin/estadisticas", response_class=HTMLResponse)
async def estadisticas(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra las estadísticas del sistema.
    """
    requerir_admin(request)

    return templates.TemplateResponse(
        "admin/estadisticas.html",
        {
            "request": request,
            "titulo": "Estadísticas - ParkSmart"
        }
    )


# API endpoints para Chart.js

@router.get("/api/estadisticas/reservas-por-dia")
async def api_reservas_por_dia(db: Session = Depends(get_db)):
    """
    Devuelve las reservas de los últimos 7 días.
    """
    hace_7_dias = date.today() - timedelta(days=7)

    resultados = db.query(
        func.date(Reserva.fecha).label('fecha'),
        func.count(Reserva.id).label('total')
    ).filter(
        func.date(Reserva.fecha) >= hace_7_dias
    ).group_by(
        func.date(Reserva.fecha)
    ).all()

    return {
        "labels": [str(r.fecha) for r in resultados],
        "data": [r.total for r in resultados]
    }


@router.get("/api/estadisticas/parqueaderos-populares")
async def api_parqueaderos_populares(db: Session = Depends(get_db)):
    """
    Devuelve los parqueaderos más utilizados.
    """
    resultados = db.query(
        Parqueadero.nombre,
        func.count(Reserva.id).label('total')
    ).join(
        Espacio, Reserva.espacio_id == Espacio.id
    ).join(
        Parqueadero, Espacio.parqueadero_id == Parqueadero.id
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
    """
    Devuelve los horarios con mayor demanda.
    """
    resultados = db.query(
        Reserva.hora_entrada,
        func.count(Reserva.id).label('total')
    ).group_by(
        Reserva.hora_entrada
    ).order_by(
        func.count(Reserva.id).desc()
    ).limit(8).all()

    return {
        "labels": [r.hora_entrada for r in resultados],
        "data": [r.total for r in resultados]
    }


@router.get("/api/estadisticas/ingresos-por-mes")
async def api_ingresos_por_mes(db: Session = Depends(get_db)):
    """
    Devuelve los ingresos por mes (últimos 6 meses).
    """
    resultados = db.query(
        extract('month', Reserva.fecha_reserva).label('mes'),
        func.sum(Reserva.total).label('total')
    ).filter(
        Reserva.estado.in_([EstadoReserva.CONFIRMADA.value, EstadoReserva.COMPLETADA.value])
    ).group_by(
        extract('month', Reserva.fecha_reserva)
    ).order_by(
        extract('month', Reserva.fecha_reserva)
    ).limit(6).all()

    meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

    return {
        "labels": [meses[int(r.mes) - 1] for r in resultados],
        "data": [float(r.total) for r in resultados]
    }


@router.get("/api/estadisticas/reservas-por-estado")
async def api_reservas_por_estado(db: Session = Depends(get_db)):
    """
    Devuelve las reservas por estado.
    """
    resultados = db.query(
        Reserva.estado,
        func.count(Reserva.id).label('total')
    ).group_by(
        Reserva.estado
    ).all()

    return {
        "labels": [r.estado for r in resultados],
        "data": [r.total for r in resultados]
    }


@router.get("/api/estadisticas/tipos-vehiculos")
async def api_tipos_vehiculos(db: Session = Depends(get_db)):
    """
    Devuelve la distribución entre carros y motos.
    """
    resultados = db.query(
        Vehiculo.tipo,
        func.count(Reserva.id).label('total')
    ).join(
        Reserva, Vehiculo.id == Reserva.vehiculo_id
    ).group_by(
        Vehiculo.tipo
    ).all()

    return {
        "labels": [r.tipo for r in resultados],
        "data": [r.total for r in resultados]
    }
