"""
Router de vehículos para ParkSmart.

Rutas:
- GET  /mis-vehiculos              → Lista de vehículos del usuario
- GET  /mis-vehiculos/crear        → Formulario crear
- POST /mis-vehiculos/crear        → Procesar creación
- GET  /mis-vehiculos/{id}/editar   → Formulario editar
- POST /mis-vehiculos/{id}/editar   → Procesar edición
- POST /mis-vehiculos/{id}/eliminar → Eliminar vehículo
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Vehiculo, TipoVehiculo

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["vehiculos"]
)


def requerir_autenticacion(request: Request):
    """
    Verifica que el usuario esté autenticado.
    """
    if not request.session.get("usuario_id"):
        raise HTTPException(status_code=401, detail="Debes iniciar sesión")


# ============================================
# RUTAS
# ============================================

@router.get("/mis-vehiculos", response_class=HTMLResponse)
async def listar_vehiculos(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra la lista de vehículos del usuario autenticado.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    vehiculos = db.query(Vehiculo).filter(
        Vehiculo.usuario_id == usuario_id
    ).all()

    return templates.TemplateResponse(
        "mis_vehiculos.html",
        {
            "request": request,
            "titulo": "Mis Vehículos - ParkSmart",
            "vehiculos": vehiculos
        }
    )


@router.get("/mis-vehiculos/crear", response_class=HTMLResponse)
async def formulario_crear_vehiculo(
    request: Request
):
    """
    Muestra el formulario para crear un vehículo.
    """
    requerir_autenticacion(request)

    return templates.TemplateResponse(
        "vehiculo_form.html",
        {
            "request": request,
            "titulo": "Registrar Vehículo - ParkSmart",
            "vehiculo": None
        }
    )


@router.post("/mis-vehiculos/crear")
async def crear_vehiculo(
    request: Request,
    placa: str = Form(...),
    tipo: str = Form(...),
    marca: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Procesa la creación de un nuevo vehículo.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    # Verificar que no exista un vehículo con la misma placa
    vehiculo_existente = db.query(Vehiculo).filter(
        Vehiculo.placa == placa.upper()
    ).first()

    if vehiculo_existente:
        return templates.TemplateResponse(
            "vehiculo_form.html",
            {
                "request": request,
                "titulo": "Registrar Vehículo - ParkSmart",
                "vehiculo": None,
                "error": f"Ya existe un vehículo con la placa {placa.upper()}"
            },
            status_code=400
        )

    nuevo_vehiculo = Vehiculo(
        usuario_id=usuario_id,
        placa=placa.upper(),
        tipo=tipo,
        marca=marca if marca else None
    )

    db.add(nuevo_vehiculo)
    db.commit()

    return RedirectResponse(
        url="/mis-vehiculos",
        status_code=302
    )


@router.get("/mis-vehiculos/{vehiculo_id}/editar", response_class=HTMLResponse)
async def formulario_editar_vehiculo(
    request: Request,
    vehiculo_id: int,
    db: Session = Depends(get_db)
):
    """
    Muestra el formulario para editar un vehículo.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    vehiculo = db.query(Vehiculo).filter(
        Vehiculo.id == vehiculo_id,
        Vehiculo.usuario_id == usuario_id
    ).first()

    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")

    return templates.TemplateResponse(
        "vehiculo_form.html",
        {
            "request": request,
            "titulo": "Editar Vehículo - ParkSmart",
            "vehiculo": vehiculo
        }
    )


@router.post("/mis-vehiculos/{vehiculo_id}/editar")
async def editar_vehiculo(
    request: Request,
    vehiculo_id: int,
    placa: str = Form(...),
    tipo: str = Form(...),
    marca: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Procesa la edición de un vehículo.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    vehiculo = db.query(Vehiculo).filter(
        Vehiculo.id == vehiculo_id,
        Vehiculo.usuario_id == usuario_id
    ).first()

    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")

    # Verificar que no exista otro vehículo con la misma placa
    vehiculo_existente = db.query(Vehiculo).filter(
        Vehiculo.placa == placa.upper(),
        Vehiculo.id != vehiculo_id
    ).first()

    if vehiculo_existente:
        return templates.TemplateResponse(
            "vehiculo_form.html",
            {
                "request": request,
                "titulo": "Editar Vehículo - ParkSmart",
                "vehiculo": vehiculo,
                "error": f"Ya existe otro vehículo con la placa {placa.upper()}"
            },
            status_code=400
        )

    vehiculo.placa = placa.upper()
    vehiculo.tipo = tipo
    vehiculo.marca = marca if marca else None

    db.commit()

    return RedirectResponse(
        url="/mis-vehiculos",
        status_code=302
    )


@router.post("/mis-vehiculos/{vehiculo_id}/eliminar")
async def eliminar_vehiculo(
    request: Request,
    vehiculo_id: int,
    db: Session = Depends(get_db)
):
    """
    Elimina un vehículo.
    """
    requerir_autenticacion(request)

    usuario_id = request.session.get("usuario_id")

    vehiculo = db.query(Vehiculo).filter(
        Vehiculo.id == vehiculo_id,
        Vehiculo.usuario_id == usuario_id
    ).first()

    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")

    db.delete(vehiculo)
    db.commit()

    return RedirectResponse(
        url="/mis-vehiculos",
        status_code=302
    )
