"""
Script para eliminar 'bus' del enum tipoespacio.
"""

from app.database import engine
from sqlalchemy import text


def eliminar_bus():
    """
    Elimina 'bus' del enum tipoespacio.
    """
    print("Eliminando 'bus' del enum tipoespacio...")

    with engine.connect() as conn:
        # Convertir la columna a VARCHAR temporalmente
        conn.execute(text("""
            ALTER TABLE espacios ALTER COLUMN tipo TYPE VARCHAR(10)
        """))
        print("  - Columna convertida a VARCHAR")

        # Eliminar el enum antiguo
        conn.execute(text("""
            DROP TYPE IF EXISTS tipoespacio
        """))
        print("  - Enum antiguo eliminado")

        # Crear el nuevo enum solo con 'carro' y 'moto'
        conn.execute(text("""
            CREATE TYPE tipoespacio AS ENUM ('carro', 'moto')
        """))
        print("  - Nuevo enum creado con 'carro' y 'moto'")

        # Asignar el nuevo enum a la columna
        conn.execute(text("""
            ALTER TABLE espacios ALTER COLUMN tipo TYPE tipoespacio USING tipo::tipoespacio
        """))
        print("  - Columna asignada al nuevo enum")

        conn.commit()
        print("\n¡Eliminación completada!")
        print("El enum ahora solo tiene: 'carro', 'moto'")


if __name__ == "__main__":
    eliminar_bus()
