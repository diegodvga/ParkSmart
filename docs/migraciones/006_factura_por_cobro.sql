-- =====================================================================
-- ParkSmart · Migración 006: una factura por cada cobro
-- Ejecutar UNA SOLA VEZ, después de 005
-- =====================================================================
-- Antes: un solo comprobante por operación (cuenta), emitido al salir.
-- Ahora: el pago anticipado (pasarela o caja) tiene su propia factura en
-- el momento del pago, y al salir se emite una factura de CIERRE solo por
-- los cobros que todavía no estaban facturados (por ejemplo, el tiempo
-- extra).
--
-- Qué cambia:
--   comprobantes.pago_id  → el pago que factura (NULL = factura de cierre).
--   Se quita UNIQUE (cuenta_id): una cuenta puede tener varias facturas.
--   UNIQUE (pago_id)                 → un pago no se factura dos veces.
--   Índice único parcial de cierre   → una sola factura de cierre por cuenta.
--   FK (pago_id, cuenta_id) → pagos  → el pago pertenece a la MISMA cuenta.
--
-- Compatible con los datos existentes: los comprobantes ya emitidos
-- quedan con pago_id NULL (son facturas de cierre) y ya eran únicos por
-- cuenta, así que el índice parcial se crea sin conflicto.
-- =====================================================================

BEGIN;

ALTER TABLE comprobantes DROP CONSTRAINT comprobantes_cuenta_id_key;

-- Necesaria para la FK compuesta de abajo (el id ya es único; el par también)
ALTER TABLE pagos
    ADD CONSTRAINT uq_pagos_id_cuenta UNIQUE (id, cuenta_id);

ALTER TABLE comprobantes ADD COLUMN pago_id INTEGER;

ALTER TABLE comprobantes
    ADD CONSTRAINT uq_comprobantes_pago UNIQUE (pago_id);

-- MATCH SIMPLE (por defecto): con pago_id NULL la FK no se verifica
ALTER TABLE comprobantes
    ADD CONSTRAINT fk_comprobantes_pago_cuenta
        FOREIGN KEY (pago_id, cuenta_id)
        REFERENCES pagos (id, cuenta_id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX uq_comprobantes_cierre
    ON comprobantes (cuenta_id) WHERE pago_id IS NULL;

-- Las facturas se buscan por cuenta (antes lo cubría el UNIQUE quitado)
CREATE INDEX ix_comprobantes_cuenta ON comprobantes (cuenta_id);

COMMIT;
