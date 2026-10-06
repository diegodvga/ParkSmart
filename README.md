# ParkSmart

Sistema de información web para la gestión de parqueaderos y reservas de espacios.

## Descripción

ParkSmart es una aplicación web que permite a los usuarios consultar información de parqueaderos, conocer la disponibilidad de espacios, registrar sus vehículos y realizar reservas. Incluye un módulo administrativo para gestionar parqueaderos, espacios, usuarios y reservas.

## Tecnologías

- **Backend:** FastAPI (Python)
- **Frontend:** Jinja2 + HTML5 + CSS3 + JavaScript
- **Base de datos:** PostgreSQL (Supabase)
- **ORM:** SQLAlchemy
- **Gráficos:** Chart.js
- **Despliegue:** Vercel

## Estructura del Proyecto

```
parksmart/
├── app/
│   ├── main.py              # Punto de entrada
│   ├── database.py          # Conexión con Supabase
│   ├── models.py            # Modelos SQLAlchemy
│   ├── schemas.py           # Esquemas Pydantic
│   ├── auth.py              # Autenticación
│   ├── routers/             # Rutas de la aplicación
│   ├── templates/           # Plantillas Jinja2
│   └── static/              # Archivos estáticos
├── .env                     # Variables de entorno
├── requirements.txt         # Dependencias
├── vercel.json              # Configuración de Vercel
└── README.md
```

## Instalación Local

1. Clonar el repositorio
2. Crear entorno virtual: `python -m venv venv`
3. Activar entorno virtual: `venv\Scripts\activate` (Windows)
4. Instalar dependencias: `pip install -r requirements.txt`
5. Configurar variables de entorno en `.env`
6. Ejecutar: `uvicorn app.main:app --reload`

## Configuración de Supabase

1. Crear proyecto en [supabase.com](https://supabase.com)
2. Obtener URL de conexión en Settings → Database
3. Configurar `DATABASE_URL` en `.env`

## Despliegue en Vercel

1. Subir código a GitHub
2. Importar proyecto en [vercel.com](https://vercel.com)
3. Configurar variables de entorno en Vercel
4. Deploy automático

## Licencia

Proyecto universitario de programación.
