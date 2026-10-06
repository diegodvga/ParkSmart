"""
Router de parqueaderos para ParkSmart.

Rutas públicas:
- GET /parqueaderos              → Lista de parqueaderos
- GET /parqueaderos/{id}         → Detalle de un parqueadero

Rutas administrativas:
- GET  /admin/parqueaderos               → Gestión de parqueaderos
- GET  /admin/parqueaderos/crear         → Formulario crear
- POST /admin/parqueaderos/crear         → Procesar creación
- GET  /admin/parqueaderos/{id}/editar    → Formulario editar
- POST /admin/parqueaderos/{id}/editar    → Procesar edición
- POST /admin/parqueaderos/{id}/eliminar  → Eliminar parqueadero
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from app.database import get_db
from app.models import Parqueadero, Espacio, EstadoParqueadero, RolUsuario

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["parqueaderos"]
)


# ============================================
# FUNCIONES AUXILIARES
# ============================================

def obtener_usuario_rol(request: Request) -> Optional[str]:
    """
    Obtiene el rol del usuario desde la sesión.
    """
    return request.session.get("usuario_rol")


def requerir_admin(request: Request):
    """
    Verifica que el usuario sea administrador.
    Si no lo es, lanza un error 403.
    """
    rol = obtener_usuario_rol(request)
    if rol != RolUsuario.ADMINISTRADOR.value:
        raise HTTPException(status_code=403, detail="Acceso denegado. Se requiere rol de administrador.")


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
# RUTAS ADMINISTRATIVAS
# ============================================

@router.get("/admin/parqueaderos", response_class=HTMLResponse)
async def admin_parqueaderos(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Muestra la gestión de parqueaderos (solo administradores).
    """
    requerir_admin(request)

    parqueaderos = db.query(Parqueadero).all()

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
    db: Session = Depends(get_db)
):
    """
    Muestra el formulario para crear un parqueadero (solo administradores).
    """
    requerir_admin(request)

    return templates.TemplateResponse(
        "admin/parqueadero_form.html",
        {
            "request": request,
            "titulo": "Crear Parqueadero - ParkSmart",
            "parqueadero": None
        }
    )


@router.post("/admin/parqueaderos/crear")
async def crear_parqueadero(
    request: Request,
    nombre: str = Form(...),
    direccion: str = Form(...),
    descripcion: str = Form(""),
    capacidad: int = Form(...),
    tarifa_hora_carro: float = Form(...),
    tarifa_hora_moto: float = Form(...),
    horario_apertura: str = Form(...),
    horario_cierre: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Procesa la creación de un nuevo parqueadero (solo administradores).
    """
    requerir_admin(request)

    nuevo_parqueadero = Parqueadero(
        nombre=nombre,
        direccion=direccion,
        descripcion=descripcion,
        capacidad=capacidad,
        tarifa_hora_carro=tarifa_hora_carro,
        tarifa_hora_moto=tarifa_hora_moto,
        horario_apertura=horario_apertura,
        horario_cierre=horario_cierre,
        estado=EstadoParqueadero.ACTIVO
    )

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
    db: Session = Depends(get_db)
):
    """
    Muestra el formulario para editar un parqueadero (solo administradores).
    """
    requerir_admin(request)

    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

    return templates.TemplateResponse(
        "admin/parqueadero_form.html",
        {
            "request": request,
            "titulo": "Editar Parqueadero - ParkSmart",
            "parqueadero": parqueadero
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
    tarifa_hora_carro: float = Form(...),
    tarifa_hora_moto: float = Form(...),
    horario_apertura: str = Form(...),
    horario_cierre: str = Form(...),
    estado: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Procesa la edición de un parqueadero (solo administradores).
    """
    requerir_admin(request)

    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

    # Actualizar campos
    parqueadero.nombre = nombre
    parqueadero.direccion = direccion
    parqueadero.descripcion = descripcion
    parqueadero.capacidad = capacidad
    parqueadero.tarifa_hora_carro = tarifa_hora_carro
    parqueadero.tarifa_hora_moto = tarifa_hora_moto
    parqueadero.horario_apertura = horario_apertura
    parqueadero.horario_cierre = horario_cierre
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
    db: Session = Depends(get_db)
):
    """
    Elimina un parqueadero (solo administradores).
    """
    requerir_admin(request)

    parqueadero = db.query(Parqueadero).filter(
        Parqueadero.id == parqueadero_id
    ).first()

    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

    db.delete(parqueadero)
    db.commit()

    return RedirectResponse(
        url="/admin/parqueaderos",
        status_code=302
    )
