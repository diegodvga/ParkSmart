"""
Modelos SQLAlchemy para ParkSmart.

Este módulo define las clases que representan las tablas de la base de datos.
Cada clase hereda de Base (definida en database.py) y define las columnas
de la tabla correspondiente.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Enum, Text
from sqlalchemy.orm import relationship
from app.database import Base
import enum


# ============================================
# ENUMS (Valores permitidos para columnas)
# ============================================

class RolUsuario(str, enum.Enum):
    """Roles posibles para un usuario."""
    USUARIO = "usuario"
    ADMINISTRADOR = "administrador"


class TipoEspacio(str, enum.Enum):
    """Tipos de espacio en un parqueadero."""
    CARRO = "carro"
    MOTO = "moto"


class EstadoEspacio(str, enum.Enum):
    """Estados posibles de un espacio."""
    DISPONIBLE = "disponible"
    OCUPADO = "ocupado"
    MANTENIMIENTO = "mantenimiento"


class TipoVehiculo(str, enum.Enum):
    """Tipos de vehículo."""
    CARRO = "carro"
    MOTO = "moto"


class EstadoReserva(str, enum.Enum):
    """Estados posibles de una reserva."""
    PENDIENTE = "pendiente"
    CONFIRMADA = "confirmada"
    COMPLETADA = "completada"
    CANCELADA = "cancelada"


class EstadoParqueadero(str, enum.Enum):
    """Estados posibles de un parqueadero."""
    ACTIVO = "activo"
    INACTIVO = "inactivo"


# ============================================
# MODELO: USUARIO
# ============================================

class Usuario(Base):
    """
    Tabla: usuarios

    Representa a los usuarios del sistema (clientes y administradores).
    """
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    correo = Column(String(150), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)  # Hash de la contraseña
    rol = Column(Enum(RolUsuario), default=RolUsuario.USUARIO, nullable=False)
    fecha_registro = Column(DateTime, nullable=False)

    # Relaciones
    vehiculos = relationship("Vehiculo", back_populates="usuario", cascade="all, delete-orphan")
    reservas = relationship("Reserva", back_populates="usuario", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Usuario(id={self.id}, nombre={self.nombre}, correo={self.correo})>"


# ============================================
# MODELO: PARQUEADERO
# ============================================

class Parqueadero(Base):
    """
    Tabla: parqueaderos

    Representa los parqueaderos registrados en el sistema.
    """
    __tablename__ = "parqueaderos"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    direccion = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=True)
    capacidad = Column(Integer, nullable=False)
    tarifa_hora_carro = Column(Float, nullable=False)
    tarifa_hora_moto = Column(Float, nullable=False)
    horario_apertura = Column(String(5), nullable=False)  # Formato: "08:00"
    horario_cierre = Column(String(5), nullable=False)   # Formato: "20:00"
    estado = Column(Enum(EstadoParqueadero), default=EstadoParqueadero.ACTIVO, nullable=False)

    # Relaciones
    espacios = relationship("Espacio", back_populates="parqueadero", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Parqueadero(id={self.id}, nombre={self.nombre})>"


# ============================================
# MODELO: ESPACIO
# ============================================

class Espacio(Base):
    """
    Tabla: espacios

    Representa los espacios individuales dentro de un parqueadero.
    """
    __tablename__ = "espacios"

    id = Column(Integer, primary_key=True, index=True)
    parqueadero_id = Column(Integer, ForeignKey("parqueaderos.id"), nullable=False)
    numero = Column(String(10), nullable=False)
    tipo = Column(Enum(TipoEspacio), nullable=False)
    estado = Column(Enum(EstadoEspacio), default=EstadoEspacio.DISPONIBLE, nullable=False)

    # Relaciones
    parqueadero = relationship("Parqueadero", back_populates="espacios")
    reservas = relationship("Reserva", back_populates="espacio")

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

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    placa = Column(String(10), unique=True, nullable=False, index=True)
    tipo = Column(Enum(TipoVehiculo), nullable=False)
    marca = Column(String(50), nullable=True)

    # Relaciones
    usuario = relationship("Usuario", back_populates="vehiculos")
    reservas = relationship("Reserva", back_populates="vehiculo")

    def __repr__(self):
        return f"<Vehiculo(id={self.id}, placa={self.placa}, tipo={self.tipo})>"


# ============================================
# MODELO: RESERVA
# ============================================

class Reserva(Base):
    """
    Tabla: reservas

    Representa las reservas realizadas por los usuarios.
    """
    __tablename__ = "reservas"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    vehiculo_id = Column(Integer, ForeignKey("vehiculos.id"), nullable=False)
    espacio_id = Column(Integer, ForeignKey("espacios.id"), nullable=False)
    fecha = Column(DateTime, nullable=False)
    hora_entrada = Column(String(5), nullable=False)   # Formato: "08:00"
    hora_salida = Column(String(5), nullable=False)    # Formato: "10:00"
    estado = Column(Enum(EstadoReserva), default=EstadoReserva.PENDIENTE, nullable=False)
    total = Column(Float, nullable=False)
    fecha_reserva = Column(DateTime, nullable=False)

    # Relaciones
    usuario = relationship("Usuario", back_populates="reservas")
    vehiculo = relationship("Vehiculo", back_populates="reservas")
    espacio = relationship("Espacio", back_populates="reservas")

    def __repr__(self):
        return f"<Reserva(id={self.id}, usuario_id={self.usuario_id}, estado={self.estado})>"
