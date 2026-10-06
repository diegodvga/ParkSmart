"""
Script para migrar la tabla de espacios y agregar 'bus' como tipo válido.
"""

from app.database import engine
from sqlalchemy import text


def migrar():
    """
    Elimina la restricción CHECK antigua y agrega una nueva que incluye 'bus'.
    """
    print("Migrando tabla de espacios...")

    with engine.connect() as conn:
        # Buscar y eliminar restricciones CHECK existentes
        result = conn.execute(text("""
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'espacios'::regclass
            AND contype = 'c'
        """))

        restricciones = [r[0] for r in result]

        for restriccion in restricciones:
            conn.execute(text(f"""
                ALTER TABLE espacios
                DROP CONSTRAINT {restriccion}
            """))
            print(f"  - Restricción {restriccion} eliminada")

        # Agregar nueva restricción CHECK que incluye 'bus'
        conn.execute(text("""
            ALTER TABLE espacios
            ADD CONSTRAINT espacios_tipo_check
            CHECK (tipo IN ('carro', 'moto', 'bus'))
        """))
        print("  - Nueva restricción agregada: tipo IN ('carro', 'moto', 'bus')")

        conn.commit()
        print("\n¡Migración completada!")
        print("Ahora pueden crearse espacios de tipo 'carro', 'moto' y 'bus'.")


if __name__ == "__main__":
    migrar()
