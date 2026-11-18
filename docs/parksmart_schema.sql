-- =====================================================================
-- ParkSmart · Esquema multi-propietario (marketplace)
-- Ejecutar UNA SOLA VEZ en: Supabase → SQL Editor → New query → Run
-- Base de datos: PostgreSQL (Supabase)
-- Este archivo refleja el estado ACTUAL del esquema, incluidas las
-- migraciones de docs/migraciones/. En una base que ya existe, ejecutar
-- solo las migraciones pendientes, no este archivo.
-- La migración 004 (tarea programada con pg_cron) NO cambia tablas: en
-- una instalación nueva se ejecuta aparte, después del despliegue.
-- =====================================================================
-- Decisión de diseño: los "enums" se guardan como VARCHAR + CHECK
-- (no como tipos ENUM nativos de PostgreSQL). Así SQLAlchemy los maneja
-- con Enum(..., native_enum=False) y agregar un valor nuevo en el futuro
-- es solo cambiar un CHECK, sin ALTER TYPE.
-- =====================================================================

BEGIN;

-- Migración 003: btree_gist permite el EXCLUDE de reservas (espacio = y
-- rango &&). En Supabase las extensiones van en el esquema "extensions".
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;

-- ---------------------------------------------------------------------
-- 1. USUARIOS
--    Cambio clave: rol ahora es cliente | propietario | operario | admin_global
-- ---------------------------------------------------------------------
CREATE TABLE usuarios (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(100) NOT NULL,
    correo          VARCHAR(150) NOT NULL UNIQUE,
    password        VARCHAR(255) NOT NULL,                 -- hash, nunca texto plano
    telefono        VARCHAR(20),                           -- NUEVO: contacto del propietario/cliente
    rol             VARCHAR(20)  NOT NULL DEFAULT 'cliente',
    activo          BOOLEAN      NOT NULL DEFAULT TRUE,    -- NUEVO: suspender cuentas sin borrarlas
    fecha_registro  TIMESTAMP    NOT NULL DEFAULT NOW(),
    cedula          VARCHAR(15),                           -- Migración 005: solo dígitos; obligatoria para propietarios nuevos (backend)
    CONSTRAINT ck_usuarios_cedula CHECK (cedula ~ '^[0-9]{4,15}$'),
    CONSTRAINT ck_usuarios_rol
        CHECK (rol IN ('cliente', 'propietario', 'operario', 'admin_global'))   -- 'operario': migración 003
);

-- ---------------------------------------------------------------------
-- 2. PARQUEADEROS
--    Cambio clave: propietario_id → usuarios.id
--    Estado incluye 'pendiente' y 'rechazado' (aprobación del admin_global)
-- ---------------------------------------------------------------------
CREATE TABLE parqueaderos (
    id                 SERIAL PRIMARY KEY,
    propietario_id     INTEGER       NOT NULL
                       REFERENCES usuarios(id) ON DELETE RESTRICT,   -- NUEVO
    nombre             VARCHAR(100)  NOT NULL,
    direccion          VARCHAR(200)  NOT NULL,
    barrio             VARCHAR(100),                                  -- NUEVO
    ciudad             VARCHAR(100)  NOT NULL DEFAULT 'Santa Marta',  -- NUEVO
    latitud            NUMERIC(9,6),                                  -- NUEVO (opcional, para mapa)
    longitud           NUMERIC(9,6),                                  -- NUEVO (opcional, para mapa)
    descripcion        TEXT,
    capacidad          INTEGER       NOT NULL,
    -- Migración 001: tipos de vehículo aceptados; la tarifa solo existe
    -- para los tipos aceptados (si no se acepta, queda en NULL)
    acepta_carros      BOOLEAN       NOT NULL DEFAULT TRUE,
    acepta_motos       BOOLEAN       NOT NULL DEFAULT TRUE,
    acepta_buses       BOOLEAN       NOT NULL DEFAULT FALSE,
    tarifa_hora_carro  NUMERIC(10,2),
    tarifa_hora_moto   NUMERIC(10,2),
    tarifa_hora_bus    NUMERIC(10,2),                                 -- Migración 001
    horario_apertura   VARCHAR(5)    NOT NULL,   -- "08:00"
    horario_cierre     VARCHAR(5)    NOT NULL,   -- "20:00"
    estado             VARCHAR(20)   NOT NULL DEFAULT 'pendiente',
    fecha_creacion     TIMESTAMP     NOT NULL DEFAULT NOW(),          -- NUEVO
    -- Migración 003: reglas operativas y de cobro
    margen_minutos       SMALLINT      NOT NULL DEFAULT 15,   -- tiempo libre entre reservas
    gracia_minutos       SMALLINT      NOT NULL DEFAULT 5,    -- exceso que no se cobra
    umbral_aviso_minutos SMALLINT      NOT NULL DEFAULT 60,   -- aviso de reserva próxima en walk-in
    recargo_exceso_pct   NUMERIC(5,2)  NOT NULL DEFAULT 50,   -- 50 = hora extra a 1.5x
    impuesto_pct         NUMERIC(5,2)  NOT NULL DEFAULT 0,
    pago_anticipado      BOOLEAN       NOT NULL DEFAULT FALSE, -- TRUE: la reserva nace pendiente
    CONSTRAINT ck_parqueaderos_estado
        CHECK (estado IN ('pendiente', 'activo', 'inactivo', 'rechazado')),
    CONSTRAINT ck_parqueaderos_capacidad   CHECK (capacidad > 0),
    CONSTRAINT ck_parqueaderos_tarifas
        CHECK (tarifa_hora_carro >= 0 AND tarifa_hora_moto >= 0 AND tarifa_hora_bus >= 0),
    CONSTRAINT ck_parqueaderos_algun_vehiculo
        CHECK (acepta_carros OR acepta_motos OR acepta_buses),
    CONSTRAINT ck_parqueaderos_tarifa_segun_tipo
        CHECK (
            acepta_carros = (tarifa_hora_carro IS NOT NULL)
            AND acepta_motos = (tarifa_hora_moto IS NOT NULL)
            AND acepta_buses = (tarifa_hora_bus IS NOT NULL)
        ),
    CONSTRAINT ck_parqueaderos_apertura    CHECK (horario_apertura ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    CONSTRAINT ck_parqueaderos_cierre      CHECK (horario_cierre   ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    CONSTRAINT ck_parqueaderos_margen         CHECK (margen_minutos IN (10, 15)),
    CONSTRAINT ck_parqueaderos_gracia         CHECK (gracia_minutos BETWEEN 0 AND 60),
    CONSTRAINT ck_parqueaderos_umbral_aviso   CHECK (umbral_aviso_minutos BETWEEN 0 AND 240),
    CONSTRAINT ck_parqueaderos_recargo_exceso CHECK (recargo_exceso_pct BETWEEN 0 AND 300),
    CONSTRAINT ck_parqueaderos_impuesto       CHECK (impuesto_pct BETWEEN 0 AND 100)
);

CREATE INDEX ix_parqueaderos_propietario_id ON parqueaderos (propietario_id);
CREATE INDEX ix_parqueaderos_estado         ON parqueaderos (estado);
CREATE INDEX ix_parqueaderos_ciudad_barrio  ON parqueaderos (ciudad, barrio);

-- ---------------------------------------------------------------------
-- 3. ESPACIOS
-- ---------------------------------------------------------------------
CREATE TABLE espacios (
    id              SERIAL PRIMARY KEY,
    parqueadero_id  INTEGER     NOT NULL
                    REFERENCES parqueaderos(id) ON DELETE CASCADE,
    numero          VARCHAR(10) NOT NULL,
    tipo            VARCHAR(10) NOT NULL,
    estado          VARCHAR(20) NOT NULL DEFAULT 'disponible',
    CONSTRAINT ck_espacios_tipo   CHECK (tipo IN ('carro', 'moto', 'bus')),   -- 'bus': migración 001
    CONSTRAINT ck_espacios_estado CHECK (estado IN ('disponible', 'ocupado', 'mantenimiento')),
    -- NUEVO: no puede haber dos espacios "A-01" en el mismo parqueadero
    CONSTRAINT uq_espacios_parqueadero_numero UNIQUE (parqueadero_id, numero),
    -- Migración 003: permite FK compuestas (espacio del MISMO parqueadero)
    CONSTRAINT uq_espacios_id_parqueadero UNIQUE (id, parqueadero_id)
);

CREATE INDEX ix_espacios_parqueadero_id ON espacios (parqueadero_id);

-- ---------------------------------------------------------------------
-- 4. VEHICULOS
-- ---------------------------------------------------------------------
CREATE TABLE vehiculos (
    id          SERIAL PRIMARY KEY,
    usuario_id  INTEGER     NOT NULL
                REFERENCES usuarios(id) ON DELETE CASCADE,
    placa       VARCHAR(10) NOT NULL UNIQUE,
    tipo        VARCHAR(10) NOT NULL,
    marca       VARCHAR(50),
    CONSTRAINT ck_vehiculos_tipo CHECK (tipo IN ('carro', 'moto', 'bus'))   -- 'bus': migración 001
);

CREATE INDEX ix_vehiculos_usuario_id ON vehiculos (usuario_id);

-- ---------------------------------------------------------------------
-- 5. RESERVAS (agenda)
--    Migración 003: inicio/fin como instantes (TIMESTAMPTZ), margen y
--    tarifa congelados, y un EXCLUDE que impide en PostgreSQL que dos
--    reservas activas de la misma plaza se crucen (margen incluido).
--    fecha/hora_entrada/hora_salida son columnas viejas, opcionales,
--    que se eliminarán cuando el código use solo inicio/fin.
-- ---------------------------------------------------------------------

-- Calcula bloqueo = [inicio, fin + margen). No puede ser columna generada
-- porque "timestamptz + interval" no es IMMUTABLE en PostgreSQL.
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

CREATE TABLE reservas (
    id             SERIAL PRIMARY KEY,
    usuario_id     INTEGER       NOT NULL
                   REFERENCES usuarios(id)  ON DELETE CASCADE,
    vehiculo_id    INTEGER       NOT NULL
                   REFERENCES vehiculos(id) ON DELETE RESTRICT,
    espacio_id     INTEGER       NOT NULL
                   REFERENCES espacios(id)  ON DELETE RESTRICT,
    fecha          TIMESTAMP,                -- viejo
    hora_entrada   VARCHAR(5),               -- viejo: "08:00"
    hora_salida    VARCHAR(5),               -- viejo: "10:00"
    estado         VARCHAR(20)   NOT NULL DEFAULT 'pendiente',
    total          NUMERIC(10,2) NOT NULL,
    fecha_reserva  TIMESTAMP     NOT NULL DEFAULT NOW(),
    inicio              TIMESTAMPTZ   NOT NULL,
    fin                 TIMESTAMPTZ   NOT NULL,
    fin_original        TIMESTAMPTZ,              -- fin antes de la primera extensión
    margen_minutos      SMALLINT      NOT NULL,   -- copia del margen al confirmar
    tarifa_hora         NUMERIC(10,2) NOT NULL,   -- copia de la tarifa al confirmar
    bloqueo             TSTZRANGE     NOT NULL,   -- lo calcula trg_reservas_bloqueo
    pendiente_expira_en TIMESTAMPTZ,              -- hasta cuándo aparta una pendiente
    exceso_desde        TIMESTAMPTZ,              -- inicio del tiempo extra
    cancelada_en        TIMESTAMPTZ,
    motivo_cancelacion  VARCHAR(30),
    CONSTRAINT ck_reservas_total CHECK (total >= 0),
    CONSTRAINT ck_reservas_horas CHECK (hora_salida > hora_entrada),
    CONSTRAINT ck_reservas_estado
        CHECK (estado IN ('pendiente', 'confirmada', 'en_curso',
                          'completada', 'excedida', 'cancelada')),
    CONSTRAINT ck_reservas_intervalo CHECK (fin > inicio),
    CONSTRAINT ck_reservas_margen CHECK (margen_minutos IN (10, 15)),
    CONSTRAINT ck_reservas_tarifa CHECK (tarifa_hora >= 0),
    CONSTRAINT ck_reservas_motivo_cancelacion
        CHECK (motivo_cancelacion IN ('cliente', 'propietario', 'admin',
                                      'pendiente_vencida', 'sin_alternativa')),
    -- LA REGLA CENTRAL: en una misma plaza no pueden existir dos reservas
    -- activas cuyos rangos [inicio, fin + margen) se crucen.
    CONSTRAINT ex_reservas_espacio_bloqueo
        EXCLUDE USING gist (espacio_id WITH =, bloqueo WITH &&)
        WHERE (estado IN ('pendiente', 'confirmada', 'en_curso', 'excedida'))
);

CREATE TRIGGER trg_reservas_bloqueo
    BEFORE INSERT OR UPDATE OF inicio, fin, margen_minutos, bloqueo
    ON reservas
    FOR EACH ROW
    EXECUTE FUNCTION reservas_calcular_bloqueo();

CREATE INDEX ix_reservas_usuario_id  ON reservas (usuario_id);
CREATE INDEX ix_reservas_espacio_id  ON reservas (espacio_id);
CREATE INDEX ix_reservas_vehiculo_id ON reservas (vehiculo_id);
CREATE INDEX ix_reservas_fecha       ON reservas (fecha);
CREATE INDEX ix_reservas_inicio      ON reservas (inicio);
CREATE INDEX ix_reservas_estado_fin  ON reservas (estado, fin);

-- ---------------------------------------------------------------------
-- 6. OPERARIOS (migración 003): qué operario trabaja en qué parqueadero
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
-- 7. ESTANCIAS (migración 003): ocupación física real, con reserva o
--    ingreso directo (el conductor no necesita cuenta)
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
-- Una reserva tiene como máximo una estancia
CREATE UNIQUE INDEX uq_estancias_reserva
    ON estancias (reserva_id) WHERE reserva_id IS NOT NULL;
-- La misma placa no puede estar dos veces dentro del mismo parqueadero
CREATE UNIQUE INDEX uq_estancias_placa_activa
    ON estancias (parqueadero_id, placa) WHERE salida_en IS NULL;
CREATE INDEX ix_estancias_parqueadero_entrada ON estancias (parqueadero_id, entrada_en);

-- ---------------------------------------------------------------------
-- 8. INCIDENCIAS (migración 003). UNIQUE (reserva_id, tipo) hace
--    idempotentes los reintentos de la tarea programada.
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
-- 9. REASIGNACIONES (migración 003): historial de cambios de plaza
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
-- 10. NOTIFICACIONES (migración 003, solo dentro de la app)
--     "clave" única (ej. 'recordatorio_fin:42'): un reintento no duplica.
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
-- 11. CUENTAS (migración 003): una por operación, desglose congelado.
--     Saldo = total - pagos pagados + reembolsos pagados.
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
    liquidada_en   TIMESTAMPTZ,   -- NOT NULL = la salida ya se liquidó
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
-- 12. PAGOS (migración 003). clave_idempotencia única: repetir un
--     intento o una confirmación de la pasarela no duplica el cobro.
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
    CONSTRAINT uq_pagos_id_cuenta UNIQUE (id, cuenta_id),   -- migración 006
    CONSTRAINT ck_pagos_tipo
        CHECK (tipo IN ('anticipado', 'en_sitio', 'extra', 'reembolso')),
    CONSTRAINT ck_pagos_metodo
        CHECK (metodo IN ('efectivo', 'tarjeta', 'transferencia', 'pasarela')),
    CONSTRAINT ck_pagos_estado CHECK (estado IN ('pendiente', 'pagado', 'fallido')),
    CONSTRAINT ck_pagos_monto  CHECK (monto > 0)
);

CREATE INDEX ix_pagos_cuenta_id ON pagos (cuenta_id);

-- ---------------------------------------------------------------------
-- 13. NUMERACIÓN Y COMPROBANTES (migración 003)
--     INSERT ... ON CONFLICT DO UPDATE SET ultimo = ultimo + 1 RETURNING
--     bloquea la fila: dos operaciones simultáneas no repiten número.
-- ---------------------------------------------------------------------
CREATE TABLE secuencias_comprobante (
    parqueadero_id INTEGER PRIMARY KEY REFERENCES parqueaderos(id) ON DELETE CASCADE,
    ultimo         INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT ck_secuencias_comprobante_ultimo CHECK (ultimo >= 0)
);

-- Una factura por cada cobro (migración 006): pago_id con valor = factura
-- del pago anticipado; pago_id NULL = factura de cierre al salir (una por
-- cuenta, índice parcial uq_comprobantes_cierre).
CREATE TABLE comprobantes (
    id             SERIAL PRIMARY KEY,
    parqueadero_id INTEGER     NOT NULL,
    cuenta_id      INTEGER     NOT NULL,
    pago_id        INTEGER,                -- migración 006
    numero         INTEGER     NOT NULL,
    datos          JSONB       NOT NULL,   -- copia completa: el historial no cambia
    emitido_en     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_comprobantes_cuenta_parqueadero
        FOREIGN KEY (cuenta_id, parqueadero_id)
        REFERENCES cuentas (id, parqueadero_id) ON DELETE RESTRICT,
    CONSTRAINT fk_comprobantes_pago_cuenta
        FOREIGN KEY (pago_id, cuenta_id)
        REFERENCES pagos (id, cuenta_id) ON DELETE RESTRICT,
    CONSTRAINT uq_comprobantes_parqueadero_numero UNIQUE (parqueadero_id, numero),
    CONSTRAINT uq_comprobantes_pago UNIQUE (pago_id),
    CONSTRAINT ck_comprobantes_numero CHECK (numero > 0)
);

CREATE UNIQUE INDEX uq_comprobantes_cierre ON comprobantes (cuenta_id) WHERE pago_id IS NULL;
CREATE INDEX ix_comprobantes_cuenta ON comprobantes (cuenta_id);

-- ---------------------------------------------------------------------
-- 14. SEGURIDAD EN SUPABASE (Row Level Security)
--    Tu backend FastAPI se conecta con el usuario "postgres", que ignora
--    RLS, así que la app sigue funcionando igual. Activarlo bloquea el
--    acceso directo a las tablas desde la API pública de Supabase
--    (clave anon), que ParkSmart no usa.
-- ---------------------------------------------------------------------
ALTER TABLE usuarios     ENABLE ROW LEVEL SECURITY;
ALTER TABLE parqueaderos ENABLE ROW LEVEL SECURITY;
ALTER TABLE espacios     ENABLE ROW LEVEL SECURITY;
ALTER TABLE vehiculos    ENABLE ROW LEVEL SECURITY;
ALTER TABLE reservas     ENABLE ROW LEVEL SECURITY;
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

-- =====================================================================
-- 7. PRIMER ADMIN GLOBAL (ejecutar DESPUÉS, por separado)
--    1) Regístrate normalmente desde la app con tu correo (quedas como 'cliente').
--    2) Ejecuta esto en el SQL Editor cambiando el correo:
--
-- UPDATE usuarios SET rol = 'admin_global' WHERE correo = 'tu_correo@ejemplo.com';
--
--    Así la contraseña queda hasheada por tu propia app y ningún
--    formulario público permite elegir el rol admin_global.
-- =====================================================================
