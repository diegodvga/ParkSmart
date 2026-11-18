-- =====================================================================
-- ParkSmart · Migración 001: tipos de vehículo por parqueadero y buses
-- Ejecutar UNA SOLA VEZ, después de docs/parksmart_schema.sql
-- =====================================================================
-- Qué cambia:
--   1. Cada parqueadero indica qué vehículos acepta (carros, motos, buses).
--   2. Nueva tarifa por hora para buses.
--   3. Las tarifas solo existen para los tipos aceptados: si un parqueadero
--      no acepta motos, tarifa_hora_moto queda en NULL (y viceversa).
--   4. Espacios y vehículos admiten el tipo 'bus'.
--
-- La ciudad NO necesita cambios: la columna parqueaderos.ciudad ya existe.
-- La lista de ciudades se valida en la aplicación (app/schemas.py) para
-- poder ampliarla sin tocar la base de datos.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. PARQUEADEROS: tipos de vehículo aceptados y tarifa de buses
-- ---------------------------------------------------------------------
ALTER TABLE parqueaderos
    ADD COLUMN acepta_carros   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN acepta_motos    BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN acepta_buses    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN tarifa_hora_bus NUMERIC(10,2),
    ALTER COLUMN tarifa_hora_carro DROP NOT NULL,
    ALTER COLUMN tarifa_hora_moto  DROP NOT NULL;

-- La regla vieja exigía tarifa de carro y moto; ahora cada una es opcional
ALTER TABLE parqueaderos DROP CONSTRAINT ck_parqueaderos_tarifas;

ALTER TABLE parqueaderos
    -- Ninguna tarifa puede ser negativa (NULL pasa el CHECK)
    ADD CONSTRAINT ck_parqueaderos_tarifas
        CHECK (tarifa_hora_carro >= 0 AND tarifa_hora_moto >= 0 AND tarifa_hora_bus >= 0),
    -- Un parqueadero debe aceptar al menos un tipo de vehículo
    ADD CONSTRAINT ck_parqueaderos_algun_vehiculo
        CHECK (acepta_carros OR acepta_motos OR acepta_buses),
    -- Hay tarifa si y solo si el tipo es aceptado
    ADD CONSTRAINT ck_parqueaderos_tarifa_segun_tipo
        CHECK (
            acepta_carros = (tarifa_hora_carro IS NOT NULL)
            AND acepta_motos = (tarifa_hora_moto IS NOT NULL)
            AND acepta_buses = (tarifa_hora_bus IS NOT NULL)
        );

-- ---------------------------------------------------------------------
-- 2. ESPACIOS y VEHICULOS: nuevo tipo 'bus'
-- ---------------------------------------------------------------------
ALTER TABLE espacios DROP CONSTRAINT ck_espacios_tipo;
ALTER TABLE espacios
    ADD CONSTRAINT ck_espacios_tipo CHECK (tipo IN ('carro', 'moto', 'bus'));

ALTER TABLE vehiculos DROP CONSTRAINT ck_vehiculos_tipo;
ALTER TABLE vehiculos
    ADD CONSTRAINT ck_vehiculos_tipo CHECK (tipo IN ('carro', 'moto', 'bus'));

COMMIT;
