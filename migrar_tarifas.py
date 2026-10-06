"""
Script para migrar la tabla de parqueaderos con tarifas separadas.

Ejecutar con: python migrar_tarifas.py
"""

from app.database import engine
from sqlalchemy import text


def migrar():
    """
    Agrega las columnas tarifa_hora_carro, tarifa_hora_moto y tarifa_hora_bus.
    """
    print("Migrando tabla de parqueaderos...")

    with engine.connect() as conn:
        # Verificar si la columna tarifa_hora existe (estructura antigua)
        result = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'parqueaderos' AND column_name = 'tarifa_hora'
        """))

        if result.fetchone():
            # Renombrar tarifa_hora a tarifa_hora_carro
            conn.execute(text("""
                ALTER TABLE parqueaderos
                RENAME COLUMN tarifa_hora TO tarifa_hora_carro
            """))
            print("  - Columna tarifa_hora renombrada a tarifa_hora_carro")

            # Agregar tarifa_hora_moto (70% del carro)
            conn.execute(text("""
                ALTER TABLE parqueaderos
                ADD COLUMN tarifa_hora_moto FLOAT NOT NULL DEFAULT 0
            """))
            print("  - Columna tarifa_hora_moto agregada")

            conn.execute(text("""
                UPDATE parqueaderos
                SET tarifa_hora_moto = tarifa_hora_carro * 0.7
            """))
            print("  - Tarifas de moto calculadas (70% del carro)")

            # Agregar tarifa_hora_bus (150% del carro)
            conn.execute(text("""
                ALTER TABLE parqueaderos
                ADD COLUMN tarifa_hora_bus FLOAT NOT NULL DEFAULT 0
            """))
            print("  - Columna tarifa_hora_bus agregada")

            conn.execute(text("""
                UPDATE parqueaderos
                SET tarifa_hora_bus = tarifa_hora_carro * 1.5
            """))
            print("  - Tarifas de bus calculadas (150% del carro)")

            conn.commit()
            print("\n¡Migración completada!")
        else:
            # Verificar si ya existen las columnas nuevas
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'parqueaderos' AND column_name = 'tarifa_hora_carro'
            """))

            if result.fetchone():
                print("Las columnas ya existen. No se necesita migración.")
            else:
                print("ERROR: No se encontró la columna tarifa_hora.")
                print("Es posible que la estructura de la tabla sea diferente.")


if __name__ == "__main__":
    migrar()
