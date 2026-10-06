"""
Script para corregir el enum tipoespacio y estandarizar a minúsculas.
"""

from app.database import engine
from sqlalchemy import text


def corregir():
    """
    Elimina el enum actual y crea uno nuevo con valores en minúsculas.
    """
    print("Corrigiendo enum tipoespacio...")

    with engine.connect() as conn:
        # Paso 1: Convertir la columna a VARCHAR temporalmente
        print("  Paso 1: Convirtiendo columna a VARCHAR...")
        conn.execute(text("""
            ALTER TABLE espacios ALTER COLUMN tipo TYPE VARCHAR(10)
        """))
        print("  ✓ Columna convertida a VARCHAR")

        # Paso 2: Actualizar los valores existentes a minúsculas
        print("  Paso 2: Actualizando valores a minúsculas...")
        conn.execute(text("""
            UPDATE espacios SET tipo = LOWER(tipo)
        """))
        print("  ✓ Valores actualizados")

        # Paso 3: Eliminar el enum antiguo
        print("  Paso 3: Eliminando enum antiguo...")
        conn.execute(text("""
            DROP TYPE IF EXISTS tipoespacio
        """))
        print("  ✓ Enum antiguo eliminado")

        # Paso 4: Crear el nuevo enum con valores en minúsculas
        print("  Paso 4: Creando nuevo enum...")
        conn.execute(text("""
            CREATE TYPE tipoespacio AS ENUM ('carro', 'moto', 'bus')
        """))
        print("  ✓ Nuevo enum creado")

        # Paso 5: Asignar el nuevo enum a la columna
        print("  Paso 5: Asignando nuevo enum a la columna...")
        conn.execute(text("""
            ALTER TABLE espacios ALTER COLUMN tipo TYPE tipoespacio USING tipo::tipoespacio
        """))
        print("  ✓ Columna asignada al nuevo enum")

        conn.commit()
        print("\n¡Corrección completada!")
        print("El enum ahora tiene valores consistentes: 'carro', 'moto', 'bus'")


if __name__ == "__main__":
    corregir()
