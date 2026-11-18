# ParkSmart · Contexto para Claude Code

## 1. Contexto del proyecto

ParkSmart es un sistema web de gestión de parqueaderos y reservas. Fue diseñado para un solo negocio con un Administrador Global y se está evolucionando a una plataforma **multi-propietario (tipo marketplace)**:

- Los **propietarios** registran y administran SUS propios parqueaderos.
- Los **clientes** ven y reservan en los parqueaderos activos de toda la ciudad.
- El **admin_global** aprueba o rechaza parqueaderos y supervisa la plataforma.

**Stack (NO se cambia):** FastAPI, Jinja2, SQLAlchemy, Supabase (PostgreSQL), HTML/CSS/JS, Chart.js, desplegado en Vercel.

El usuario está aprendiendo: explicar cada cambio de forma didáctica (qué se cambió, por qué y qué pasaría si no se hiciera).

## 2. Estado de la base de datos

- Base de datos NUEVA en Supabase, creada desde cero con el script `docs/parksmart_schema.sql`. No hay datos que migrar.
- La `DATABASE_URL` está en `.env` y usa el **Transaction pooler** de Supabase (puerto 6543).
- **La fuente de verdad del esquema es `docs/parksmart_schema.sql`.** Los modelos de SQLAlchemy deben coincidir exactamente con ese SQL.

Puntos clave del esquema:

- Los "enums" se guardan como `VARCHAR` + `CHECK`, NO como tipos ENUM nativos de PostgreSQL.
- Roles: `cliente`, `propietario`, `admin_global` (antes `usuario` y `administrador`).
- `parqueaderos.propietario_id` → FK a `usuarios.id` (obligatorio).
- Estados de parqueadero: `pendiente`, `activo`, `inactivo`, `rechazado`. Un parqueadero nuevo nace `pendiente` hasta que el admin_global lo aprueba.
- Columnas nuevas: `usuarios.telefono`, `usuarios.activo`, `parqueaderos.barrio`, `ciudad`, `latitud`, `longitud`, `fecha_creacion`.
- Tarifas y total son `NUMERIC(10,2)`.

### Decisiones de conexión (no cambiar sin hablarlo)

- **`docs/parksmart_schema.sql` es la fuente de verdad del esquema.** Ya fue ejecutado en Supabase; NO se vuelve a ejecutar. Las tablas NO se crean con `Base.metadata.create_all()`.
- Driver: **psycopg 3** (`psycopg[binary]==3.2.3`), NO `psycopg2-binary`. `app/database.py` normaliza `postgres://` y `postgresql://` a `postgresql+psycopg://`.
- `create_engine(..., poolclass=NullPool, connect_args={"prepare_threshold": None, "sslmode": "require"})`.
  - `NullPool`: Vercel es serverless y el Transaction pooler de Supabase ya reutiliza conexiones.
  - `prepare_threshold: None`: psycopg 3 no usa prepared statements; el pooler en modo transacción no los soporta (error `prepared statement "_pg3_0" already exists`).
- Enums en modelos: `Enum(..., native_enum=False, values_callable=..., validate_strings=True)` vía el helper `columna_enum()` de `app/models.py`, con `length` igual al `VARCHAR(n)` del SQL.
- Dinero (`tarifa_hora_*`, `total`) y coordenadas: `Numeric(..., asdecimal=False)` para que en Python sean `float`.
- Verificación del esquema: `venv\Scripts\python.exe scripts\verificar_modelos.py` (compara columnas/FK y prueba inserción con rollback).

### Hechos verificados del repositorio (2026-11-13)

- Python 3.13, Pydantic v2 (`pydantic[email]==2.9.2`), SQLAlchemy 2.0.35, FastAPI 0.115.0.
- En la raíz hay scripts sueltos de la versión anterior (`crear_tablas.py`, `migrar_*.py`, `corregir_enum.py`, `limpiar_datos.py`, etc.) pensados para el esquema viejo. Varios ejecutan ALTER/DROP/DELETE: **no ejecutarlos** contra la base nueva.

## 3. Reglas de trabajo (aplican a TODAS las etapas)

1. Trabajar **una etapa a la vez**. Al terminar una etapa, detenerse, resumir lo hecho y **esperar confirmación** antes de seguir.
2. Antes de modificar, **leer los archivos reales del proyecto** (no asumir su contenido). Si algo no coincide con el plan, avisar antes de cambiarlo.
3. **No cambiar el stack** ni agregar librerías nuevas sin preguntar.
4. **Nunca mostrar ni imprimir el contenido de `.env`** ni contraseñas. `.env` debe estar en `.gitignore`.
5. **No ejecutar nada que borre o modifique datos o tablas** en Supabase (DROP, DELETE, TRUNCATE, ALTER) sin pedir permiso. Las pruebas de escritura van dentro de una transacción con `rollback`.
6. No hacer commits ni push sin que el usuario lo pida.
7. Mantener los comentarios y docstrings en español, con el mismo estilo de los archivos actuales.
8. Al final de cada etapa entregar: lista de archivos modificados, cómo probarlo y qué podría fallar.

## 4. Plan de etapas

- **ETAPA 1: Modelos, schemas y conexión.** `models.py`, `schemas.py` y `database.py` alineados con `docs/parksmart_schema.sql`; script `scripts/verificar_modelos.py` (comparación de columnas + inserción con rollback); inventario de usos de roles/estados viejos. No se tocan routers ni templates.
- **ETAPA 2: Autenticación y roles.** Registro público solo para clientes (las cuentas de propietario las crea el `admin_global` desde Admin → Usuarios → "Crear propietario", con número de cédula obligatorio y una contraseña inicial; las contraseñas las cambia el `admin_global` desde Gestión de usuarios); dependencias de FastAPI tipo `requiere_rol(...)`; reemplazar los roles viejos en todo el proyecto (usando el inventario de la Etapa 1); redirección según rol después del login; bloqueo de usuarios con `activo = False`. Instrucciones para convertir una cuenta en `admin_global` con un `UPDATE` en Supabase.
- **ETAPA 3: Panel del propietario.** CRUD de SUS parqueaderos y espacios, filtrando SIEMPRE por `propietario_id` del usuario en sesión (un propietario nunca puede ver ni editar lo de otro). Reservas de sus parqueaderos y estadísticas con Chart.js.
- **ETAPA 4: Panel del admin_global.** Aprobar o rechazar parqueaderos pendientes, activar o suspender usuarios, estadísticas globales con Chart.js.
- **ETAPA 5: Vista del cliente.** Listado de parqueaderos `activo` de toda la ciudad con filtros por barrio y tipo de vehículo, detalle, reserva y "mis reservas".
- **ETAPA 6: Diseño y pulido.** Unificar el diseño de los templates Jinja2 (layout base, navegación según rol, responsive), revisión de seguridad y prueba final del despliegue en Vercel.

## 5. Estructura del proyecto

```
app/
  main.py          # App FastAPI, SessionMiddleware, /static, include_router
  database.py      # engine, SessionLocal, Base, get_db
  models.py        # Modelos SQLAlchemy
  schemas.py       # Schemas Pydantic
  auth.py          # Hash de contraseñas y helpers de sesión/JWT
  routers/         # auth, parqueaderos, espacios, vehiculos, reservas, admin
  templates/       # Jinja2 (base.html, vistas públicas y admin/)
  static/          # css/style.css, js/main.js
*.py (raíz)        # Scripts utilitarios del esquema anterior
vercel.json        # Entrada: app/main.py
```
