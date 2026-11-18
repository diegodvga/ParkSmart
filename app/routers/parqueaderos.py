"""
Router de parqueaderos para ParkSmart.

Rutas públicas:
- GET /parqueaderos              → Lista de parqueaderos
- GET /parqueaderos/{id}         → Detalle de un parqueadero

Rutas de gestión (propietario: solo los suyos; admin_global: todos):
- GET  /admin/parqueaderos               → Gestión de parqueaderos
- GET  /admin/parqueaderos/crear         → Formulario crear (solo propietario)
- POST /admin/parqueaderos/crear         → Procesar creación con sus espacios (solo propietario)
- GET  /admin/parqueaderos/{id}/editar    → Formulario editar
- POST /admin/parqueaderos/{id}/editar    → Procesar edición
- POST /admin/parqueaderos/{id}/eliminar  → Eliminar parqueadero
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from app.database import get_db
from app.dependencies import obtener_parqueadero_autorizado, requiere_rol
from app.models import Parqueadero, Espacio, EstadoParqueadero, RolUsuario, TipoEspacio, Usuario
from app.schemas import (
    CIUDADES_COLOMBIA, MARGEN_PREDETERMINADO, MAX_ESPACIOS_POR_TIPO, EspaciosPorTipo, ParqueaderoBase,
)
from app.services.espacios import construir_espacios

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["parqueaderos"]
)


# ============================================
# DEPENDENCIAS DE ROL
# ============================================

# Gestionar (ver, editar, eliminar): el propietario (de SU parqueadero) o el admin_global
gestor_parqueaderos = requiere_rol(RolUsuario.PROPIETARIO, RolUsuario.ADMIN_GLOBAL)

# Crear: por ahora solo el propietario, porque propietario_id sale de la sesión.
# Cómo crea el admin_global (eligiendo un propietario) está pendiente de decidir.
solo_propietario = requiere_rol(RolUsuario.PROPIETARIO)


# ============================================
# VALIDACIÓN DEL FORMULARIO DE PARQUEADERO
# ============================================

# Campos del formulario que van a la tabla (mismos nombres que las columnas)
CAMPOS_PARQUEADERO = (
    "nombre", "direccion", "descripcion", "capacidad", "ciudad",
    "acepta_carros", "acepta_motos", "acepta_buses",
    "tarifa_hora_carro", "tarifa_hora_moto", "tarifa_hora_bus",
    "horario_apertura", "horario_cierre", "margen_minutos",
)


def validar_formulario_parqueadero(valores: dict) -> dict:
    """
    Valida los datos del formulario con ParqueaderoBase y devuelve
    un diccionario listo para asignar al modelo Parqueadero.

    Las tarifas llegan como texto: si están vacías se convierten en None,
    y el schema decide si eso es válido según los tipos aceptados.
    """
    datos = dict(valores)
    for campo in ("tarifa_hora_carro", "tarifa_hora_moto", "tarifa_hora_bus"):
        if isinstance(datos[campo], str) and not datos[campo].strip():
            datos[campo] = None
    validado = ParqueaderoBase(**datos)
    return {campo: getattr(validado, campo) for campo in CAMPOS_PARQUEADERO}


def mensaje_error_parqueadero(error: ValidationError) -> str:
    """Convierte el primer error de Pydantic en un mensaje legible."""
    primer_error = error.errors()[0]
    campo = primer_error["loc"][0] if primer_error["loc"] else None
    if campo == "ciudad":
        return "Elige una ciudad de la lista."
    if campo and str(campo).startswith("tarifa_hora_"):
        return "Las tarifas deben ser números mayores o iguales a 0."
    if campo == "capacidad":
        return "La capacidad debe ser mayor que 0."
    if campo == "margen_minutos":
        return "El margen entre reservas debe ser de 10 o 15 minutos."
    if campo in ("motos", "carros", "buses"):
        return f"La cantidad de espacios debe ser un número entero entre 0 y {MAX_ESPACIOS_POR_TIPO}."
    if campo in ("horario_apertura", "horario_cierre"):
        return "El horario debe tener el formato HH:MM."
    return primer_error["msg"].removeprefix("Value error, ")


def leer_cantidad(texto: str):
    """
    Convierte la cantidad escrita en el formulario a entero.

    - Vacío cuenta como 0 (esa categoría no tiene espacios).
    - Si no es un entero ("2.5", "dos"), devuelve el texto tal cual para
      que EspaciosPorTipo lo rechace con un mensaje claro.
    """
    texto = (texto or "").strip()
    if not texto:
        return 0
    try:
        return int(texto)
    except ValueError:
        return texto


def mostrar_formulario_con_error(request, parqueadero, valores, error):
    """Vuelve a mostrar el formulario con el error y lo que el usuario escribió."""
    return templates.TemplateResponse(
        "admin/parqueadero_form.html",
        {
            "request": request,
            "titulo": ("Editar" if parqueadero else "Crear") + " Parqueadero - ParkSmart",
            "parqueadero": parqueadero,
            "valores": valores,
            "ciudades": CIUDADES_COLOMBIA,
            "error": mensaje_error_parqueadero(error),
        },
        status_code=400
    )


# ============================================
# RUTAS PÚBLICAS
# ============================================

@router.get("/parqueaderos", response_class=HTMLResponse)
async def listar_parqueaderos(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra la lista de parqueaderos activos con filtros.
    """
    # Obtener todos los parqueaderos activos
    parqueaderos = db.query(Parqueadero).filter(
        Parqueadero.estado == EstadoParqueadero.ACTIVO
    ).all()

    # Para cada parqueadero, contar espacios disponibles y ocupados
    resultado = []
    for p in parqueaderos:
        espacios_disponibles = db.query(Espacio).filter(
            Espacio.parqueadero_id == p.id,
            Espacio.estado == "disponible"
        ).count()

        espacios_ocupados = db.query(Espacio).filter(
            Espacio.parqueadero_id == p.id,
            Espacio.estado == "ocupado"
        ).count()

        resultado.append({
            "parqueadero": p,
            "espacios_disponibles": espacios_disponibles,
            "espacios_ocupados": espacios_ocupados
        })

    return templates.TemplateResponse(
        "parqueaderos.html",
        {
            "request": request,
            "titulo": "Parqueaderos - ParkSmart",
            "parqueaderos": resultado
        }
    )


@router.get("/parqueaderos/{parqueadero_id}", response_class=HTMLResponse)
async def detalle_parqueadero(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db)
):
    """
    Muestra el detalle de un parqueadero con sus espacios.
    """
    # Buscar el parqueadero
    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id,
        Parqueadero.estado == EstadoParqueadero.ACTIVO
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

    # Obtener los espacios del parqueadero
    espacios = db.query(Espacio).filter(
        Espacio.parqueadero_id == parqueadero_id
    ).all()

    # Contar espacios por estado
    espacios_disponibles = sum(1 for e in espacios if e.estado == "disponible")
    espacios_ocupados = sum(1 for e in espacios if e.estado == "ocupado")
    espacios_mantenimiento = sum(1 for e in espacios if e.estado == "mantenimiento")

    return templates.TemplateResponse(
        "detalle_parqueadero.html",
        {
            "request": request,
            "titulo": f"{parqueadero.nombre} - ParkSmart",
            "parqueadero": parqueadero,
            "espacios": espacios,
            "espacios_disponibles": espacios_disponibles,
            "espacios_ocupados": espacios_ocupados,
            "espacios_mantenimiento": espacios_mantenimiento
        }
    )


# ============================================
# RUTAS DE GESTIÓN
# ============================================

@router.get("/admin/parqueaderos", response_class=HTMLResponse)
async def admin_parqueaderos(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_parqueaderos)
):
    """
    Muestra la gestión de parqueaderos.

    El admin_global ve todos; el propietario solo ve los suyos.
    """
    query = db.query(Parqueadero)
    if usuario.rol == RolUsuario.PROPIETARIO:
        query = query.filter(Parqueadero.propietario_id == usuario.id)

    parqueaderos = query.all()

    return templates.TemplateResponse(
        "admin/parqueaderos.html",
        {
            "request": request,
            "titulo": "Gestión de Parqueaderos - ParkSmart",
            "parqueaderos": parqueaderos
        }
    )


@router.get("/admin/parqueaderos/crear", response_class=HTMLResponse)
async def formulario_crear_parqueadero(
    request: Request,
    usuario: Usuario = Depends(solo_propietario)
):
    """
    Muestra el formulario para crear un parqueadero (solo propietarios).
    """
    return templates.TemplateResponse(
        "admin/parqueadero_form.html",
        {
            "request": request,
            "titulo": "Crear Parqueadero - ParkSmart",
            "parqueadero": None,
            "ciudades": CIUDADES_COLOMBIA
        }
    )


@router.post("/admin/parqueaderos/crear")
async def crear_parqueadero(
    request: Request,
    nombre: str = Form(...),
    direccion: str = Form(...),
    descripcion: str = Form(""),
    ciudad: str = Form(""),
    cantidad_motos: str = Form("0"),
    cantidad_carros: str = Form("0"),
    cantidad_buses: str = Form("0"),
    tarifa_hora_carro: str = Form(""),
    tarifa_hora_moto: str = Form(""),
    tarifa_hora_bus: str = Form(""),
    horario_apertura: str = Form(...),
    horario_cierre: str = Form(...),
    margen_minutos: str = Form(str(MARGEN_PREDETERMINADO)),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(solo_propietario)
):
    """
    Procesa la creación de un nuevo parqueadero con sus espacios (solo propietarios).

    El propietario indica cuántos espacios hay para motos, carros y buses
    (una categoría puede tener 0). Con eso:
    - capacidad = suma de los espacios;
    - acepta_motos/carros/buses = ese tipo tiene al menos un espacio;
    - los espacios se numeran solos: M01..., C01..., B01...
    - margen_minutos (10 o 15, por defecto 15): tiempo libre entre reservas.

    El parqueadero y sus espacios se guardan en UNA sola transacción:
    si algo falla, no queda un parqueadero a medias sin espacios.

    propietario_id y estado NUNCA vienen del formulario:
    - propietario_id = usuario en sesión
    - estado = pendiente (hasta que el admin_global lo apruebe)
    """
    valores = {
        "nombre": nombre, "direccion": direccion, "descripcion": descripcion,
        "ciudad": ciudad,
        "cantidad_motos": cantidad_motos, "cantidad_carros": cantidad_carros,
        "cantidad_buses": cantidad_buses,
        "tarifa_hora_carro": tarifa_hora_carro, "tarifa_hora_moto": tarifa_hora_moto,
        "tarifa_hora_bus": tarifa_hora_bus,
        "horario_apertura": horario_apertura, "horario_cierre": horario_cierre,
        "margen_minutos": margen_minutos,
    }
    try:
        cantidades = EspaciosPorTipo(
            motos=leer_cantidad(cantidad_motos),
            carros=leer_cantidad(cantidad_carros),
            buses=leer_cantidad(cantidad_buses),
        )
        datos = validar_formulario_parqueadero({
            **valores,
            "margen_minutos": leer_cantidad(margen_minutos),
            "capacidad": cantidades.total,
            "acepta_motos": cantidades.motos > 0,
            "acepta_carros": cantidades.carros > 0,
            "acepta_buses": cantidades.buses > 0,
        })
    except ValidationError as error:
        return mostrar_formulario_con_error(request, None, valores, error)

    nuevo_parqueadero = Parqueadero(
        propietario_id=usuario.id,
        **datos,
        estado=EstadoParqueadero.PENDIENTE
    )
    # Al asignar la lista, SQLAlchemy inserta el parqueadero y luego todos
    # sus espacios en lote, dentro de la misma transacción del commit.
    nuevo_parqueadero.espacios = construir_espacios({
        TipoEspacio.MOTO: cantidades.motos,
        TipoEspacio.CARRO: cantidades.carros,
        TipoEspacio.BUS: cantidades.buses,
    })

    db.add(nuevo_parqueadero)
    db.commit()
    db.refresh(nuevo_parqueadero)

    return RedirectResponse(
        url="/admin/parqueaderos",
        status_code=302
    )


@router.get("/admin/parqueaderos/{parqueadero_id}/editar", response_class=HTMLResponse)
async def formulario_editar_parqueadero(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_parqueaderos)
):
    """
    Muestra el formulario para editar un parqueadero (propietario dueño o admin_global).
    """
    parqueadero = obtener_parqueadero_autorizado(db, parqueadero_id, usuario)

    return templates.TemplateResponse(
        "admin/parqueadero_form.html",
        {
            "request": request,
            "titulo": "Editar Parqueadero - ParkSmart",
            "parqueadero": parqueadero,
            "ciudades": CIUDADES_COLOMBIA
        }
    )


@router.post("/admin/parqueaderos/{parqueadero_id}/editar")
async def editar_parqueadero(
    request: Request,
    parqueadero_id: int,
    nombre: str = Form(...),
    direccion: str = Form(...),
    descripcion: str = Form(""),
    capacidad: int = Form(...),
    ciudad: str = Form(""),
    acepta_carros: bool = Form(False),
    acepta_motos: bool = Form(False),
    acepta_buses: bool = Form(False),
    tarifa_hora_carro: str = Form(""),
    tarifa_hora_moto: str = Form(""),
    tarifa_hora_bus: str = Form(""),
    horario_apertura: str = Form(...),
    horario_cierre: str = Form(...),
    margen_minutos: Optional[str] = Form(None),
    estado: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_parqueaderos)
):
    """
    Procesa la edición de un parqueadero (propietario dueño o admin_global).

    Solo el admin_global puede cambiar el estado. Si un propietario envía
    'estado' en el formulario, se ignora: así no puede aprobarse a sí mismo.

    Si no llega margen_minutos, se conserva el actual. Un margen nuevo
    aplica solo a las reservas nuevas: cada reserva guarda su propio margen.
    """
    parqueadero = obtener_parqueadero_autorizado(db, parqueadero_id, usuario)

    if margen_minutos is None:
        margen_minutos = str(parqueadero.margen_minutos)

    valores = {
        "nombre": nombre, "direccion": direccion, "descripcion": descripcion,
        "capacidad": capacidad, "ciudad": ciudad,
        "acepta_carros": acepta_carros, "acepta_motos": acepta_motos,
        "acepta_buses": acepta_buses,
        "tarifa_hora_carro": tarifa_hora_carro, "tarifa_hora_moto": tarifa_hora_moto,
        "tarifa_hora_bus": tarifa_hora_bus,
        "horario_apertura": horario_apertura, "horario_cierre": horario_cierre,
        "margen_minutos": margen_minutos,
    }
    try:
        datos = validar_formulario_parqueadero({**valores, "margen_minutos": leer_cantidad(margen_minutos)})
    except ValidationError as error:
        return mostrar_formulario_con_error(request, parqueadero, valores, error)

    # Actualizar campos (ya validados)
    for campo, valor in datos.items():
        setattr(parqueadero, campo, valor)
    if usuario.rol == RolUsuario.ADMIN_GLOBAL and estado:
        if estado not in [e.value for e in EstadoParqueadero]:
            raise HTTPException(status_code=400, detail="Estado no válido")
        parqueadero.estado = estado

    db.commit()

    return RedirectResponse(
        url="/admin/parqueaderos",
        status_code=302
    )


@router.post("/admin/parqueaderos/{parqueadero_id}/eliminar")
async def eliminar_parqueadero(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_parqueaderos)
):
    """
    Elimina un parqueadero (propietario dueño o admin_global).
    """
    parqueadero = obtener_parqueadero_autorizado(db, parqueadero_id, usuario)

    db.delete(parqueadero)
    db.commit()

    return RedirectResponse(
        url="/admin/parqueaderos",
        status_code=302
    )
