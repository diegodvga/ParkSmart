-- =====================================================================
-- ParkSmart · Migración 004: tarea programada del protocolo de retraso
-- Ejecutar UNA SOLA VEZ, DESPUÉS de desplegar en Vercel la versión que
-- tiene la ruta POST /tareas/retrasos y la variable TAREAS_CLAVE.
-- =====================================================================
-- Qué hace:
--   Cada minuto, Supabase (pg_cron) llama a POST /tareas/retrasos de la
--   app (pg_net) con la cabecera X-Tarea-Clave. La app aplica el protocolo
--   de retraso: recordatorios, excesos, alertas y reasignaciones. La tarea
--   es idempotente: si corre dos veces, no duplica nada.
--
-- Por qué pg_cron y no Vercel Cron: en el plan Hobby de Vercel el cron
-- corre solo una vez por día (±59 min); el protocolo necesita minutos.
--
-- ANTES de ejecutar este archivo (paso manual, NO se guarda en Git):
--   1. Generar una clave larga y aleatoria y ponerla en Vercel como
--      variable de entorno TAREAS_CLAVE (y en .env local si se prueba).
--   2. Guardar la URL de la app y la clave en Supabase Vault (SQL Editor):
--
--      select vault.create_secret('https://TU-APP.vercel.app', 'parksmart_url_app');
--      select vault.create_secret('LA_MISMA_CLAVE_DE_TAREAS_CLAVE', 'parksmart_tareas_clave');
--
-- Nota de seguridad (documentación de Supabase): mientras una petición
-- espera en net.http_request_queue, un rol que pueda iniciar sesión en la
-- base podría leer la cabecera. ParkSmart solo usa el rol postgres.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Extensiones (sintaxis de la guía de Supabase Cron)
-- ---------------------------------------------------------------------
create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

create extension if not exists pg_net with schema extensions;

-- ---------------------------------------------------------------------
-- 2. Programar la tarea cada minuto
--    Si ya existía (se ejecutó antes), se reemplaza para no duplicarla.
-- ---------------------------------------------------------------------
select cron.unschedule(jobid)
from cron.job
where jobname = 'parksmart-retrasos';

select cron.schedule(
    'parksmart-retrasos',
    '* * * * *',   -- cada minuto
    $$
    select net.http_post(
        url := (select decrypted_secret from vault.decrypted_secrets
                where name = 'parksmart_url_app') || '/tareas/retrasos',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'X-Tarea-Clave', (select decrypted_secret from vault.decrypted_secrets
                              where name = 'parksmart_tareas_clave')
        ),
        body := '{}'::jsonb
    ) as request_id;
    $$
);

-- ---------------------------------------------------------------------
-- 3. Verificación (solo lectura)
-- ---------------------------------------------------------------------
-- Tarea programada:
--   select jobid, jobname, schedule, active from cron.job where jobname = 'parksmart-retrasos';
-- Últimas ejecuciones:
--   select status, start_time, return_message from cron.job_run_details
--   where jobid = (select jobid from cron.job where jobname = 'parksmart-retrasos')
--   order by start_time desc limit 5;
-- Respuestas de la app (código 200 = bien, 401 = clave distinta, 503 = falta TAREAS_CLAVE):
--   select status_code, content from net._http_response order by created desc limit 5;
--
-- Para detenerla:
--   select cron.unschedule('parksmart-retrasos');
-- =====================================================================
