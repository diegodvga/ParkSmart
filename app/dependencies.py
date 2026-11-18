"""
Dependencias de autorización para ParkSmart.

Este módulo centraliza "quién puede entrar a qué":
- get_usuario_actual: devuelve el usuario en sesión o redirige al login.
- requiere_rol(*roles): solo deja pasar a los roles indicados (si no, 403).
- obtener_parqueadero_autorizado: verifica que un propietario solo toque
  SUS parqueaderos (el admin_global puede tocarlos todos).

Se usan con Depends() de FastAPI, por ejemplo:

    @router.get("/admin")
    async def dashboard(usuario: Usuario = Depends(requiere_rol(RolUsuario.ADMIN_GLOBAL))):
        ...
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Parqueadero, ParqueaderoOperario, RolUsuario, Usuario


def get_usuario_actual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """
    Devuelve el usuario que inició sesión.

    - Si no hay sesión, redirige al login (303 + cabecera Location).
    - Si el usuario ya no existe o fue suspendido (activo = False),
      limpia la sesión y redirige al login.
    - Actualiza el rol guardado en la sesión con el de la base de datos,
      así un cambio de rol (por ejemplo a admin_global) se aplica sin
      tener que cerrar sesión.
    """
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    usuario = db.get(Usuario, usuario_id)
    if not usuario or not usuario.activo:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    request.session["usuario_rol"] = usuario.rol.value
    return usuario


def requiere_rol(*roles: RolUsuario):
    """
    Crea una dependencia que solo permite el acceso a los roles indicados.

    Ejemplo: Depends(requiere_rol(RolUsuario.PROPIETARIO, RolUsuario.ADMIN_GLOBAL))

    Returns:
        Función dependencia que devuelve el usuario si su rol está permitido
        o lanza un 403 si no lo está.
    """
    roles_permitidos = {rol.value for rol in roles}

    def verificar_rol(usuario: Usuario = Depends(get_usuario_actual)) -> Usuario:
        if usuario.rol.value not in roles_permitidos:
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado. No tienes permiso para ver esta página."
            )
        return usuario

    return verificar_rol


def obtener_parqueadero_autorizado(db: Session, parqueadero_id: int, usuario: Usuario) -> Parqueadero:
    """
    Busca un parqueadero y verifica que el usuario pueda gestionarlo.

    - 404 si el parqueadero no existe.
    - 403 si el usuario es propietario y el parqueadero NO es suyo.
    - El admin_global puede gestionar cualquier parqueadero.

    Esta es la regla de seguridad más importante del marketplace:
    un propietario nunca puede ver ni modificar lo de otro.
    """
    parqueadero = db.get(Parqueadero, parqueadero_id)
    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")

    es_admin = usuario.rol == RolUsuario.ADMIN_GLOBAL
    if not es_admin and parqueadero.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Este parqueadero no te pertenece")

    return parqueadero


def puede_operar(db: Session, parqueadero: Parqueadero, usuario: Usuario) -> bool:
    """
    True si el usuario puede registrar entradas y salidas en el parqueadero.

    - Propietario: solo en SUS parqueaderos.
    - Operario: solo donde el propietario lo asignó y la asignación está activa.
    """
    if usuario.rol == RolUsuario.PROPIETARIO:
        return parqueadero.propietario_id == usuario.id
    if usuario.rol == RolUsuario.OPERARIO:
        return db.query(ParqueaderoOperario).filter(
            ParqueaderoOperario.parqueadero_id == parqueadero.id,
            ParqueaderoOperario.usuario_id == usuario.id,
            ParqueaderoOperario.activo.is_(True),
        ).first() is not None
    return False


def obtener_parqueadero_operable(db: Session, parqueadero_id: int, usuario: Usuario) -> Parqueadero:
    """
    Busca un parqueadero y verifica que el usuario pueda operarlo.

    - 404 si no existe.
    - 403 si no es el propietario ni un operario asignado: cambiar el id en
      la URL no da acceso a otro parqueadero.
    """
    parqueadero = db.get(Parqueadero, parqueadero_id)
    if not parqueadero:
        raise HTTPException(status_code=404, detail="Parqueadero no encontrado")
    if not puede_operar(db, parqueadero, usuario):
        raise HTTPException(status_code=403, detail="No puedes operar este parqueadero")
    return parqueadero
