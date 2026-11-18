"""
Router de espacios para ParkSmart.

Rutas de gestión (propietario: solo espacios de SUS parqueaderos; admin_global: todos):
- GET  /admin/parqueaderos/{id}/espacios              → Lista de espacios
- GET  /admin/parqueaderos/{id}/espacios/crear        → Formulario crear
- POST /admin/parqueaderos/{id}/espacios/crear        → Procesar creación
- GET  /admin/espacios/{id}/editar                     → Formulario editar
- POST /admin/espacios/{id}/editar                     → Procesar edición
- POST /admin/espacios/{id}/estado                     → Cambiar estado
- POST /admin/espacios/{id}/eliminar                   → Eliminar espacio
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.dependencies import obtener_parqueadero_autorizado, requiere_rol
from app.models import Espacio, Parqueadero, TipoEspacio, EstadoEspacio, RolUsuario, Usuario
from app.schemas import NOMBRES_TIPO_VEHICULO

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["espacios"]
)

# Gestionar espacios: el propietario (de SU parqueadero) o el admin_global.
# El rol se valida aquí; que el parqueadero sea suyo se valida en cada ruta
# con obtener_parqueadero_autorizado().
gestor_espacios = requiere_rol(RolUsuario.PROPIETARIO, RolUsuario.ADMIN_GLOBAL)


def obtener_espacio_autorizado(db: Session, espacio_id: int, usuario: Usuario) -> Espacio:
    """
    Busca un espacio y verifica que su parqueadero sea del usuario (o que sea admin_global).
    """
    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()
    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

    obtener_parqueadero_autorizado(db, espacio.parqueadero_id, usuario)
    return espacio


# ============================================
# RUTAS ADMINISTRATIVAS
# ============================================

def validar_tipo_espacio(parqueadero, tipo, tipo_actual=None):
    """
    Revisa que el tipo de espacio exista y que el parqueadero acepte
    ese tipo de vehículo. Devuelve un mensaje de error o None si es válido.

    Al editar, se permite conservar el tipo que el espacio ya tenía.
    """
    tipo = tipo.strip().lower()
    if tipo not in [t.value for t in TipoEspacio]:
        return "Tipo de espacio no válido."
    tipo_actual = getattr(tipo_actual, "value", tipo_actual)
    if tipo != tipo_actual and parqueadero.tarifa_para(tipo) is None:
        return f"Este parqueadero no acepta {NOMBRES_TIPO_VEHICULO[tipo]}."
    return None


@router.get("/admin/parqueaderos/{parqueadero_id}/espacios", response_class=HTMLResponse)
async def listar_espacios(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Muestra la lista de espacios de un parqueadero.
    """
    parqueadero = obtener_parqueadero_autorizado(db, parqueadero_id, usuario)

    espacios = db.query(Espacio).filter(
        Espacio.parqueadero_id == parqueadero_id
    ).all()

    # Contar espacios por estado
    disponibles = sum(1 for e in espacios if e.estado == EstadoEspacio.DISPONIBLE.value)
    ocupados = sum(1 for e in espacios if e.estado == EstadoEspacio.OCUPADO.value)
    mantenimiento = sum(1 for e in espacios if e.estado == EstadoEspacio.MANTENIMIENTO.value)

    return templates.TemplateResponse(
        "admin/espacios.html",
        {
            "request": request,
            "titulo": f"Espacios - {parqueadero.nombre}",
            "parqueadero": parqueadero,
            "espacios": espacios,
            "disponibles": disponibles,
            "ocupados": ocupados,
            "mantenimiento": mantenimiento
        }
    )


@router.get("/admin/parqueaderos/{parqueadero_id}/espacios/crear", response_class=HTMLResponse)
async def formulario_crear_espacio(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Muestra el formulario para crear un espacio.
    """
    parqueadero = obtener_parqueadero_autorizado(db, parqueadero_id, usuario)

    return templates.TemplateResponse(
        "admin/espacio_form.html",
        {
            "request": request,
            "titulo": "Crear Espacio",
            "parqueadero": parqueadero,
            "espacio": None
        }
    )


@router.post("/admin/parqueaderos/{parqueadero_id}/espacios/crear")
async def crear_espacio(
    request: Request,
    parqueadero_id: int,
    numero: str = Form(...),
    tipo: str = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Procesa la creación de un nuevo espacio.
    """
    # Verificar que el parqueadero existe y es del usuario
    parqueadero = obtener_parqueadero_autorizado(db, parqueadero_id, usuario)

    # Verificar que no exista un espacio con el mismo número en ese parqueadero
    espacio_existente = db.query(Espacio).filter(
        Espacio.parqueadero_id == parqueadero_id,
        Espacio.numero == numero
    ).first()

    if espacio_existente:
        return templates.TemplateResponse(
            "admin/espacio_form.html",
            {
                "request": request,
                "titulo": "Crear Espacio",
                "parqueadero": parqueadero,
                "espacio": None,
                "error": f"Ya existe un espacio con el número {numero} en este parqueadero"
            },
            status_code=400
        )

    error_tipo = validar_tipo_espacio(parqueadero, tipo)
    if error_tipo:
        return templates.TemplateResponse(
            "admin/espacio_form.html",
            {
                "request": request,
                "titulo": "Crear Espacio",
                "parqueadero": parqueadero,
                "espacio": None,
                "error": error_tipo
            },
            status_code=400
        )

    nuevo_espacio = Espacio(
        parqueadero_id=parqueadero_id,
        numero=numero.upper(),
        tipo=tipo.strip().lower(),
        estado=EstadoEspacio.DISPONIBLE.value
    )

    db.add(nuevo_espacio)
    db.commit()

    return RedirectResponse(
        url=f"/admin/parqueaderos/{parqueadero_id}/espacios",
        status_code=302
    )


@router.get("/admin/espacios/{espacio_id}/editar", response_class=HTMLResponse)
async def formulario_editar_espacio(
    request: Request,
    espacio_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Muestra el formulario para editar un espacio.
    """
    espacio = obtener_espacio_autorizado(db, espacio_id, usuario)

    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == espacio.parqueadero_id
    ).first()

    return templates.TemplateResponse(
        "admin/espacio_form.html",
        {
            "request": request,
            "titulo": "Editar Espacio",
            "parqueadero": parqueadero,
            "espacio": espacio
        }
    )


@router.post("/admin/espacios/{espacio_id}/editar")
async def editar_espacio(
    request: Request,
    espacio_id: int,
    numero: str = Form(...),
    tipo: str = Form(...),
    estado: str = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Procesa la edición de un espacio.
    """
    espacio = obtener_espacio_autorizado(db, espacio_id, usuario)

    # Verificar que no exista otro espacio con el mismo número
    espacio_existente = db.query(Espacio).filter(
        Espacio.parqueadero_id == espacio.parqueadero_id,
        Espacio.numero == numero,
        Espacio.id != espacio_id
    ).first()

    if espacio_existente:
        return templates.TemplateResponse(
            "admin/espacio_form.html",
            {
                "request": request,
                "titulo": "Editar Espacio",
                "parqueadero": espacio.parqueadero,
                "espacio": espacio,
                "error": f"Ya existe un espacio con el número {numero} en este parqueadero"
            },
            status_code=400
        )

    error_tipo = validar_tipo_espacio(espacio.parqueadero, tipo, tipo_actual=espacio.tipo)
    if error_tipo:
        return templates.TemplateResponse(
            "admin/espacio_form.html",
            {
                "request": request,
                "titulo": "Editar Espacio",
                "parqueadero": espacio.parqueadero,
                "espacio": espacio,
                "error": error_tipo
            },
            status_code=400
        )

    espacio.numero = numero
    espacio.tipo = tipo.strip().lower()
    espacio.estado = estado

    db.commit()

    return RedirectResponse(
        url=f"/admin/parqueaderos/{espacio.parqueadero_id}/espacios",
        status_code=302
    )


@router.post("/admin/espacios/{espacio_id}/estado")
async def cambiar_estado_espacio(
    request: Request,
    espacio_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Cambia el estado de un espacio (disponible/ocupado/mantenimiento).
    """
    espacio = obtener_espacio_autorizado(db, espacio_id, usuario)

    # Validar que el estado sea válido
    estados_validos = [e.value for e in EstadoEspacio]
    if estado not in estados_validos:
        raise HTTPException(status_code=400, detail="Estado no válido")

    espacio.estado = estado
    db.commit()

    return RedirectResponse(
        url=f"/admin/parqueaderos/{espacio.parqueadero_id}/espacios",
        status_code=302
    )


@router.post("/admin/espacios/{espacio_id}/eliminar")
async def eliminar_espacio(
    request: Request,
    espacio_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(gestor_espacios)
):
    """
    Elimina un espacio.
    """
    espacio = obtener_espacio_autorizado(db, espacio_id, usuario)

    parqueadero_id = espacio.parqueadero_id

    db.delete(espacio)
    db.commit()

    return RedirectResponse(
        url=f"/admin/parqueaderos/{parqueadero_id}/espacios",
        status_code=302
    )
