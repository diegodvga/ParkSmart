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
from pydantic import ValidationError
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

# Página a la que va cada rol después de iniciar sesión
INICIO_POR_ROL = {
    RolUsuario.CLIENTE: "/parqueaderos",
    RolUsuario.PROPIETARIO: "/propietario/panel",
    RolUsuario.OPERARIO: "/operacion",
    RolUsuario.ADMIN_GLOBAL: "/admin",
}

# Mensajes claros (en español) para los errores de validación del registro
MENSAJES_REGISTRO = {
    "nombre": "El nombre debe tener entre 2 y 100 caracteres.",
    "correo": "El correo electrónico no es válido.",
    "password": "La contraseña debe tener al menos 6 caracteres.",
    "confirmar_password": "La contraseña debe tener al menos 6 caracteres.",
    "telefono": "El teléfono es obligatorio y solo puede tener números, espacios, guiones y un + inicial (7 a 20 caracteres).",
    "rol": "Solo puedes registrarte como cliente. Las cuentas de propietario de parqueadero las crea el administrador.",
}


def mensaje_error_registro(error: ValidationError) -> str:
    """
    Convierte el primer error de Pydantic en un mensaje legible.

    Los errores de un campo usan MENSAJES_REGISTRO; los errores generales
    (por ejemplo, contraseñas distintas) usan el texto del validador.
    """
    primer_error = error.errors()[0]
    campo = primer_error["loc"][0] if primer_error["loc"] else None
    if campo in MENSAJES_REGISTRO:
        return MENSAJES_REGISTRO[campo]
    return primer_error["msg"].removeprefix("Value error, ")


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
    3. Bloquea a los usuarios suspendidos (activo = False)
    4. Guarda los datos en sesión
    5. Redirige según el rol
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

    # Un usuario suspendido no puede iniciar sesión
    if not usuario.activo:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "titulo": "Iniciar Sesión - ParkSmart",
                "error": "Tu cuenta está suspendida. Contacta al administrador."
            },
            status_code=403
        )

    # Guardar datos del usuario en la sesión
    request.session["usuario_id"] = usuario.id
    request.session["usuario_nombre"] = usuario.nombre
    request.session["usuario_correo"] = usuario.correo
    request.session["usuario_rol"] = usuario.rol.value

    # Cada rol empieza en su propia página
    return RedirectResponse(url=INICIO_POR_ROL.get(usuario.rol, "/"), status_code=302)


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
    rol: str = Form(""),
    telefono: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Procesa el registro de un nuevo usuario.

    1. Valida los datos con el schema UsuarioCrear. El registro público
       solo crea clientes: si un formulario manipulado envía 'propietario'
       o 'admin_global', se rechaza. Los propietarios los crea el admin_global.
    2. Verifica que el correo no esté registrado
    3. Crea el usuario con hash de contraseña
    4. Redirige al login
    """
    # Validar todos los datos (rol, correo, contraseñas) con Pydantic
    try:
        datos = UsuarioCrear(
            nombre=nombre,
            correo=correo,
            password=password,
            confirmar_password=confirmar_password,
            # El formulario ya no envía rol: vacío = cliente
            rol=rol or "cliente",
            telefono=telefono
        )
    except ValidationError as error:
        return templates.TemplateResponse(
            "registro.html",
            {
                "request": request,
                "titulo": "Registro - ParkSmart",
                "error": mensaje_error_registro(error)
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
    # El rol viene ya validado: siempre 'cliente'
    nuevo_usuario = Usuario(
        nombre=datos.nombre,
        correo=correo,
        password=hash_password(datos.password),
        telefono=datos.telefono,
        rol=RolUsuario(datos.rol),
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




