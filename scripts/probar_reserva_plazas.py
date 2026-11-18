"""
Pruebas del selector de plazas al crear o editar una reserva.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- La reserva usa el MISMO componente de plazas que el panel del dueño
  (encabezado, leyenda, cuadrícula y tarjetas).
- La disponibilidad es para el horario pedido, con el margen operativo:
  C01 reservada de 3:00 a 5:00 p. m. se puede elegir de 6:00 a 8:00 p. m.
  pero no de 3:30 a 4:30 ni de 5:10 a 6:00 (margen de 15 min).
- Solo se eligen plazas compatibles con el vehículo y que no estén en
  mantenimiento; la cuadrícula se recalcula al cambiar fecha, horas o vehículo.
- El servidor vuelve a validar al guardar y, si la plaza ya no sirve,
  conserva fecha, horario y vehículo para elegir otra.
- Editar una reserva: misma cuadrícula, su propia plaza no cuenta como
  ocupada, y solo el dueño de la reserva puede editarla.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_reserva_plazas.py
"""

import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

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
    Cuenta, Espacio, EstadoEspacio, EstadoPago, EstadoParqueadero, EstadoReserva, MetodoPago, Pago, Parqueadero,
    Reserva, TipoPago, TipoVehiculo, Usuario, Vehiculo,
)
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio  # noqa: E402


def tarjeta(cuerpo, numero):
    """HTML de la tarjeta de una plaza (label si se puede elegir, div si no)."""
    m = re.search(rf'<(label|div) class="pp-plaza[^"]*"[^>]*data-numero="{numero}"[^>]*>.*?</\1>', cuerpo, re.S)
    return m.group(0) if m else ""


def elegible(cuerpo, numero):
    """True si la plaza se puede elegir (tiene su radio de selección)."""
    return 'name="espacio_id"' in tarjeta(cuerpo, numero)


def ejecutar_pruebas(base, db: Session):
    manana = (ahora_negocio() + timedelta(days=1)).date()
    fecha = manana.isoformat()

    def h(hora):
        horas, minutos = map(int, hora.split(":"))
        return datetime(manana.year, manana.month, manana.day, horas, minutos, tzinfo=ZONA_NEGOCIO)

    # --- Preparar: parqueadero activo (2 motos, 3 carros), cliente con carro y moto ---
    correo_prop = f"prop_sel_{SUFIJO}@parksmart-prueba.com"
    arnes.crear_propietario("Propietario Selector", correo_prop)
    prop = Navegador(base)
    prop.login(correo_prop)
    nombre = f"Selector {SUFIJO}"
    prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre))
    db.expire_all()
    parq = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
    parq.estado = EstadoParqueadero.ACTIVO
    db.flush()
    e = {x.numero: x for x in db.query(Espacio).filter(Espacio.parqueadero_id == parq.id)}

    def cliente_con_vehiculos(letra):
        correo = f"cli_{letra}_sel_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        nav.registrar(f"Cliente {letra}", correo, "cliente")
        nav.login(correo)
        u = db.query(Usuario).filter(Usuario.correo == correo).one()
        carro = Vehiculo(usuario_id=u.id, placa=f"{letra}C{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
        moto = Vehiculo(usuario_id=u.id, placa=f"{letra}M{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.MOTO)
        db.add_all([carro, moto])
        db.flush()
        return nav, u, carro, moto

    nav_a, usuario_a, carro_a, moto_a = cliente_con_vehiculos("A")
    nav_b, usuario_b, carro_b, _ = cliente_con_vehiculos("B")

    # C01 ocupada en agenda por otra persona de 3:00 a 5:00 p. m.
    db.add(Reserva(usuario_id=usuario_b.id, vehiculo_id=carro_b.id, espacio_id=e["C01"].id, inicio=h("15:00"),
                   fin=h("17:00"), margen_minutos=15, tarifa_hora=3000, total=6000, estado=EstadoReserva.CONFIRMADA))
    e["C03"].estado = EstadoEspacio.MANTENIMIENTO
    db.flush()

    formulario = f"/parqueaderos/{parq.id}/reservar"

    def plazas(vehiculo, entrada, salida, extra=None):
        consulta = {"vehiculo_id": vehiculo.id, "fecha": fecha, "hora_entrada": entrada, "hora_salida": salida}
        consulta.update(extra or {})
        return nav_a.pedir(f"{formulario}/plazas?{urlencode(consulta)}")

    # ================= 1. Mismo componente que el panel del dueño =================
    status, _, cuerpo = nav_a.pedir(formulario)
    verificar("Formulario de reserva -> 200 con la sección 'Plazas', leyenda y cuadrícula",
              status == 200 and "<h2>Plazas</h2>" in cuerpo and 'class="pp-leyenda"' in cuerpo
              and 'class="pp-plazas"' in cuerpo and tarjeta(cuerpo, "C01") != "", f"status={status}")
    verificar("  ...carga las plazas reales del parqueadero (M01..C03)",
              all(tarjeta(cuerpo, n) for n in ("M01", "M02", "C01", "C02", "C03")))
    _, _, panel = prop.pedir(f"/propietario/parqueaderos/{parq.id}")
    verificar("El panel del dueño usa el mismo componente (mismas clases y tarjetas)",
              '<h2>Plazas</h2>' in panel and 'class="pp-leyenda"' in panel and tarjeta(panel, "C01") != "")
    verificar("Sin horario elegido, ninguna plaza se puede seleccionar todavía",
              not any(elegible(cuerpo, n) for n in ("C01", "C02", "M01")))

    # ================= 2. Disponibilidad para el horario (con margen) =================
    status, _, cuerpo = plazas(carro_a, "18:00", "20:00")
    verificar("C01 (reservada 3-5 p. m.) se puede elegir de 6:00 a 8:00 p. m.",
              status == 200 and elegible(cuerpo, "C01"), f"status={status}")
    verificar("  ...C02 también; C03 no (mantenimiento)", elegible(cuerpo, "C02") and not elegible(cuerpo, "C03"))
    verificar("  ...las motos no sirven para un carro (incompatibles)",
              not elegible(cuerpo, "M01") and "pp-incompatible" in tarjeta(cuerpo, "M01"))
    _, _, cuerpo = plazas(carro_a, "15:30", "16:30")
    verificar("De 3:30 a 4:30 p. m. C01 aparece ocupada y no se puede elegir",
              not elegible(cuerpo, "C01") and "pp-reservada" in tarjeta(cuerpo, "C01"))
    _, _, cuerpo = plazas(carro_a, "17:10", "18:00")
    verificar("De 5:10 p. m. C01 no se puede elegir (margen de 15 min después de las 5:00)",
              not elegible(cuerpo, "C01"))
    _, _, cuerpo = plazas(carro_a, "17:15", "18:00")
    verificar("Desde 5:15 p. m. C01 vuelve a estar disponible", elegible(cuerpo, "C01"))
    _, _, cuerpo = plazas(carro_a, "13:00", "14:50")
    verificar("Hasta 2:45 p. m. sirve; de 1:00 a 2:50 no (su margen choca con las 3:00)",
              not elegible(cuerpo, "C01") and elegible(plazas(carro_a, "13:00", "14:45")[2], "C01"))
    _, _, cuerpo = plazas(moto_a, "18:00", "20:00")
    verificar("Al cambiar a la moto, la cuadrícula se recalcula: motos sí, carros no",
              elegible(cuerpo, "M01") and not elegible(cuerpo, "C01"))
    _, _, cuerpo = plazas(carro_a, "18:00", "20:00", {"espacio_id": e["C01"].id})
    verificar("La plaza elegida aparece resaltada",
              "pp-seleccionada" in tarjeta(cuerpo, "C01") and "checked" in tarjeta(cuerpo, "C01"))
    verificar("El cliente solo ve acciones de selección (nada de entrada/salida/cobro)",
              "Registrar entrada" not in cuerpo and "Registrar salida" not in cuerpo and "Cobrar" not in cuerpo)

    # ================= 3. Guardar: validación en el servidor =================
    def reservar(espacio, entrada, salida, vehiculo=None, nav=None):
        vehiculo = vehiculo or carro_a
        return (nav or nav_a).pedir(formulario, {
            "vehiculo_id": vehiculo.id, "fecha": fecha, "hora_entrada": entrada,
            "hora_salida": salida, "espacio_id": espacio.id if espacio else "",
        })

    status, location, _ = reservar(e["C01"], "18:00", "20:00")
    db.expire_all()
    nueva = db.query(Reserva).filter(Reserva.vehiculo_id == carro_a.id).first()
    verificar("Reservar C01 de 6:00 a 8:00 p. m. -> 302 a Mis reservas",
              status == 302 and location == "/mis-reservas" and nueva is not None
              and nueva.espacio_id == e["C01"].id and nueva.inicio == h("18:00"), f"status={status}")

    status, _, cuerpo = reservar(e["C01"], "16:00", "17:00", vehiculo=moto_a)
    verificar("Elegir una plaza de carro con la moto -> 400", status == 400 and "es para" in cuerpo, f"status={status}")
    status, _, cuerpo = reservar(None, "10:00", "11:00")
    verificar("Guardar sin elegir plaza -> 400 pidiendo elegir una", status == 400 and "Elige una plaza" in cuerpo)

    # La plaza se ocupó entre que el cliente la vio y guardó (otra persona)
    status, _, cuerpo = reservar(e["C01"], "15:30", "16:30")
    verificar("Plaza que dejó de estar disponible -> 400 con mensaje claro",
              status == 400 and "Ya existe una reserva en ese horario" in cuerpo, f"status={status}")
    verificar("  ...conserva fecha, horario y vehículo ingresados",
              f'value="{fecha}"' in cuerpo and 'value="15:30"' in cuerpo and 'value="16:30"' in cuerpo
              and re.search(rf'<option value="{carro_a.id}"[^>]*selected', cuerpo) is not None)
    verificar("  ...y la cuadrícula se actualiza: C01 ocupada, C02 para elegir otra",
              not elegible(cuerpo, "C01") and elegible(cuerpo, "C02"))

    status, _, _ = reservar(e["M01"], "18:00", "20:00", vehiculo=carro_b, nav=nav_a)
    verificar("No se puede reservar con un vehículo ajeno -> 400", status == 400, f"status={status}")

    # Enlace antiguo por plaza: lleva al nuevo formulario con la plaza preelegida
    status, location, _ = nav_a.pedir(f"/reservas/nueva/{e['C02'].id}")
    verificar("El enlace antiguo /reservas/nueva/{plaza} redirige al nuevo formulario",
              status == 303 and location == f"{formulario}?espacio_id={e['C02'].id}", f"{status} {location}")

    # ================= 4. Editar una reserva =================
    editar = f"/reservas/{nueva.id}/editar"
    _, _, cuerpo = nav_a.pedir("/mis-reservas")
    verificar("Mis reservas ofrece 'Editar' en una reserva futura", f'href="{editar}"' in cuerpo)
    status, _, cuerpo = nav_a.pedir(editar)
    verificar("Editar -> 200 con la misma cuadrícula y su plaza actual seleccionada",
              status == 200 and "<h2>Plazas</h2>" in cuerpo and "pp-seleccionada" in tarjeta(cuerpo, "C01")
              and 'value="18:00"' in cuerpo, f"status={status}")
    status, location, _ = nav_a.pedir(editar, {"vehiculo_id": carro_a.id, "fecha": fecha, "hora_entrada": "18:30",
                                               "hora_salida": "19:30", "espacio_id": e["C02"].id})
    db.expire_all()
    r = db.get(Reserva, nueva.id)
    verificar("Cambiar a C02 de 6:30 a 7:30 p. m. -> 302 y la reserva se actualiza (1 h = $3000)",
              status == 302 and r.espacio_id == e["C02"].id and r.inicio == h("18:30") and r.fin == h("19:30")
              and r.total == 3000, f"status={status} total={r.total}")
    _, _, cuerpo = nav_a.pedir(f"{formulario}/plazas?" + urlencode({
        "vehiculo_id": carro_a.id, "fecha": fecha, "hora_entrada": "18:30", "hora_salida": "19:30",
        "reserva_id": nueva.id}))
    verificar("Al editar, la propia reserva no cuenta como ocupación (C02 sigue elegible)", elegible(cuerpo, "C02"))
    status, _, cuerpo = nav_a.pedir(editar, {"vehiculo_id": carro_a.id, "fecha": fecha, "hora_entrada": "15:30",
                                             "hora_salida": "16:30", "espacio_id": e["C01"].id})
    db.expire_all()
    verificar("Editar a un horario ocupado -> 400, conserva lo escrito y no cambia la reserva",
              status == 400 and 'value="15:30"' in cuerpo and db.get(Reserva, nueva.id).espacio_id == e["C02"].id,
              f"status={status}")
    status, _, _ = nav_b.pedir(editar)
    verificar("Otro cliente no puede editarla -> 404", status == 404, f"status={status}")

    # Con un pago registrado no se edita (se cancela y se hace otra)
    cuenta = Cuenta(parqueadero_id=parq.id, reserva_id=nueva.id, tarifa_hora=3000, total=3000)
    db.add(cuenta)
    db.flush()
    db.add(Pago(cuenta_id=cuenta.id, tipo=TipoPago.ANTICIPADO, metodo=MetodoPago.EFECTIVO, monto=3000,
                estado=EstadoPago.PAGADO, clave_idempotencia=f"sel-{SUFIJO}"))
    db.flush()
    status, _, cuerpo = nav_a.pedir(editar, {"vehiculo_id": carro_a.id, "fecha": fecha, "hora_entrada": "18:00",
                                             "hora_salida": "19:00", "espacio_id": e["C02"].id})
    verificar("Reserva con pago registrado -> no se edita (400 con explicación)",
              status == 400 and "pago" in cuerpo, f"status={status}")


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
