"""
Modelos SQLAlchemy para ParkSmart.

Este módulo define las clases que representan las tablas de la base de datos.
Cada clase hereda de Base (definida en database.py) y define las columnas
de la tabla correspondiente.

IMPORTANTE: la fuente de verdad del esquema es docs/parksmart_schema.sql.
Las tablas se crean con ese script en Supabase, NO con create_all().
Si cambias algo aquí, debe coincidir exactamente con ese SQL.
"""

from datetime import datetime, timezone
import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE
from sqlalchemy.orm import relationship

from app.database import Base


# ============================================
# ENUMS (Valores permitidos para columnas)
# ============================================

class RolUsuario(str, enum.Enum):
    """Roles posibles para un usuario."""
    CLIENTE = "cliente"
    PROPIETARIO = "propietario"
    OPERARIO = "operario"          # Registra entradas y salidas (migración 003)
    ADMIN_GLOBAL = "admin_global"


class TipoEspacio(str, enum.Enum):
    """Tipos de espacio en un parqueadero."""
    CARRO = "carro"
    MOTO = "moto"
    BUS = "bus"


class EstadoEspacio(str, enum.Enum):
    """Estados posibles de un espacio."""
    DISPONIBLE = "disponible"
    OCUPADO = "ocupado"
    MANTENIMIENTO = "mantenimiento"


class TipoVehiculo(str, enum.Enum):
    """Tipos de vehículo."""
    CARRO = "carro"
    MOTO = "moto"
    BUS = "bus"


class EstadoReserva(str, enum.Enum):
    """
    Estados posibles de una reserva (agenda).

    - PENDIENTE: espera el pago anticipado; aparta el horario hasta
      pendiente_expira_en.
    - CONFIRMADA: horario asegurado.
    - EN_CURSO: el vehículo ya entró.
    - EXCEDIDA: pasó la hora de fin y el vehículo sigue dentro.
    - COMPLETADA / CANCELADA: ya no ocupan la agenda.
    """
    PENDIENTE = "pendiente"
    CONFIRMADA = "confirmada"
    EN_CURSO = "en_curso"
    COMPLETADA = "completada"
    EXCEDIDA = "excedida"
    CANCELADA = "cancelada"


# Estados que ocupan la agenda de una plaza (los mismos del EXCLUDE en el SQL)
ESTADOS_RESERVA_ACTIVOS = (
    EstadoReserva.PENDIENTE,
    EstadoReserva.CONFIRMADA,
    EstadoReserva.EN_CURSO,
    EstadoReserva.EXCEDIDA,
)


class MotivoCancelacion(str, enum.Enum):
    """Por qué se canceló una reserva."""
    CLIENTE = "cliente"
    PROPIETARIO = "propietario"
    ADMIN = "admin"
    PENDIENTE_VENCIDA = "pendiente_vencida"
    SIN_ALTERNATIVA = "sin_alternativa"


class EstadoParqueadero(str, enum.Enum):
    """
    Estados posibles de un parqueadero.

    Un parqueadero nuevo nace PENDIENTE hasta que el admin_global
    lo aprueba (ACTIVO) o lo rechaza (RECHAZADO).
    """
    PENDIENTE = "pendiente"
    ACTIVO = "activo"
    INACTIVO = "inactivo"
    RECHAZADO = "rechazado"


# ============================================
# HELPERS
# ============================================

def columna_enum(enum_cls, length=20):
    """
    Crea el tipo Enum de SQLAlchemy que coincide con VARCHAR + CHECK del SQL.

    - native_enum=False: no crea un tipo ENUM nativo de PostgreSQL, usa VARCHAR.
    - values_callable: guarda el valor ('cliente') y no el nombre ('CLIENTE').
    - validate_strings=True: si se asigna un texto que no existe en el enum,
      falla en Python antes de llegar a la base de datos.
    - length: debe coincidir con el VARCHAR(n) de la columna en el SQL.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
        length=length,
        validate_strings=True,
    )


def ahora_utc():
    """
    Fecha y hora actual en UTC, sin zona horaria.

    Equivale a datetime.utcnow() (obsoleto desde Python 3.12). La columna
    es TIMESTAMP sin zona horaria y NOW() en Supabase trabaja en UTC,
    así que ambos defaults (Python y servidor) quedan coherentes.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ============================================
# MODELO: USUARIO
# ============================================

class Usuario(Base):
    """
    Tabla: usuarios

    Representa a los usuarios del sistema: clientes, propietarios
    de parqueaderos y el administrador global.
    """
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), nullable=False)
    correo = Column(String(150), unique=True, nullable=False)
    password = Column(String(255), nullable=False)  # Hash de la contraseña
    telefono = Column(String(20), nullable=True)
    rol = Column(
        columna_enum(RolUsuario),
        nullable=False,
        default=RolUsuario.CLIENTE,
        server_default=RolUsuario.CLIENTE.value,
    )
    activo = Column(Boolean, nullable=False, default=True, server_default="true")
    fecha_registro = Column(
        DateTime, nullable=False, default=ahora_utc, server_default=func.now()
    )
    # Migración 005: cédula como texto de dígitos. Opcional en la base (los
    # propietarios anteriores no la tienen); obligatoria para propietarios
    # nuevos (lo valida PropietarioCrear).
    cedula = Column(String(15), nullable=True)

    # Relaciones
    vehiculos = relationship("Vehiculo", back_populates="usuario", cascade="all, delete-orphan")
    reservas = relationship("Reserva", back_populates="usuario", cascade="all, delete-orphan")
    # ON DELETE RESTRICT: no se borra un usuario que todavía tiene parqueaderos.
    # passive_deletes="all" evita que SQLAlchemy intente poner propietario_id
    # en NULL; deja que PostgreSQL rechace el borrado.
    parqueaderos = relationship("Parqueadero", back_populates="propietario", passive_deletes="all")

    def __repr__(self):
        return f"<Usuario(id={self.id}, nombre={self.nombre}, correo={self.correo}, rol={self.rol})>"


# ============================================
# MODELO: PARQUEADERO
# ============================================

class Parqueadero(Base):
    """
    Tabla: parqueaderos

    Representa los parqueaderos registrados en la plataforma.
    Cada parqueadero pertenece a un propietario (usuario con rol propietario).
    """
    __tablename__ = "parqueaderos"
    __table_args__ = (
        Index("ix_parqueaderos_estado", "estado"),
        Index("ix_parqueaderos_ciudad_barrio", "ciudad", "barrio"),
    )

    id = Column(Integer, primary_key=True)
    propietario_id = Column(
        Integer,
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    nombre = Column(String(100), nullable=False)
    direccion = Column(String(200), nullable=False)
    barrio = Column(String(100), nullable=True)
    ciudad = Column(String(100), nullable=False, default="Santa Marta", server_default="Santa Marta")
    # asdecimal=False: en Python llegan como float, no como Decimal
    latitud = Column(Numeric(9, 6, asdecimal=False), nullable=True)
    longitud = Column(Numeric(9, 6, asdecimal=False), nullable=True)
    descripcion = Column(Text, nullable=True)
    capacidad = Column(Integer, nullable=False)
    # Tipos de vehículo que acepta el parqueadero (al menos uno, por CHECK)
    acepta_carros = Column(Boolean, nullable=False, default=True, server_default=true())
    acepta_motos = Column(Boolean, nullable=False, default=True, server_default=true())
    acepta_buses = Column(Boolean, nullable=False, default=False, server_default=false())
    # Cada tarifa existe solo si su tipo es aceptado; si no, queda en None (NULL)
    tarifa_hora_carro = Column(Numeric(10, 2, asdecimal=False), nullable=True)
    tarifa_hora_moto = Column(Numeric(10, 2, asdecimal=False), nullable=True)
    tarifa_hora_bus = Column(Numeric(10, 2, asdecimal=False), nullable=True)
    horario_apertura = Column(String(5), nullable=False)  # Formato: "08:00"
    horario_cierre = Column(String(5), nullable=False)   # Formato: "20:00"
    estado = Column(
        columna_enum(EstadoParqueadero),
        nullable=False,
        default=EstadoParqueadero.PENDIENTE,
        server_default=EstadoParqueadero.PENDIENTE.value,
    )
    fecha_creacion = Column(
        DateTime, nullable=False, default=ahora_utc, server_default=func.now()
    )
    # Reglas operativas y de cobro (migración 003)
    margen_minutos = Column(SmallInteger, nullable=False, default=15, server_default="15")         # 10 o 15
    gracia_minutos = Column(SmallInteger, nullable=False, default=5, server_default="5")
    umbral_aviso_minutos = Column(SmallInteger, nullable=False, default=60, server_default="60")
    recargo_exceso_pct = Column(Numeric(5, 2, asdecimal=False), nullable=False, default=50, server_default="50")
    impuesto_pct = Column(Numeric(5, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    pago_anticipado = Column(Boolean, nullable=False, default=False, server_default=false())

    # Relaciones
    propietario = relationship("Usuario", back_populates="parqueaderos")
    espacios = relationship("Espacio", back_populates="parqueadero", cascade="all, delete-orphan")

    def tarifa_para(self, tipo):
        """
        Tarifa por hora para un tipo de vehículo ('carro', 'moto' o 'bus').

        Devuelve None si el parqueadero no acepta ese tipo.
        """
        tipo = getattr(tipo, "value", tipo)
        tipo = str(tipo).lower() if tipo else ""
        return {
            "carro": self.tarifa_hora_carro if self.acepta_carros else None,
            "moto": self.tarifa_hora_moto if self.acepta_motos else None,
            "bus": self.tarifa_hora_bus if self.acepta_buses else None,
        }.get(tipo)

    @property
    def propietario_nombre(self):
        """Nombre del propietario, útil para las vistas y los schemas de lectura."""
        return self.propietario.nombre if self.propietario else None

    def __repr__(self):
        return f"<Parqueadero(id={self.id}, nombre={self.nombre}, estado={self.estado})>"


# ============================================
# MODELO: ESPACIO
# ============================================

class Espacio(Base):
    """
    Tabla: espacios

    Representa los espacios individuales dentro de un parqueadero.
    """
    __tablename__ = "espacios"
    __table_args__ = (
        # No puede haber dos espacios "A-01" en el mismo parqueadero
        UniqueConstraint("parqueadero_id", "numero", name="uq_espacios_parqueadero_numero"),
    )

    id = Column(Integer, primary_key=True)
    parqueadero_id = Column(
        Integer,
        ForeignKey("parqueaderos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    numero = Column(String(10), nullable=False)
    tipo = Column(columna_enum(TipoEspacio, length=10), nullable=False)
    estado = Column(
        columna_enum(EstadoEspacio),
        nullable=False,
        default=EstadoEspacio.DISPONIBLE,
        server_default=EstadoEspacio.DISPONIBLE.value,
    )

    # Relaciones
    parqueadero = relationship("Parqueadero", back_populates="espacios")
    # ON DELETE RESTRICT: no se borra un espacio que tiene reservas.
    reservas = relationship("Reserva", back_populates="espacio", passive_deletes="all")

    def __repr__(self):
        return f"<Espacio(id={self.id}, numero={self.numero}, tipo={self.tipo})>"


# ============================================
# MODELO: VEHICULO
# ============================================

class Vehiculo(Base):
    """
    Tabla: vehiculos

    Representa los vehículos registrados por los usuarios.
    """
    __tablename__ = "vehiculos"

    id = Column(Integer, primary_key=True)
    usuario_id = Column(
        Integer,
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    placa = Column(String(10), unique=True, nullable=False)
    tipo = Column(columna_enum(TipoVehiculo, length=10), nullable=False)
    marca = Column(String(50), nullable=True)

    # Relaciones
    usuario = relationship("Usuario", back_populates="vehiculos")
    # ON DELETE RESTRICT: no se borra un vehículo que tiene reservas.
    reservas = relationship("Reserva", back_populates="vehiculo", passive_deletes="all")

    def __repr__(self):
        return f"<Vehiculo(id={self.id}, placa={self.placa}, tipo={self.tipo})>"


# ============================================
# MODELO: RESERVA
# ============================================

class Reserva(Base):
    """
    Tabla: reservas

    Representa las reservas realizadas por los usuarios (la AGENDA).
    La ocupación física real va en otra tabla (estancias).

    inicio/fin son instantes con zona horaria. PostgreSQL impide, con el
    EXCLUDE ex_reservas_espacio_bloqueo, que dos reservas activas de la
    misma plaza se crucen en [inicio, fin + margen_minutos).
    fecha/hora_entrada/hora_salida son las columnas viejas; se siguen
    llenando para las vistas actuales.
    """
    __tablename__ = "reservas"

    id = Column(Integer, primary_key=True)
    usuario_id = Column(
        Integer,
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vehiculo_id = Column(
        Integer,
        ForeignKey("vehiculos.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    espacio_id = Column(
        Integer,
        ForeignKey("espacios.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fecha = Column(DateTime, nullable=True, index=True)      # Viejo
    hora_entrada = Column(String(5), nullable=True)          # Viejo. Formato: "08:00"
    hora_salida = Column(String(5), nullable=True)           # Viejo. Formato: "10:00"
    estado = Column(
        columna_enum(EstadoReserva),
        nullable=False,
        default=EstadoReserva.PENDIENTE,
        server_default=EstadoReserva.PENDIENTE.value,
    )
    total = Column(Numeric(10, 2, asdecimal=False), nullable=False)
    fecha_reserva = Column(
        DateTime, nullable=False, default=ahora_utc, server_default=func.now()
    )
    # Agenda (migración 003)
    inicio = Column(DateTime(timezone=True), nullable=False, index=True)
    fin = Column(DateTime(timezone=True), nullable=False)
    fin_original = Column(DateTime(timezone=True), nullable=True)   # fin antes de la primera extensión
    margen_minutos = Column(SmallInteger, nullable=False)           # copia del margen al confirmar
    tarifa_hora = Column(Numeric(10, 2, asdecimal=False), nullable=False)  # copia de la tarifa
    # Lo calcula el trigger trg_reservas_bloqueo: FetchedValue le dice a
    # SQLAlchemy que no lo envíe y que lo lea de la base después.
    bloqueo = Column(TSTZRANGE, nullable=False, server_default=FetchedValue(), server_onupdate=FetchedValue())
    pendiente_expira_en = Column(DateTime(timezone=True), nullable=True)
    exceso_desde = Column(DateTime(timezone=True), nullable=True)
    cancelada_en = Column(DateTime(timezone=True), nullable=True)
    motivo_cancelacion = Column(columna_enum(MotivoCancelacion, length=30), nullable=True)

    # Relaciones
    usuario = relationship("Usuario", back_populates="reservas")
    vehiculo = relationship("Vehiculo", back_populates="reservas")
    espacio = relationship("Espacio", back_populates="reservas")

    def __repr__(self):
        return f"<Reserva(id={self.id}, usuario_id={self.usuario_id}, estado={self.estado})>"


# ============================================
# MODELO: PARQUEADERO_OPERARIOS (migración 003)
# ============================================

class ParqueaderoOperario(Base):
    """
    Tabla: parqueadero_operarios

    Qué usuario con rol 'operario' trabaja en qué parqueadero. Solo los
    operarios asignados y activos registran entradas y salidas ahí.
    """
    __tablename__ = "parqueadero_operarios"

    parqueadero_id = Column(
        Integer, ForeignKey("parqueaderos.id", ondelete="CASCADE"), primary_key=True
    )
    usuario_id = Column(
        Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    activo = Column(Boolean, nullable=False, default=True, server_default=true())
    fecha_asignacion = Column(
        DateTime, nullable=False, default=ahora_utc, server_default=func.now()
    )


# ============================================
# MODELO: ESTANCIA (migración 003)
# ============================================

class Estancia(Base):
    """
    Tabla: estancias

    La OCUPACIÓN FÍSICA real: un vehículo que entró a una plaza.
    - reserva_id con valor: el cliente llegó con reserva.
    - reserva_id NULL: ingreso directo (walk-in); el conductor no necesita
      cuenta, por eso placa, tipo, nombre y teléfono se guardan aquí.
    - salida_en NULL: el vehículo sigue dentro.

    La base garantiza (índices únicos parciales) que una plaza no tenga dos
    vehículos dentro a la vez y que una reserva no tenga dos entradas.
    """
    __tablename__ = "estancias"
    __table_args__ = (
        # El espacio debe pertenecer al mismo parqueadero de la estancia
        ForeignKeyConstraint(
            ["espacio_id", "parqueadero_id"],
            ["espacios.id", "espacios.parqueadero_id"],
            name="fk_estancias_espacio_parqueadero",
            ondelete="RESTRICT",
        ),
    )

    id = Column(Integer, primary_key=True)
    parqueadero_id = Column(Integer, nullable=False)
    espacio_id = Column(Integer, nullable=False)
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="RESTRICT"), nullable=True)
    placa = Column(String(10), nullable=False)
    tipo_vehiculo = Column(columna_enum(TipoVehiculo, length=10), nullable=False)
    nombre_conductor = Column(String(100), nullable=True)
    telefono_conductor = Column(String(20), nullable=True)
    entrada_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    salida_estimada = Column(DateTime(timezone=True), nullable=True)
    salida_en = Column(DateTime(timezone=True), nullable=True)
    tarifa_hora = Column(Numeric(10, 2, asdecimal=False), nullable=False)   # copia al entrar
    registrado_por = Column(Integer, ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=False)
    cerrado_por = Column(Integer, ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=True)

    # Relaciones
    espacio = relationship("Espacio", foreign_keys=[espacio_id], primaryjoin="Estancia.espacio_id == Espacio.id")
    reserva = relationship("Reserva")

    def __repr__(self):
        return f"<Estancia(id={self.id}, espacio_id={self.espacio_id}, placa={self.placa})>"


# ============================================
# MODELO: CUENTA (migración 003)
# ============================================

class EstadoPagoCuenta(str, enum.Enum):
    """Situación de pago de una cuenta."""
    PENDIENTE = "pendiente"
    PARCIAL = "parcial"
    PAGADO = "pagado"
    REEMBOLSADO = "reembolsado"


class Cuenta(Base):
    """
    Tabla: cuentas

    Una por operación (reserva y/o estancia), con el desglose CONGELADO:
    si el propietario cambia la tarifa después, esta cuenta no cambia.
    liquidada_en con valor = la salida ya se cobró; repetir el check-out
    no recalcula.
    """
    __tablename__ = "cuentas"
    __table_args__ = (
        UniqueConstraint("id", "parqueadero_id", name="uq_cuentas_id_parqueadero"),
    )

    id = Column(Integer, primary_key=True)
    parqueadero_id = Column(
        Integer, ForeignKey("parqueaderos.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="RESTRICT"), unique=True, nullable=True)
    estancia_id = Column(Integer, ForeignKey("estancias.id", ondelete="RESTRICT"), unique=True, nullable=True)
    tarifa_hora = Column(Numeric(10, 2, asdecimal=False), nullable=False)
    minutos_base = Column(Integer, nullable=False, default=0, server_default="0")
    minutos_extra = Column(Integer, nullable=False, default=0, server_default="0")
    valor_base = Column(Numeric(10, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    valor_extra = Column(Numeric(10, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    penalizacion = Column(Numeric(10, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    subtotal = Column(Numeric(10, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    impuesto_pct = Column(Numeric(5, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    impuesto = Column(Numeric(10, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    total = Column(Numeric(10, 2, asdecimal=False), nullable=False, default=0, server_default="0")
    estado_pago = Column(
        columna_enum(EstadoPagoCuenta),
        nullable=False,
        default=EstadoPagoCuenta.PENDIENTE,
        server_default=EstadoPagoCuenta.PENDIENTE.value,
    )
    liquidada_en = Column(DateTime(timezone=True), nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relaciones
    estancia = relationship("Estancia")
    reserva = relationship("Reserva")

    def __repr__(self):
        return f"<Cuenta(id={self.id}, total={self.total}, estado_pago={self.estado_pago})>"


# ============================================
# MODELO: INCIDENCIA (migración 003)
# ============================================

class TipoIncidencia(str, enum.Enum):
    """Qué pasó con una reserva."""
    EXCESO = "exceso"                    # El vehículo sigue dentro después del fin
    RETRASO = "retraso"                  # La plaza de la próxima reserva sigue ocupada
    REASIGNACION = "reasignacion"        # La próxima reserva se movió a otra plaza
    SIN_ALTERNATIVA = "sin_alternativa"  # No hubo otra plaza para la próxima reserva


class EstadoIncidencia(str, enum.Enum):
    """Una incidencia está abierta hasta que se resuelve."""
    ABIERTA = "abierta"
    RESUELTA = "resuelta"


class Incidencia(Base):
    """
    Tabla: incidencias

    Problemas vinculados a la reserva afectada. UNIQUE (reserva_id, tipo):
    si la tarea programada corre dos veces, no se duplica la incidencia.
    """
    __tablename__ = "incidencias"
    __table_args__ = (
        UniqueConstraint("reserva_id", "tipo", name="uq_incidencias_reserva_tipo"),
        Index("ix_incidencias_parqueadero_estado", "parqueadero_id", "estado"),
    )

    id = Column(Integer, primary_key=True)
    parqueadero_id = Column(Integer, ForeignKey("parqueaderos.id", ondelete="CASCADE"), nullable=False)
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="CASCADE"), nullable=True)
    espacio_id = Column(Integer, ForeignKey("espacios.id", ondelete="SET NULL"), nullable=True)
    tipo = Column(columna_enum(TipoIncidencia), nullable=False)
    estado = Column(
        columna_enum(EstadoIncidencia),
        nullable=False,
        default=EstadoIncidencia.ABIERTA,
        server_default=EstadoIncidencia.ABIERTA.value,
    )
    detalle = Column(Text, nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resuelto_en = Column(DateTime(timezone=True), nullable=True)

    reserva = relationship("Reserva")
    espacio = relationship("Espacio")


# ============================================
# MODELO: RESERVA_REASIGNACIONES (migración 003)
# ============================================

class ReservaReasignacion(Base):
    """
    Tabla: reserva_reasignaciones

    Historial de cambios de plaza: conserva la plaza original y la nueva.
    """
    __tablename__ = "reserva_reasignaciones"

    id = Column(Integer, primary_key=True)
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="CASCADE"), nullable=False, index=True)
    espacio_origen_id = Column(Integer, ForeignKey("espacios.id", ondelete="RESTRICT"), nullable=False)
    espacio_destino_id = Column(Integer, ForeignKey("espacios.id", ondelete="RESTRICT"), nullable=False)
    incidencia_id = Column(Integer, ForeignKey("incidencias.id", ondelete="SET NULL"), nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    espacio_origen = relationship("Espacio", foreign_keys=[espacio_origen_id])
    espacio_destino = relationship("Espacio", foreign_keys=[espacio_destino_id])


# ============================================
# MODELO: NOTIFICACION (migración 003)
# ============================================

class TipoNotificacion(str, enum.Enum):
    """Mensajes que la app muestra a clientes y propietarios."""
    RECORDATORIO_FIN = "recordatorio_fin"
    EXCESO = "exceso"
    ALERTA_RETRASO = "alerta_retraso"
    REASIGNACION = "reasignacion"
    SIN_ALTERNATIVA = "sin_alternativa"
    INCIDENCIA_RESUELTA = "incidencia_resuelta"


class Notificacion(Base):
    """
    Tabla: notificaciones (solo dentro de la app)

    "clave" es única (por ejemplo 'recordatorio_fin:42'): un reintento de
    la tarea programada no envía dos veces el mismo mensaje.
    """
    __tablename__ = "notificaciones"
    __table_args__ = (
        Index("ix_notificaciones_usuario_leida", "usuario_id", "leida"),
    )

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    parqueadero_id = Column(Integer, ForeignKey("parqueaderos.id", ondelete="CASCADE"), nullable=True)
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="CASCADE"), nullable=True)
    tipo = Column(columna_enum(TipoNotificacion, length=30), nullable=False)
    prioridad = Column(String(10), nullable=False, default="normal", server_default="normal")
    mensaje = Column(Text, nullable=False)
    clave = Column(String(100), nullable=False, unique=True)
    leida = Column(Boolean, nullable=False, default=False, server_default=false())
    creado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ============================================
# MODELO: PAGO (migración 003)
# ============================================

class TipoPago(str, enum.Enum):
    """Para qué es un movimiento de dinero."""
    ANTICIPADO = "anticipado"   # Antes de usar la plaza (reserva con pago anticipado)
    EN_SITIO = "en_sitio"       # Al salir
    EXTRA = "extra"             # Al salir, por el tiempo excedido de una reserva ya pagada
    REEMBOLSO = "reembolso"     # Devolución al cliente


class MetodoPago(str, enum.Enum):
    """Cómo se pagó."""
    EFECTIVO = "efectivo"
    TARJETA = "tarjeta"
    TRANSFERENCIA = "transferencia"
    PASARELA = "pasarela"


class EstadoPago(str, enum.Enum):
    """Situación de un movimiento de dinero."""
    PENDIENTE = "pendiente"     # Iniciado en la pasarela, sin confirmar
    PAGADO = "pagado"
    FALLIDO = "fallido"


class Pago(Base):
    """
    Tabla: pagos

    Cada movimiento de dinero de una cuenta. clave_idempotencia es única:
    repetir el mismo intento (doble clic, reintento de red) no cobra dos
    veces. referencia_externa (de la pasarela) también es única.
    """
    __tablename__ = "pagos"
    __table_args__ = (
        # Para la FK compuesta de comprobantes (migración 006)
        UniqueConstraint("id", "cuenta_id", name="uq_pagos_id_cuenta"),
    )

    id = Column(Integer, primary_key=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas.id", ondelete="RESTRICT"), nullable=False, index=True)
    tipo = Column(columna_enum(TipoPago), nullable=False)
    metodo = Column(columna_enum(MetodoPago), nullable=False)
    monto = Column(Numeric(10, 2, asdecimal=False), nullable=False)
    estado = Column(
        columna_enum(EstadoPago),
        nullable=False,
        default=EstadoPago.PENDIENTE,
        server_default=EstadoPago.PENDIENTE.value,
    )
    clave_idempotencia = Column(String(64), nullable=False, unique=True)
    referencia_externa = Column(String(100), nullable=True, unique=True)
    registrado_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    confirmado_en = Column(DateTime(timezone=True), nullable=True)

    cuenta = relationship("Cuenta", back_populates="pagos")


Cuenta.pagos = relationship("Pago", back_populates="cuenta", order_by="Pago.id")


# ============================================
# MODELOS: NUMERACIÓN Y COMPROBANTES (migración 003)
# ============================================

class SecuenciaComprobante(Base):
    """
    Tabla: secuencias_comprobante

    Último número de comprobante emitido por parqueadero. Se incrementa con
    INSERT ... ON CONFLICT DO UPDATE ... RETURNING: la fila queda bloqueada
    hasta el commit, así dos operaciones simultáneas no repiten número.
    """
    __tablename__ = "secuencias_comprobante"

    parqueadero_id = Column(
        Integer, ForeignKey("parqueaderos.id", ondelete="CASCADE"), primary_key=True
    )
    ultimo = Column(Integer, nullable=False, default=0, server_default="0")


class Comprobante(Base):
    """
    Tabla: comprobantes

    Factura interna (no es factura electrónica DIAN). "datos" guarda una
    copia completa (cliente, vehículo, desglose, pagos) para que el
    historial no cambie aunque después cambien las tarifas o los datos.

    Una factura por cada cobro (migración 006):
    - pago_id con valor: factura del pago anticipado, emitida al pagar.
    - pago_id NULL: factura de CIERRE, emitida al salir, por los cobros
      que todavía no tenían factura. Una sola por cuenta (índice único
      parcial uq_comprobantes_cierre, definido en el SQL).
    """
    __tablename__ = "comprobantes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cuenta_id", "parqueadero_id"],
            ["cuentas.id", "cuentas.parqueadero_id"],
            name="fk_comprobantes_cuenta_parqueadero",
            ondelete="RESTRICT",
        ),
        # El pago facturado pertenece a la MISMA cuenta
        ForeignKeyConstraint(
            ["pago_id", "cuenta_id"],
            ["pagos.id", "pagos.cuenta_id"],
            name="fk_comprobantes_pago_cuenta",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("parqueadero_id", "numero", name="uq_comprobantes_parqueadero_numero"),
        UniqueConstraint("pago_id", name="uq_comprobantes_pago"),
        Index("ix_comprobantes_cuenta", "cuenta_id"),
    )

    id = Column(Integer, primary_key=True)
    parqueadero_id = Column(Integer, nullable=False)
    cuenta_id = Column(Integer, nullable=False)
    pago_id = Column(Integer, nullable=True)
    numero = Column(Integer, nullable=False)
    datos = Column(JSONB, nullable=False)
    emitido_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cuenta = relationship("Cuenta", foreign_keys=[cuenta_id], primaryjoin="Comprobante.cuenta_id == Cuenta.id")
