"""
Servicio de ocupación física para ParkSmart (Módulo 2).

Maneja lo que pasa en la puerta del parqueadero:
- Ingreso directo (walk-in): el conductor llega sin reserva y sin cuenta.
- Entrada con reserva (check-in).
- Salida (check-out) con el cálculo del importe final.

Agenda y ocupación son cosas distintas:
- reservas  = lo programado.
- estancias = el vehículo que está dentro AHORA.

Reglas de cobro (decisiones aprobadas):
- Se cobra por fracciones de 15 minutos, redondeando hacia arriba,
  en pesos enteros.
- Con reserva, el tiempo reservado cuesta lo pactado (reserva.total).
  El tiempo después del fin es tiempo extra; los primeros gracia_minutos
  no se cobran. Pasada la gracia se cobra TODO el exceso.
- El tiempo extra paga la tarifa normal más un recargo
  (recargo_exceso_pct, 50 % por defecto = 1.5 veces).
- El impuesto (impuesto_pct) se aplica al subtotal.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ESTADOS_RESERVA_ACTIVOS,
    Cuenta,
    Espacio,
    EstadoEspacio,
    EstadoReserva,
    EstadoIncidencia,
    Estancia,
    Incidencia,
    Parqueadero,
    Reserva,
    TipoEspacio,
    TipoIncidencia,
    Usuario,
)
from app.services.reservas import ZONA_NEGOCIO

FRACCION_MINUTOS = 15


class OperacionError(Exception):
    """Error de negocio con un mensaje listo para mostrarle al operario."""


# ============================================
# LIQUIDACIÓN (función pura, sin base de datos)
# ============================================

@dataclass
class Liquidacion:
    """Desglose del importe final de una estancia."""
    minutos_base: int
    minutos_extra: int
    valor_base: Decimal
    valor_extra: Decimal
    penalizacion: Decimal
    subtotal: Decimal
    impuesto: Decimal
    total: Decimal


def pesos(valor: Decimal) -> Decimal:
    """Redondea a pesos enteros (0.5 sube)."""
    return valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def minutos_cobrables(desde: datetime, hasta: datetime) -> int:
    """Minutos entre dos instantes, subidos a la siguiente fracción de 15."""
    segundos = max(0, int((hasta - desde).total_seconds()))
    fraccion = FRACCION_MINUTOS * 60
    fracciones = -(-segundos // fraccion)  # división hacia arriba
    return fracciones * FRACCION_MINUTOS


def valor_por_minutos(tarifa_hora: Decimal, minutos: int) -> Decimal:
    """Precio de unos minutos a una tarifa por hora, en pesos enteros."""
    return pesos(tarifa_hora * minutos / 60)


def liquidar(
    tarifa_hora,
    entrada: datetime,
    salida: datetime,
    impuesto_pct=0,
    recargo_exceso_pct=0,
    gracia_minutos: int = 0,
    reserva_inicio: Optional[datetime] = None,
    reserva_fin: Optional[datetime] = None,
    reserva_total=None,
) -> Liquidacion:
    """
    Calcula el importe final de una estancia.

    - Sin reserva: se cobra el tiempo real, en fracciones de 15 min.
    - Con reserva: base = lo pactado (reserva_total); el tiempo después de
      reserva_fin es extra si supera la gracia.
    """
    tarifa = Decimal(str(tarifa_hora))

    if reserva_fin is None:
        minutos_base = minutos_cobrables(entrada, salida)
        valor_base = valor_por_minutos(tarifa, minutos_base)
        minutos_extra = 0
    else:
        minutos_base = int((reserva_fin - reserva_inicio).total_seconds() // 60)
        valor_base = pesos(Decimal(str(reserva_total)))
        exceso_segundos = (salida - reserva_fin).total_seconds()
        if exceso_segundos > gracia_minutos * 60:
            minutos_extra = minutos_cobrables(reserva_fin, salida)
        else:
            minutos_extra = 0

    valor_extra = valor_por_minutos(tarifa, minutos_extra)
    penalizacion = pesos(valor_extra * Decimal(str(recargo_exceso_pct)) / 100)
    subtotal = valor_base + valor_extra + penalizacion
    impuesto = pesos(subtotal * Decimal(str(impuesto_pct)) / 100)

    return Liquidacion(
        minutos_base=minutos_base,
        minutos_extra=minutos_extra,
        valor_base=valor_base,
        valor_extra=valor_extra,
        penalizacion=penalizacion,
        subtotal=subtotal,
        impuesto=impuesto,
        total=subtotal + impuesto,
    )


# ============================================
# ASIGNACIÓN DE PLAZA PARA UN INGRESO DIRECTO
# ============================================

@dataclass
class Candidata:
    """Plaza libre que puede recibir un ingreso directo."""
    espacio: Espacio
    proxima_reserva: Optional[Reserva]


def hora_texto(instante: datetime) -> str:
    """Hora local legible, por ejemplo '3:05 p. m.'."""
    local = instante.astimezone(ZONA_NEGOCIO)
    sufijo = "a. m." if local.hour < 12 else "p. m."
    hora = local.hour % 12 or 12
    return f"{hora}:{local.minute:02d} {sufijo}"


def orden_codigo(espacio: Espacio):
    """Ordena C2 antes que C10: por prefijo y luego por número."""
    numero = "".join(c for c in espacio.numero if c.isdigit())
    return (espacio.numero.rstrip("0123456789"), int(numero) if numero else 0, espacio.numero)


def plazas_candidatas(
    db: Session,
    parqueadero: Parqueadero,
    tipo,
    ahora: datetime,
    salida_estimada: Optional[datetime] = None,
) -> list[Candidata]:
    """
    Plazas del tipo pedido que pueden recibir un vehículo AHORA, en orden
    de preferencia.

    Se descarta una plaza si:
    - está en mantenimiento o tiene un vehículo dentro;
    - una reserva activa ya está en su ventana: desde (inicio - margen)
      hasta (fin + margen). Ese tiempo es del cliente que reservó;
    - se conoce la salida estimada y no deja libre el margen antes de la
      próxima reserva.

    Orden: primero las plazas sin reservas próximas; después, las que
    tienen la próxima reserva más lejana.
    """
    margen = timedelta(minutes=parqueadero.margen_minutos)

    ocupadas = db.query(Estancia.espacio_id).filter(
        Estancia.parqueadero_id == parqueadero.id,
        Estancia.salida_en.is_(None),
    )
    espacios = db.query(Espacio).filter(
        Espacio.parqueadero_id == parqueadero.id,
        # El tipo de vehículo y el de espacio usan los mismos valores
        Espacio.tipo == TipoEspacio(getattr(tipo, "value", tipo)),
        Espacio.estado != EstadoEspacio.MANTENIMIENTO,
        Espacio.id.not_in(ocupadas),
    ).all()
    if not espacios:
        return []

    reservas = db.query(Reserva).filter(
        Reserva.espacio_id.in_([e.id for e in espacios]),
        Reserva.estado.in_(ESTADOS_RESERVA_ACTIVOS),
        Reserva.fin > ahora - timedelta(hours=24),
    ).all()

    candidatas = []
    for espacio in espacios:
        bloqueada = False
        proxima = None
        for reserva in (r for r in reservas if r.espacio_id == espacio.id):
            fin_bloqueo = reserva.fin + timedelta(minutes=reserva.margen_minutos)
            if reserva.inicio - margen <= ahora < fin_bloqueo:
                bloqueada = True
                break
            if reserva.inicio > ahora and (proxima is None or reserva.inicio < proxima.inicio):
                proxima = reserva
        if bloqueada:
            continue
        if salida_estimada and proxima and salida_estimada + margen > proxima.inicio:
            continue
        candidatas.append(Candidata(espacio, proxima))

    # Sin reserva próxima primero; luego la próxima reserva más lejana
    candidatas.sort(key=lambda c: (
        c.proxima_reserva is not None,
        -(c.proxima_reserva.inicio.timestamp()) if c.proxima_reserva else 0,
        orden_codigo(c.espacio),
    ))
    return candidatas


def advertencia_reserva_proxima(candidata: Candidata, parqueadero: Parqueadero, ahora: datetime):
    """Mensaje para el operario si la plaza tiene una reserva dentro del umbral."""
    proxima = candidata.proxima_reserva
    if proxima is None:
        return None
    if proxima.inicio - ahora > timedelta(minutes=parqueadero.umbral_aviso_minutos):
        return None
    limite = proxima.inicio - timedelta(minutes=parqueadero.margen_minutos)
    return (
        f"La plaza {candidata.espacio.numero} tiene una reserva a las {hora_texto(proxima.inicio)}. "
        f"El vehículo debe salir antes de las {hora_texto(limite)}."
    )


# ============================================
# INGRESO DIRECTO (walk-in)
# ============================================

def registrar_ingreso_directo(
    db: Session,
    parqueadero: Parqueadero,
    usuario: Usuario,
    placa: str,
    tipo,
    ahora: datetime,
    nombre: Optional[str] = None,
    telefono: Optional[str] = None,
    salida_estimada: Optional[datetime] = None,
    espacio_id: Optional[int] = None,
):
    """
    Registra un vehículo que llega sin reserva y le asigna una plaza.

    espacio_id: plaza elegida por el operario. Debe ser una de las
    candidatas (libre, del tipo del vehículo y sin una reserva que lo
    impida); si no se indica, se usa la primera candidata.

    Returns:
        (estancia, advertencia): advertencia es None o un texto para el
        operario si la plaza tiene una reserva cercana.

    Si dos operarios registran a la vez y eligen la misma plaza, el índice
    único uq_estancias_espacio_activa rechaza al segundo; aquí se reintenta
    con la siguiente plaza candidata.
    """
    tarifa = parqueadero.tarifa_para(tipo)
    if tarifa is None:
        raise OperacionError("Este parqueadero no acepta ese tipo de vehículo.")
    if salida_estimada is not None and salida_estimada <= ahora:
        raise OperacionError("La salida estimada debe ser posterior a la hora actual.")

    ya_dentro = db.query(Estancia.id).filter(
        Estancia.parqueadero_id == parqueadero.id,
        Estancia.placa == placa,
        Estancia.salida_en.is_(None),
    ).first()
    if ya_dentro:
        raise OperacionError(f"El vehículo {placa} ya está dentro del parqueadero.")

    candidatas = plazas_candidatas(db, parqueadero, tipo, ahora, salida_estimada)
    if not candidatas:
        if salida_estimada:
            raise OperacionError(
                "No hay plazas libres de ese tipo que queden desocupadas a tiempo "
                "para la próxima reserva."
            )
        raise OperacionError("No hay plazas disponibles para ese tipo de vehículo en este momento.")
    if espacio_id is not None:
        candidatas = [c for c in candidatas if c.espacio.id == espacio_id]
        if not candidatas:
            raise OperacionError(
                "La plaza elegida no está disponible para ese vehículo (está ocupada, es de otro tipo "
                "o tiene una reserva próxima). Elige otra o deja que se asigne automáticamente."
            )

    for candidata in candidatas:
        estancia = Estancia(
            parqueadero_id=parqueadero.id,
            espacio_id=candidata.espacio.id,
            placa=placa,
            tipo_vehiculo=tipo,
            nombre_conductor=nombre or None,
            telefono_conductor=telefono or None,
            entrada_en=ahora,
            salida_estimada=salida_estimada,
            tarifa_hora=tarifa,
            registrado_por=usuario.id,
        )
        try:
            # SAVEPOINT: si la plaza se ocupó un instante antes, se deshace
            # solo este intento y se prueba la siguiente plaza.
            with db.begin_nested():
                db.add(estancia)
                db.flush()
        except IntegrityError as error:
            if "uq_estancias_placa_activa" in str(error.orig):
                raise OperacionError(f"El vehículo {placa} ya está dentro del parqueadero.")
            continue
        candidata.espacio.estado = EstadoEspacio.OCUPADO
        db.commit()
        return estancia, advertencia_reserva_proxima(candidata, parqueadero, ahora)

    raise OperacionError("Las plazas libres se ocuparon mientras se registraba. Intenta de nuevo.")


# ============================================
# ENTRADA CON RESERVA (check-in)
# ============================================

def registrar_entrada_reserva(
    db: Session,
    parqueadero: Parqueadero,
    usuario: Usuario,
    reserva_id: int,
    ahora: datetime,
):
    """
    Registra la llegada de un cliente con reserva.

    - Se puede entrar desde (inicio - margen) hasta el fin de la reserva.
    - La plaza tiene que estar físicamente libre.
    - Repetir la entrada no duplica nada: devuelve la estancia existente.

    Returns:
        (estancia, repetida): repetida = True si ya se había registrado.
    """
    reserva = db.query(Reserva).join(Espacio).filter(
        Reserva.id == reserva_id,
        Espacio.parqueadero_id == parqueadero.id,
    ).with_for_update(of=Reserva).first()
    if reserva is None:
        raise OperacionError("La reserva no existe en este parqueadero.")

    existente = db.query(Estancia).filter(Estancia.reserva_id == reserva.id).first()
    if existente:
        return existente, True

    if reserva.estado == EstadoReserva.PENDIENTE:
        raise OperacionError("La reserva está pendiente de pago: no se puede registrar la entrada.")
    if reserva.estado != EstadoReserva.CONFIRMADA:
        raise OperacionError(f"La reserva está {reserva.estado.value}: no se puede registrar la entrada.")

    desde = reserva.inicio - timedelta(minutes=reserva.margen_minutos)
    if ahora < desde:
        raise OperacionError(
            f"Todavía no es la hora de la reserva: se puede entrar desde las {hora_texto(desde)}."
        )
    if ahora >= reserva.fin:
        raise OperacionError("La reserva ya terminó: registra el vehículo como ingreso directo.")

    espacio = reserva.espacio
    if espacio.estado == EstadoEspacio.MANTENIMIENTO:
        raise OperacionError(f"La plaza {espacio.numero} está en mantenimiento.")
    ocupada = db.query(Estancia.id).filter(
        Estancia.espacio_id == espacio.id,
        Estancia.salida_en.is_(None),
    ).first()
    if ocupada:
        raise OperacionError(f"La plaza {espacio.numero} está ocupada por otro vehículo.")

    vehiculo = reserva.vehiculo
    cliente = reserva.usuario
    estancia = Estancia(
        parqueadero_id=parqueadero.id,
        espacio_id=espacio.id,
        reserva_id=reserva.id,
        placa=vehiculo.placa,
        tipo_vehiculo=vehiculo.tipo,
        nombre_conductor=cliente.nombre,
        telefono_conductor=cliente.telefono,
        entrada_en=ahora,
        salida_estimada=reserva.fin,
        tarifa_hora=reserva.tarifa_hora,
        registrado_por=usuario.id,
    )
    try:
        with db.begin_nested():
            db.add(estancia)
            db.flush()
    except IntegrityError:
        # Otra petición registró la misma entrada o la plaza se ocupó justo antes
        existente = db.query(Estancia).filter(Estancia.reserva_id == reserva.id).first()
        if existente:
            return existente, True
        raise OperacionError(f"La plaza {espacio.numero} está ocupada por otro vehículo.")

    reserva.estado = EstadoReserva.EN_CURSO
    espacio.estado = EstadoEspacio.OCUPADO
    db.commit()
    return estancia, False


# ============================================
# SALIDA (check-out)
# ============================================

def registrar_salida(
    db: Session,
    parqueadero: Parqueadero,
    usuario: Usuario,
    estancia_id: int,
    ahora: datetime,
) -> Cuenta:
    """
    Registra la salida, libera la plaza y deja la cuenta liquidada.

    - Bloquea la fila de la estancia (FOR UPDATE): dos salidas simultáneas
      no pueden liquidar dos veces.
    - Repetir la salida devuelve la misma cuenta sin recalcular.
    - El total queda como saldo pendiente (estado_pago 'pendiente') hasta
      que se registre el pago.
    """
    estancia = db.query(Estancia).filter(
        Estancia.id == estancia_id,
        Estancia.parqueadero_id == parqueadero.id,
    ).with_for_update().first()
    if estancia is None:
        raise OperacionError("Ese vehículo no está registrado en este parqueadero.")

    cuenta = db.query(Cuenta).filter(Cuenta.estancia_id == estancia.id).first()
    if estancia.salida_en is not None and cuenta is not None:
        return cuenta

    reserva = estancia.reserva
    if cuenta is None and reserva is not None:
        # Una reserva con pago anticipado ya puede tener su cuenta
        cuenta = db.query(Cuenta).filter(Cuenta.reserva_id == reserva.id).first()
    if cuenta is None:
        # estancia_id desde el inicio: si SQLAlchemy hace un autoflush antes
        # de terminar, la fila ya cumple ck_cuentas_operacion
        cuenta = Cuenta(
            parqueadero_id=parqueadero.id,
            reserva_id=reserva.id if reserva else None,
            estancia_id=estancia.id,
            tarifa_hora=estancia.tarifa_hora,
        )
        db.add(cuenta)

    liquidacion = liquidar(
        tarifa_hora=estancia.tarifa_hora,
        entrada=estancia.entrada_en,
        salida=ahora,
        impuesto_pct=parqueadero.impuesto_pct,
        recargo_exceso_pct=parqueadero.recargo_exceso_pct,
        gracia_minutos=parqueadero.gracia_minutos,
        reserva_inicio=reserva.inicio if reserva else None,
        reserva_fin=reserva.fin if reserva else None,
        reserva_total=reserva.total if reserva else None,
    )

    estancia.salida_en = ahora
    estancia.cerrado_por = usuario.id
    cuenta.estancia_id = estancia.id
    cuenta.tarifa_hora = estancia.tarifa_hora
    cuenta.minutos_base = liquidacion.minutos_base
    cuenta.minutos_extra = liquidacion.minutos_extra
    cuenta.valor_base = float(liquidacion.valor_base)
    cuenta.valor_extra = float(liquidacion.valor_extra)
    cuenta.penalizacion = float(liquidacion.penalizacion)
    cuenta.subtotal = float(liquidacion.subtotal)
    cuenta.impuesto_pct = parqueadero.impuesto_pct
    cuenta.impuesto = float(liquidacion.impuesto)
    cuenta.total = float(liquidacion.total)
    cuenta.liquidada_en = ahora
    db.flush()

    # Import local: pagos importa este módulo (evita el import circular)
    from app.services.pagos import actualizar_estado_pago, emitir_comprobante
    db.refresh(cuenta)
    # Un pago anticipado ya registrado se descuenta solo del total final
    actualizar_estado_pago(cuenta)

    if reserva is not None:
        reserva.estado = EstadoReserva.COMPLETADA
        # El exceso ya quedó cobrado: su incidencia se cierra con la salida
        db.query(Incidencia).filter(
            Incidencia.reserva_id == reserva.id,
            Incidencia.tipo == TipoIncidencia.EXCESO,
            Incidencia.estado == EstadoIncidencia.ABIERTA,
        ).update(
            {Incidencia.estado: EstadoIncidencia.RESUELTA, Incidencia.resuelto_en: ahora},
            synchronize_session=False,
        )

    espacio = db.get(Espacio, estancia.espacio_id)
    if espacio.estado == EstadoEspacio.OCUPADO:
        espacio.estado = EstadoEspacio.DISPONIBLE

    # Si el anticipo cubre todo (o el total es 0), el comprobante sale ya
    emitir_comprobante(db, cuenta, ahora, confirmar=False)
    db.commit()
    return cuenta
