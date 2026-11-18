"""
ParkSmart - Punto de entrada de la aplicación.

Este archivo:
1. Crea la aplicación FastAPI
2. Configura las plantillas Jinja2
3. Configura los archivos estáticos (CSS, JS, imágenes)
4. Incluye los routers (se agregarán en etapas posteriores)
5. Define la ruta principal (/)
"""

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
import os
from dotenv import load_dotenv

from app.database import get_db
from app.models import Usuario, Parqueadero, Espacio, Reserva, RolUsuario, EstadoParqueadero

# Cargar variables de entorno
load_dotenv()

# Crear la aplicación FastAPI
app = FastAPI(
    title="ParkSmart",
    description="Sistema de gestión de parqueaderos y reservas",
    version="1.0.0"
)

# Configurar el middleware de sesiones
# Esto permite usar request.session para guardar datos del usuario
# La clave secreta se usa para firmar las cookies de forma segura
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "clave_por_defecto_cambiar_en_produccion")
)

# Configurar las plantillas Jinja2
# Las plantillas estarán en la carpeta app/templates
templates = Jinja2Templates(directory="app/templates")

# Configurar los archivos estáticos (CSS, JS, imágenes)
# Estarán en la carpeta app/static
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    """
    Página de inicio de ParkSmart.

    Muestra:
    - Nombre del sistema
    - Descripción breve
    - Botón para consultar parqueaderos
    - Estadísticas dinámicas del sistema
    """
    # Obtener estadísticas reales de la base de datos
    total_parqueaderos = db.query(Parqueadero).filter(
        Parqueadero.estado == EstadoParqueadero.ACTIVO.value
    ).count()

    total_espacios = db.query(Espacio).count()

    total_usuarios = db.query(Usuario).filter(
        Usuario.rol == RolUsuario.CLIENTE.value
    ).count()

    total_reservas = db.query(Reserva).count()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "titulo": "ParkSmart - Sistema de Gestión de Parqueaderos",
            "total_parqueaderos": total_parqueaderos,
            "total_espacios": total_espacios,
            "total_usuarios": total_usuarios,
            "total_reservas": total_reservas
        }
    )


# Incluir routers de la aplicación
from app.routers import (
    auth, parqueaderos, espacios, vehiculos, reservas, admin, propietario, operacion, tareas, comprobantes,
)
app.include_router(auth.router)
app.include_router(parqueaderos.router)
app.include_router(espacios.router)
app.include_router(vehiculos.router)
app.include_router(reservas.router)
app.include_router(admin.router)
app.include_router(propietario.router)
app.include_router(operacion.router)
app.include_router(tareas.router)
app.include_router(comprobantes.router)
