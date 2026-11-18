"""
Servicio del protocolo de retraso para ParkSmart (Módulo 3).

T = hora de fin de la reserva A (el vehículo que sigue dentro).

| Momento                        | Acción                                                      |
|--------------------------------|-------------------------------------------------------------|
| T - 30 min                     | Recordatorio al cliente A.                                  |
| T                              | Si A sigue dentro: reserva 'excedida', incidencia de exceso.|
| T + 15 min (o antes, si B llega| Alerta prioritaria al propietario e incidencia de retraso   |
|  pronto)                       | vinculada a la reserva afectada (B).                        |
| 30 min antes del inicio de B   | Buscar otra plaza del mismo tipo: libre AHORA y con agenda  |
|                                | libre durante todo B (más su margen). Reasignar sin costo.  |
| Sin alternativa                | Incidencia sin_alternativa; avisar a B (cambio de horario o |
|                                | reembolso). Si la plaza se libera a tiempo, se resuelve.    |

Idempotencia: la función se puede correr muchas veces (cada minuto desde
pg_cron y además al abrir el panel). Una segunda ejecución no duplica:
- notificaciones: "clave" única + INSERT ... ON CONFLICT DO NOTHING;
- incidencias: UNIQUE (reserva_id, tipo) + ON CONFLICT DO NOTHING;
- reasignaciones: la reserva B se bloquea (FOR UPDATE) y solo se mueve si
  sigue en la plaza ocupada.

El margen entre reservas y la gracia de cobro son reglas distintas: aquí
solo importa el margen; la gracia se aplica al liquidar la salida.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from psycopg.errors import ExclusionViolation
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Espacio,
    EstadoEspacio,
    EstadoIncidencia,
    EstadoReserva,
    Estancia,
    Incidencia,
    Notificacion,
    Parqueadero,
    Reserva,
    ReservaReasignacion,
    TipoIncidencia,
    TipoNotificacion,
)
from app.services.ocupacion import (
    OperacionError,
    hora_texto,
    minutos_cobrables,
    orden_codigo,
    valor_por_minutos,
)
from app.services.reservas import hay_cruce

RECORDATORIO_MINUTOS = 30   # Aviso al cliente antes del fin de su reserva
ALERTA_MINUTOS = 15         # Alerta al propietario después del fin (T + 15)
BUSQUEDA_MINUTOS = 30       # La búsqueda de otra plaza empieza 30 min antes de B


# ============================================
# AYUDANTES IDEMPOTENTES
# ============================================

def notificar(
    db: Session,
    usuario_id: int,
    tipo: TipoNotificacion,
    mensaje: str,
    clave: str,
    reserva_id: Optional[int] = None,
    parqueadero_id: Optional[int] = None,
    prioridad: str = "normal",
) -> bool:
    """Crea la notificación si su clave no existe. True si se creó."""
    sentencia = pg_insert(Notificacion).values(
        usuario_id=usuario_id,
        tipo=tipo,
        mensaje=mensaje,
        clave=clave,
        reserva_id=reserva_id,
        parqueadero_id=parqueadero_id,
        prioridad=prioridad,
    ).on_conflict_do_nothing(index_elements=["clave"]).returning(Notificacion.id)
    return db.execute(sentencia).first() is not None


def registrar_incidencia(
    db: Session,
    reserva: Reserva,
    tipo: TipoIncidencia,
    detalle: str,
    estado: EstadoIncidencia = EstadoIncidencia.ABIERTA,
    ahora: Optional[datetime] = None,
) -> Incidencia:
    """Crea la incidencia (reserva, tipo) si no existe y la devuelve."""
    db.execute(
        pg_insert(Incidencia).values(
            parqueadero_id=reserva.espacio.parqueadero_id,
            reserva_id=reserva.id,
            espacio_id=reserva.espacio_id,
            tipo=tipo,
            estado=estado,
            detalle=detalle,
            resuelto_en=ahora if estado == EstadoIncidencia.RESUELTA else None,
        ).on_conflict_do_nothing(constraint="uq_incidencias_reserva_tipo")
    )
    return db.query(Incidencia).filter(
        Incidencia.reserva_id == reserva.id,
        Incidencia.tipo == tipo,
    ).one()


def resolver_incidencias(db: Session, reserva_id: int, tipos, ahora: datetime) -> int:
    """Marca como resueltas las incidencias abiertas de una reserva. Devuelve cuántas."""
    return db.query(Incidencia).filter(
        Incidencia.reserva_id == reserva_id,
        Incidencia.tipo.in_(tipos),
        Incidencia.estado == EstadoIncidencia.ABIERTA,
    ).update(
        {Incidencia.estado: EstadoIncidencia.RESUELTA, Incidencia.resuelto_en: ahora},
        synchronize_session=False,
    )


def plaza_ocupada(db: Session, espacio_id: int) -> bool:
    """True si hay un vehículo dentro de la plaza en este momento."""
    return db.query(Estancia.id).filter(
        Estancia.espacio_id == espacio_id,
        Estancia.salida_en.is_(None),
    ).first() is not None


def reservas_en(db: Session, parqueadero_id: Optional[int]):
    """Consulta de reservas, opcionalmente limitada a un parqueadero."""
    consulta = db.query(Reserva).join(Espacio, Reserva.espacio_id == Espacio.id)
    if parqueadero_id is not None:
        consulta = consulta.filter(Espacio.parqueadero_id == parqueadero_id)
    return consulta


# ============================================
# REASIGNACIÓN
# ============================================

def intentar_reasignar(db: Session, reserva_b: Reserva, espacio_ocupado_id: int, ahora: datetime) -> bool:
    """
    Mueve la reserva B a otra plaza del mismo tipo y parqueadero.

    La plaza nueva debe estar libre AHORA (para recibir al cliente) y su
    agenda libre durante todo el horario de B más el margen. El EXCLUDE de
    PostgreSQL vuelve a validar la agenda al cambiar espacio_id.

    Returns:
        True si se reasignó, False si no hubo alternativa.
    """
    # Bloquear B y verificar que siga en la plaza ocupada (otra ejecución
    # pudo haberla movido un instante antes)
    reserva_b = db.query(Reserva).filter(Reserva.id == reserva_b.id).with_for_update().one()
    if reserva_b.espacio_id != espacio_ocupado_id or reserva_b.estado != EstadoReserva.CONFIRMADA:
        return reserva_b.espacio_id != espacio_ocupado_id

    origen = db.get(Espacio, espacio_ocupado_id)
    ocupadas = db.query(Estancia.espacio_id).filter(
        Estancia.parqueadero_id == origen.parqueadero_id,
        Estancia.salida_en.is_(None),
    )
    candidatas = sorted(
        db.query(Espacio).filter(
            Espacio.parqueadero_id == origen.parqueadero_id,
            Espacio.tipo == origen.tipo,
            Espacio.id != origen.id,
            Espacio.estado != EstadoEspacio.MANTENIMIENTO,
            Espacio.id.not_in(ocupadas),
        ).all(),
        key=orden_codigo,
    )

    for destino in candidatas:
        if hay_cruce(db, destino.id, reserva_b.inicio, reserva_b.fin, reserva_b.margen_minutos,
                     excluir_id=reserva_b.id):
            continue
        try:
            with db.begin_nested():
                reserva_b.espacio_id = destino.id
                db.flush()
        except IntegrityError as error:
            if isinstance(error.orig, ExclusionViolation):
                continue
            raise

        db.refresh(reserva_b)
        incidencia = registrar_incidencia(
            db, reserva_b, TipoIncidencia.REASIGNACION,
            f"Reasignada de {origen.numero} a {destino.numero} por retraso del vehículo anterior.",
            estado=EstadoIncidencia.RESUELTA, ahora=ahora,
        )
        db.add(ReservaReasignacion(
            reserva_id=reserva_b.id,
            espacio_origen_id=origen.id,
            espacio_destino_id=destino.id,
            incidencia_id=incidencia.id,
        ))
        resolver_incidencias(db, reserva_b.id, [TipoIncidencia.RETRASO, TipoIncidencia.SIN_ALTERNATIVA], ahora)
        notificar(
            db, reserva_b.usuario_id, TipoNotificacion.REASIGNACION,
            f"Tu plaza cambió de {origen.numero} a {destino.numero} porque el vehículo anterior "
            f"no ha salido. Tu reserva de las {hora_texto(reserva_b.inicio)} sigue igual, sin costo adicional.",
            clave=f"reasignacion:{reserva_b.id}:{destino.id}",
            reserva_id=reserva_b.id, parqueadero_id=origen.parqueadero_id,
        )
        return True
    return False


# ============================================
# PROTOCOLO
# ============================================

def procesar_retrasos(db: Session, ahora: datetime, parqueadero_id: Optional[int] = None) -> dict:
    """
    Ejecuta una pasada del protocolo de retraso. Es idempotente.

    Args:
        ahora: instante de referencia (en producción, la hora actual; en las
            pruebas, una hora fija para simular el paso del tiempo).
        parqueadero_id: limita la pasada a un parqueadero (panel); None = todos.

    Returns:
        Resumen con lo que se creó o cambió en ESTA pasada.
    """
    resumen = {
        "recordatorios": 0, "excedidas": 0, "alertas": 0,
        "reasignaciones": 0, "sin_alternativa": 0, "resueltas": 0,
    }
    dentro = db.query(Estancia.reserva_id).filter(
        Estancia.reserva_id.is_not(None),
        Estancia.salida_en.is_(None),
    )

    # 1. T - 30: recordatorio al cliente que está dentro
    for reserva in reservas_en(db, parqueadero_id).filter(
        Reserva.estado == EstadoReserva.EN_CURSO,
        Reserva.fin > ahora,
        Reserva.fin <= ahora + timedelta(minutes=RECORDATORIO_MINUTOS),
        Reserva.id.in_(dentro),
    ):
        if notificar(
            db, reserva.usuario_id, TipoNotificacion.RECORDATORIO_FIN,
            f"Te quedan 30 minutos: tu reserva en la plaza {reserva.espacio.numero} termina a las "
            f"{hora_texto(reserva.fin)}. Si necesitas más tiempo, pide una extensión.",
            clave=f"recordatorio_fin:{reserva.id}",
            reserva_id=reserva.id, parqueadero_id=reserva.espacio.parqueadero_id,
        ):
            resumen["recordatorios"] += 1

    # 2. T: el vehículo sigue dentro → excedida
    for reserva in reservas_en(db, parqueadero_id).filter(
        Reserva.estado == EstadoReserva.EN_CURSO,
        Reserva.fin <= ahora,
        Reserva.id.in_(dentro),
    ):
        reserva.estado = EstadoReserva.EXCEDIDA
        reserva.exceso_desde = reserva.fin
        registrar_incidencia(
            db, reserva, TipoIncidencia.EXCESO,
            f"El vehículo sigue en {reserva.espacio.numero} después de las {hora_texto(reserva.fin)}.",
        )
        notificar(
            db, reserva.usuario_id, TipoNotificacion.EXCESO,
            f"Tu reserva terminó a las {hora_texto(reserva.fin)}. Desde ese momento corre tiempo extra "
            f"con recargo hasta que registres la salida.",
            clave=f"exceso:{reserva.id}",
            reserva_id=reserva.id, parqueadero_id=reserva.espacio.parqueadero_id,
        )
        resumen["excedidas"] += 1
    db.flush()

    # 3. Plaza todavía ocupada con una reserva siguiente (B)
    for reserva_a in reservas_en(db, parqueadero_id).filter(
        Reserva.estado == EstadoReserva.EXCEDIDA,
        Reserva.id.in_(dentro),
    ).all():
        parqueadero = db.get(Parqueadero, reserva_a.espacio.parqueadero_id)
        reserva_b = db.query(Reserva).filter(
            Reserva.espacio_id == reserva_a.espacio_id,
            Reserva.estado == EstadoReserva.CONFIRMADA,
            Reserva.inicio >= reserva_a.fin,
        ).order_by(Reserva.inicio).first()
        if reserva_b is None:
            continue

        # Alerta: en T + 15, o antes si B empieza pronto; solo si B está cerca
        momento_alerta = min(
            reserva_a.fin + timedelta(minutes=ALERTA_MINUTOS),
            reserva_b.inicio - timedelta(minutes=BUSQUEDA_MINUTOS),
        )
        cerca = reserva_b.inicio - ahora <= timedelta(minutes=parqueadero.umbral_aviso_minutos)
        if ahora >= momento_alerta and cerca:
            registrar_incidencia(
                db, reserva_b, TipoIncidencia.RETRASO,
                f"La plaza {reserva_a.espacio.numero} sigue ocupada por {reserva_a.vehiculo.placa} "
                f"(reserva terminó a las {hora_texto(reserva_a.fin)}).",
            )
            if notificar(
                db, parqueadero.propietario_id, TipoNotificacion.ALERTA_RETRASO,
                f"Plaza {reserva_a.espacio.numero} ocupada después de su hora de fin "
                f"({hora_texto(reserva_a.fin)}). Hay una reserva a las {hora_texto(reserva_b.inicio)}.",
                clave=f"alerta_retraso:{reserva_b.id}",
                reserva_id=reserva_b.id, parqueadero_id=parqueadero.id, prioridad="alta",
            ):
                resumen["alertas"] += 1

        # Búsqueda de otra plaza: 30 min antes de que llegue B
        if ahora >= reserva_b.inicio - timedelta(minutes=BUSQUEDA_MINUTOS):
            if intentar_reasignar(db, reserva_b, reserva_a.espacio_id, ahora):
                resumen["reasignaciones"] += 1
            else:
                registrar_incidencia(
                    db, reserva_b, TipoIncidencia.SIN_ALTERNATIVA,
                    f"No hay otra plaza libre para la reserva de las {hora_texto(reserva_b.inicio)}.",
                )
                avisado = notificar(
                    db, reserva_b.usuario_id, TipoNotificacion.SIN_ALTERNATIVA,
                    f"La plaza {reserva_a.espacio.numero} de tu reserva de las {hora_texto(reserva_b.inicio)} "
                    f"sigue ocupada y no hay otra disponible. Puedes cambiar el horario o cancelar desde "
                    f"Mis reservas con reembolso completo. Te avisaremos si se libera a tiempo.",
                    clave=f"sin_alternativa:{reserva_b.id}",
                    reserva_id=reserva_b.id, parqueadero_id=parqueadero.id,
                )
                notificar(
                    db, parqueadero.propietario_id, TipoNotificacion.SIN_ALTERNATIVA,
                    f"Sin plaza alternativa para la reserva de las {hora_texto(reserva_b.inicio)} "
                    f"en {reserva_a.espacio.numero}. El cliente fue avisado.",
                    clave=f"sin_alternativa_propietario:{reserva_b.id}",
                    reserva_id=reserva_b.id, parqueadero_id=parqueadero.id, prioridad="alta",
                )
                if avisado:
                    resumen["sin_alternativa"] += 1

    # 4. La plaza se liberó a tiempo: resolver y conservar la reserva
    abiertas = reservas_en(db, parqueadero_id).join(
        Incidencia, Incidencia.reserva_id == Reserva.id
    ).filter(
        Reserva.estado == EstadoReserva.CONFIRMADA,
        Incidencia.estado == EstadoIncidencia.ABIERTA,
        Incidencia.tipo.in_([TipoIncidencia.RETRASO, TipoIncidencia.SIN_ALTERNATIVA]),
    ).distinct().all()
    for reserva_b in abiertas:
        if plaza_ocupada(db, reserva_b.espacio_id):
            continue
        hubo_aviso = db.query(Incidencia.id).filter(
            Incidencia.reserva_id == reserva_b.id,
            Incidencia.tipo == TipoIncidencia.SIN_ALTERNATIVA,
        ).first() is not None
        resumen["resueltas"] += resolver_incidencias(
            db, reserva_b.id, [TipoIncidencia.RETRASO, TipoIncidencia.SIN_ALTERNATIVA], ahora
        )
        if hubo_aviso:
            notificar(
                db, reserva_b.usuario_id, TipoNotificacion.INCIDENCIA_RESUELTA,
                f"Buenas noticias: la plaza {reserva_b.espacio.numero} ya está libre. "
                f"Conservas tu reserva de las {hora_texto(reserva_b.inicio)}.",
                clave=f"incidencia_resuelta:{reserva_b.id}",
                reserva_id=reserva_b.id, parqueadero_id=reserva_b.espacio.parqueadero_id,
            )

    db.commit()
    return resumen


# ============================================
# EXTENSIÓN PEDIDA POR EL CLIENTE
# ============================================

def extender_reserva(db: Session, reserva: Reserva, nuevo_fin: datetime, ahora: datetime) -> Reserva:
    """
    Extiende el fin de una reserva si respeta la siguiente reserva y su margen.

    - Solo reservas confirmadas o en curso (antes de su hora de fin; una
      reserva excedida ya está cobrando tiempo extra).
    - El tiempo agregado se cobra a la tarifa congelada de la reserva, en
      fracciones de 15 minutos.
    - El EXCLUDE de PostgreSQL vuelve a validar al guardar.
    """
    if reserva.estado not in (EstadoReserva.CONFIRMADA, EstadoReserva.EN_CURSO):
        raise OperacionError("Solo puedes extender una reserva confirmada o en curso.")
    if nuevo_fin <= reserva.fin:
        raise OperacionError("La nueva hora de fin debe ser posterior a la actual.")
    if nuevo_fin <= ahora:
        raise OperacionError("La nueva hora de fin ya pasó.")

    mensaje_cruce = (
        "No se puede extender hasta esa hora: choca con la siguiente reserva de la plaza "
        f"(se respeta un margen de {reserva.margen_minutos} minutos)."
    )
    if hay_cruce(db, reserva.espacio_id, reserva.inicio, nuevo_fin, reserva.margen_minutos,
                 excluir_id=reserva.id):
        raise OperacionError(mensaje_cruce)

    adicional = valor_por_minutos(Decimal(str(reserva.tarifa_hora)), minutos_cobrables(reserva.fin, nuevo_fin))
    fin_anterior = reserva.fin
    try:
        with db.begin_nested():
            reserva.fin_original = reserva.fin_original or fin_anterior
            reserva.fin = nuevo_fin
            reserva.total = float(Decimal(str(reserva.total)) + adicional)
            # Columna vieja que todavía muestran las vistas
            reserva.hora_salida = nuevo_fin.astimezone(fin_anterior.tzinfo).strftime("%H:%M")
            db.flush()
    except IntegrityError as error:
        if isinstance(error.orig, ExclusionViolation):
            raise OperacionError(mensaje_cruce)
        raise

    estancia = db.query(Estancia).filter(Estancia.reserva_id == reserva.id).first()
    if estancia is not None:
        estancia.salida_estimada = nuevo_fin
    db.commit()
    return reserva
