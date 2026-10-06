"""
Configuración de la base de datos para ParkSmart.

Este módulo se encarga de:
1. Cargar las variables de entorno desde el archivo .env
2. Crear el motor de conexión con SQLAlchemy
3. Crear la sesión para interactuar con la base de datos
4. Proporcionar la función get_db() que usarán los routers
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
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

# Si la URL usa "postgresql://", cambiar a "postgresql+psycopg://"
# para usar el driver Psycopg 3 en lugar de psycopg2
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Crear el motor de conexión con SQLAlchemy
# El motor es el punto de entrada para conectarse a la base de datos
engine = create_engine(DATABASE_URL)

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
