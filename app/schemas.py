"""
Esquemas Pydantic para ParkSmart.

Los esquemas definen la estructura de los datos que recibimos
y validan que cumplan con los requisitos antes de procesarlos.
"""

import re
from datetime import datetime
from typing import Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ============================================
# CONSTANTES DE VALIDACIÓN
# ============================================

# Hora en formato 24h "HH:MM" (00:00 a 23:59), igual que el CHECK del SQL
PATRON_HORA = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"

# Margen operativo entre reservas (minutos). Debe coincidir con el CHECK
# ck_parqueaderos_margen del SQL.
MARGENES_PERMITIDOS = (10, 15)
MARGEN_PREDETERMINADO = 15

# Rol del registro público: solo clientes.
# - Las cuentas de propietario las crea el admin_global desde su panel.
# - admin_global NUNCA se elige desde un formulario: se asigna con un UPDATE en Supabase.
RolRegistro = Literal["cliente"]

# Principales ciudades de Colombia donde se puede registrar un parqueadero.
# Se valida aquí y no con un CHECK en la base para poder ampliar la lista
# sin migraciones.
CiudadTexto = Literal[
    "Armenia", "Barranquilla", "Bogotá", "Bucaramanga", "Cali", "Cartagena",
    "Cúcuta", "Ibagué", "Manizales", "Medellín", "Montería", "Neiva",
    "Pasto", "Pereira", "Popayán", "Santa Marta", "Sincelejo", "Tunja",
    "Valledupar", "Villavicencio",
]
# La misma lista, para llenar el <select> de los formularios
CIUDADES_COLOMBIA = list(get_args(CiudadTexto))

# Tipos de vehículo y nombre legible para los mensajes
NOMBRES_TIPO_VEHICULO = {"carro": "carros", "moto": "motos", "bus": "buses"}

# Todos los estados posibles de un parqueadero
EstadoParqueaderoTexto = Literal["pendiente", "activo", "inactivo", "rechazado"]


# ============================================
# ESQUEMAS DE USUARIO
# ============================================

class UsuarioBase(BaseModel):
    """Datos base de un usuario."""
    nombre: str = Field(..., min_length=2, max_length=100)
    correo: EmailStr
    telefono: Optional[str] = Field(None, max_length=20)


class CuentaNueva(UsuarioBase):
    """
    Datos comunes para crear cualquier cuenta: contraseña con confirmación
    y teléfono obligatorio. La usan el registro público (UsuarioCrear) y
    el formulario del admin para propietarios (PropietarioCrear).
    """
    password: str = Field(..., min_length=6, max_length=100)
    confirmar_password: str = Field(..., min_length=6, max_length=100)

    @field_validator("telefono", mode="before")
    @classmethod
    def validar_telefono(cls, valor):
        """
        El teléfono es obligatorio en el registro.

        Solo admite números, espacios, guiones y un '+' inicial,
        con una longitud de 7 a 20 caracteres.
        """
        valor = "" if valor is None else str(valor).strip()
        if not valor:
            raise ValueError("El teléfono es obligatorio.")
        if not re.fullmatch(r"\+?[0-9 \-]{7,20}", valor) or len(valor) > 20:
            raise ValueError("El teléfono solo puede tener números, espacios, guiones y un + inicial (7 a 20 caracteres).")
        return valor

    @model_validator(mode="after")
    def validar_passwords(self):
        """Verifica que la contraseña y su confirmación coincidan."""
        if self.password != self.confirmar_password:
            raise ValueError("Las contraseñas no coinciden.")
        return self


class UsuarioCrear(CuentaNueva):
    """
    Datos para el registro público: siempre crea un cliente.

    Un formulario manipulado que envíe rol 'propietario' o 'admin_global'
    se rechaza (no se ignora en silencio), para que el error sea visible.
    """
    rol: RolRegistro = "cliente"


# Número de cédula: solo dígitos, de 4 a 15 (igual que ck_usuarios_cedula del SQL)
PATRON_CEDULA = r"[0-9]{4,15}"
MENSAJE_CEDULA = "El número de cédula es obligatorio y debe tener solo dígitos (entre 4 y 15)."


def normalizar_cedula(valor) -> str:
    """
    Quita espacios y valida la cédula. Se guarda como TEXTO: una cédula no
    es una cantidad y así no se pierden ceros a la izquierda.
    """
    cedula = re.sub(r"\s", "", str(valor or ""))
    if not re.fullmatch(PATRON_CEDULA, cedula):
        raise ValueError(MENSAJE_CEDULA)
    return cedula


class PropietarioCrear(CuentaNueva):
    """
    Datos con los que el admin_global crea la cuenta de un propietario.

    No tiene campo rol: el rol lo fija el backend (siempre 'propietario'),
    así que enviar rol en el formulario no cambia nada.
    La cédula es obligatoria para las altas nuevas.
    """
    cedula: str

    @field_validator("cedula", mode="before")
    @classmethod
    def validar_cedula(cls, valor):
        return normalizar_cedula(valor)


class ContrasenaAsignada(BaseModel):
    """
    Contraseña que el admin_global asigna a un usuario desde Gestión de usuarios.

    Mismas reglas que al crear una cuenta (CuentaNueva): 6 a 100
    caracteres y confirmación igual.
    """
    password: str = Field(..., min_length=6, max_length=100)
    confirmar_password: str

    @model_validator(mode="after")
    def validar_confirmacion(self):
        """La contraseña y su confirmación deben coincidir."""
        if self.password != self.confirmar_password:
            raise ValueError("Las contraseñas no coinciden.")
        return self


class UsuarioActualizar(BaseModel):
    """Datos que el propio usuario puede actualizar (todos opcionales)."""
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    telefono: Optional[str] = Field(None, max_length=20)


class UsuarioLogin(BaseModel):
    """Datos para iniciar sesión."""
    correo: EmailStr
    password: str


class UsuarioRespuesta(UsuarioBase):
    """Datos del usuario que se muestran (sin contraseña)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    rol: str
    activo: bool
    fecha_registro: datetime


# ============================================
# ESQUEMAS DE PARQUEADERO
# ============================================

class ParqueaderoBase(BaseModel):
    """Datos base de un parqueadero."""
    nombre: str = Field(..., min_length=2, max_length=100)
    direccion: str = Field(..., min_length=5, max_length=200)
    barrio: Optional[str] = Field(None, max_length=100)
    ciudad: CiudadTexto = "Santa Marta"
    latitud: Optional[float] = Field(None, ge=-90, le=90)
    longitud: Optional[float] = Field(None, ge=-180, le=180)
    descripcion: Optional[str] = None
    capacidad: int = Field(..., gt=0)
    acepta_carros: bool = True
    acepta_motos: bool = True
    acepta_buses: bool = False
    tarifa_hora_carro: Optional[float] = Field(None, ge=0)
    tarifa_hora_moto: Optional[float] = Field(None, ge=0)
    tarifa_hora_bus: Optional[float] = Field(None, ge=0)
    horario_apertura: str = Field(..., pattern=PATRON_HORA)
    horario_cierre: str = Field(..., pattern=PATRON_HORA)
    # Tiempo libre obligatorio entre dos reservas de la misma plaza
    margen_minutos: int = MARGEN_PREDETERMINADO

    @field_validator("margen_minutos")
    @classmethod
    def validar_margen(cls, valor):
        """Igual que el CHECK del SQL: solo 10 o 15 minutos."""
        if valor not in MARGENES_PERMITIDOS:
            raise ValueError("El margen entre reservas debe ser de 10 o 15 minutos.")
        return valor

    @model_validator(mode="after")
    def validar_tipos_y_tarifas(self):
        """
        Aplica las mismas reglas que los CHECK de la base de datos:
        - debe aceptar al menos un tipo de vehículo;
        - cada tipo aceptado necesita su tarifa;
        - la tarifa de un tipo NO aceptado se descarta (queda en None).
        """
        if not (self.acepta_carros or self.acepta_motos or self.acepta_buses):
            raise ValueError("Elige al menos un tipo de vehículo que acepte el parqueadero.")
        for tipo, acepta in (
            ("carro", self.acepta_carros),
            ("moto", self.acepta_motos),
            ("bus", self.acepta_buses),
        ):
            campo = f"tarifa_hora_{tipo}"
            if not acepta:
                setattr(self, campo, None)
            elif getattr(self, campo) is None:
                raise ValueError(f"Indica la tarifa por hora para {NOMBRES_TIPO_VEHICULO[tipo]}.")
        return self


MAX_ESPACIOS_POR_TIPO = 500


class EspaciosPorTipo(BaseModel):
    """
    Cuántos espacios tiene un parqueadero nuevo para cada tipo de vehículo.

    - Cada cantidad es un entero entre 0 y MAX_ESPACIOS_POR_TIPO.
    - Una categoría puede tener cero espacios (por ejemplo, sin buses).
    - El total debe ser al menos 1: la capacidad del parqueadero es la suma
      y el SQL exige capacidad > 0.
    - strict=True: "2.5" o "dos" se rechazan; la ruta convierte el texto
      del formulario a entero antes de validar.
    """
    model_config = ConfigDict(strict=True)

    motos: int = Field(0, ge=0, le=MAX_ESPACIOS_POR_TIPO)
    carros: int = Field(0, ge=0, le=MAX_ESPACIOS_POR_TIPO)
    buses: int = Field(0, ge=0, le=MAX_ESPACIOS_POR_TIPO)

    @property
    def total(self) -> int:
        """Capacidad total: suma de los espacios de todos los tipos."""
        return self.motos + self.carros + self.buses

    @model_validator(mode="after")
    def validar_total(self):
        """Un parqueadero necesita al menos un espacio."""
        if self.total == 0:
            raise ValueError("Agrega al menos un espacio para algún tipo de vehículo.")
        return self


class ParqueaderoCrear(ParqueaderoBase):
    """
    Datos para crear un parqueadero desde el formulario.

    NO incluye propietario_id ni estado: el backend asigna el usuario
    en sesión como propietario y el estado inicial 'pendiente'.
    extra="forbid" rechaza esos campos si alguien intenta enviarlos.
    """
    model_config = ConfigDict(extra="forbid")


class ParqueaderoActualizar(BaseModel):
    """
    Datos que el propietario puede actualizar (todos opcionales).

    El estado NO se cambia aquí: tiene su propio schema
    (ParqueaderoCambiarEstado) porque depende del rol de quien lo cambia.
    """
    model_config = ConfigDict(extra="forbid")

    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    direccion: Optional[str] = Field(None, min_length=5, max_length=200)
    barrio: Optional[str] = Field(None, max_length=100)
    ciudad: Optional[CiudadTexto] = None
    latitud: Optional[float] = Field(None, ge=-90, le=90)
    longitud: Optional[float] = Field(None, ge=-180, le=180)
    descripcion: Optional[str] = None
    capacidad: Optional[int] = Field(None, gt=0)
    acepta_carros: Optional[bool] = None
    acepta_motos: Optional[bool] = None
    acepta_buses: Optional[bool] = None
    tarifa_hora_carro: Optional[float] = Field(None, ge=0)
    tarifa_hora_moto: Optional[float] = Field(None, ge=0)
    tarifa_hora_bus: Optional[float] = Field(None, ge=0)
    horario_apertura: Optional[str] = Field(None, pattern=PATRON_HORA)
    horario_cierre: Optional[str] = Field(None, pattern=PATRON_HORA)


class ParqueaderoCambiarEstado(BaseModel):
    """Cambio de estado de un parqueadero (aprobación, rechazo, suspensión)."""
    estado: EstadoParqueaderoTexto


class ParqueaderoRespuesta(ParqueaderoBase):
    """Datos del parqueadero que se muestran."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    propietario_id: int
    propietario_nombre: Optional[str] = None
    estado: str
    fecha_creacion: datetime
    espacios_disponibles: int = 0
    espacios_ocupados: int = 0


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
    model_config = ConfigDict(from_attributes=True)

    id: int
    parqueadero_id: int
    estado: str


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
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int


# ============================================
# ESQUEMAS DE RESERVA
# ============================================

class ReservaBase(BaseModel):
    """Datos base de una reserva."""
    vehiculo_id: int
    espacio_id: int
    fecha: datetime
    hora_entrada: str = Field(..., pattern=PATRON_HORA)
    hora_salida: str = Field(..., pattern=PATRON_HORA)


class ReservaCrear(ReservaBase):
    """Datos para crear una reserva."""
    pass


class ReservaActualizar(BaseModel):
    """Datos para actualizar una reserva."""
    estado: Optional[str] = Field(None, pattern=r"^(pendiente|confirmada|completada|cancelada)$")


class ReservaRespuesta(ReservaBase):
    """Datos de la reserva que se muestran."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int
    estado: str
    total: float
    fecha_reserva: datetime


# ============================================
# ESQUEMAS DE OPERACIÓN (entradas y salidas)
# ============================================

class IngresoDirecto(BaseModel):
    """
    Datos de un vehículo que llega sin reserva (walk-in).

    El conductor no necesita cuenta en ParkSmart: solo placa y tipo son
    obligatorios; nombre y teléfono son opcionales.
    """
    placa: str
    tipo: Literal["carro", "moto", "bus"]
    nombre: Optional[str] = Field(None, max_length=100)
    telefono: Optional[str] = None

    @field_validator("placa", mode="before")
    @classmethod
    def normalizar_placa(cls, valor):
        """Mayúsculas y sin espacios ni guiones: 'abc-123' → 'ABC123'."""
        placa = re.sub(r"[\s-]", "", str(valor or "")).upper()
        if not re.fullmatch(r"[A-Z0-9]{4,10}", placa):
            raise ValueError("La placa debe tener entre 4 y 10 letras o números.")
        return placa

    @field_validator("nombre", "telefono", mode="before")
    @classmethod
    def vacio_es_none(cls, valor):
        """Un campo opcional vacío se guarda como NULL."""
        valor = "" if valor is None else str(valor).strip()
        return valor or None

    @field_validator("telefono")
    @classmethod
    def validar_telefono(cls, valor):
        """Mismo formato que el registro: números, espacios, guiones y un + inicial."""
        if valor is not None and not re.fullmatch(r"\+?[0-9 \-]{7,20}", valor):
            raise ValueError("El teléfono solo puede tener números, espacios, guiones y un + inicial (7 a 20 caracteres).")
        return valor
