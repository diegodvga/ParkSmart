"""
Router del propietario para ParkSmart (Módulo 5: paneles).

Rutas (solo rol propietario):
- GET /propietario/panel                                → Panel consolidado (todos sus parqueaderos)
- GET /propietario/parqueaderos/{id}                     → Panel individual de un parqueadero
- GET /propietario/api/metricas                          → Métricas consolidadas (JSON)
- GET /propietario/api/parqueaderos/{id}/metricas        → Métricas de un parqueadero (JSON)

Permisos:
- Siempre se filtra por propietario_id = usuario en sesión. Cambiar el id
  en la URL devuelve 403.
- El operario NO entra aquí: opera la puerta (/operacion) pero no ve
  ingresos.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import requiere_rol
from app.models import Parqueadero, RolUsuario, Usuario
from app.services.metricas import resumen_parqueadero, resumen_propietario
from app.services.reservas import ahora_negocio

# Configurar templates
templates = Jinja2Templates(directory="app/templates")
# 12500 → "$12.500" (separador de miles colombiano)
templates.env.filters["pesos"] = lambda valor: "$" + f"{valor or 0:,.0f}".replace(",", ".")

router = APIRouter(
    prefix="/propietario",
    tags=["propietario"]
)

# Todas las rutas de este router son solo para propietarios
solo_propietario = requiere_rol(RolUsuario.PROPIETARIO)


def obtener_parqueadero_propio(db: Session, parqueadero_id: int, usuario: Usuario) -> Parqueadero:
    """404 si no existe; 403 si es de otro propietario."""
    parqueadero = db.get(Parqueadero, parqueadero_id)
    if parqueadero is None:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")
    if parqueadero.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Este parqueadero no te pertenece")
    return parqueadero


@router.get("/panel", response_class=HTMLResponse)
async def panel_propietario(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(solo_propietario)
):
    """
    Panel consolidado: ingresos, ocupación global, vehículos activos,
    comparativa por parqueadero y alertas pendientes.
    """
    return templates.TemplateResponse(
        "propietario/panel.html",
        {
            "request": request,
            "titulo": "Mi Panel - ParkSmart",
            "usuario": usuario,
            "resumen": resumen_propietario(db, usuario, ahora_negocio()),
        }
    )


@router.get("/parqueaderos/{parqueadero_id}", response_class=HTMLResponse)
async def panel_parqueadero(
    request: Request,
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(solo_propietario)
):
    """Panel individual: plazas, ingresos, ocupación, reservas próximas y alertas."""
    parqueadero = obtener_parqueadero_propio(db, parqueadero_id, usuario)
    return templates.TemplateResponse(
        "propietario/parqueadero.html",
        {
            "request": request,
            "titulo": f"{parqueadero.nombre} - ParkSmart",
            "parqueadero": parqueadero,
            "m": resumen_parqueadero(db, parqueadero, ahora_negocio()),
        }
    )


@router.get("/api/metricas")
async def metricas_consolidadas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(solo_propietario)
):
    """Métricas consolidadas del propietario en JSON."""
    return resumen_propietario(db, usuario, ahora_negocio())


@router.get("/api/parqueaderos/{parqueadero_id}/metricas")
async def metricas_parqueadero(
    parqueadero_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(solo_propietario)
):
    """Métricas de un parqueadero propio en JSON."""
    parqueadero = obtener_parqueadero_propio(db, parqueadero_id, usuario)
    return resumen_parqueadero(db, parqueadero, ahora_negocio())
