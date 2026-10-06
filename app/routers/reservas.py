"""
Router de reservas para ParkSmart.

Rutas:
- GET  /reservas/nueva/{espacio_id}  → Formulario para crear reserva
- POST /reservas/nueva/{espacio_id}  → Procesar creación de reserva
- GET  /mis-reservas                 → Lista de reservas del usuario
- POST /reservas/{id}/cancelar       → Cancelar una reserva
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, date, time, timedelta

from app.database import get_db
from app.models import Reserva, Espacio, Parqueadero, Vehiculo, EstadoReserva, EstadoEspacio

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["reservas"]
)


def requerir_autenticacion(request: Request):
    """
    Verifica que el usuario esté autenticado.
    """
    if not request.session.get("usuario_id"):
        raise HTTPException(status_code=401, detail="Debes iniciar sesión")


def calcular_horas(hora_entrada: str, hora_salida: str) -> float:
    """
    Calcula la duración en horas entre dos horas en formato HH:MM.
    """
    formato = "%H:%M"
    entrada = datetime.strptime(hora_entrada, formato)
    salida = datetime.strptime(hora_salida, formato)

    if salida <= entrada:
        return 0

    diferencia = salida - entrada
    return diferencia.total_seconds() / 3600


def verificar_cruce_reservas(
    db: Session,
    espacio_id: int,
    fecha: date,
    hora_entrada: str,
    hora_salida: str,
    reserva_id_excluir: int = None
) -> bool:
    """
    Verifica si existe una reserva que se cruce con el horario solicitado.

    Returns:
        bool: True si hay un cruce, False si no hay cruce
    """
    # Obtener reservas existentes para ese espacio y fecha
    query = db.query(Reserva).filter(
        Reserva.espacio_id == espacio_id,
        Reserva.fecha == fecha,
        Reserva.estado.in_([EstadoReserva.PENDIENTE.value, EstadoReserva.CONFIRMADA.value])
    )

    if reserva_id_excluir:
        query = query.filter(Reserva.id != reserva_id_excluir)

    reservas_existentes = query.all()

    for reserva in reservas_existentes:
        # Verificar si hay superposición de horarios
        # Hay cruce si: la nueva hora de entrada es anterior a la hora de salida existente
        # Y la nueva hora de salida es posterior a la hora de entrada existente
        if hora_entrada < reserva.hora_salida and hora_salida > reserva.hora_entrada:
            return True

    return False


# ============================================
# RUTAS
# ============================================

@router.get("/reservas/nueva/{espacio_id}", response_class=HTMLResponse)
async def formulario_reserva(
    request: Request,
    espacio_id: int,
    db: Session = Depends(get_db)
):
    """
    Muestra el formulario para crear una reserva.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    # Obtener el espacio
    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()
    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

    # Verificar que el espacio esté disponible
    if espacio.estado != EstadoEspacio.DISPONIBLE.value:
        raise HTTPException(status_code=400, detail="El espacio no está disponible")

    # Obtener el parqueadero
    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == espacio.parqueadero_id
    ).first()

    # Obtener los vehículos del usuario
    vehiculos = db.query(Vehiculo).filter(
        Vehiculo.usuario_id == usuario_id
    ).all()

    if not vehiculos:
        return templates.TemplateResponse(
            "reservar.html",
            {
                "request": request,
                "titulo": "Reservar Espacio",
                "error": "No tienes vehículos registrados. Registra uno primero.",
                "espacio": espacio,
                "parqueadero": parqueadero,
                "vehiculos": []
            },
            status_code=400
        )

    return templates.TemplateResponse(
        "reservar.html",
        {
            "request": request,
            "titulo": "Reservar Espacio",
            "espacio": espacio,
            "parqueadero": parqueadero,
            "vehiculos": vehiculos
        }
    )


@router.post("/reservas/nueva/{espacio_id}")
async def crear_reserva(
    request: Request,
    espacio_id: int,
    vehiculo_id: int = Form(...),
    fecha: str = Form(...),
    hora_entrada: str = Form(...),
    hora_salida: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Procesa la creación de una reserva.

    Reglas de negocio:
    1. El espacio debe estar disponible
    2. No debe haber reservas que se crucen
    3. La hora de salida debe ser posterior a la hora de entrada
    4. Calcular el precio automáticamente
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    # Obtener el espacio
    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()
    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

    # Verificar que el espacio esté disponible
    if espacio.estado != EstadoEspacio.DISPONIBLE.value:
        return templates.TemplateResponse(
            "reservar.html",
            {
                "request": request,
                "titulo": "Reservar Espacio",
                "error": "El espacio no está disponible para reservar",
                "espacio": espacio,
                "parqueadero": espacio.parqueadero,
                "vehiculos": db.query(Vehiculo).filter(Vehiculo.usuario_id == usuario_id).all()
            },
            status_code=400
        )

    # Verificar que el vehículo pertenece al usuario
    vehiculo = db.query(Vehiculo).filter(
        Vehiculo.id == vehiculo_id,
        Vehiculo.usuario_id == usuario_id
    ).first()

    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")

    # Convertir fecha
    try:
        fecha_reserva = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Fecha inválida")

    # Verificar que la fecha no sea en el pasado
    if fecha_reserva < date.today():
        return templates.TemplateResponse(
            "reservar.html",
            {
                "request": request,
                "titulo": "Reservar Espacio",
                "error": "No puedes reservar en una fecha pasada",
                "espacio": espacio,
                "parqueadero": espacio.parqueadero,
                "vehiculos": db.query(Vehiculo).filter(Vehiculo.usuario_id == usuario_id).all()
            },
            status_code=400
        )

    # Verificar que la hora de salida sea posterior a la hora de entrada
    if hora_salida <= hora_entrada:
        return templates.TemplateResponse(
            "reservar.html",
            {
                "request": request,
                "titulo": "Reservar Espacio",
                "error": "La hora de salida debe ser posterior a la hora de entrada",
                "espacio": espacio,
                "parqueadero": espacio.parqueadero,
                "vehiculos": db.query(Vehiculo).filter(Vehiculo.usuario_id == usuario_id).all()
            },
            status_code=400
        )

    # Verificar que no haya reservas que se crucen
    if verificar_cruce_reservas(db, espacio_id, fecha_reserva, hora_entrada, hora_salida):
        return templates.TemplateResponse(
            "reservar.html",
            {
                "request": request,
                "titulo": "Reservar Espacio",
                "error": "Ya existe una reserva en ese horario para este espacio",
                "espacio": espacio,
                "parqueadero": espacio.parqueadero,
                "vehiculos": db.query(Vehiculo).filter(Vehiculo.usuario_id == usuario_id).all()
            },
            status_code=400
        )

    # Obtener el parqueadero para calcular el precio
    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == espacio.parqueadero_id
    ).first()

    # Calcular el precio según el tipo de vehículo
    horas = calcular_horas(hora_entrada, hora_salida)
    if vehiculo.tipo == "MOTO":
        total = horas * parqueadero.tarifa_hora_moto
    else:
        total = horas * parqueadero.tarifa_hora_carro

    # Crear la reserva
    nueva_reserva = Reserva(
        usuario_id=usuario_id,
        vehiculo_id=vehiculo_id,
        espacio_id=espacio_id,
        fecha=fecha_reserva,
        hora_entrada=hora_entrada,
        hora_salida=hora_salida,
        estado=EstadoReserva.PENDIENTE.value,
        total=total,
        fecha_reserva=datetime.now()
    )

    db.add(nueva_reserva)

    # Actualizar el estado del espacio a ocupado
    espacio.estado = EstadoEspacio.OCUPADO.value

    db.commit()

    return RedirectResponse(
        url="/mis-reservas",
        status_code=302
    )


@router.get("/mis-reservas", response_class=HTMLResponse)
async def listar_reservas(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra la lista de reservas del usuario autenticado.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    reservas = db.query(Reserva).filter(
        Reserva.usuario_id == usuario_id
    ).order_by(Reserva.fecha.desc()).all()

    # Enriquecer con datos relacionados
    reservas_enriquecidas = []
    for r in reservas:
        espacio = db.query(Espacio).filter(Espacio.id == r.espacio_id).first()
        parqueadero = db.query(Parqueadero).filter(Parqueadero.id == espacio.parqueadero_id).first() if espacio else None
        vehiculo = db.query(Vehiculo).filter(Vehiculo.id == r.vehiculo_id).first()

        reservas_enriquecidas.append({
            "reserva": r,
            "espacio": espacio,
            "parqueadero": parqueadero,
            "vehiculo": vehiculo
        })

    return templates.TemplateResponse(
        "mis_reservas.html",
        {
            "request": request,
            "titulo": "Mis Reservas - ParkSmart",
            "reservas": reservas_enriquecidas
        }
    )


@router.post("/reservas/{reserva_id}/cancelar")
async def cancelar_reserva(
    request: Request,
    reserva_id: int,
    db: Session = Depends(get_db)
):
    """
    Cancela una reserva (solo si está pendiente o confirmada).
    """
    requerir_autenticacion(request)

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

    reserva.estado = EstadoReserva.CANCELADA.value

    # Liberar el espacio
    espacio = db.query(Espacio).filter(Espacio.id == reserva.espacio_id).first()
    if espacio:
        espacio.estado = EstadoEspacio.DISPONIBLE.value

    db.commit()

    return RedirectResponse(
        url="/mis-reservas",
        status_code=302
    )
