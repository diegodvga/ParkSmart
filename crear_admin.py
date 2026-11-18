"""
Script para crear un usuario administrador en ParkSmart.

Ejecutar con: python crear_admin.py
"""

from app.database import SessionLocal
from app.models import Usuario, RolUsuario
from app.auth import hash_password
from datetime import datetime


def crear_administrador():
    """
    Crea un usuario administrador si no existe ya.
    """
    db = SessionLocal()

    try:
        # Verificar si ya existe un administrador
        admin_existente = db.query(Usuario).filter(
            Usuario.rol == RolUsuario.ADMIN_GLOBAL
        ).first()

        if admin_existente:
            print("Ya existe un administrador:")
            print(f"  - Nombre: {admin_existente.nombre}")
            print(f"  - Correo: {admin_existente.correo}")
            return

        # Crear nuevo administrador
        nuevo_admin = Usuario(
            nombre="Administrador",
            correo="admin@parksmart.com",
            password=hash_password("admin123"),
            rol=RolUsuario.ADMIN_GLOBAL,
            fecha_registro=datetime.now()
        )

        db.add(nuevo_admin)
        db.commit()
        db.refresh(nuevo_admin)

        print("Administrador creado exitosamente:")
        print(f"  - Nombre: {nuevo_admin.nombre}")
        print(f"  - Correo: {nuevo_admin.correo}")
        print(f"  - Contraseña: admin123")
        print(f"  - Rol: {nuevo_admin.rol.value}")

    finally:
        db.close()


if __name__ == "__main__":
    crear_administrador()
