"""
Script para actualizar el enum de PostgreSQL y agregar 'bus' como valor válido.
"""

from app.database import engine
from sqlalchemy import text


def migrar():
    """
    Agrega 'bus' al enum de PostgreSQL para la columna tipo de espacios.
    """
    print("Actualizando enum de PostgreSQL...")

    with engine.connect() as conn:
        # Verificar el enum actual
        result = conn.execute(text("""
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
            WHERE pg_type.typname = 'tipoespacio'
            ORDER BY pg_enum.enumsortorder
        """))

        valores_actuales = [r[0] for r in result]
        print(f"  Valores actuales: {valores_actuales}")

        # Agregar 'bus' al enum
        if 'bus' not in valores_actuales:
            conn.execute(text("""
                ALTER TYPE tipoespacio
                ADD VALUE 'bus'
            """))
            print("  - Valor 'bus' agregado al enum")
        else:
            print("  - El valor 'bus' ya existe en el enum")

        conn.commit()
        print("\n¡Migración completada!")
        print("Ahora pueden crearse espacios de tipo 'carro', 'moto' y 'bus'.")


if __name__ == "__main__":
    migrar()
