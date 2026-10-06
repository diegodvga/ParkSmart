"""
Script para crear las tablas en la base de datos Supabase PostgreSQL.

Ejecutar con: python crear_tablas.py

Este script:
1. Importa todos los modelos SQLAlchemy
2. Crea las tablas en la base de datos si no existen
3. Si ya existen, no las modifica (no borra datos)
"""

from app.database import Base, engine
from app.models import Usuario, Parqueadero, Espacio, Vehiculo, Reserva


def crear_tablas():
    """
    Crea todas las tablas definidas en los modelos SQLAlchemy.
    """
    print("Conectando con la base de datos...")
    print(f"URL: {engine.url}")

    print("\nCreando tablas...")
    Base.metadata.create_all(bind=engine)

    print("\nTablas creadas exitosamente:")
    print("  - usuarios")
    print("  - parqueaderos")
    print("  - espacios")
    print("  - vehiculos")
    print("  - reservas")

    print("\nListo! Las tablas están disponibles en Supabase.")


if __name__ == "__main__":
    crear_tablas()
