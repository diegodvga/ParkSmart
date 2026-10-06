"""
Router de espacios para ParkSmart.

Rutas administrativas:
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
from app.models import Espacio, Parqueadero, TipoEspacio, EstadoEspacio, RolUsuario

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["espacios"]
)


def requerir_admin(request: Request):
    """
    Verifica que el usuario sea administrador.
    """
    rol = request.session.get("usuario_rol")
    if rol != RolUsuario.ADMINISTRADOR.value:
        raise HTTPException(status_code=403, detail="Acceso denegado")


# ============================================
# RUTAS ADMINISTRATIVAS
# ============================================

@router.get("/admin/parqueaderos/{parqueadero_id}/espacios", response_class=HTMLResponse)
async def listar_espacios(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db)
):
    """
    Muestra la lista de espacios de un parqueadero.
    """
    requerir_admin(request)

    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

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
    db: Session = Depends(get_db)
):
    """
    Muestra el formulario para crear un espacio.
    """
    requerir_admin(request)

    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

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
    db: Session = Depends(get_db)
):
    """
    Procesa la creación de un nuevo espacio.
    """
    requerir_admin(request)

    # Verificar que el parqueadero existe
    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

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

    nuevo_espacio = Espacio(
        parqueadero_id=parqueadero_id,
        numero=numero.upper(),
        tipo=tipo.upper(),
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
    db: Session = Depends(get_db)
):
    """
    Muestra el formulario para editar un espacio.
    """
    requerir_admin(request)

    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()

    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

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
    db: Session = Depends(get_db)
):
    """
    Procesa la edición de un espacio.
    """
    requerir_admin(request)

    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()

    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

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

    espacio.numero = numero
    espacio.tipo = tipo
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
    db: Session = Depends(get_db)
):
    """
    Cambia el estado de un espacio (disponible/ocupado/mantenimiento).
    """
    requerir_admin(request)

    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()

    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

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
    db: Session = Depends(get_db)
):
    """
    Elimina un espacio.
    """
    requerir_admin(request)

    espacio = db.query(Espacio).filter(Espacio.id == espacio_id).first()

    if not espacio:
        raise HTTPException(status_code=404, detail="Espacio no encontrado")

    parqueadero_id = espacio.parqueadero_id

    db.delete(espacio)
    db.commit()

    return RedirectResponse(
        url=f"/admin/parqueaderos/{parqueadero_id}/espacios",
        status_code=302
    )
