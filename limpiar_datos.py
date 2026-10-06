"""
Script para limpiar todos los datos de las tablas.
CUIDADO: Esto eliminará todos los datos existentes.

Ejecutar con: python limpiar_datos.py
"""

from app.database import SessionLocal
from app.models import Reserva, Vehiculo, Espacio, Parqueadero, Usuario


def limpiar():
    """
    Elimina todos los datos de las tablas.
    """
    db = SessionLocal()

    try:
        print("=== LIMPIANDO DATOS ===\n")

        # Eliminar en orden correcto (respetando claves foráneas)
        print("1. Eliminando reservas...")
        db.query(Reserva).delete()
        db.commit()
        print("   ✓ Reservas eliminadas")

        print("\n2. Eliminando vehículos...")
        db.query(Vehiculo).delete()
        db.commit()
        print("   ✓ Vehículos eliminados")

        print("\n3. Eliminando espacios...")
        db.query(Espacio).delete()
        db.commit()
        print("   ✓ Espacios eliminados")

        print("\n4. Eliminando parqueaderos...")
        db.query(Parqueadero).delete()
        db.commit()
        print("   ✓ Parqueaderos eliminados")

        print("\n5. Eliminando usuarios (excepto admin)...")
        db.query(Usuario).filter(Usuario.correo != "admin@parksmart.com").delete()
        db.commit()
        print("   ✓ Usuarios eliminados")

        print("\n=== LIMPIEZA COMPLETADA ===")
        print("\nAhora puedes ejecutar: python insertar_datos.py")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    limpiar()
