"""
Script para verificar que todo esté correcto.
"""

from app.database import engine
from sqlalchemy import text


def verificar():
    print("=== VERIFICACIÓN COMPLETA ===\n")

    with engine.connect() as conn:
        # 1. Verificar columnas de parqueaderos
        result = conn.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'parqueaderos' 
            ORDER BY ordinal_position
        """))
        columnas = [r[0] for r in result]
        print("1. Columnas de parqueaderos:")
        for c in columnas:
            print(f"   - {c}")

        if 'tarifa_hora_bus' in columnas:
            print("   ⚠️  ADVERTENCIA: tarifa_hora_bus aún existe")
        else:
            print("   ✅ tarifa_hora_bus fue eliminada")

        # 2. Verificar columnas de espacios
        result = conn.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'espacios' 
            ORDER BY ordinal_position
        """))
        columnas_espacios = [r[0] for r in result]
        print("\n2. Columnas de espacios:")
        for c in columnas_espacios:
            print(f"   - {c}")

        # 3. Verificar enum de espacios
        result = conn.execute(text("""
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
            WHERE pg_type.typname = 'tipoespacio'
            ORDER BY pg_enum.enumsortorder
        """))
        valores_enum = [r[0] for r in result]
        print(f"\n3. Enum tipoespacio: {valores_enum}")

        # 4. Contar registros
        result = conn.execute(text("SELECT COUNT(*) FROM parqueaderos"))
        print(f"\n4. Parqueaderos: {result.scalar()}")

        result = conn.execute(text("SELECT COUNT(*) FROM espacios"))
        print(f"   Espacios: {result.scalar()}")

        result = conn.execute(text("SELECT COUNT(*) FROM usuarios"))
        print(f"   Usuarios: {result.scalar()}")

        result = conn.execute(text("SELECT COUNT(*) FROM vehiculos"))
        print(f"   Vehículos: {result.scalar()}")

        result = conn.execute(text("SELECT COUNT(*) FROM reservas"))
        print(f"   Reservas: {result.scalar()}")

    print("\n=== FIN DE LA VERIFICACIÓN ===")


if __name__ == "__main__":
    verificar()
