"""
Servicio de métricas para los paneles del propietario (Módulo 5).

Definiciones (documentadas también en los paneles):
- Zona horaria: America/Bogota. "Hoy" va de 00:00 a 24:00 hora de Colombia
  y "este mes" desde el día 1 a las 00:00.
- Ingresos: suma de los pagos CONFIRMADOS en el período (por su fecha de
  confirmación) MENOS los reembolsos hechos en ese mismo período. Un
  reembolso resta el día en que se devuelve, no el día del pago original.
  Pagos pendientes o fallidos no cuentan.
- Capacidad operativa: plazas que no están en mantenimiento.
- Ocupación: plazas operativas con un vehículo dentro / capacidad operativa.
  Para varios parqueaderos se suman ocupadas y operativas (no se promedian
  porcentajes: un parqueadero grande pesa más que uno pequeño).
- Plaza "afectada": tiene un vehículo cuya reserva está excedida o una
  incidencia abierta (exceso, retraso o sin alternativa).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import (
    Cuenta,
    Espacio,
    EstadoEspacio,
    EstadoIncidencia,
    EstadoPago,
    EstadoParqueadero,
    EstadoReserva,
    Estancia,
    Incidencia,
    Pago,
    Parqueadero,
    Reserva,
    RolUsuario,
    TipoPago,
    Usuario,
)
from app.services.ocupacion import hora_texto, orden_codigo
from app.services.reservas import ZONA_NEGOCIO

HORAS_RESERVAS_PROXIMAS = 24


# ============================================
# PERÍODOS (hora de Colombia)
# ============================================

def rango_dia(ahora: datetime) -> tuple[datetime, datetime]:
    """[00:00 de hoy, 00:00 de mañana) en hora de Colombia."""
    local = ahora.astimezone(ZONA_NEGOCIO)
    inicio = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return inicio, inicio + timedelta(days=1)


def rango_mes(ahora: datetime) -> tuple[datetime, datetime]:
    """[día 1 a las 00:00, día 1 del mes siguiente) en hora de Colombia."""
    local = ahora.astimezone(ZONA_NEGOCIO)
    inicio = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    siguiente = (inicio + timedelta(days=32)).replace(day=1)
    return inicio, siguiente


# ============================================
# INGRESOS
# ============================================

def ingresos(db: Session, parqueadero_ids: list[int], desde: datetime, hasta: datetime) -> Decimal:
    """Pagos confirmados en [desde, hasta) menos reembolsos del mismo período."""
    if not parqueadero_ids:
        return Decimal(0)
    signo = case((Pago.tipo == TipoPago.REEMBOLSO, -Pago.monto), else_=Pago.monto)
    total = db.query(func.coalesce(func.sum(signo), 0)).select_from(Pago).join(
        Cuenta, Pago.cuenta_id == Cuenta.id
    ).filter(
        Cuenta.parqueadero_id.in_(parqueadero_ids),
        Pago.estado == EstadoPago.PAGADO,
        Pago.confirmado_en >= desde,
        Pago.confirmado_en < hasta,
    ).scalar()
    return Decimal(str(total))


def ingresos_por_parqueadero(db: Session, parqueadero_ids: list[int], desde: datetime, hasta: datetime) -> dict:
    """{parqueadero_id: ingresos} en una sola consulta (para la comparativa)."""
    if not parqueadero_ids:
        return {}
    signo = case((Pago.tipo == TipoPago.REEMBOLSO, -Pago.monto), else_=Pago.monto)
    filas = db.query(Cuenta.parqueadero_id, func.sum(signo)).select_from(Pago).join(
        Cuenta, Pago.cuenta_id == Cuenta.id
    ).filter(
        Cuenta.parqueadero_id.in_(parqueadero_ids),
        Pago.estado == EstadoPago.PAGADO,
        Pago.confirmado_en >= desde,
        Pago.confirmado_en < hasta,
    ).group_by(Cuenta.parqueadero_id).all()
    resultado = {pid: Decimal(0) for pid in parqueadero_ids}
    resultado.update({pid: Decimal(str(valor)) for pid, valor in filas})
    return resultado


# ============================================
# OCUPACIÓN Y PLAZAS
# ============================================

def porcentaje(parte: int, total: int) -> float:
    """Porcentaje con dos decimales (0 si no hay capacidad)."""
    return round(parte * 100 / total, 2) if total else 0.0


def estado_plazas(db: Session, parqueadero: Parqueadero) -> list[dict]:
    """
    Situación de cada plaza, en este orden de prioridad:
    mantenimiento > afectada > reserva > directo > libre.
    """
    espacios = sorted(
        db.query(Espacio).filter(Espacio.parqueadero_id == parqueadero.id).all(),
        key=orden_codigo,
    )
    dentro = {
        e.espacio_id: e for e in db.query(Estancia).filter(
            Estancia.parqueadero_id == parqueadero.id,
            Estancia.salida_en.is_(None),
        )
    }
    con_incidencia = {
        fila[0] for fila in db.query(Incidencia.espacio_id).filter(
            Incidencia.parqueadero_id == parqueadero.id,
            Incidencia.estado == EstadoIncidencia.ABIERTA,
            Incidencia.espacio_id.is_not(None),
        )
    }

    plazas = []
    for espacio in espacios:
        estancia = dentro.get(espacio.id)
        excedida = estancia is not None and estancia.reserva is not None \
            and estancia.reserva.estado == EstadoReserva.EXCEDIDA
        if espacio.estado == EstadoEspacio.MANTENIMIENTO:
            situacion = "mantenimiento"
        elif excedida or espacio.id in con_incidencia:
            situacion = "afectada"
        elif estancia is not None and estancia.reserva_id:
            situacion = "reserva"
        elif estancia is not None:
            situacion = "directo"
        else:
            situacion = "libre"
        plazas.append({
            "numero": espacio.numero,
            "tipo": espacio.tipo.value,
            "situacion": situacion,
            "placa": estancia.placa if estancia else None,
        })
    return plazas


def ocupacion(plazas: list[dict]) -> dict:
    """Ocupación a partir de la situación de las plazas."""
    operativas = sum(1 for p in plazas if p["situacion"] != "mantenimiento")
    ocupadas = sum(1 for p in plazas if p["situacion"] != "mantenimiento" and p["placa"])
    return {
        "espacios": len(plazas),
        "mantenimiento": len(plazas) - operativas,
        "operativas": operativas,
        "ocupadas": ocupadas,
        "porcentaje": porcentaje(ocupadas, operativas),
    }


def alertas_activas(db: Session, parqueadero_ids: list[int]) -> list[Incidencia]:
    """Incidencias abiertas, de la más reciente a la más antigua."""
    if not parqueadero_ids:
        return []
    return db.query(Incidencia).filter(
        Incidencia.parqueadero_id.in_(parqueadero_ids),
        Incidencia.estado == EstadoIncidencia.ABIERTA,
    ).order_by(Incidencia.creado_en.desc()).all()


def reservas_proximas(db: Session, parqueadero: Parqueadero, ahora: datetime) -> list[Reserva]:
    """Reservas confirmadas o pendientes que empiezan en las próximas 24 h."""
    return db.query(Reserva).join(Espacio).filter(
        Espacio.parqueadero_id == parqueadero.id,
        Reserva.estado.in_([EstadoReserva.CONFIRMADA, EstadoReserva.PENDIENTE]),
        Reserva.inicio >= ahora,
        Reserva.inicio < ahora + timedelta(hours=HORAS_RESERVAS_PROXIMAS),
    ).order_by(Reserva.inicio).all()


# ============================================
# RESÚMENES PARA LOS PANELES
# ============================================

def alerta_a_dict(incidencia: Incidencia) -> dict:
    return {
        "id": incidencia.id,
        "parqueadero_id": incidencia.parqueadero_id,
        "tipo": incidencia.tipo.value,
        "detalle": incidencia.detalle,
        "plaza": incidencia.espacio.numero if incidencia.espacio else None,
        "desde": incidencia.creado_en.isoformat(),
    }


def resumen_parqueadero(db: Session, parqueadero: Parqueadero, ahora: datetime) -> dict:
    """Todo lo del panel individual, listo para JSON o para la plantilla."""
    plazas = estado_plazas(db, parqueadero)
    return {
        "id": parqueadero.id,
        "nombre": parqueadero.nombre,
        "zona_horaria": "America/Bogota",
        "plazas": plazas,
        "ocupacion": ocupacion(plazas),
        "ingresos_dia": float(ingresos(db, [parqueadero.id], *rango_dia(ahora))),
        "ingresos_mes": float(ingresos(db, [parqueadero.id], *rango_mes(ahora))),
        "reservas_proximas": [
            {
                "id": r.id,
                "plaza": r.espacio.numero,
                "placa": r.vehiculo.placa,
                "inicio": r.inicio.isoformat(),
                "inicio_texto": hora_texto(r.inicio),
                "fin_texto": hora_texto(r.fin),
                "estado": r.estado.value,
            }
            for r in reservas_proximas(db, parqueadero, ahora)
        ],
        "alertas": [alerta_a_dict(i) for i in alertas_activas(db, [parqueadero.id])],
    }


def resumen_propietario(db: Session, usuario: Usuario, ahora: datetime) -> dict:
    """
    Panel consolidado: todos los parqueaderos del propietario.

    Solo se consultan parqueaderos con propietario_id = usuario.id: el
    filtro está aquí, no en la plantilla, así nadie ve datos ajenos.
    """
    parqueaderos = db.query(Parqueadero).filter(
        Parqueadero.propietario_id == usuario.id
    ).order_by(Parqueadero.nombre).all()
    ids = [p.id for p in parqueaderos]
    por_dia = ingresos_por_parqueadero(db, ids, *rango_dia(ahora))
    por_mes = ingresos_por_parqueadero(db, ids, *rango_mes(ahora))

    filas = []
    ocupadas = operativas = 0
    for p in parqueaderos:
        o = ocupacion(estado_plazas(db, p))
        ocupadas += o["ocupadas"]
        operativas += o["operativas"]
        filas.append({
            "id": p.id,
            "nombre": p.nombre,
            "estado": p.estado.value,
            "ocupadas": o["ocupadas"],
            "operativas": o["operativas"],
            "porcentaje": o["porcentaje"],
            "ingresos_dia": float(por_dia.get(p.id, 0)),
            "ingresos_mes": float(por_mes.get(p.id, 0)),
        })

    alertas = alertas_activas(db, ids)
    return {
        "zona_horaria": "America/Bogota",
        "parqueaderos": filas,
        "ingresos_dia": float(sum(por_dia.values(), Decimal(0))),
        "ingresos_mes": float(sum(por_mes.values(), Decimal(0))),
        "ocupacion": {"ocupadas": ocupadas, "operativas": operativas, "porcentaje": porcentaje(ocupadas, operativas)},
        "vehiculos_activos": ocupadas,
        "alertas_pendientes": len(alertas),
        "alertas": [alerta_a_dict(i) for i in alertas[:10]],
    }


# ============================================
# RESUMEN GLOBAL (panel del admin_global)
# ============================================

def resumen_global(db: Session, ahora: datetime) -> dict:
    """
    Indicadores de toda la plataforma con las mismas definiciones que los
    paneles del propietario (ingresos = pagos - reembolsos del período,
    ocupación = ocupadas / operativas, sumando parqueaderos activos).
    """
    todos = db.query(Parqueadero).all()
    ids = [p.id for p in todos]

    por_estado = {e.value: 0 for e in EstadoParqueadero}
    for p in todos:
        por_estado[p.estado.value] += 1

    por_rol = {r.value: 0 for r in RolUsuario}
    for rol, cantidad in db.query(Usuario.rol, func.count(Usuario.id)).group_by(Usuario.rol):
        por_rol[getattr(rol, "value", rol)] = cantidad
    suspendidos = db.query(func.count(Usuario.id)).filter(Usuario.activo.is_(False)).scalar()

    ocupadas = operativas = 0
    for p in todos:
        if p.estado == EstadoParqueadero.ACTIVO:
            o = ocupacion(estado_plazas(db, p))
            ocupadas += o["ocupadas"]
            operativas += o["operativas"]

    desde, hasta = rango_dia(ahora)
    reservas_hoy = db.query(func.count(Reserva.id)).filter(
        Reserva.inicio >= desde,
        Reserva.inicio < hasta,
        Reserva.estado != EstadoReserva.CANCELADA,
    ).scalar()

    return {
        "zona_horaria": "America/Bogota",
        "ingresos_dia": float(ingresos(db, ids, *rango_dia(ahora))),
        "ingresos_mes": float(ingresos(db, ids, *rango_mes(ahora))),
        "parqueaderos": por_estado,
        "usuarios": por_rol,
        "usuarios_suspendidos": suspendidos,
        "ocupacion": {"ocupadas": ocupadas, "operativas": operativas, "porcentaje": porcentaje(ocupadas, operativas)},
        "reservas_hoy": reservas_hoy,
        "alertas_pendientes": len(alertas_activas(db, ids)),
    }
