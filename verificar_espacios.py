"""
Script para verificar la estructura de la tabla espacios.
"""

from app.database import engine
from sqlalchemy import text


def verificar():
    with engine.connect() as conn:
        # Verificar columnas
        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'espacios'
            ORDER BY ordinal_position
        """))
        print("Columnas de la tabla espacios:")
        for r in result:
            print(f"  - {r[0]}: {r[1]}")

        # Verificar restricciones CHECK
        result = conn.execute(text("""
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'espacios'::regclass
            AND contype = 'c'
        """))
        print("\nRestricciones CHECK:")
        for r in result:
            print(f"  - {r[0]}: {r[1]}")


if __name__ == "__main__":
    verificar()
