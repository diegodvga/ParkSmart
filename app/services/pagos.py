"""
Servicio de pagos y comprobantes para ParkSmart (Módulo 4).

Conceptos:
- Cuenta: una por operación (reserva y/o estancia), con el desglose congelado.
- Pago: cada movimiento de dinero de la cuenta (anticipado, en sitio,
  extra o reembolso). Solo los pagos en estado 'pagado' cuentan.
- Saldo = total - (pagado - reembolsado). Así un pago anticipado se
  descuenta solo del total final que se calcula en la salida.
- Factura (tabla comprobantes): documento interno, NO es factura
  electrónica DIAN. Una por cada cobro (migración 006):
  * pago anticipado (pasarela o caja) → su factura en el momento del pago;
  * al salir, con el saldo en 0 → factura de CIERRE solo por los cobros
    que todavía no tenían factura (por ejemplo, el tiempo extra). Si el
    anticipo ya cubría todo, no hay factura de cierre.
  Numeración consecutiva por parqueadero.

Pasarela simulada (pagar_reserva_en_linea): el cliente paga su reserva
con tarjeta o transferencia; si se aprueba, la reserva se confirma y se
emite la factura. No hay dinero real ni se guardan datos de tarjeta.

Evitar cargos duplicados:
- clave_idempotencia única por intento de pago: repetir el formulario
  (doble clic, reintento de red) devuelve el pago ya registrado.
- referencia_externa única: la pasarela puede reenviar la confirmación
  sin que se cobre dos veces.
- La cuenta se bloquea (FOR UPDATE) mientras se registra un pago.

Reembolsos al cancelar (decisión aprobada):
- 100 % si faltan más de 2 horas para el inicio o si la reserva quedó
  sin plaza alternativa; 0 % en otro caso.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Comprobante,
    Cuenta,
    EstadoIncidencia,
    EstadoPago,
    EstadoPagoCuenta,
    EstadoReserva,
    Incidencia,
    MetodoPago,
    Pago,
    Parqueadero,
    Reserva,
    SecuenciaComprobante,
    TipoIncidencia,
    TipoPago,
    Usuario,
)
from app.services.ocupacion import OperacionError, hora_texto

HORAS_CANCELACION_GRATIS = 2


def dinero(valor) -> Decimal:
    """Convierte un float de la base a Decimal sin errores de redondeo binario."""
    return Decimal(str(valor or 0))


# ============================================
# SALDO Y ESTADO DE LA CUENTA
# ============================================

def totales(cuenta: Cuenta) -> tuple[Decimal, Decimal]:
    """(pagado, reembolsado): suma de los pagos confirmados de la cuenta."""
    pagado = sum((dinero(p.monto) for p in cuenta.pagos
                  if p.estado == EstadoPago.PAGADO and p.tipo != TipoPago.REEMBOLSO), Decimal(0))
    reembolsado = sum((dinero(p.monto) for p in cuenta.pagos
                       if p.estado == EstadoPago.PAGADO and p.tipo == TipoPago.REEMBOLSO), Decimal(0))
    return pagado, reembolsado


def saldo(cuenta: Cuenta) -> Decimal:
    """Lo que falta pagar. Nunca negativo."""
    pagado, reembolsado = totales(cuenta)
    return max(Decimal(0), dinero(cuenta.total) - (pagado - reembolsado))


def actualizar_estado_pago(cuenta: Cuenta) -> None:
    """Recalcula estado_pago a partir de los pagos confirmados."""
    pagado, reembolsado = totales(cuenta)
    neto = pagado - reembolsado
    if reembolsado > 0 and neto <= 0:
        cuenta.estado_pago = EstadoPagoCuenta.REEMBOLSADO
    elif neto >= dinero(cuenta.total):
        cuenta.estado_pago = EstadoPagoCuenta.PAGADO
    elif neto > 0:
        cuenta.estado_pago = EstadoPagoCuenta.PARCIAL
    else:
        cuenta.estado_pago = EstadoPagoCuenta.PENDIENTE


def bloquear_cuenta(db: Session, cuenta_id: int) -> Cuenta:
    """Relee la cuenta con FOR UPDATE: un solo pago a la vez por cuenta."""
    cuenta = db.query(Cuenta).filter(Cuenta.id == cuenta_id).with_for_update().one()
    db.refresh(cuenta)
    return cuenta


def pago_por_clave(db: Session, clave: str) -> Optional[Pago]:
    """Pago ya registrado con esa clave de idempotencia (o None)."""
    return db.query(Pago).filter(Pago.clave_idempotencia == clave).first()


def guardar_pago(db: Session, pago: Pago) -> Pago:
    """
    Inserta el pago dentro de un SAVEPOINT. Si otra petición registró la
    misma clave un instante antes, devuelve ese pago en vez de duplicar.
    """
    try:
        with db.begin_nested():
            db.add(pago)
            db.flush()
    except IntegrityError:
        existente = pago_por_clave(db, pago.clave_idempotencia)
        if existente is None:
            raise
        return existente
    return pago


# ============================================
# PAGOS
# ============================================

def tipo_de_cobro(cuenta: Cuenta) -> TipoPago:
    """
    Tipo del cobro según el momento:
    - antes de la salida → anticipado;
    - después, si ya hubo anticipo → extra (el tiempo excedido);
    - después, sin anticipo → en sitio.
    """
    if cuenta.liquidada_en is None:
        return TipoPago.ANTICIPADO
    hubo_anticipo = any(p.tipo == TipoPago.ANTICIPADO and p.estado == EstadoPago.PAGADO for p in cuenta.pagos)
    return TipoPago.EXTRA if hubo_anticipo else TipoPago.EN_SITIO


def registrar_pago(
    db: Session,
    cuenta: Cuenta,
    metodo: MetodoPago,
    monto,
    clave: str,
    usuario: Optional[Usuario],
    ahora: datetime,
):
    """
    Registra un pago confirmado (efectivo, tarjeta o transferencia en la caja).

    Returns:
        (pago, factura): un pago anticipado tiene su factura enseguida; un
        cobro de salida, solo cuando el saldo llega a 0. Si no corresponde,
        factura es None.
    """
    existente = pago_por_clave(db, clave)
    if existente is not None:
        # Mismo intento repetido: no se cobra otra vez
        return existente, factura_del_pago(db, existente, ahora, confirmar=True)

    cuenta = bloquear_cuenta(db, cuenta.id)
    monto = dinero(monto)
    if monto <= 0:
        raise OperacionError("El monto debe ser mayor que 0.")
    pendiente = saldo(cuenta)
    if monto > pendiente:
        raise OperacionError(f"El monto ${monto:,.0f} supera el saldo pendiente de ${pendiente:,.0f}.".replace(",", "."))

    pago = guardar_pago(db, Pago(
        cuenta_id=cuenta.id,
        tipo=tipo_de_cobro(cuenta),
        metodo=metodo,
        monto=float(monto),
        estado=EstadoPago.PAGADO,
        clave_idempotencia=clave,
        registrado_por=usuario.id if usuario else None,
        confirmado_en=ahora,
    ))
    db.refresh(cuenta)
    actualizar_estado_pago(cuenta)
    factura = factura_del_pago(db, pago, ahora, confirmar=False)
    db.commit()
    return pago, factura


def cuenta_de_reserva(db: Session, reserva: Reserva) -> Cuenta:
    """
    Cuenta de la reserva; la crea con el total pactado si no existe.

    Si ya existía pero todavía no tiene pagos confirmados (por ejemplo, un
    intento rechazado y luego el cliente modificó la reserva), se alinea
    con el total actual de la reserva.
    """
    cuenta = db.query(Cuenta).filter(Cuenta.reserva_id == reserva.id).first()
    if cuenta is not None and cuenta.liquidada_en is None and not any(
            p.estado == EstadoPago.PAGADO for p in cuenta.pagos):
        cuenta.tarifa_hora = reserva.tarifa_hora
        cuenta.total = reserva.total
        cuenta.subtotal = reserva.total
        db.flush()
    if cuenta is None:
        cuenta = Cuenta(
            parqueadero_id=reserva.espacio.parqueadero_id,
            reserva_id=reserva.id,
            tarifa_hora=reserva.tarifa_hora,
            total=reserva.total,
            subtotal=reserva.total,
        )
        db.add(cuenta)
        db.flush()
    return cuenta


def registrar_pago_anticipado(
    db: Session,
    reserva: Reserva,
    metodo: MetodoPago,
    clave: str,
    usuario: Optional[Usuario],
    ahora: datetime,
) -> Pago:
    """
    Registra el pago anticipado del total pactado de una reserva pendiente
    (en la caja), la confirma y emite la factura del pago. Repetirlo con la
    misma clave no cobra dos veces.
    """
    existente = pago_por_clave(db, clave)
    if existente is not None:
        return existente
    if reserva.estado not in (EstadoReserva.PENDIENTE, EstadoReserva.CONFIRMADA):
        raise OperacionError(f"La reserva está {reserva.estado.value}: no admite pago anticipado.")

    cuenta = cuenta_de_reserva(db, reserva)
    cuenta = bloquear_cuenta(db, cuenta.id)
    pendiente = saldo(cuenta)
    if pendiente <= 0:
        raise OperacionError("La reserva ya está pagada.")

    pago = guardar_pago(db, Pago(
        cuenta_id=cuenta.id,
        tipo=TipoPago.ANTICIPADO,
        metodo=metodo,
        monto=float(pendiente),
        estado=EstadoPago.PAGADO,
        clave_idempotencia=clave,
        registrado_por=usuario.id if usuario else None,
        confirmado_en=ahora,
    ))
    db.refresh(cuenta)
    actualizar_estado_pago(cuenta)
    confirmar_reserva(reserva)
    emitir_factura_pago(db, pago, ahora)
    db.commit()
    return pago


def confirmar_reserva(reserva: Optional[Reserva]) -> None:
    """Una reserva pendiente de pago pasa a confirmada (deja de vencer)."""
    if reserva is not None and reserva.estado == EstadoReserva.PENDIENTE:
        reserva.estado = EstadoReserva.CONFIRMADA
        reserva.pendiente_expira_en = None


def reembolsar(
    db: Session,
    cuenta: Cuenta,
    monto,
    metodo: MetodoPago,
    clave: str,
    usuario: Optional[Usuario],
    ahora: datetime,
) -> Pago:
    """Devuelve dinero al cliente (como máximo lo que pagó neto)."""
    existente = pago_por_clave(db, clave)
    if existente is not None:
        return existente
    cuenta = bloquear_cuenta(db, cuenta.id)
    pagado, reembolsado = totales(cuenta)
    monto = dinero(monto)
    if monto <= 0 or monto > pagado - reembolsado:
        raise OperacionError("El reembolso no puede superar lo pagado.")
    pago = guardar_pago(db, Pago(
        cuenta_id=cuenta.id,
        tipo=TipoPago.REEMBOLSO,
        metodo=metodo,
        monto=float(monto),
        estado=EstadoPago.PAGADO,
        clave_idempotencia=clave,
        registrado_por=usuario.id if usuario else None,
        confirmado_en=ahora,
    ))
    db.refresh(cuenta)
    actualizar_estado_pago(cuenta)
    db.commit()
    return pago


def reembolso_por_cancelacion(db: Session, reserva: Reserva, ahora: datetime) -> Optional[Pago]:
    """
    Aplica la política de reembolso al cancelar una reserva.

    - Sin plaza alternativa (incidencia abierta): 100 %.
    - Más de HORAS_CANCELACION_GRATIS antes del inicio: 100 %.
    - En otro caso: 0 % (no se crea reembolso).

    Se devuelve por el mismo método del último pago. No hace commit.
    """
    cuenta = db.query(Cuenta).filter(Cuenta.reserva_id == reserva.id).first()
    if cuenta is None:
        return None
    pagado, reembolsado = totales(cuenta)
    neto = pagado - reembolsado
    if neto <= 0:
        return None
    sin_alternativa = db.query(Incidencia.id).filter(
        Incidencia.reserva_id == reserva.id,
        Incidencia.tipo == TipoIncidencia.SIN_ALTERNATIVA,
        Incidencia.estado == EstadoIncidencia.ABIERTA,
    ).first() is not None
    if not sin_alternativa and reserva.inicio - ahora <= timedelta(hours=HORAS_CANCELACION_GRATIS):
        return None

    ultimo = [p for p in cuenta.pagos if p.estado == EstadoPago.PAGADO and p.tipo != TipoPago.REEMBOLSO][-1]
    pago = guardar_pago(db, Pago(
        cuenta_id=cuenta.id,
        tipo=TipoPago.REEMBOLSO,
        metodo=ultimo.metodo,
        monto=float(neto),
        estado=EstadoPago.PAGADO,
        clave_idempotencia=f"reembolso-cancelacion:{reserva.id}",
        confirmado_en=ahora,
    ))
    db.refresh(cuenta)
    actualizar_estado_pago(cuenta)
    return pago


# ============================================
# PASARELA (simulada; preparada para una integración real)
# ============================================

def iniciar_pago_pasarela(
    db: Session,
    cuenta: Cuenta,
    monto,
    referencia: str,
    clave: str,
    ahora: datetime,
    metodo: MetodoPago = MetodoPago.PASARELA,
    confirmar: bool = True,
) -> Pago:
    """Crea un pago 'pendiente' que la pasarela confirmará o rechazará."""
    existente = pago_por_clave(db, clave)
    if existente is not None:
        return existente
    cuenta = bloquear_cuenta(db, cuenta.id)
    if dinero(monto) > saldo(cuenta):
        raise OperacionError("El monto supera el saldo pendiente.")
    pago = guardar_pago(db, Pago(
        cuenta_id=cuenta.id,
        tipo=tipo_de_cobro(cuenta),
        metodo=metodo,
        monto=float(dinero(monto)),
        estado=EstadoPago.PENDIENTE,
        clave_idempotencia=clave,
        referencia_externa=referencia,
    ))
    if confirmar:
        db.commit()
    return pago


def confirmar_pago_pasarela(
    db: Session, referencia: str, exito: bool, ahora: datetime, confirmar: bool = True,
) -> Optional[Pago]:
    """
    Aplica la respuesta de la pasarela. Es idempotente: si el pago ya no
    está pendiente (la pasarela reenvió la confirmación), no cambia nada.

    Si se aprueba: confirma la reserva pendiente de la cuenta y emite la
    factura que corresponda (anticipo enseguida; cierre si el saldo es 0).
    """
    pago = db.query(Pago).filter(Pago.referencia_externa == referencia).with_for_update().first()
    if pago is None or pago.estado != EstadoPago.PENDIENTE:
        return pago
    cuenta = bloquear_cuenta(db, pago.cuenta_id)
    pago.estado = EstadoPago.PAGADO if exito else EstadoPago.FALLIDO
    pago.confirmado_en = ahora
    db.flush()
    db.refresh(cuenta)
    actualizar_estado_pago(cuenta)
    if exito:
        confirmar_reserva(cuenta.reserva)
        factura_del_pago(db, pago, ahora, confirmar=False)
    if confirmar:
        db.commit()
    return pago


# Métodos que acepta la pasarela simulada del cliente
METODOS_EN_LINEA = (MetodoPago.TARJETA, MetodoPago.TRANSFERENCIA)


def pagar_reserva_en_linea(
    db: Session,
    reserva: Reserva,
    metodo: MetodoPago,
    aprobado: bool,
    clave: str,
    ahora: datetime,
):
    """
    Paga por adelantado el total de una reserva con la pasarela simulada.

    Todo ocurre en UNA transacción, con la reserva bloqueada (FOR UPDATE):
    así la tarea que vence las reservas pendientes no puede cancelarla en
    medio del cobro (no queda un pago aprobado de una reserva cancelada).

    Args:
        aprobado: respuesta simulada del banco (la decide el router).
        clave: clave de idempotencia del formulario (doble clic, reintento).

    Returns:
        (pago, factura): si el banco rechaza, el pago queda 'fallido', la
        reserva sigue pendiente y factura es None.

    Raises:
        OperacionError: plazo vencido, reserva no pagable o ya pagada.
    """
    existente = pago_por_clave(db, clave)
    if existente is not None:
        return existente, factura_del_pago(db, existente, ahora, confirmar=True)
    if metodo not in METODOS_EN_LINEA:
        raise OperacionError("Elige tarjeta o transferencia.")

    reserva = db.query(Reserva).filter(Reserva.id == reserva.id).with_for_update().one()
    db.refresh(reserva)
    if reserva.estado == EstadoReserva.PENDIENTE and reserva.pendiente_expira_en is not None \
            and reserva.pendiente_expira_en < ahora:
        # Import local: reservas importa este módulo (evita el import circular)
        from app.services.reservas import vencer_pendientes
        vencer_pendientes(db, reserva.espacio_id)
        db.commit()
        raise OperacionError("El tiempo para pagar venció y la reserva se canceló. Haz una nueva reserva.")
    if reserva.estado not in (EstadoReserva.PENDIENTE, EstadoReserva.CONFIRMADA):
        raise OperacionError(f"La reserva está {reserva.estado.value}: no se puede pagar en línea.")

    cuenta = bloquear_cuenta(db, cuenta_de_reserva(db, reserva).id)
    pendiente = saldo(cuenta)
    if pendiente <= 0:
        raise OperacionError("Esta reserva ya está pagada.")

    referencia = f"SIM-{uuid.uuid4().hex[:20].upper()}"
    iniciar_pago_pasarela(db, cuenta, pendiente, referencia, clave, ahora, metodo=metodo, confirmar=False)
    pago = confirmar_pago_pasarela(db, referencia, aprobado, ahora, confirmar=False)
    factura = factura_del_pago(db, pago, ahora, confirmar=False) if aprobado else None
    db.commit()
    return pago, factura


# ============================================
# COMPROBANTES
# ============================================

def siguiente_numero(db: Session, parqueadero_id: int) -> int:
    """
    Siguiente número de comprobante del parqueadero.

    INSERT ... ON CONFLICT DO UPDATE ... RETURNING es una sola sentencia
    atómica: la fila queda bloqueada hasta el commit, así dos operaciones
    simultáneas nunca reciben el mismo número.
    """
    sentencia = pg_insert(SecuenciaComprobante).values(parqueadero_id=parqueadero_id, ultimo=1)
    sentencia = sentencia.on_conflict_do_update(
        index_elements=["parqueadero_id"],
        set_={"ultimo": SecuenciaComprobante.ultimo + 1},
    ).returning(SecuenciaComprobante.ultimo)
    return db.execute(sentencia).scalar_one()


def facturas_de_cuenta(db: Session, cuenta_id: int) -> list:
    """Facturas de una cuenta, en orden de emisión."""
    return db.query(Comprobante).filter(Comprobante.cuenta_id == cuenta_id).order_by(Comprobante.numero).all()


def cobros_sin_factura(db: Session, cuenta: Cuenta) -> list:
    """Cobros confirmados de la cuenta que todavía no tienen su factura."""
    facturados = {f.pago_id for f in facturas_de_cuenta(db, cuenta.id) if f.pago_id is not None}
    return [p for p in cuenta.pagos
            if p.estado == EstadoPago.PAGADO and p.tipo != TipoPago.REEMBOLSO and p.id not in facturados]


def datos_pago(pago: Pago) -> dict:
    """Un pago tal como se copia en la factura (sin datos de tarjeta)."""
    return {"tipo": pago.tipo.value, "metodo": pago.metodo.value, "monto": pago.monto,
            "referencia": pago.referencia_externa,
            "fecha": (pago.confirmado_en or pago.creado_en).isoformat()}


def datos_factura_anticipo(pago: Pago, parqueadero: Parqueadero, numero: int, ahora: datetime) -> dict:
    """
    Copia de la factura de un pago anticipado. Se emite ANTES de la salida,
    así que muestra el horario reservado (o la entrada, si ya entró).
    """
    cuenta = pago.cuenta
    reserva = cuenta.reserva
    estancia = cuenta.estancia
    cliente = reserva.usuario if reserva else None
    if estancia is not None:
        vehiculo = {"placa": estancia.placa, "tipo": estancia.tipo_vehiculo.value}
        plaza = estancia.espacio.numero
    else:
        vehiculo = {"placa": reserva.vehiculo.placa, "tipo": reserva.vehiculo.tipo.value}
        plaza = reserva.espacio.numero
    return {
        "numero": numero,
        "concepto": "anticipado",
        "emitido_en": ahora.isoformat(),
        "parqueadero": {"id": parqueadero.id, "nombre": parqueadero.nombre, "direccion": parqueadero.direccion},
        "cliente": {
            "nombre": cliente.nombre if cliente else (estancia.nombre_conductor if estancia else None),
            "telefono": cliente.telefono if cliente else (estancia.telefono_conductor if estancia else None),
        },
        "vehiculo": vehiculo,
        "plaza": plaza,
        "con_reserva": reserva is not None,
        "reserva": {
            "inicio": reserva.inicio.isoformat(), "fin": reserva.fin.isoformat(),
            "inicio_texto": hora_texto(reserva.inicio), "fin_texto": hora_texto(reserva.fin),
        } if reserva else None,
        "tarifa_hora": cuenta.tarifa_hora,
        "total": pago.monto,
        "valor_factura": pago.monto,
        "pagos": [datos_pago(pago)],
        "metodo_pago": pago.metodo.value,
    }


def datos_comprobante(
    cuenta: Cuenta, parqueadero: Parqueadero, numero: int, ahora: datetime,
    cobros: Optional[list] = None, previas: Optional[list] = None,
) -> dict:
    """
    Copia completa de la operación para la factura de CIERRE: no cambia
    nunca más.

    cobros: los pagos que factura este documento (los que no tenían
    factura). previas: números de las facturas de anticipo ya emitidas,
    para que se vea que esos valores no se cobran dos veces.
    """
    estancia = cuenta.estancia
    reserva = cuenta.reserva
    cliente = reserva.usuario if reserva else None
    pagos = [p for p in cuenta.pagos if p.estado == EstadoPago.PAGADO]
    if cobros is None:
        cobros = [p for p in pagos if p.tipo != TipoPago.REEMBOLSO]
    return {
        "numero": numero,
        "concepto": "cierre",
        "emitido_en": ahora.isoformat(),
        "parqueadero": {"id": parqueadero.id, "nombre": parqueadero.nombre, "direccion": parqueadero.direccion},
        "cliente": {
            "nombre": cliente.nombre if cliente else estancia.nombre_conductor,
            "telefono": cliente.telefono if cliente else estancia.telefono_conductor,
        },
        "vehiculo": {"placa": estancia.placa, "tipo": estancia.tipo_vehiculo.value},
        "plaza": estancia.espacio.numero,
        "entrada": estancia.entrada_en.isoformat(),
        "salida": estancia.salida_en.isoformat(),
        "entrada_texto": hora_texto(estancia.entrada_en),
        "salida_texto": hora_texto(estancia.salida_en),
        "con_reserva": reserva is not None,
        "tarifa_hora": cuenta.tarifa_hora,
        "tiempo_base": {"minutos": cuenta.minutos_base, "valor": cuenta.valor_base},
        "tiempo_extra": {"minutos": cuenta.minutos_extra, "valor": cuenta.valor_extra},
        "penalizacion": cuenta.penalizacion,
        "subtotal": cuenta.subtotal,
        "impuesto_pct": cuenta.impuesto_pct,
        "impuesto": cuenta.impuesto,
        "total": cuenta.total,
        "pagos": [datos_pago(p) for p in pagos],
        "valor_factura": float(sum((dinero(p.monto) for p in cobros), Decimal(0))),
        "facturas_previas": list(previas or []),
        "metodo_pago": cobros[-1].metodo.value if cobros else None,
    }


def emitir_factura_pago(db: Session, pago: Pago, ahora: datetime) -> Comprobante:
    """
    Emite la factura de un pago anticipado (una por pago). Si ya existe,
    la devuelve. No hace commit.
    """
    existente = db.query(Comprobante).filter(Comprobante.pago_id == pago.id).first()
    if existente is not None:
        return existente
    parqueadero = db.get(Parqueadero, pago.cuenta.parqueadero_id)
    numero = siguiente_numero(db, parqueadero.id)
    factura = Comprobante(
        parqueadero_id=parqueadero.id,
        cuenta_id=pago.cuenta_id,
        pago_id=pago.id,
        numero=numero,
        datos=datos_factura_anticipo(pago, parqueadero, numero, ahora),
        emitido_en=ahora,
    )
    db.add(factura)
    db.flush()
    return factura


def factura_del_pago(db: Session, pago: Pago, ahora: datetime, confirmar: bool) -> Optional[Comprobante]:
    """
    La factura que corresponde a un pago confirmado:
    - anticipado → su propia factura, enseguida;
    - cobro de la salida → la factura de cierre, si el saldo ya es 0.
    """
    if pago.estado != EstadoPago.PAGADO or pago.tipo == TipoPago.REEMBOLSO:
        return None
    if pago.tipo == TipoPago.ANTICIPADO:
        factura = emitir_factura_pago(db, pago, ahora)
        if confirmar:
            db.commit()
        return factura
    return emitir_comprobante(db, pago.cuenta, ahora, confirmar=confirmar)


def emitir_comprobante(db: Session, cuenta: Cuenta, ahora: datetime, confirmar: bool = True) -> Optional[Comprobante]:
    """
    Emite la factura de CIERRE de la cuenta si corresponde y no existe.

    Corresponde cuando la salida está registrada (liquidada_en) y el saldo
    es 0. Factura solo los cobros que no tenían factura: si el anticipo ya
    cubría todo, no se emite (devuelve None). Si ya existe, la devuelve.
    """
    existente = db.query(Comprobante).filter(
        Comprobante.cuenta_id == cuenta.id, Comprobante.pago_id.is_(None)
    ).first()
    if existente is not None:
        return existente
    if cuenta.liquidada_en is None or cuenta.estancia_id is None or saldo(cuenta) > 0:
        return None
    previas = [f.numero for f in facturas_de_cuenta(db, cuenta.id)]
    cobros = cobros_sin_factura(db, cuenta)
    if previas and not cobros:
        return None   # todo quedó facturado con el anticipo

    parqueadero = db.get(Parqueadero, cuenta.parqueadero_id)
    numero = siguiente_numero(db, parqueadero.id)
    comprobante = Comprobante(
        parqueadero_id=parqueadero.id,
        cuenta_id=cuenta.id,
        numero=numero,
        datos=datos_comprobante(cuenta, parqueadero, numero, ahora, cobros, previas),
        emitido_en=ahora,
    )
    db.add(comprobante)
    db.flush()
    if confirmar:
        db.commit()
    return comprobante
