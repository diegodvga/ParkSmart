"""
Router de tareas programadas para ParkSmart.

Rutas:
- POST /tareas/retrasos → Una pasada del protocolo de retraso (idempotente).

La llama pg_cron en Supabase cada minuto (ver
docs/migraciones/004_tareas_programadas.sql) con la cabecera
X-Tarea-Clave. La clave vive en la variable de entorno TAREAS_CLAVE
(en .env local y en Vercel); NUNCA se escribe en el código.
"""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.reservas import ahora_negocio
from app.services.retrasos import procesar_retrasos

router = APIRouter(prefix="/tareas", tags=["tareas"])


def verificar_clave(x_tarea_clave: str = Header(default="")):
    """
    Deja pasar solo a quien envía la clave correcta.

    - 503 si el servidor no tiene TAREAS_CLAVE configurada (mejor fallar
      visible que dejar la ruta abierta).
    - 401 si la clave falta o no coincide. compare_digest compara en tiempo
      constante para no filtrar pistas sobre la clave.
    """
    esperada = os.getenv("TAREAS_CLAVE")
    if not esperada:
        raise HTTPException(status_code=503, detail="Tareas programadas no configuradas")
    if not secrets.compare_digest(x_tarea_clave.encode(), esperada.encode()):
        raise HTTPException(status_code=401, detail="Clave de tarea inválida")


@router.post("/retrasos", dependencies=[Depends(verificar_clave)])
async def tarea_retrasos(db: Session = Depends(get_db)):
    """Ejecuta el protocolo de retraso en todos los parqueaderos y devuelve un resumen."""
    return procesar_retrasos(db, ahora_negocio())
