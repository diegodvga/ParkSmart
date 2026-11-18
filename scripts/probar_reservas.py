"""
Pruebas de reservas con agenda por horario y margen operativo.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- Caso A/B: C01 de 3:00 a 5:00 p. m. y de 6:00 a 8:00 p. m. se confirman las dos.
- Un horario que se cruza, o que no respeta el margen, se rechaza.
- El margen es el del parqueadero (10 o 15 min) y queda congelado en la reserva.
- Reservar NO marca el espacio como ocupado (agenda ≠ ocupación física).
- Cancelar libera el horario; una reserva pendiente vencida deja de apartarlo.
- Reglas previas: fecha pasada, tipo de vehículo no aceptado, espacio en
  mantenimiento.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_reservas.py
"""

import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# Permite importar "app" y el arnés de probar_etapa2 al ejecutar desde la raíz
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

import uvicorn  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import probar_etapa2 as arnes  # noqa: E402
from probar_etapa2 import SUFIJO, Navegador, puerto_libre, verificar  # noqa: E402
from probar_espacios_lote import datos_creacion  # noqa: E402
from app.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Espacio, EstadoEspacio, EstadoParqueadero, EstadoReserva, Parqueadero, Reserva,
    TipoVehiculo, Usuario, Vehiculo,
)
from app.services.reservas import ZONA_NEGOCIO  # noqa: E402


def ejecutar_pruebas(base, db: Session):
    # Fecha futura en hora de Colombia
    dia = (datetime.now(ZONA_NEGOCIO) + timedelta(days=7)).date()
    fecha = dia.isoformat()

    def instante(hora):
        h, m = map(int, hora.split(":"))
        return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=ZONA_NEGOCIO)

    def reservas_de(espacio_id):
        db.expire_all()
        return db.query(Reserva).filter(Reserva.espacio_id == espacio_id).order_by(Reserva.id).all()

    def activas_de(espacio_id):
        return [r for r in reservas_de(espacio_id) if r.estado != EstadoReserva.CANCELADA]

    # --- Preparar: propietario crea un parqueadero (2 motos, 3 carros) y queda activo ---
    correo_prop = f"prop_res_{SUFIJO}@parksmart-prueba.com"
    prop = Navegador(base)
    arnes.crear_propietario("Propietario Reservas", correo_prop)
    prop.login(correo_prop)
    nombre = f"Reservas {SUFIJO}"
    prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre))
    db.expire_all()
    parq = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
    parq.estado = EstadoParqueadero.ACTIVO
    db.flush()
    espacios = {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == parq.id)}
    c01, c02, c03 = espacios["C01"].id, espacios["C02"].id, espacios["C03"].id

    # Dos clientes, cada uno con su carro
    clientes = {}
    for letra in ("A", "B"):
        correo = f"cli_{letra.lower()}_res_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        nav.registrar(f"Cliente {letra}", correo, "cliente")
        nav.login(correo)
        usuario = db.query(Usuario).filter(Usuario.correo == correo).one()
        vehiculo = Vehiculo(usuario_id=usuario.id, placa=f"{letra}{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
        db.add(vehiculo)
        db.flush()
        clientes[letra] = (nav, vehiculo.id)

    def reservar(letra, espacio_id, entrada, salida, dia_texto=fecha):
        nav, vehiculo_id = clientes[letra]
        return nav.pedir(f"/reservas/nueva/{espacio_id}", {
            "vehiculo_id": vehiculo_id, "fecha": dia_texto,
            "hora_entrada": entrada, "hora_salida": salida,
        })

    # --- 1. Caso A/B: 15-17 y 18-20 en C01 ---
    status_a, _, _ = reservar("A", c01, "15:00", "17:00")
    status_b, _, _ = reservar("B", c01, "18:00", "20:00")
    activas = activas_de(c01)
    verificar("Caso A/B: C01 15:00-17:00 y 18:00-20:00 -> ambas 302",
              status_a == 302 and status_b == 302 and len(activas) == 2, f"A={status_a} B={status_b}")
    if len(activas) == 2:
        a = activas[0]
        verificar("  ...nacen confirmadas", all(r.estado == EstadoReserva.CONFIRMADA for r in activas))
        verificar("  ...inicio y fin en hora de Colombia",
                  a.inicio == instante("15:00") and a.fin == instante("17:00"), f"{a.inicio} - {a.fin}")
        verificar("  ...margen 15 y tarifa 3000 congelados en la reserva",
                  a.margen_minutos == 15 and a.tarifa_hora == 3000)
        verificar("  ...total = 2 h x 3000", a.total == 6000, f"total={a.total}")
    db.expire_all()
    verificar("Reservar NO marca el espacio como ocupado",
              db.get(Espacio, c01).estado == EstadoEspacio.DISPONIBLE)

    # --- 2. Cruces y margen de 15 minutos ---
    status, _, cuerpo = reservar("B", c01, "16:00", "18:00")
    verificar("Cruce 16:00-18:00 en C01 -> 400", status == 400 and len(activas_de(c01)) == 2, f"status={status}")
    verificar("  ...con mensaje claro", "Ya existe una reserva en ese horario" in cuerpo)
    status, _, _ = reservar("B", c01, "17:10", "17:40")
    verificar("17:10 incumple el margen de 15 min tras A -> 400", status == 400, f"status={status}")
    status, _, _ = reservar("B", c01, "17:15", "17:45")
    verificar("17:15-17:45 respeta el margen antes y después -> 302",
              status == 302 and len(activas_de(c01)) == 3, f"status={status}")

    # --- 3. Margen de 10 minutos y congelado ---
    parq.margen_minutos = 10
    db.flush()
    reservar("A", c02, "15:00", "17:00")
    status, _, _ = reservar("B", c02, "17:10", "17:30")
    activas = activas_de(c02)
    verificar("Con margen 10: 17:10 tras una reserva que acaba 17:00 -> 302",
              status == 302 and len(activas) == 2, f"status={status}")
    status, _, _ = reservar("B", c02, "17:35", "17:50")
    verificar("Con margen 10: 17:35 tras una que acaba 17:30 -> 400", status == 400, f"status={status}")
    parq.margen_minutos = 15
    db.flush()
    verificar("Cambiar el margen del parqueadero no altera las reservas confirmadas",
              all(r.margen_minutos == 10 for r in activas_de(c02)))

    # --- 4. Cancelar libera el horario ---
    a = activas_de(c01)[0]
    nav_a, _ = clientes["A"]
    status, _, _ = nav_a.pedir(f"/reservas/{a.id}/cancelar", {})
    db.expire_all()
    a = db.get(Reserva, a.id)
    verificar("Cancelar -> 302, estado cancelada, motivo cliente y fecha",
              status == 302 and a.estado == EstadoReserva.CANCELADA
              and a.motivo_cancelacion is not None and a.motivo_cancelacion.value == "cliente"
              and a.cancelada_en is not None, f"status={status}")
    status, _, _ = reservar("B", c01, "15:30", "16:30")
    verificar("El horario cancelado queda libre (15:30-16:30) -> 302", status == 302, f"status={status}")

    # --- 5. Pago anticipado: pendiente aparta 15 min y luego vence ---
    parq.pago_anticipado = True
    db.flush()
    reservar("A", c03, "09:00", "10:00")
    pendiente = activas_de(c03)[0] if activas_de(c03) else None
    verificar("Con pago anticipado la reserva nace pendiente con vencimiento",
              pendiente is not None and pendiente.estado == EstadoReserva.PENDIENTE
              and pendiente.pendiente_expira_en is not None)
    status, _, _ = reservar("B", c03, "09:00", "10:00")
    verificar("Una pendiente vigente aparta el horario -> 400", status == 400, f"status={status}")
    if pendiente is not None:
        pendiente.pendiente_expira_en = datetime.now(ZONA_NEGOCIO) - timedelta(minutes=1)
        db.flush()
        status, _, _ = reservar("B", c03, "09:00", "10:00")
        db.expire_all()
        vencida = db.get(Reserva, pendiente.id)
        verificar("Pendiente vencida deja de apartar: otro cliente reserva -> 302", status == 302, f"status={status}")
        verificar("  ...y la vencida queda cancelada con motivo pendiente_vencida",
                  vencida.estado == EstadoReserva.CANCELADA
                  and vencida.motivo_cancelacion.value == "pendiente_vencida")
    parq.pago_anticipado = False
    db.flush()

    # --- 6. Reglas que ya existían ---
    ayer = (datetime.now(ZONA_NEGOCIO) - timedelta(days=1)).date().isoformat()
    status, _, cuerpo = reservar("A", c03, "15:00", "16:00", dia_texto=ayer)
    verificar("Fecha pasada -> 400", status == 400 and "pasad" in cuerpo, f"status={status}")
    status, _, cuerpo = reservar("A", c03, "16:00", "15:00")
    verificar("Salida antes de la entrada -> 400", status == 400 and "posterior" in cuerpo, f"status={status}")

    # Parqueadero sin buses: un bus no puede reservar
    nav_a, _ = clientes["A"]
    usuario_a = db.query(Usuario).filter(Usuario.correo == f"cli_a_res_{SUFIJO}@parksmart-prueba.com").one()
    bus = Vehiculo(usuario_id=usuario_a.id, placa=f"BUS{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.BUS)
    db.add(bus)
    db.flush()
    status, _, cuerpo = nav_a.pedir(f"/reservas/nueva/{c03}", {
        "vehiculo_id": bus.id, "fecha": fecha, "hora_entrada": "11:00", "hora_salida": "12:00"})
    verificar("Tipo de vehículo no aceptado -> 400", status == 400 and "no acepta" in cuerpo, f"status={status}")

    # Espacio en mantenimiento no se puede reservar
    espacio = db.get(Espacio, c03)
    espacio.estado = EstadoEspacio.MANTENIMIENTO
    db.flush()
    status, _, _ = reservar("A", c03, "11:00", "12:00")
    verificar("Espacio en mantenimiento -> 400", status == 400, f"status={status}")
    espacio.estado = EstadoEspacio.DISPONIBLE
    db.flush()

    # El formulario abre aunque la plaza tenga reservas futuras
    # (el enlace antiguo por plaza ahora redirige al formulario con selector de plazas)
    status, location, _ = nav_a.pedir(f"/reservas/nueva/{c01}")
    status_form, _, _ = nav_a.pedir(location) if location else (None, None, None)
    verificar("Formulario de una plaza con reservas futuras -> redirige y abre (200)",
              status == 303 and status_form == 200, f"status={status} -> {status_form}")
    status, _, cuerpo = nav_a.pedir("/mis-reservas")
    verificar("Mis reservas -> 200 y lista el parqueadero", status == 200 and nombre in cuerpo, f"status={status}")


def main():
    conexion = engine.connect()
    transaccion = conexion.begin()
    db = Session(bind=conexion, join_transaction_mode="create_savepoint")
    arnes.SESION = db

    def get_db_prueba():
        yield db

    app.dependency_overrides[get_db] = get_db_prueba

    puerto = puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning"))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    while not servidor.started:
        time.sleep(0.1)

    try:
        ejecutar_pruebas(f"http://127.0.0.1:{puerto}", db)
    finally:
        servidor.should_exit = True
        hilo.join(timeout=10)
        db.close()
        transaccion.rollback()
        conexion.close()
        app.dependency_overrides.clear()
        print("\nROLLBACK hecho: no quedó ningún dato de prueba en la base.")

    total = len(arnes.resultados)
    aprobadas = sum(arnes.resultados)
    print(f"Resultado: {aprobadas}/{total} pruebas OK")
    sys.exit(0 if aprobadas == total else 1)


if __name__ == "__main__":
    main()
