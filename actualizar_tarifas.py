"""
Script para actualizar la tabla de parqueaderos con tarifas separadas.

Este script:
1. Agrega la columna tarifa_hora_moto si no existe
2. Copia el valor de tarifa_hora a tarifa_hora_carro
3. Establece tarifa_hora_moto como el 70% de tarifa_hora_carro

Ejecutar con: python actualizar_tarifas.py
"""

from app.database import engine
from sqlalchemy import text


def actualizar_tarifas():
    """
    Actualiza la tabla de parqueaderos con tarifas separadas para carro y moto.
    """
    print("Actualizando tarifas de parqueaderos...")

    with engine.connect() as conn:
        # Verificar si la columna tarifa_hora_moto existe
        result = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'parqueaderos' AND column_name = 'tarifa_hora_moto'
        """))

        if result.fetchone():
            print("La columna tarifa_hora_moto ya existe.")
        else:
            # Agregar columna tarifa_hora_moto
            conn.execute(text("""
                ALTER TABLE parqueaderos
                ADD COLUMN tarifa_hora_moto FLOAT NOT NULL DEFAULT 0
            """))
            print("Columna tarifa_hora_moto agregada.")

        # Verificar si la columna tarifa_hora_carro existe
        result = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'parqueaderos' AND column_name = 'tarifa_hora_carro'
        """))

        if result.fetchone():
            print("La columna tarifa_hora_carro ya existe.")
        else:
            # Renombrar tarifa_hora a tarifa_hora_carro
            conn.execute(text("""
                ALTER TABLE parqueaderos
                RENAME COLUMN tarifa_hora TO tarifa_hora_carro
            """))
            print("Columna tarifa_hora renombrada a tarifa_hora_carro.")

        # Actualizar valores: tarifa_hora_moto = 70% de tarifa_hora_carro
        conn.execute(text("""
            UPDATE parqueaderos
            SET tarifa_hora_moto = tarifa_hora_carro * 0.7
            WHERE tarifa_hora_moto = 0
        """))
        print("Tarifas de moto actualizadas al 70% de la tarifa de carro.")

        conn.commit()

    print("\n¡Actualización completada!")
    print("Los parqueaderos ahora tienen tarifas separadas para carros y motos.")


if __name__ == "__main__":
    actualizar_tarifas()
