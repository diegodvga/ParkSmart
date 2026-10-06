"""
Script para cambiar el enum tipoespacio a mayúsculas (CARRO, MOTO).
"""

from app.database import engine
from sqlalchemy import text


def cambiar_a_mayusculas():
    """
    Cambia el enum tipoespacio a valores en mayúsculas.
    """
    print("Cambiando enum tipoespacio a mayúsculas...")

    with engine.connect() as conn:
        # Paso 1: Convertir la columna a VARCHAR temporalmente
        print("  Paso 1: Convirtiendo columna a VARCHAR...")
        conn.execute(text("""
            ALTER TABLE espacios ALTER COLUMN tipo TYPE VARCHAR(10)
        """))
        print("  ✓ Columna convertida a VARCHAR")

        # Paso 2: Actualizar los valores existentes a mayúsculas
        print("  Paso 2: Actualizando valores a mayúsculas...")
        conn.execute(text("""
            UPDATE espacios SET tipo = UPPER(tipo)
        """))
        print("  ✓ Valores actualizados a mayúsculas")

        # Paso 3: Eliminar el enum antiguo
        print("  Paso 3: Eliminando enum antiguo...")
        conn.execute(text("""
            DROP TYPE IF EXISTS tipoespacio
        """))
        print("  ✓ Enum antiguo eliminado")

        # Paso 4: Crear el nuevo enum con valores en mayúsculas
        print("  Paso 4: Creando nuevo enum con mayúsculas...")
        conn.execute(text("""
            CREATE TYPE tipoespacio AS ENUM ('CARRO', 'MOTO')
        """))
        print("  ✓ Nuevo enum creado con 'CARRO' y 'MOTO'")

        # Paso 5: Asignar el nuevo enum a la columna
        print("  Paso 5: Asignando nuevo enum a la columna...")
        conn.execute(text("""
            ALTER TABLE espacios ALTER COLUMN tipo TYPE tipoespacio USING tipo::tipoespacio
        """))
        print("  ✓ Columna asignada al nuevo enum")

        conn.commit()
        print("\n¡Cambio completado!")
        print("El enum ahora tiene valores en mayúsculas: 'CARRO', 'MOTO'")


if __name__ == "__main__":
    cambiar_a_mayusculas()
