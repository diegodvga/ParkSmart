"""
Servicio de reservas (agenda) para ParkSmart.

Reglas:
- Las horas que escribe el cliente son hora de Colombia (America/Bogota).
- Una reserva ocupa la agenda de su plaza en [inicio, fin + margen).
  Dos reservas activas de la misma plaza no pueden cruzarse en ese rango.
- La verificación en Python solo sirve para dar un mensaje claro: la
  garantía real es el EXCLUDE de PostgreSQL, que también frena dos
  peticiones simultáneas.
- Una reserva pendiente aparta el horario solo hasta pendiente_expira_en.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from psycopg.errors import ExclusionViolation
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ESTADOS_RESERVA_ACTIVOS,
    Cuenta,
    Espacio,
    EstadoEspacio,
    EstadoPago,
    EstadoReserva,
    MotivoCancelacion,
    Pago,
    Parqueadero,
    Reserva,
    Vehiculo,
)


# Colombia no tiene horario de verano: siempre es UTC-5. Un desfase fijo
# evita depender de la base de zonas horarias del sistema (en Windows
# zoneinfo necesita el paquete tzdata).
ZONA_NEGOCIO = timezone(timedelta(hours=-5), "America/Bogota")

# Cuánto tiempo aparta el horario una reserva pendiente de pago
MINUTOS_PENDIENTE = 15

# Cómo va a pagar el cliente (lo elige al reservar):
# - "digital": tarjeta o transferencia en la pasarela simulada. La reserva
#   nace pendiente y aparta el horario MINUTOS_PENDIENTE mientras paga.
# - "efectivo": paga al salir. La reserva nace confirmada; no se ofrece si
#   el parqueadero exige pago anticipado.
FORMA_DIGITAL = "digital"
FORMA_EFECTIVO = "efectivo"
FORMAS_PAGO = (FORMA_DIGITAL, FORMA_EFECTIVO)


def ahora_negocio() -> datetime:
    """Fecha y hora actual en Colombia, con zona horaria."""
    return datetime.now(ZONA_NEGOCIO)


def instante_local(dia: date, hora: str) -> datetime:
    """Convierte una fecha y una hora "HH:MM" de Colombia en un instante."""
    horas, minutos = map(int, hora.split(":"))
    return datetime(dia.year, dia.month, dia.day, horas, minutos, tzinfo=ZONA_NEGOCIO)


def rango_bloqueo(inicio: datetime, fin: datetime, margen_minutos: int) -> Range:
    """Rango que aparta una reserva: [inicio, fin + margen)."""
    return Range(inicio, fin + timedelta(minutes=margen_minutos), bounds="[)")


def vencer_pendientes(db: Session, espacio_id: int) -> None:
    """
    Cancela las reservas pendientes de una plaza cuyo plazo de pago venció.

    Se llama antes de reservar: así una pendiente abandonada deja de
    apartar el horario. El EXCLUDE no puede usar NOW(), por eso lo hace
    el código.
    """
    ahora = ahora_negocio()
    db.query(Reserva).filter(
        Reserva.espacio_id == espacio_id,
        Reserva.estado == EstadoReserva.PENDIENTE,
        Reserva.pendiente_expira_en < ahora,
    ).update(
        {
            Reserva.estado: EstadoReserva.CANCELADA,
            Reserva.motivo_cancelacion: MotivoCancelacion.PENDIENTE_VENCIDA,
            Reserva.cancelada_en: ahora,
        },
        synchronize_session=False,
    )


def hay_cruce(
    db: Session,
    espacio_id: int,
    inicio: datetime,
    fin: datetime,
    margen_minutos: int,
    excluir_id: Optional[int] = None,
) -> bool:
    """
    True si [inicio, fin + margen) se cruza con alguna reserva activa de la plaza.

    Usa el mismo operador que el EXCLUDE (&& sobre rangos), así el mensaje
    que ve el cliente coincide con lo que haría la base de datos.
    excluir_id: no compara contra esa reserva (al extenderla o moverla).
    """
    consulta = db.query(Reserva.id).filter(
        Reserva.espacio_id == espacio_id,
        Reserva.estado.in_(ESTADOS_RESERVA_ACTIVOS),
        Reserva.bloqueo.overlaps(rango_bloqueo(inicio, fin, margen_minutos)),
    )
    if excluir_id is not None:
        consulta = consulta.filter(Reserva.id != excluir_id)
    return consulta.first() is not None


# ============================================
# SELECTOR DE PLAZAS Y GUARDADO (crear o editar)
# ============================================

class ReservaError(Exception):
    """Error de negocio al reservar, con un mensaje listo para el cliente."""


# Leyenda del selector del cliente (mismo componente que el panel del dueño)
LEYENDA_RESERVA = [
    ("disponible", "Disponible en ese horario"),
    ("reservada", "Ocupada en ese horario"),
    ("incompatible", "Para otro tipo de vehículo"),
    ("mantenimiento", "Mantenimiento"),
    ("seleccionada", "Tu elección"),
]

NOMBRE_TIPO = {"carro": "carros", "moto": "motos", "bus": "buses"}


def _orden(espacio: Espacio):
    """C2 antes que C10: por prefijo y luego por número."""
    numero = "".join(c for c in espacio.numero if c.isdigit())
    return (espacio.numero.rstrip("0123456789"), int(numero) if numero else 0, espacio.numero)


def horario(fecha: str, hora_entrada: str, hora_salida: str):
    """
    Convierte fecha y horas del formulario en (inicio, fin), o (None, None)
    si faltan o no son válidas (la cuadrícula se muestra sin elegir).
    """
    try:
        dia = datetime.strptime((fecha or "").strip(), "%Y-%m-%d").date()
        inicio = instante_local(dia, (hora_entrada or "").strip())
        fin = instante_local(dia, (hora_salida or "").strip())
    except ValueError:
        return None, None
    return (inicio, fin) if fin > inicio else (None, None)


def plazas_para_reserva(
    db: Session,
    parqueadero: Parqueadero,
    tipo_vehiculo,
    inicio: Optional[datetime],
    fin: Optional[datetime],
    seleccionada_id: Optional[int] = None,
    excluir_reserva_id: Optional[int] = None,
) -> list[dict]:
    """
    Tarjetas del selector de plazas para un horario.

    Una plaza se puede elegir solo si:
    - es del mismo tipo que el vehículo;
    - no está en mantenimiento;
    - su agenda está libre en [inicio, fin + margen) (mismo criterio que el
      EXCLUDE de PostgreSQL). Es disponibilidad PARA ESE HORARIO, no la
      ocupación de este momento: C01 reservada de 3 a 5 p. m. se puede
      elegir de 6 a 8 p. m.
    excluir_reserva_id: al editar, la propia reserva no cuenta como ocupación.
    """
    espacios = sorted(db.query(Espacio).filter(Espacio.parqueadero_id == parqueadero.id).all(), key=_orden)
    tipo = getattr(tipo_vehiculo, "value", tipo_vehiculo)
    con_horario = inicio is not None and fin is not None

    ocupadas = set()
    if con_horario and espacios:
        consulta = db.query(Reserva.espacio_id).filter(
            Reserva.espacio_id.in_([e.id for e in espacios]),
            Reserva.estado.in_(ESTADOS_RESERVA_ACTIVOS),
            Reserva.bloqueo.overlaps(rango_bloqueo(inicio, fin, parqueadero.margen_minutos)),
        )
        if excluir_reserva_id is not None:
            consulta = consulta.filter(Reserva.id != excluir_reserva_id)
        ocupadas = {fila[0] for fila in consulta}

    tarjetas = []
    for espacio in espacios:
        tarjeta = {"id": espacio.id, "numero": espacio.numero, "seleccionable": False, "detalle": None}
        if espacio.estado == EstadoEspacio.MANTENIMIENTO:
            tarjeta.update(situacion="mantenimiento", texto="Mantenimiento")
        elif tipo and espacio.tipo.value != tipo:
            tarjeta.update(situacion="incompatible", texto=f"Para {NOMBRE_TIPO.get(espacio.tipo.value, espacio.tipo.value)}")
        elif not tipo or not con_horario:
            tarjeta.update(situacion="sin_horario", texto="Elige horario")
        elif espacio.id in ocupadas:
            tarjeta.update(situacion="reservada", texto="Ocupada")
        else:
            tarjeta.update(situacion="disponible", texto="Disponible", seleccionable=True,
                           seleccionada=espacio.id == seleccionada_id)
        tarjetas.append(tarjeta)
    return tarjetas


def reserva_tiene_pagos(db: Session, reserva: Reserva) -> bool:
    """True si la reserva ya tiene algún pago confirmado."""
    return db.query(Pago.id).join(Cuenta, Pago.cuenta_id == Cuenta.id).filter(
        Cuenta.reserva_id == reserva.id,
        Pago.estado == EstadoPago.PAGADO,
    ).first() is not None


def validar_edicion(db: Session, reserva: Reserva, ahora: datetime) -> None:
    """Una reserva se puede modificar si no empezó, sigue vigente y no tiene pagos."""
    if reserva.estado not in (EstadoReserva.PENDIENTE, EstadoReserva.CONFIRMADA):
        raise ReservaError("Solo puedes modificar reservas pendientes o confirmadas.")
    if reserva.inicio <= ahora:
        raise ReservaError("La reserva ya empezó: no se puede modificar.")
    if reserva_tiene_pagos(db, reserva):
        raise ReservaError(
            "Esta reserva ya tiene un pago registrado: para cambiarla, cancélala y haz una nueva "
            "(se aplica la política de reembolso)."
        )


def guardar_reserva(
    db: Session,
    usuario_id: int,
    parqueadero: Parqueadero,
    espacio_id,
    vehiculo_id,
    fecha: str,
    hora_entrada: str,
    hora_salida: str,
    reserva: Optional[Reserva] = None,
    forma_pago: Optional[str] = None,
) -> Reserva:
    """
    Crea una reserva (reserva=None) o modifica una existente.

    forma_pago (solo al crear): FORMA_DIGITAL o FORMA_EFECTIVO. Si no
    llega (enlace antiguo), decide el parqueadero: con pago anticipado
    nace pendiente; sin él, confirmada.

    El servidor vuelve a validar TODO, aunque la cuadrícula ya lo haya
    mostrado: entre que el cliente ve la plaza y guarda, otra persona pudo
    tomarla. La garantía final es el EXCLUDE de PostgreSQL.

    Al editar se conservan el margen y la tarifa congelados de la reserva;
    si cambia el tipo de vehículo, se usa la tarifa actual de ese tipo.
    """
    ahora = ahora_negocio()
    if reserva is not None:
        validar_edicion(db, reserva, ahora)

    try:
        vehiculo = db.query(Vehiculo).filter(
            Vehiculo.id == int(vehiculo_id),
            Vehiculo.usuario_id == usuario_id,
        ).first()
    except (TypeError, ValueError):
        vehiculo = None
    if vehiculo is None:
        raise ReservaError("Elige uno de tus vehículos.")

    try:
        dia = datetime.strptime((fecha or "").strip(), "%Y-%m-%d").date()
        inicio = instante_local(dia, (hora_entrada or "").strip())
        fin = instante_local(dia, (hora_salida or "").strip())
    except ValueError:
        raise ReservaError("Fecha u hora inválida.")
    if dia < ahora.date():
        raise ReservaError("No puedes reservar en una fecha pasada.")
    if fin <= inicio:
        raise ReservaError("La hora de salida debe ser posterior a la hora de entrada.")
    if inicio < ahora:
        raise ReservaError("La hora de entrada ya pasó.")

    if parqueadero.tarifa_para(vehiculo.tipo) is None:
        raise ReservaError("Este parqueadero no acepta este tipo de vehículo.")

    try:
        espacio = db.query(Espacio).filter(
            Espacio.id == int(espacio_id),
            Espacio.parqueadero_id == parqueadero.id,
        ).first()
    except (TypeError, ValueError):
        espacio = None
    if espacio is None:
        raise ReservaError("Elige una plaza disponible en la cuadrícula.")
    if espacio.estado == EstadoEspacio.MANTENIMIENTO:
        raise ReservaError(f"La plaza {espacio.numero} está en mantenimiento. Elige otra.")
    if espacio.tipo.value != vehiculo.tipo.value:
        raise ReservaError(
            f"La plaza {espacio.numero} es para {NOMBRE_TIPO.get(espacio.tipo.value, espacio.tipo.value)}; "
            f"tu vehículo es {vehiculo.tipo.value}. Elige otra."
        )

    misma_categoria = reserva is not None and reserva.vehiculo.tipo == vehiculo.tipo
    tarifa = reserva.tarifa_hora if misma_categoria else parqueadero.tarifa_para(vehiculo.tipo)
    margen = reserva.margen_minutos if reserva is not None else parqueadero.margen_minutos

    mensaje_cruce = (
        f"Ya existe una reserva en ese horario para la plaza {espacio.numero} "
        f"(se respeta un margen de {margen} minutos entre reservas). Elige otra plaza u otro horario."
    )
    vencer_pendientes(db, espacio.id)
    if hay_cruce(db, espacio.id, inicio, fin, margen, excluir_id=reserva.id if reserva else None):
        raise ReservaError(mensaje_cruce)

    horas = (fin - inicio).total_seconds() / 3600
    datos = dict(
        vehiculo_id=vehiculo.id,
        espacio_id=espacio.id,
        inicio=inicio,
        fin=fin,
        tarifa_hora=tarifa,
        total=round(horas * tarifa, 2),
        # Columnas viejas: algunas vistas todavía las muestran
        fecha=dia,
        hora_entrada=inicio.strftime("%H:%M"),
        hora_salida=fin.strftime("%H:%M"),
    )
    if reserva is None:
        if forma_pago is None:
            pago_anticipado = parqueadero.pago_anticipado
        elif forma_pago == FORMA_EFECTIVO:
            if parqueadero.pago_anticipado:
                raise ReservaError(
                    "Este parqueadero exige pago anticipado: elige tarjeta o transferencia."
                )
            pago_anticipado = False
        elif forma_pago == FORMA_DIGITAL:
            pago_anticipado = True
        else:
            raise ReservaError("Elige cómo vas a pagar: tarjeta/transferencia o efectivo.")
        reserva = Reserva(
            usuario_id=usuario_id,
            margen_minutos=margen,
            estado=EstadoReserva.PENDIENTE if pago_anticipado else EstadoReserva.CONFIRMADA,
            pendiente_expira_en=ahora + timedelta(minutes=MINUTOS_PENDIENTE) if pago_anticipado else None,
            **datos,
        )
        db.add(reserva)
    else:
        for campo, valor in datos.items():
            setattr(reserva, campo, valor)

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if isinstance(error.orig, ExclusionViolation):
            # Otra petición tomó el horario entre la verificación y el commit
            raise ReservaError(mensaje_cruce)
        raise
    return reserva
