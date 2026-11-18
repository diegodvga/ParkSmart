"""
Pruebas del Módulo 2: entradas y salidas con o sin reserva.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- Liquidación: fracciones de 15 min, gracia, recargo por exceso, impuesto.
- Ingreso directo (walk-in): placa normalizada, plaza del tipo correcto,
  prioridad de plazas sin reservas próximas, aviso por reserva cercana,
  salida estimada que respeta el margen, placa repetida, sin plazas.
- Entrada con reserva: ventana desde (inicio - margen), plaza ocupada,
  pendiente de pago, repetir la entrada no duplica.
- Salida: libera la plaza, liquida, repetirla no recalcula.
- Permisos: propietario dueño y operario asignado sí; otro propietario,
  operario no asignado o inactivo y cliente no.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_operacion.py
"""

import sys
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
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
    Cuenta, Espacio, EstadoEspacio, EstadoParqueadero, EstadoPagoCuenta, EstadoReserva, Estancia,
    Parqueadero, ParqueaderoOperario, Reserva, RolUsuario, TipoVehiculo, Usuario, Vehiculo,
)
from app.services.ocupacion import liquidar  # noqa: E402
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio  # noqa: E402


def probar_liquidacion():
    """La liquidación es una función pura: se prueba con horas fijas."""
    t0 = datetime(2030, 1, 1, 14, 0, tzinfo=ZONA_NEGOCIO)
    m = lambda minutos: t0 + timedelta(minutes=minutos)  # noqa: E731

    l = liquidar(3000, t0, m(60))
    verificar("Walk-in 60 min a $3000/h -> $3000", l.total == 3000 and l.minutos_base == 60, str(l))
    l = liquidar(3000, t0, m(61))
    verificar("Walk-in 61 min -> se cobran 75 min = $3750", l.total == 3750 and l.minutos_base == 75, str(l))
    l = liquidar(3000, t0, m(10))
    verificar("Walk-in 10 min -> mínimo una fracción (15 min) = $750", l.total == 750, str(l))
    l = liquidar(3000, t0, m(60), impuesto_pct=19)
    verificar("Impuesto 19 % sobre $3000 -> $570, total $3570", l.impuesto == 570 and l.total == 3570, str(l))

    reserva = dict(reserva_inicio=t0, reserva_fin=m(120), reserva_total=6000,
                   gracia_minutos=5, recargo_exceso_pct=50)
    l = liquidar(3000, t0, m(124), **reserva)
    verificar("Con reserva, sale 4 min tarde (gracia 5) -> solo lo pactado $6000",
              l.total == 6000 and l.minutos_extra == 0, str(l))
    l = liquidar(3000, t0, m(140), **reserva)
    verificar("Con reserva, sale 20 min tarde -> 30 min extra $1500 + recargo $750 = $8250",
              l.minutos_extra == 30 and l.valor_extra == 1500 and l.penalizacion == 750 and l.total == 8250, str(l))
    l = liquidar(3000, t0, m(90), **reserva)
    verificar("Con reserva, sale antes -> paga lo pactado $6000", l.total == 6000, str(l))
    verificar("Los importes son pesos enteros (Decimal sin decimales)",
              l.total == Decimal("6000") and l.total.as_tuple().exponent == 0)


def ejecutar_pruebas(base, db: Session):
    probar_liquidacion()

    ahora = ahora_negocio()

    def en(minutos):
        return ahora + timedelta(minutes=minutos)

    def recargar(objeto):
        db.expire_all()
        return db.get(type(objeto), objeto.id)

    # --- Preparar: propietario A con un parqueadero activo (2 motos, 3 carros) ---
    def propietario_con_parqueadero(letra):
        correo = f"prop_{letra}_op_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        arnes.crear_propietario(f"Propietario {letra}", correo)
        nav.login(correo)
        nombre = f"Operacion {letra} {SUFIJO}"
        nav.pedir("/admin/parqueaderos/crear", datos_creacion(nombre))
        db.expire_all()
        parq = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
        parq.estado = EstadoParqueadero.ACTIVO
        db.flush()
        return nav, parq

    prop_a, parq = propietario_con_parqueadero("A")
    prop_b, parq_b = propietario_con_parqueadero("B")
    espacios = {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == parq.id)}
    c01, c02, c03 = espacios["C01"], espacios["C02"], espacios["C03"]
    panel = f"/operacion/parqueaderos/{parq.id}"

    # Cliente con carro (para las reservas)
    correo_cli = f"cli_op_{SUFIJO}@parksmart-prueba.com"
    cliente = Navegador(base)
    cliente.registrar("Cliente Operacion", correo_cli, "cliente")
    cliente.login(correo_cli)
    usuario_cli = db.query(Usuario).filter(Usuario.correo == correo_cli).one()
    carro = Vehiculo(usuario_id=usuario_cli.id, placa=f"R{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
    db.add(carro)
    db.flush()

    def reserva(espacio, inicio, fin, estado=EstadoReserva.CONFIRMADA, total=3000):
        r = Reserva(usuario_id=usuario_cli.id, vehiculo_id=carro.id, espacio_id=espacio.id,
                    inicio=inicio, fin=fin, margen_minutos=15, tarifa_hora=3000, total=total, estado=estado)
        db.add(r)
        db.flush()
        return r

    # R1 en C01 empieza en 10 min: la plaza ya está "apartada" (ventana desde inicio - 15)
    r1 = reserva(c01, en(10), en(70))
    # R2 en C02 empieza en 40 min: libre ahora, pero con reserva dentro del umbral de 60
    r2 = reserva(c02, en(40), en(100))

    def walk_in(placa, tipo="carro", nav=None, **extra):
        return (nav or prop_a).pedir(f"{panel}/ingreso-directo", {"placa": placa, "tipo": tipo, **extra})

    def estancia_de(placa):
        db.expire_all()
        return db.query(Estancia).filter(
            Estancia.parqueadero_id == parq.id, Estancia.placa == placa, Estancia.salida_en.is_(None)
        ).first()

    def avisos():
        _, _, cuerpo = prop_a.pedir(panel)
        return cuerpo

    # --- 1. Ingreso directo y prioridad de plazas ---
    status, location, _ = walk_in("aaa-111", nombre="Ana", telefono="300 111 2222")
    e1 = estancia_de("AAA111")
    verificar("Walk-in -> 303 al panel y placa normalizada (aaa-111 -> AAA111)",
              status == 303 and location == panel and e1 is not None, f"status={status}")
    if e1:
        verificar("  ...va a C03, la plaza sin reservas próximas", e1.espacio_id == c03.id,
                  f"espacio={e1.espacio.numero}")
        verificar("  ...sin reserva, con nombre/teléfono y quién la registró",
                  e1.reserva_id is None and e1.nombre_conductor == "Ana" and e1.registrado_por == parq.propietario_id)
        verificar("  ...la plaza queda ocupada", recargar(c03).estado == EstadoEspacio.OCUPADO)
    verificar("  ...el panel confirma la entrada", "AAA111 entró a la plaza C03" in avisos())

    walk_in("BBB222")
    e2 = estancia_de("BBB222")
    cuerpo = avisos()
    verificar("Segundo walk-in -> C02 (C01 está apartada por una reserva que empieza en 10 min)",
              e2 is not None and e2.espacio_id == c02.id, f"{e2.espacio.numero if e2 else None}")
    verificar("  ...con aviso de la reserva cercana", "tiene una reserva a las" in cuerpo and "debe salir antes" in cuerpo)

    status, _, _ = walk_in("CCC333")
    cuerpo = avisos()
    verificar("Tercer carro sin plazas -> error claro y no entra",
              "No hay plazas disponibles" in cuerpo and estancia_de("CCC333") is None)

    walk_in("BBB-222")
    verificar("Misma placa ya dentro -> error", "ya está dentro" in avisos())

    walk_in("MOT01", tipo="moto")
    e_moto = estancia_de("MOT01")
    verificar("Moto -> plaza de moto (M01)", e_moto is not None and e_moto.espacio.numero == "M01")

    walk_in("BUS001", tipo="bus")
    verificar("Bus en parqueadero sin buses -> error", "no acepta" in avisos() and estancia_de("BUS001") is None)

    walk_in("A1")
    verificar("Placa demasiado corta -> error", "La placa debe tener" in avisos())
    walk_in("TEL001", telefono="abc")
    verificar("Teléfono inválido -> error", "El teléfono solo puede" in avisos())

    # --- 2. Salida estimada y margen antes de la próxima reserva ---
    prop_a.pedir(f"{panel}/estancias/{e2.id}/salida", {})   # libera C02
    cruza_medianoche = en(45).date() != ahora.date()
    if cruza_medianoche:
        print("[AVISO] Cerca de medianoche: se omiten las pruebas de salida estimada (formato HH:MM de hoy).")
    else:
        walk_in("DDD444", salida_estimada=en(30).strftime("%H:%M"))
        verificar("Salida estimada en 30 min: C02 no deja el margen antes de R2 (en 40) -> error",
                  "a tiempo para la próxima reserva" in avisos() and estancia_de("DDD444") is None)
        walk_in("EEE555", salida_estimada=en(20).strftime("%H:%M"))
        e5 = estancia_de("EEE555")
        verificar("Salida estimada en 20 min (20 + 15 <= 40) -> C02",
                  e5 is not None and e5.espacio_id == c02.id and e5.salida_estimada is not None)
        walk_in("FFF666", tipo="moto", salida_estimada=ahora.strftime("%H:%M"))
        verificar("Salida estimada en el pasado -> error", "posterior a la hora actual" in avisos())

    # --- 3. Entrada con reserva ---
    status, _, _ = prop_a.pedir(f"{panel}/reservas/{r1.id}/entrada", {})
    db.expire_all()
    est_r1 = db.query(Estancia).filter(Estancia.reserva_id == r1.id).all()
    verificar("Entrada de R1 (empieza en 10 min, margen 15) -> registrada",
              status == 303 and len(est_r1) == 1 and est_r1[0].espacio_id == c01.id)
    verificar("  ...la reserva pasa a en_curso y la plaza a ocupada",
              recargar(r1).estado == EstadoReserva.EN_CURSO and recargar(c01).estado == EstadoEspacio.OCUPADO)
    prop_a.pedir(f"{panel}/reservas/{r1.id}/entrada", {})
    db.expire_all()
    verificar("Repetir la entrada no duplica la estancia",
              db.query(Estancia).filter(Estancia.reserva_id == r1.id).count() == 1)
    verificar("  ...y avisa que ya estaba registrada", "ya estaba registrada" in avisos())

    r_futura = reserva(c01, en(24 * 60), en(25 * 60))
    prop_a.pedir(f"{panel}/reservas/{r_futura.id}/entrada", {})
    verificar("Entrada de una reserva de mañana -> 'Todavía no es la hora'", "Todavía no es la hora" in avisos())

    # R2 (C02) en ventana: se mueve para que empiece en 5 min; C02 sigue ocupada por EEE555
    if not cruza_medianoche:
        r2.inicio, r2.fin = en(5), en(65)
        db.flush()
        prop_a.pedir(f"{panel}/reservas/{r2.id}/entrada", {})
        verificar("Entrada con la plaza ocupada por otro vehículo -> error", "está ocupada" in avisos())

    r_pend = reserva(espacios["M02"], en(5), en(60), estado=EstadoReserva.PENDIENTE)
    prop_a.pedir(f"{panel}/reservas/{r_pend.id}/entrada", {})
    verificar("Entrada de una reserva pendiente de pago -> error", "pendiente de pago" in avisos())

    # --- 4. Salida y liquidación ---
    # R1 terminó hace 20 min (1 h reservada a $3000): 30 min extra + recargo
    estancia_r1 = est_r1[0]
    r1.inicio, r1.fin = en(-80), en(-20)
    estancia_r1.entrada_en = en(-80)
    db.flush()
    status, location, _ = prop_a.pedir(f"{panel}/estancias/{estancia_r1.id}/salida", {})
    db.expire_all()
    cuenta = db.query(Cuenta).filter(Cuenta.estancia_id == estancia_r1.id).one_or_none()
    verificar("Salida con reserva -> 303 a la cuenta",
              status == 303 and location == f"{panel}/estancias/{estancia_r1.id}/cuenta", f"status={status}")
    if cuenta:
        verificar("  ...base $3000 + extra 30 min $1500 + recargo $750 = $5250",
                  cuenta.valor_base == 3000 and cuenta.minutos_extra == 30 and cuenta.valor_extra == 1500
                  and cuenta.penalizacion == 750 and cuenta.total == 5250, f"total={cuenta.total}")
        verificar("  ...saldo pendiente registrado", cuenta.estado_pago == EstadoPagoCuenta.PENDIENTE
                  and cuenta.liquidada_en is not None and cuenta.reserva_id == r1.id)
    verificar("  ...reserva completada y plaza libre",
              recargar(r1).estado == EstadoReserva.COMPLETADA and recargar(c01).estado == EstadoEspacio.DISPONIBLE)
    status, _, cuerpo = prop_a.pedir(f"{panel}/estancias/{estancia_r1.id}/cuenta")
    verificar("La página de la cuenta muestra el total y el saldo",
              status == 200 and "$5.250" in cuerpo and "Saldo pendiente" in cuerpo, f"status={status}")

    # Repetir la salida (doble clic) no recalcula ni duplica
    r1.fin = en(-60)   # aunque cambie algo, la cuenta ya liquidada no se toca
    db.flush()
    prop_a.pedir(f"{panel}/estancias/{estancia_r1.id}/salida", {})
    db.expire_all()
    cuentas = db.query(Cuenta).filter(Cuenta.estancia_id == estancia_r1.id).all()
    verificar("Repetir la salida no duplica ni recalcula la cuenta",
              len(cuentas) == 1 and cuentas[0].total == 5250, f"{[c.total for c in cuentas]}")

    # Walk-in que estuvo 61 min -> 75 min = $3750
    e1.entrada_en = en(-61)
    db.flush()
    prop_a.pedir(f"{panel}/estancias/{e1.id}/salida", {})
    db.expire_all()
    cuenta = db.query(Cuenta).filter(Cuenta.estancia_id == e1.id).one_or_none()
    verificar("Salida de walk-in de 61 min -> $3750 y sin reserva",
              cuenta is not None and cuenta.total == 3750 and cuenta.reserva_id is None,
              f"total={cuenta.total if cuenta else None}")

    # --- 5. Permisos y aislamiento entre propietarios ---
    status, _, _ = prop_b.pedir(panel)
    verificar("Propietario B no ve el panel de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"{panel}/ingreso-directo", {"placa": "HAX001", "tipo": "carro"})
    verificar("Propietario B no registra en A -> 403", status == 403 and estancia_de("HAX001") is None)
    e_moto = estancia_de("MOT01")
    status, _, _ = prop_b.pedir(f"/operacion/parqueaderos/{parq_b.id}/estancias/{e_moto.id}/salida", {})
    verificar("B no saca un vehículo de A usando SU parqueadero en la URL",
              estancia_de("MOT01") is not None, f"status={status}")
    status, _, _ = prop_b.pedir(f"/operacion/parqueaderos/{parq_b.id}/estancias/{estancia_r1.id}/cuenta")
    verificar("B no ve la cuenta de A usando SU parqueadero -> 404", status == 404, f"status={status}")
    status, _, _ = cliente.pedir(panel)
    verificar("Cliente entra al panel de operación -> 403", status == 403, f"status={status}")

    # Operario: asignado a A sí; a B no; inactivo no
    correo_op = f"oper_{SUFIJO}@parksmart-prueba.com"
    operario = Navegador(base)
    operario.registrar("Operario", correo_op, "cliente")
    usuario_op = db.query(Usuario).filter(Usuario.correo == correo_op).one()
    usuario_op.rol = RolUsuario.OPERARIO
    asignacion = ParqueaderoOperario(parqueadero_id=parq.id, usuario_id=usuario_op.id)
    db.add(asignacion)
    db.flush()
    status, location, _ = operario.login(correo_op)
    verificar("Login de operario -> /operacion", status == 302 and location == "/operacion", f"{location}")
    status, _, cuerpo = operario.pedir("/operacion")
    verificar("  ...ve solo el parqueadero asignado",
              status == 200 and parq.nombre in cuerpo and parq_b.nombre not in cuerpo)
    # Las motos están llenas (M01 ocupada, M02 apartada por una pendiente): entra un carro
    walk_in("OPE001", nav=operario)
    e_op = estancia_de("OPE001")
    verificar("Operario asignado registra un ingreso", e_op is not None and e_op.registrado_por == usuario_op.id)
    status, _, _ = operario.pedir(f"/operacion/parqueaderos/{parq_b.id}")
    verificar("Operario no asignado a B -> 403", status == 403, f"status={status}")
    asignacion.activo = False
    db.flush()
    status, _, _ = operario.pedir(panel)
    verificar("Operario con asignación inactiva -> 403", status == 403, f"status={status}")

    # Parqueadero no activo no recibe vehículos
    parq.estado = EstadoParqueadero.INACTIVO
    db.flush()
    walk_in("INA001", tipo="moto")
    verificar("Parqueadero inactivo -> no registra entradas",
              "no está activo" in avisos() and estancia_de("INA001") is None)


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
