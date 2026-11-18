"""
Configuración de la base de datos para ParkSmart.

Este módulo se encarga de:
1. Cargar las variables de entorno desde el archivo .env
2. Crear el motor de conexión con SQLAlchemy
3. Crear la sesión para interactuar con la base de datos
4. Proporcionar la función get_db() que usarán los routers

Las tablas NO se crean desde aquí: las crea el script
docs/parksmart_schema.sql, que es la fuente de verdad del esquema.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
import os

# Cargar variables de entorno desde el archivo .env
load_dotenv()

# Obtener la URL de la base de datos desde las variables de entorno
DATABASE_URL = os.getenv("DATABASE_URL")

# Verificar que la URL esté configurada
if not DATABASE_URL:
    raise ValueError(
        "La variable DATABASE_URL no está configurada. "
        "Asegúrate de tener un archivo .env con la URL de Supabase."
    )

# Usar el driver Psycopg 3 ("postgresql+psycopg://").
# Algunos proveedores entregan la URL con el prefijo antiguo "postgres://",
# que SQLAlchemy 2.x ya no acepta; ambos casos se normalizan aquí.
for prefijo in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefijo):
        DATABASE_URL = DATABASE_URL.replace(prefijo, "postgresql+psycopg://", 1)
        break

# Crear el motor de conexión con SQLAlchemy
# - NullPool: no guardamos conexiones abiertas en Python. En Vercel cada
#   función es efímera (serverless) y el Transaction pooler de Supabase
#   (puerto 6543) ya se encarga de reutilizar conexiones.
# - prepare_threshold=None: Psycopg 3 no usa prepared statements. El pooler
#   en modo transacción puede mandar cada consulta a una conexión distinta,
#   así que un prepared statement creado en una conexión no existe en otra
#   (error: prepared statement "_pg3_0" already exists / does not exist).
# - sslmode=require: la conexión con Supabase siempre va cifrada.
engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    connect_args={"prepare_threshold": None, "sslmode": "require"},
)

# Crear la sesión local
# Es la que usaremos para hacer consultas, insertar datos, etc.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para los modelos SQLAlchemy
# Todos los modelos heredarán de esta clase
Base = declarative_base()


def get_db():
    """
    Función generadora que proporciona una sesión de base de datos.

    Se usa con 'with' o como dependencia en FastAPI:
        db = get_db()
        # usar db para consultas
        # la sesión se cierra automáticamente al terminar

    Yields:
        Session: Sesión de SQLAlchemy conectada a Supabase PostgreSQL
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
