-- =====================================================================
-- ParkSmart · Migración 002: DIAGNÓSTICO (solo lectura)
-- Ejecutar ANTES de 003_agenda_ocupacion_pagos.sql
-- =====================================================================
-- Este script NO modifica nada: solo consulta. Cada bloque devuelve las
-- filas que romperían una restricción nueva de la migración 003.
-- Si todos los bloques vuelven vacíos (salvo el 0 y el 4, que son
-- informativos), la migración 003 se puede aplicar sin ajustar datos.
--
-- En el SQL Editor de Supabase se muestra solo el resultado de la última
-- consulta: selecciona un bloque y ejecútalo por separado.
-- =====================================================================

BEGIN READ ONLY;

-- ---------------------------------------------------------------------
-- 0. Informativo: extensiones que necesita la 003 y conteos
-- ---------------------------------------------------------------------
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name IN ('btree_gist', 'pg_cron', 'pg_net')
ORDER BY name;

SELECT
    (SELECT count(*) FROM usuarios)     AS usuarios,
    (SELECT count(*) FROM parqueaderos) AS parqueaderos,
    (SELECT count(*) FROM espacios)     AS espacios,
    (SELECT count(*) FROM vehiculos)    AS vehiculos,
    (SELECT count(*) FROM reservas)     AS reservas;

-- ---------------------------------------------------------------------
-- 1. Reservas con horas inválidas (no se podrían convertir a inicio/fin)
-- ---------------------------------------------------------------------
SELECT id, fecha, hora_entrada, hora_salida
FROM reservas
WHERE hora_entrada !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
   OR hora_salida  !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
   OR hora_salida <= hora_entrada;

-- ---------------------------------------------------------------------
-- 2. Reservas activas que se cruzan en el mismo espacio respetando el
--    margen de 15 minutos (el EXCLUDE de la 003 las rechazaría).
--    Hay que cancelar una de cada par antes de migrar.
-- ---------------------------------------------------------------------
WITH activas AS (
    SELECT
        id,
        espacio_id,
        (fecha::date + hora_entrada::time) AS inicio,
        (fecha::date + hora_salida::time) + INTERVAL '15 minutes' AS fin_con_margen
    FROM reservas
    WHERE estado IN ('pendiente', 'confirmada')
)
SELECT a.espacio_id, a.id AS reserva_a, b.id AS reserva_b,
       a.inicio AS inicio_a, b.inicio AS inicio_b
FROM activas a
JOIN activas b
  ON a.espacio_id = b.espacio_id
 AND a.id < b.id
 AND a.inicio < b.fin_con_margen
 AND b.inicio < a.fin_con_margen
ORDER BY a.espacio_id, a.id;

-- ---------------------------------------------------------------------
-- 3. Espacios marcados 'ocupado' por el flujo viejo (al reservar se
--    marcaba ocupado). Desde la 003 la ocupación física sale de la tabla
--    estancias, que nace vacía: estos espacios deberían volver a
--    'disponible'. La 003 NO los cambia; pide permiso antes de hacerlo.
-- ---------------------------------------------------------------------
SELECT e.id, e.parqueadero_id, e.numero, e.tipo
FROM espacios e
WHERE e.estado = 'ocupado';

-- ---------------------------------------------------------------------
-- 4. Informativo: capacidad declarada vs. espacios reales.
--    Desde la 003 la capacidad operativa se calcula con los espacios.
-- ---------------------------------------------------------------------
SELECT p.id, p.nombre, p.capacidad, count(e.id) AS espacios_reales
FROM parqueaderos p
LEFT JOIN espacios e ON e.parqueadero_id = p.id
GROUP BY p.id, p.nombre, p.capacidad
HAVING p.capacidad <> count(e.id)
ORDER BY p.id;

-- ---------------------------------------------------------------------
-- 5. Informativo: códigos de espacio que no siguen el formato nuevo
--    (M01, C01, B01...). No se renumeran: la secuencia nueva los ignora.
-- ---------------------------------------------------------------------
SELECT id, parqueadero_id, numero, tipo
FROM espacios
WHERE numero !~ '^[MCB][0-9]{2,}$'
ORDER BY parqueadero_id, id;

-- ---------------------------------------------------------------------
-- 6. Reservas en estados que no existirán como tales (debe volver vacío;
--    los estados actuales son un subconjunto de los nuevos).
-- ---------------------------------------------------------------------
SELECT id, estado
FROM reservas
WHERE estado NOT IN ('pendiente', 'confirmada', 'en_curso',
                     'completada', 'excedida', 'cancelada');

ROLLBACK;
