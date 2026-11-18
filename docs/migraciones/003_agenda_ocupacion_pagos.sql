-- =====================================================================
-- ParkSmart · Migración 003: agenda con margen, ocupación física,
-- operarios, incidencias, notificaciones, pagos y comprobantes
-- Ejecutar UNA SOLA VEZ, después de 001 y de revisar 002_diagnostico.sql
-- =====================================================================
-- Ideas clave:
--   1. AGENDA y OCUPACIÓN FÍSICA son cosas distintas:
--      - reservas  = lo que está programado (agenda).
--      - estancias = el vehículo que está dentro AHORA (ocupación real).
--   2. PostgreSQL impide que dos reservas activas de la misma plaza se
--      crucen, incluido el margen operativo, con una restricción EXCLUDE.
--      Así ni dos peticiones simultáneas pueden saltarse la regla.
--   3. Un índice único parcial impide que dos vehículos ocupen la misma
--      plaza a la vez (dos check-in simultáneos: uno falla).
--   4. Las columnas nuevas de fecha y hora son TIMESTAMPTZ (instante
--      exacto). La zona horaria del negocio es America/Bogota.
--
-- Si falla cualquier paso, el ROLLBACK automático deja todo como estaba.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. Extensión btree_gist: permite combinar "=" sobre un entero
--    (espacio_id) con "&&" (se cruzan) sobre un rango en un mismo EXCLUDE.
--    En Supabase las extensiones van en el esquema "extensions".
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;

-- ---------------------------------------------------------------------
-- 1. USUARIOS: nuevo rol 'operario' (registra entradas y salidas en los
--    parqueaderos que el propietario le asigne)
-- ---------------------------------------------------------------------
ALTER TABLE usuarios DROP CONSTRAINT ck_usuarios_rol;
ALTER TABLE usuarios
    ADD CONSTRAINT ck_usuarios_rol
        CHECK (rol IN ('cliente', 'propietario', 'operario', 'admin_global'));

-- ---------------------------------------------------------------------
-- 2. PARQUEADEROS: reglas operativas y de cobro configurables
--    - margen_minutos: tiempo libre obligatorio entre reservas (10 o 15).
--    - gracia_minutos: minutos de exceso que NO se cobran (regla distinta
--      del margen).
--    - umbral_aviso_minutos: un ingreso directo advierte si la plaza tiene
--      una reserva que empieza dentro de este umbral.
--    - recargo_exceso_pct: recargo sobre la tarifa en tiempo excedido
--      (50 = la hora extra cuesta 1.5 veces la tarifa).
--    - impuesto_pct: impuesto aplicado al subtotal (0 = sin impuesto).
--    - pago_anticipado: si es TRUE la reserva nace 'pendiente' hasta pagar;
--      si es FALSE nace 'confirmada'.
-- ---------------------------------------------------------------------
ALTER TABLE parqueaderos
    ADD COLUMN margen_minutos       SMALLINT      NOT NULL DEFAULT 15,
    ADD COLUMN gracia_minutos       SMALLINT      NOT NULL DEFAULT 5,
    ADD COLUMN umbral_aviso_minutos SMALLINT      NOT NULL DEFAULT 60,
    ADD COLUMN recargo_exceso_pct   NUMERIC(5,2)  NOT NULL DEFAULT 50,
    ADD COLUMN impuesto_pct         NUMERIC(5,2)  NOT NULL DEFAULT 0,
    ADD COLUMN pago_anticipado      BOOLEAN       NOT NULL DEFAULT FALSE;

ALTER TABLE parqueaderos
    ADD CONSTRAINT ck_parqueaderos_margen
        CHECK (margen_minutos IN (10, 15)),
    ADD CONSTRAINT ck_parqueaderos_gracia
        CHECK (gracia_minutos BETWEEN 0 AND 60),
    ADD CONSTRAINT ck_parqueaderos_umbral_aviso
        CHECK (umbral_aviso_minutos BETWEEN 0 AND 240),
    ADD CONSTRAINT ck_parqueaderos_recargo_exceso
        CHECK (recargo_exceso_pct BETWEEN 0 AND 300),
    ADD CONSTRAINT ck_parqueaderos_impuesto
        CHECK (impuesto_pct BETWEEN 0 AND 100);

-- ---------------------------------------------------------------------
-- 3. ESPACIOS: (id, parqueadero_id) único para poder declarar llaves
--    foráneas compuestas. Así la base garantiza que una estancia apunte
--    a un espacio DEL MISMO parqueadero (aislamiento entre propietarios).
-- ---------------------------------------------------------------------
ALTER TABLE espacios
    ADD CONSTRAINT uq_espacios_id_parqueadero UNIQUE (id, parqueadero_id);

-- ---------------------------------------------------------------------
-- 4. RESERVAS: agenda con instantes reales, margen y tarifa congelados
-- ---------------------------------------------------------------------
ALTER TABLE reservas
    ADD COLUMN inicio              TIMESTAMPTZ,
    ADD COLUMN fin                 TIMESTAMPTZ,
    ADD COLUMN fin_original        TIMESTAMPTZ,   -- fin antes de la primera extensión
    ADD COLUMN margen_minutos      SMALLINT,      -- copia del margen al confirmar
    ADD COLUMN tarifa_hora         NUMERIC(10,2), -- copia de la tarifa al confirmar
    ADD COLUMN bloqueo             TSTZRANGE,     -- [inicio, fin + margen), lo calcula un trigger
    ADD COLUMN pendiente_expira_en TIMESTAMPTZ,   -- hasta cuándo aparta una reserva pendiente
    ADD COLUMN exceso_desde        TIMESTAMPTZ,   -- inicio del tiempo extra (estado 'excedida')
    ADD COLUMN cancelada_en        TIMESTAMPTZ,
    ADD COLUMN motivo_cancelacion  VARCHAR(30);

-- Las columnas viejas (fecha + hora como texto) dejan de ser obligatorias.
-- Se conservan mientras el código migra a inicio/fin; una migración
-- posterior las eliminará.
ALTER TABLE reservas
    ALTER COLUMN fecha        DROP NOT NULL,
    ALTER COLUMN hora_entrada DROP NOT NULL,
    ALTER COLUMN hora_salida  DROP NOT NULL;

-- Trigger que calcula el rango bloqueado. No puede ser una columna
-- generada porque "timestamptz + interval" no es IMMUTABLE en PostgreSQL
-- (sumar días depende de la zona horaria).
CREATE FUNCTION reservas_calcular_bloqueo() RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.bloqueo := tstzrange(
        NEW.inicio,
        NEW.fin + make_interval(mins => NEW.margen_minutos),
        '[)'
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_reservas_bloqueo
    BEFORE INSERT OR UPDATE OF inicio, fin, margen_minutos, bloqueo
    ON reservas
    FOR EACH ROW
    EXECUTE FUNCTION reservas_calcular_bloqueo();

-- Completar las reservas existentes (hora local de Colombia → instante).
-- El trigger calcula "bloqueo" en este mismo UPDATE.
UPDATE reservas r
SET inicio = (r.fecha::date + r.hora_entrada::time) AT TIME ZONE 'America/Bogota',
    fin    = (r.fecha::date + r.hora_salida::time)  AT TIME ZONE 'America/Bogota',
    margen_minutos = p.margen_minutos,
    tarifa_hora = round(
        r.total / (extract(epoch FROM (r.hora_salida::time - r.hora_entrada::time)) / 3600),
        2
    )
FROM espacios e
JOIN parqueaderos p ON p.id = e.parqueadero_id
WHERE e.id = r.espacio_id;

ALTER TABLE reservas
    ALTER COLUMN inicio         SET NOT NULL,
    ALTER COLUMN fin            SET NOT NULL,
    ALTER COLUMN margen_minutos SET NOT NULL,
    ALTER COLUMN tarifa_hora    SET NOT NULL,
    ALTER COLUMN bloqueo        SET NOT NULL;

ALTER TABLE reservas DROP CONSTRAINT ck_reservas_estado;
ALTER TABLE reservas
    ADD CONSTRAINT ck_reservas_estado
        CHECK (estado IN ('pendiente', 'confirmada', 'en_curso',
                          'completada', 'excedida', 'cancelada')),
    ADD CONSTRAINT ck_reservas_intervalo CHECK (fin > inicio),
    ADD CONSTRAINT ck_reservas_margen CHECK (margen_minutos IN (10, 15)),
    ADD CONSTRAINT ck_reservas_tarifa CHECK (tarifa_hora >= 0),
    ADD CONSTRAINT ck_reservas_motivo_cancelacion
        CHECK (motivo_cancelacion IN ('cliente', 'propietario', 'admin',
                                      'pendiente_vencida', 'sin_alternativa')),
    -- LA REGLA CENTRAL: en una misma plaza no pueden existir dos reservas
    -- activas cuyos rangos [inicio, fin + margen) se crucen.
    -- Una reserva pendiente también aparta el horario hasta que el código
    -- la cancele por vencimiento (pendiente_expira_en).
    ADD CONSTRAINT ex_reservas_espacio_bloqueo
        EXCLUDE USING gist (espacio_id WITH =, bloqueo WITH &&)
        WHERE (estado IN ('pendiente', 'confirmada', 'en_curso', 'excedida'));

CREATE INDEX ix_reservas_inicio     ON reservas (inicio);
CREATE INDEX ix_reservas_estado_fin ON reservas (estado, fin);

-- ---------------------------------------------------------------------
-- 5. OPERARIOS: qué usuario con rol 'operario' trabaja en qué parqueadero
-- ---------------------------------------------------------------------
CREATE TABLE parqueadero_operarios (
    parqueadero_id   INTEGER   NOT NULL REFERENCES parqueaderos(id) ON DELETE CASCADE,
    usuario_id       INTEGER   NOT NULL REFERENCES usuarios(id)     ON DELETE CASCADE,
    activo           BOOLEAN   NOT NULL DEFAULT TRUE,
    fecha_asignacion TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (parqueadero_id, usuario_id)
);

CREATE INDEX ix_parqueadero_operarios_usuario_id ON parqueadero_operarios (usuario_id);

-- ---------------------------------------------------------------------
-- 6. ESTANCIAS: ocupación física real (con reserva o ingreso directo)
--    El conductor de un ingreso directo no necesita cuenta: placa, tipo,
--    nombre y teléfono se guardan aquí, no en vehiculos.
-- ---------------------------------------------------------------------
CREATE TABLE estancias (
    id                 SERIAL PRIMARY KEY,
    parqueadero_id     INTEGER       NOT NULL,
    espacio_id         INTEGER       NOT NULL,
    reserva_id         INTEGER       REFERENCES reservas(id) ON DELETE RESTRICT,  -- NULL = ingreso directo
    placa              VARCHAR(10)   NOT NULL,
    tipo_vehiculo      VARCHAR(10)   NOT NULL,
    nombre_conductor   VARCHAR(100),
    telefono_conductor VARCHAR(20),
    entrada_en         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    salida_estimada    TIMESTAMPTZ,
    salida_en          TIMESTAMPTZ,                                 -- NULL = sigue dentro
    tarifa_hora        NUMERIC(10,2) NOT NULL,                      -- copia al entrar
    registrado_por     INTEGER       NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    cerrado_por        INTEGER       REFERENCES usuarios(id) ON DELETE RESTRICT,
    -- El espacio debe pertenecer al mismo parqueadero de la estancia
    CONSTRAINT fk_estancias_espacio_parqueadero
        FOREIGN KEY (espacio_id, parqueadero_id)
        REFERENCES espacios (id, parqueadero_id) ON DELETE RESTRICT,
    CONSTRAINT ck_estancias_tipo_vehiculo CHECK (tipo_vehiculo IN ('carro', 'moto', 'bus')),
    CONSTRAINT ck_estancias_tarifa        CHECK (tarifa_hora >= 0),
    CONSTRAINT ck_estancias_salida        CHECK (salida_en IS NULL OR salida_en >= entrada_en),
    CONSTRAINT ck_estancias_salida_estimada
        CHECK (salida_estimada IS NULL OR salida_estimada > entrada_en)
);

-- Dos vehículos no pueden estar a la vez en la misma plaza
CREATE UNIQUE INDEX uq_estancias_espacio_activa
    ON estancias (espacio_id) WHERE salida_en IS NULL;
-- Una reserva tiene como máximo una estancia (repetir el check-in no duplica)
CREATE UNIQUE INDEX uq_estancias_reserva
    ON estancias (reserva_id) WHERE reserva_id IS NOT NULL;
-- La misma placa no puede estar dos veces dentro del mismo parqueadero
CREATE UNIQUE INDEX uq_estancias_placa_activa
    ON estancias (parqueadero_id, placa) WHERE salida_en IS NULL;
CREATE INDEX ix_estancias_parqueadero_entrada ON estancias (parqueadero_id, entrada_en);

-- ---------------------------------------------------------------------
-- 7. INCIDENCIAS: retrasos, excesos, reasignaciones y conflictos
--    UNIQUE (reserva_id, tipo): si la tarea programada corre dos veces,
--    la segunda no duplica la incidencia.
-- ---------------------------------------------------------------------
CREATE TABLE incidencias (
    id             SERIAL PRIMARY KEY,
    parqueadero_id INTEGER     NOT NULL REFERENCES parqueaderos(id) ON DELETE CASCADE,
    reserva_id     INTEGER     REFERENCES reservas(id) ON DELETE CASCADE,
    espacio_id     INTEGER     REFERENCES espacios(id) ON DELETE SET NULL,
    tipo           VARCHAR(20) NOT NULL,
    estado         VARCHAR(20) NOT NULL DEFAULT 'abierta',
    detalle        TEXT,
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resuelto_en    TIMESTAMPTZ,
    CONSTRAINT ck_incidencias_tipo
        CHECK (tipo IN ('exceso', 'retraso', 'reasignacion', 'sin_alternativa')),
    CONSTRAINT ck_incidencias_estado CHECK (estado IN ('abierta', 'resuelta')),
    CONSTRAINT uq_incidencias_reserva_tipo UNIQUE (reserva_id, tipo)
);

CREATE INDEX ix_incidencias_parqueadero_estado ON incidencias (parqueadero_id, estado);

-- ---------------------------------------------------------------------
-- 8. REASIGNACIONES: historial de cambios de plaza de una reserva
-- ---------------------------------------------------------------------
CREATE TABLE reserva_reasignaciones (
    id                 SERIAL PRIMARY KEY,
    reserva_id         INTEGER     NOT NULL REFERENCES reservas(id)    ON DELETE CASCADE,
    espacio_origen_id  INTEGER     NOT NULL REFERENCES espacios(id)    ON DELETE RESTRICT,
    espacio_destino_id INTEGER     NOT NULL REFERENCES espacios(id)    ON DELETE RESTRICT,
    incidencia_id      INTEGER     REFERENCES incidencias(id) ON DELETE SET NULL,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_reasignaciones_distinto CHECK (espacio_origen_id <> espacio_destino_id)
);

CREATE INDEX ix_reserva_reasignaciones_reserva_id ON reserva_reasignaciones (reserva_id);

-- ---------------------------------------------------------------------
-- 9. NOTIFICACIONES (solo dentro de la app)
--    "clave" única: ej. 'recordatorio_fin:42'. Un reintento no duplica.
-- ---------------------------------------------------------------------
CREATE TABLE notificaciones (
    id             SERIAL PRIMARY KEY,
    usuario_id     INTEGER      NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    parqueadero_id INTEGER      REFERENCES parqueaderos(id) ON DELETE CASCADE,
    reserva_id     INTEGER      REFERENCES reservas(id) ON DELETE CASCADE,
    tipo           VARCHAR(30)  NOT NULL,
    prioridad      VARCHAR(10)  NOT NULL DEFAULT 'normal',
    mensaje        TEXT         NOT NULL,
    clave          VARCHAR(100) NOT NULL UNIQUE,
    leida          BOOLEAN      NOT NULL DEFAULT FALSE,
    creado_en      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_notificaciones_tipo
        CHECK (tipo IN ('recordatorio_fin', 'exceso', 'alerta_retraso',
                        'reasignacion', 'sin_alternativa', 'incidencia_resuelta')),
    CONSTRAINT ck_notificaciones_prioridad CHECK (prioridad IN ('normal', 'alta'))
);

CREATE INDEX ix_notificaciones_usuario_leida ON notificaciones (usuario_id, leida);

-- ---------------------------------------------------------------------
-- 10. CUENTAS: una por operación (reserva y/o estancia) con el desglose
--     congelado. Cambiar la tarifa del parqueadero después NO altera esto.
--     El saldo se calcula: total - pagos pagados + reembolsos pagados.
-- ---------------------------------------------------------------------
CREATE TABLE cuentas (
    id             SERIAL PRIMARY KEY,
    parqueadero_id INTEGER       NOT NULL REFERENCES parqueaderos(id) ON DELETE RESTRICT,
    reserva_id     INTEGER       UNIQUE REFERENCES reservas(id)  ON DELETE RESTRICT,
    estancia_id    INTEGER       UNIQUE REFERENCES estancias(id) ON DELETE RESTRICT,
    tarifa_hora    NUMERIC(10,2) NOT NULL,
    minutos_base   INTEGER       NOT NULL DEFAULT 0,
    minutos_extra  INTEGER       NOT NULL DEFAULT 0,
    valor_base     NUMERIC(10,2) NOT NULL DEFAULT 0,
    valor_extra    NUMERIC(10,2) NOT NULL DEFAULT 0,
    penalizacion   NUMERIC(10,2) NOT NULL DEFAULT 0,
    subtotal       NUMERIC(10,2) NOT NULL DEFAULT 0,
    impuesto_pct   NUMERIC(5,2)  NOT NULL DEFAULT 0,
    impuesto       NUMERIC(10,2) NOT NULL DEFAULT 0,
    total          NUMERIC(10,2) NOT NULL DEFAULT 0,
    estado_pago    VARCHAR(20)   NOT NULL DEFAULT 'pendiente',
    liquidada_en   TIMESTAMPTZ,   -- NOT NULL = la salida ya se liquidó (repetirla no recalcula)
    creado_en      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cuentas_id_parqueadero UNIQUE (id, parqueadero_id),
    CONSTRAINT ck_cuentas_operacion CHECK (reserva_id IS NOT NULL OR estancia_id IS NOT NULL),
    CONSTRAINT ck_cuentas_estado_pago
        CHECK (estado_pago IN ('pendiente', 'parcial', 'pagado', 'reembolsado')),
    CONSTRAINT ck_cuentas_valores CHECK (
        tarifa_hora >= 0 AND minutos_base >= 0 AND minutos_extra >= 0
        AND valor_base >= 0 AND valor_extra >= 0 AND penalizacion >= 0
        AND subtotal >= 0 AND impuesto >= 0 AND total >= 0
        AND impuesto_pct BETWEEN 0 AND 100
    )
);

CREATE INDEX ix_cuentas_parqueadero_id ON cuentas (parqueadero_id);

-- ---------------------------------------------------------------------
-- 11. PAGOS: cada movimiento de dinero de una cuenta
--     clave_idempotencia única: repetir el mismo intento (doble clic,
--     reintento de red, confirmación repetida de la pasarela) no duplica.
-- ---------------------------------------------------------------------
CREATE TABLE pagos (
    id                 SERIAL PRIMARY KEY,
    cuenta_id          INTEGER       NOT NULL REFERENCES cuentas(id) ON DELETE RESTRICT,
    tipo               VARCHAR(20)   NOT NULL,
    metodo             VARCHAR(20)   NOT NULL,
    monto              NUMERIC(10,2) NOT NULL,
    estado             VARCHAR(20)   NOT NULL DEFAULT 'pendiente',
    clave_idempotencia VARCHAR(64)   NOT NULL UNIQUE,
    referencia_externa VARCHAR(100)  UNIQUE,
    registrado_por     INTEGER       REFERENCES usuarios(id) ON DELETE SET NULL,
    creado_en          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    confirmado_en      TIMESTAMPTZ,
    CONSTRAINT ck_pagos_tipo
        CHECK (tipo IN ('anticipado', 'en_sitio', 'extra', 'reembolso')),
    CONSTRAINT ck_pagos_metodo
        CHECK (metodo IN ('efectivo', 'tarjeta', 'transferencia', 'pasarela')),
    CONSTRAINT ck_pagos_estado CHECK (estado IN ('pendiente', 'pagado', 'fallido')),
    CONSTRAINT ck_pagos_monto  CHECK (monto > 0)
);

CREATE INDEX ix_pagos_cuenta_id ON pagos (cuenta_id);

-- ---------------------------------------------------------------------
-- 12. NUMERACIÓN DE COMPROBANTES por parqueadero
--     INSERT ... ON CONFLICT DO UPDATE SET ultimo = ultimo + 1 RETURNING
--     bloquea la fila: dos operaciones simultáneas nunca reciben el mismo
--     número.
-- ---------------------------------------------------------------------
CREATE TABLE secuencias_comprobante (
    parqueadero_id INTEGER PRIMARY KEY REFERENCES parqueaderos(id) ON DELETE CASCADE,
    ultimo         INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT ck_secuencias_comprobante_ultimo CHECK (ultimo >= 0)
);

-- ---------------------------------------------------------------------
-- 13. COMPROBANTES: recibo interno, uno por operación (cuenta)
--     "datos" guarda una copia completa (cliente, vehículo, desglose,
--     método de pago) para que el historial no cambie nunca.
-- ---------------------------------------------------------------------
CREATE TABLE comprobantes (
    id             SERIAL PRIMARY KEY,
    parqueadero_id INTEGER     NOT NULL,
    cuenta_id      INTEGER     NOT NULL UNIQUE,
    numero         INTEGER     NOT NULL,
    datos          JSONB       NOT NULL,
    emitido_en     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- La cuenta debe pertenecer al mismo parqueadero del comprobante
    CONSTRAINT fk_comprobantes_cuenta_parqueadero
        FOREIGN KEY (cuenta_id, parqueadero_id)
        REFERENCES cuentas (id, parqueadero_id) ON DELETE RESTRICT,
    CONSTRAINT uq_comprobantes_parqueadero_numero UNIQUE (parqueadero_id, numero),
    CONSTRAINT ck_comprobantes_numero CHECK (numero > 0)
);

-- ---------------------------------------------------------------------
-- 14. Row Level Security en las tablas nuevas (igual que las existentes:
--     bloquea la API pública de Supabase; el backend entra como postgres)
-- ---------------------------------------------------------------------
ALTER TABLE parqueadero_operarios  ENABLE ROW LEVEL SECURITY;
ALTER TABLE estancias              ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidencias            ENABLE ROW LEVEL SECURITY;
ALTER TABLE reserva_reasignaciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE notificaciones         ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas                ENABLE ROW LEVEL SECURITY;
ALTER TABLE pagos                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE secuencias_comprobante ENABLE ROW LEVEL SECURITY;
ALTER TABLE comprobantes           ENABLE ROW LEVEL SECURITY;

COMMIT;
