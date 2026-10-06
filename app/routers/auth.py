"""
Router de autenticación para ParkSmart.

Rutas:
- GET  /login      → Mostrar formulario de inicio de sesión
- POST /login      → Procesar inicio de sesión
- GET  /registro   → Mostrar formulario de registro
- POST /registro   → Procesar registro de usuario
- GET  /logout     → Cerrar sesión
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.models import Usuario, RolUsuario
from app.auth import hash_password, verify_password, create_access_token
from app.schemas import UsuarioCrear, UsuarioLogin

# Configurar templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="",
    tags=["autenticacion"]
)


@router.get("/login", response_class=HTMLResponse)
async def mostrar_login(request: Request):
    """
    Muestra la página de inicio de sesión.
    """
    # Si ya está autenticado, redirigir al inicio
    if request.session.get("usuario_id"):
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "titulo": "Iniciar Sesión - ParkSmart"}
    )


@router.post("/login")
async def procesar_login(
    request: Request,
    correo: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Procesa el inicio de sesión.

    1. Busca el usuario por correo
    2. Verifica la contraseña
    3. Crea el token y guarda en sesión
    4. Redirige al inicio
    """
    # Buscar usuario por correo
    usuario = db.query(Usuario).filter(Usuario.correo == correo).first()

    # Verificar que existe y la contraseña es correcta
    if not usuario or not verify_password(password, usuario.password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "titulo": "Iniciar Sesión - ParkSmart",
                "error": "Correo o contraseña incorrectos"
            },
            status_code=401
        )

    # Guardar datos del usuario en la sesión
    request.session["usuario_id"] = usuario.id
    request.session["usuario_nombre"] = usuario.nombre
    request.session["usuario_correo"] = usuario.correo
    request.session["usuario_rol"] = usuario.rol.value

    return RedirectResponse(url="/", status_code=302)


@router.get("/registro", response_class=HTMLResponse)
async def mostrar_registro(request: Request):
    """
    Muestra la página de registro de usuario.
    """
    # Si ya está autenticado, redirigir al inicio
    if request.session.get("usuario_id"):
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(
        "registro.html",
        {"request": request, "titulo": "Registro - ParkSmart"}
    )


@router.post("/registro")
async def procesar_registro(
    request: Request,
    nombre: str = Form(...),
    correo: str = Form(...),
    password: str = Form(...),
    confirmar_password: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Procesa el registro de un nuevo usuario.

    1. Valida que las contraseñas coincidan
    2. Verifica que el correo no esté registrado
    3. Crea el usuario con hash de contraseña
    4. Redirige al login
    """
    # Validar que las contraseñas coincidan
    if password != confirmar_password:
        return templates.TemplateResponse(
            "registro.html",
            {
                "request": request,
                "titulo": "Registro - ParkSmart",
                "error": "Las contraseñas no coinciden"
            },
            status_code=400
        )

    # Verificar que el correo no esté registrado
    usuario_existente = db.query(Usuario).filter(Usuario.correo == correo).first()
    if usuario_existente:
        return templates.TemplateResponse(
            "registro.html",
            {
                "request": request,
                "titulo": "Registro - ParkSmart",
                "error": "Este correo ya está registrado"
            },
            status_code=400
        )

    # Crear nuevo usuario
    nuevo_usuario = Usuario(
        nombre=nombre,
        correo=correo,
        password=hash_password(password),
        rol=RolUsuario.USUARIO,
        fecha_registro=datetime.now()
    )

    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)

    # Redirigir al login con mensaje de éxito
    return RedirectResponse(
        url="/login?registro=exitoso",
        status_code=302
    )


@router.get("/logout")
async def cerrar_sesion(request: Request):
    """
    Cierra la sesión del usuario y redirige al inicio.
    """
    # Limpiar todos los datos de la sesión
    request.session.clear()

    return RedirectResponse(url="/", status_code=302)



