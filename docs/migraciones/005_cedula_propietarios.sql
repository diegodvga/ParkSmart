-- =====================================================================
-- ParkSmart · Migración 005: número de cédula de los usuarios
-- Ejecutar UNA SOLA VEZ, después de 003 (004 no cambia tablas)
-- =====================================================================
-- Qué cambia:
--   usuarios.cedula: número de cédula como TEXTO de solo dígitos (no
--   como número: una cédula no se suma ni se promedia, y así no se
--   pierden ceros a la izquierda).
--
-- Compatible con los datos existentes:
--   - La columna es opcional (NULL): los propietarios ya creados quedan
--     sin cédula y el admin la completa después desde Gestión de usuarios.
--   - La obligatoriedad para los propietarios NUEVOS la aplica el backend
--     (schema PropietarioCrear). No se hace con un CHECK porque la base no
--     distingue una cuenta nueva de una anterior.
-- =====================================================================

BEGIN;

ALTER TABLE usuarios ADD COLUMN cedula VARCHAR(15);

-- Solo dígitos, de 4 a 15 (NULL pasa el CHECK)
ALTER TABLE usuarios
    ADD CONSTRAINT ck_usuarios_cedula CHECK (cedula ~ '^[0-9]{4,15}$');

COMMIT;
