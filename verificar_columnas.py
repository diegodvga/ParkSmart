"""
Script para verificar las columnas de la tabla parqueaderos.
"""

from app.database import engine
from sqlalchemy import text


def verificar():
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'parqueaderos'
            ORDER BY ordinal_position
        """))
        columnas = [r[0] for r in result]
        print("Columnas de la tabla parqueaderos:")
        for c in columnas:
            print(f"  - {c}")

        # Verificar si existen las columnas de tarifas
        tarifas = ['tarifa_hora_carro', 'tarifa_hora_moto', 'tarifa_hora_bus']
        print("\nVerificación de tarifas:")
        for t in tarifas:
            if t in columnas:
                print(f"  ✓ {t} existe")
            else:
                print(f"  ✗ {t} NO existe")


if __name__ == "__main__":
    verificar()
