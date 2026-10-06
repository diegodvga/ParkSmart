"""
Script para diagnosticar problemas con la tabla espacios.
"""

from app.database import engine
from sqlalchemy import text


def diagnosticar():
    print("=== DIAGNÓSTICO DE LA TABLA ESPACIOS ===\n")

    with engine.connect() as conn:
        # 1. Verificar columnas
        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'espacios'
            ORDER BY ordinal_position
        """))
        print("1. Columnas de la tabla espacios:")
        for r in result:
            print(f"   - {r[0]}: {r[1]}")

        # 2. Verificar enum
        result = conn.execute(text("""
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
            WHERE pg_type.typname = 'tipoespacio'
            ORDER BY pg_enum.enumsortorder
        """))
        valores = [r[0] for r in result]
        print(f"\n2. Valores del enum tipoespacio: {valores}")

        # 3. Verificar espacios existentes
        result = conn.execute(text("""
            SELECT id, numero, tipo, estado
            FROM espacios
            LIMIT 10
        """))
        print("\n3. Espacios existentes:")
        for r in result:
            print(f"   - ID: {r[0]}, Número: {r[1]}, Tipo: {r[2]}, Estado: {r[3]}")

        # 4. Verificar parqueaderos
        result = conn.execute(text("""
            SELECT id, nombre, estado
            FROM parqueaderos
        """))
        print("\n4. Parqueaderos:")
        for r in result:
            print(f"   - ID: {r[0]}, Nombre: {r[1]}, Estado: {r[2]}")

    print("\n=== FIN DEL DIAGNÓSTICO ===")


if __name__ == "__main__":
    diagnosticar()
