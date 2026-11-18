"""
Router de comprobantes (facturas internas) para ParkSmart (Módulo 4).

En la interfaz se llaman "facturas": son documentos internos, NO facturas
electrónicas DIAN. Hay una por cada cobro (ver app/services/pagos.py).

Rutas:
- GET /comprobantes/{id}.json → Datos de la factura en JSON (descarga)
- GET /comprobantes/{id}      → Vista imprimible (?pagado=1 al volver de la pasarela)

Quién puede verlo:
- El propietario del parqueadero o un operario asignado.
- El cliente dueño de la reserva del comprobante.
- El admin_global (supervisión).
A cualquier otro se le responde 404, igual que si no existiera: así no se
revela que ese comprobante existe.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_usuario_actual, puede_operar
from app.models import Comprobante, Parqueadero, RolUsuario, Usuario

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["pesos"] = lambda valor: "$" + f"{valor or 0:,.0f}".replace(",", ".")

router = APIRouter(prefix="/comprobantes", tags=["comprobantes"])


def obtener_comprobante_visible(db: Session, comprobante_id: int, usuario: Usuario) -> Comprobante:
    """Busca el comprobante y verifica que el usuario pueda verlo (si no, 404)."""
    comprobante = db.get(Comprobante, comprobante_id)
    if comprobante is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    if usuario.rol == RolUsuario.ADMIN_GLOBAL:
        return comprobante
    parqueadero = db.get(Parqueadero, comprobante.parqueadero_id)
    if puede_operar(db, parqueadero, usuario):
        return comprobante
    reserva = comprobante.cuenta.reserva
    if usuario.rol == RolUsuario.CLIENTE and reserva is not None and reserva.usuario_id == usuario.id:
        return comprobante
    raise HTTPException(status_code=404, detail="Comprobante no encontrado")


# La ruta .json va primero: "/{id}" también aceptaría "5.json" como texto
@router.get("/{comprobante_id}.json")
async def comprobante_json(
    comprobante_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Descarga los datos del comprobante en JSON."""
    comprobante = obtener_comprobante_visible(db, comprobante_id, usuario)
    return JSONResponse(
        comprobante.datos,
        headers={
            "Content-Disposition": f'attachment; filename="factura-{comprobante.parqueadero_id}-{comprobante.numero}.json"'
        },
    )


@router.get("/{comprobante_id}", response_class=HTMLResponse)
async def comprobante_imprimible(
    request: Request,
    comprobante_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Vista de la factura lista para imprimir (o guardar como PDF desde el navegador)."""
    comprobante = obtener_comprobante_visible(db, comprobante_id, usuario)
    return templates.TemplateResponse(
        "comprobante.html",
        {
            "request": request,
            "titulo": f"Factura N.º {comprobante.numero} - ParkSmart",
            "comprobante": comprobante,
            # Aviso de éxito solo cuando el cliente llega desde la pasarela
            "recien_pagado": request.query_params.get("pagado") == "1",
        },
    )
