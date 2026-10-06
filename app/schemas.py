"""
Esquemas Pydantic para ParkSmart.

Los esquemas definen la estructura de los datos que recibimos
y validan que cumplan con los requisitos antes de procesarlos.
"""

from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional


# ============================================
# ESQUEMAS DE USUARIO
# ============================================

class UsuarioBase(BaseModel):
    """Datos base de un usuario."""
    nombre: str = Field(..., min_length=2, max_length=100)
    correo: EmailStr


class UsuarioCrear(UsuarioBase):
    """Datos para crear un usuario (incluye contraseña)."""
    password: str = Field(..., min_length=6, max_length=100)
    confirmar_password: str = Field(..., min_length=6, max_length=100)


class UsuarioLogin(BaseModel):
    """Datos para iniciar sesión."""
    correo: EmailStr
    password: str


class UsuarioRespuesta(UsuarioBase):
    """Datos del usuario que se muestran (sin contraseña)."""
    id: int
    rol: str
    fecha_registro: datetime

    class Config:
        from_attributes = True


# ============================================
# ESQUEMAS DE PARQUEADERO
# ============================================

class ParqueaderoBase(BaseModel):
    """Datos base de un parqueadero."""
    nombre: str = Field(..., min_length=2, max_length=100)
    direccion: str = Field(..., min_length=5, max_length=200)
    descripcion: Optional[str] = None
    capacidad: int = Field(..., gt=0)
    tarifa_hora_carro: float = Field(..., ge=0)
    tarifa_hora_moto: float = Field(..., ge=0)
    tarifa_hora_bus: float = Field(..., ge=0)
    horario_apertura: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    horario_cierre: str = Field(..., pattern=r"^\d{2}:\d{2}$")


class ParqueaderoCrear(ParqueaderoBase):
    """Datos para crear un parqueadero."""
    pass


class ParqueaderoActualizar(BaseModel):
    """Datos para actualizar un parqueadero (todos opcionales)."""
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    direccion: Optional[str] = Field(None, min_length=5, max_length=200)
    descripcion: Optional[str] = None
    capacidad: Optional[int] = Field(None, gt=0)
    tarifa_hora_carro: Optional[float] = Field(None, ge=0)
    tarifa_hora_moto: Optional[float] = Field(None, ge=0)
    tarifa_hora_bus: Optional[float] = Field(None, ge=0)
    horario_apertura: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    horario_cierre: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    estado: Optional[str] = None


class ParqueaderoRespuesta(ParqueaderoBase):
    """Datos del parqueadero que se muestran."""
    id: int
    estado: str
    espacios_disponibles: int = 0
    espacios_ocupados: int = 0

    class Config:
        from_attributes = True


# ============================================
# ESQUEMAS DE ESPACIO
# ============================================

class EspacioBase(BaseModel):
    """Datos base de un espacio."""
    numero: str = Field(..., min_length=1, max_length=10)
    tipo: str = Field(..., pattern=r"^(carro|moto)$")


class EspacioCrear(EspacioBase):
    """Datos para crear un espacio."""
    pass


class EspacioActualizar(BaseModel):
    """Datos para actualizar un espacio."""
    numero: Optional[str] = Field(None, min_length=1, max_length=10)
    tipo: Optional[str] = Field(None, pattern=r"^(carro|moto)$")
    estado: Optional[str] = Field(None, pattern=r"^(disponible|ocupado|mantenimiento)$")


class EspacioRespuesta(EspacioBase):
    """Datos del espacio que se muestran."""
    id: int
    parqueadero_id: int
    estado: str

    class Config:
        from_attributes = True


# ============================================
# ESQUEMAS DE VEHICULO
# ============================================

class VehiculoBase(BaseModel):
    """Datos base de un vehículo."""
    placa: str = Field(..., min_length=5, max_length=10)
    tipo: str = Field(..., pattern=r"^(carro|moto)$")
    marca: Optional[str] = Field(None, max_length=50)


class VehiculoCrear(VehiculoBase):
    """Datos para crear un vehículo."""
    pass


class VehiculoActualizar(BaseModel):
    """Datos para actualizar un vehículo."""
    placa: Optional[str] = Field(None, min_length=5, max_length=10)
    tipo: Optional[str] = Field(None, pattern=r"^(carro|moto)$")
    marca: Optional[str] = Field(None, max_length=50)


class VehiculoRespuesta(VehiculoBase):
    """Datos del vehículo que se muestran."""
    id: int
    usuario_id: int

    class Config:
        from_attributes = True


# ============================================
# ESQUEMAS DE RESERVA
# ============================================

class ReservaBase(BaseModel):
    """Datos base de una reserva."""
    vehiculo_id: int
    espacio_id: int
    fecha: datetime
    hora_entrada: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    hora_salida: str = Field(..., pattern=r"^\d{2}:\d{2}$")


class ReservaCrear(ReservaBase):
    """Datos para crear una reserva."""
    pass


class ReservaActualizar(BaseModel):
    """Datos para actualizar una reserva."""
    estado: Optional[str] = Field(None, pattern=r"^(pendiente|confirmada|completada|cancelada)$")


class ReservaRespuesta(ReservaBase):
    """Datos de la reserva que se muestran."""
    id: int
    usuario_id: int
    estado: str
    total: float
    fecha_reserva: datetime

    class Config:
        from_attributes = True
