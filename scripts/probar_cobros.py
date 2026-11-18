"""
Pruebas de cobros y facturas: pasarela simulada, efectivo y una factura
por cada cobro (feature odd/tasks/cobros-facturas.md, S1-S7).

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- S1: con tarjeta o transferencia la reserva nace pendiente y lleva a la
  pasarela simulada; un pago aprobado la confirma; la tarjeta terminada
  en 0002 se rechaza; los datos de la tarjeta no se guardan; repetir el
  envío no cobra dos veces; un plazo vencido no se cobra.
- S2: en efectivo la reserva nace confirmada sin pagos; si el parqueadero
  exige pago anticipado, el efectivo no se ofrece y el servidor lo rechaza.
- S3: el pago anticipado (pasarela o caja) genera su factura al pagar.
- S4: al salir, la factura de cierre cubre solo lo que no estaba
  facturado (tiempo extra); si el anticipo cubría todo, no hay otra.
- S5: ingreso sin reserva ni vehículo registrado, salida y factura.
- S6: la base impide dos facturas de cierre por cuenta y facturar un pago
  de otra cuenta.
- S7: la factura aclara que es interna y no es factura electrónica DIAN.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_cobros.py
"""

import json
import re
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
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import probar_etapa2 as arnes  # noqa: E402
from probar_etapa2 import SUFIJO, Navegador, puerto_libre, verificar  # noqa: E402
from probar_espacios_lote import datos_creacion  # noqa: E402
from app.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Comprobante, Cuenta, Espacio, EstadoPago, EstadoParqueadero, EstadoReserva, Estancia, MetodoPago, Pago,
    Parqueadero, Reserva, TipoPago, TipoVehiculo, Usuario, Vehiculo,
)
from app.services.ocupacion import registrar_entrada_reserva, registrar_salida  # noqa: E402
from app.services.pagos import saldo  # noqa: E402
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio  # noqa: E402

TARJETA_OK = "4242 4242 4242 4242"
TARJETA_RECHAZADA = "4000 0000 0000 0002"


def ejecutar_pruebas(base, db: Session):
    ahora = ahora_negocio()
    manana = (ahora + timedelta(days=1)).date()
    fecha = manana.isoformat()
    vence = f"12/{(ahora.year + 2) % 100:02d}"

    def t(hora):
        horas, minutos = map(int, hora.split(":"))
        return datetime(manana.year, manana.month, manana.day, horas, minutos, tzinfo=ZONA_NEGOCIO)

    # --- Preparar: dos parqueaderos (uno exige pago anticipado) y un cliente con carro ---
    correo_prop = f"prop_cob_{SUFIJO}@parksmart-prueba.com"
    arnes.crear_propietario("Propietario Cobros", correo_prop)
    prop = Navegador(base)
    prop.login(correo_prop)
    usuario_prop = db.query(Usuario).filter(Usuario.correo == correo_prop).one()

    def parqueadero(nombre, anticipado):
        prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre))
        db.expire_all()
        p = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
        p.estado = EstadoParqueadero.ACTIVO
        p.pago_anticipado = anticipado
        db.flush()
        return p, {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == p.id)}

    parq, e = parqueadero(f"Cobros {SUFIJO}", False)
    parq_ant, e_ant = parqueadero(f"Cobros anticipado {SUFIJO}", True)

    def cliente(letra):
        correo = f"cli_{letra}_cob_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        nav.registrar(f"Cliente {letra}", correo, "cliente")
        nav.login(correo)
        u = db.query(Usuario).filter(Usuario.correo == correo).one()
        carro = Vehiculo(usuario_id=u.id, placa=f"{letra}K{SUFIJO[:4]}".upper(), tipo=TipoVehiculo.CARRO)
        db.add(carro)
        db.flush()
        return nav, carro

    nav, carro = cliente("A")
    nav_b, _ = cliente("B")

    def reservar(p, espacio, entrada, salida, forma):
        return nav.pedir(f"/parqueaderos/{p.id}/reservar", {
            "vehiculo_id": carro.id, "fecha": fecha, "hora_entrada": entrada, "hora_salida": salida,
            "espacio_id": espacio.id, "forma_pago": forma,
        })

    def reserva_en(espacio):
        db.expire_all()
        return db.query(Reserva).filter(Reserva.espacio_id == espacio.id, Reserva.vehiculo_id == carro.id).one_or_none()

    def facturas_de(reserva=None, cuenta=None):
        db.expire_all()
        consulta = db.query(Comprobante).join(Cuenta, Comprobante.cuenta_id == Cuenta.id)
        if reserva is not None:
            consulta = consulta.filter(Cuenta.reserva_id == reserva.id)
        else:
            consulta = consulta.filter(Cuenta.id == cuenta.id)
        return consulta.order_by(Comprobante.numero).all()

    def pagos_de(reserva):
        db.expire_all()
        return db.query(Pago).join(Cuenta, Pago.cuenta_id == Cuenta.id).filter(
            Cuenta.reserva_id == reserva.id).order_by(Pago.id).all()

    def pagar_tarjeta(reserva, numero, clave, vencimiento=None, navegador=None):
        return (navegador or nav).pedir(f"/reservas/{reserva.id}/pagar", {
            "metodo": "tarjeta", "titular": "Cliente A", "numero_tarjeta": numero,
            "vencimiento": vencimiento or vence, "cvv": "123", "clave_idempotencia": clave,
        })

    def pagar_transferencia(reserva, banco, clave):
        return nav.pedir(f"/reservas/{reserva.id}/pagar", {
            "metodo": "transferencia", "banco": banco, "documento": "1234567", "clave_idempotencia": clave,
        })

    # ================= S2. Efectivo =================
    status, _, cuerpo = nav.pedir(f"/parqueaderos/{parq.id}/reservar")
    verificar("S2 · El formulario ofrece tarjeta, transferencia y efectivo",
              status == 200 and all(f'value="{v}"' in cuerpo for v in ("digital", "efectivo")), f"status={status}")
    status, _, cuerpo = nav.pedir(f"/parqueaderos/{parq_ant.id}/reservar")
    verificar("S2 · Si el parqueadero exige pago anticipado, el efectivo no se ofrece",
              status == 200 and 'value="digital"' in cuerpo and 'value="efectivo"' not in cuerpo)

    status, location, _ = reservar(parq, e["C01"], "08:00", "10:00", "efectivo")
    r_efe = reserva_en(e["C01"])
    verificar("S2 · Reserva en efectivo -> 302 a Mis reservas, confirmada y sin pagos",
              status == 302 and location == "/mis-reservas" and r_efe is not None
              and r_efe.estado == EstadoReserva.CONFIRMADA and r_efe.pendiente_expira_en is None
              and not pagos_de(r_efe), f"status={status} location={location}")

    status, _, cuerpo = reservar(parq_ant, e_ant["C01"], "08:00", "10:00", "efectivo")
    verificar("S2 · Efectivo en un parqueadero que exige pago anticipado -> 400 y no se crea",
              status == 400 and "exige pago anticipado" in cuerpo and reserva_en(e_ant["C01"]) is None,
              f"status={status}")
    status, _, cuerpo = reservar(parq, e["C02"], "08:00", "10:00", "cheque")
    verificar("S2 · Forma de pago inválida -> 400", status == 400 and "cómo vas a pagar" in cuerpo, f"status={status}")

    # ================= S1. Pasarela simulada (tarjeta) =================
    status, location, _ = reservar(parq, e["C02"], "10:00", "12:00", "digital")
    r_tar = reserva_en(e["C02"])
    verificar("S1 · Reserva con pago digital -> pendiente y redirige a la pasarela",
              r_tar is not None and status in (302, 303) and location == f"/reservas/{r_tar.id}/pagar"
              and r_tar.estado == EstadoReserva.PENDIENTE and r_tar.pendiente_expira_en is not None,
              f"status={status} location={location}")

    status, _, cuerpo = nav.pedir(f"/reservas/{r_tar.id}/pagar")
    verificar("S1 · La pasarela muestra el monto y los dos medios",
              status == 200 and "$6.000" in cuerpo and "Tarjeta" in cuerpo and "Transferencia" in cuerpo,
              f"status={status}")
    status, _, _ = nav_b.pedir(f"/reservas/{r_tar.id}/pagar")
    verificar("S1 · Otro cliente no ve la pasarela de una reserva ajena -> 404", status == 404, f"status={status}")
    status, _, _ = pagar_tarjeta(r_tar, TARJETA_OK, f"ajeno-{SUFIJO}", navegador=nav_b)
    verificar("S1 · Otro cliente no puede pagarla -> 404 y sin pagos", status == 404 and not pagos_de(r_tar))

    status, _, cuerpo = pagar_tarjeta(r_tar, "4242 4242 4242 4241", f"luhn-{SUFIJO}")
    verificar("S1 · Número de tarjeta inválido -> 400 y no se crea ningún pago",
              status == 400 and "número de tarjeta" in cuerpo and not pagos_de(r_tar), f"status={status}")
    status, _, cuerpo = pagar_tarjeta(r_tar, TARJETA_OK, f"vencida-{SUFIJO}", vencimiento="01/20")
    verificar("S1 · Tarjeta vencida -> 400 y sin pago", status == 400 and "vencida" in cuerpo and not pagos_de(r_tar))

    status, _, cuerpo = pagar_tarjeta(r_tar, TARJETA_RECHAZADA, f"rech-{SUFIJO}")
    pagos = pagos_de(r_tar)
    verificar("S1 · Tarjeta terminada en 0002 -> rechazada (400), pago fallido y reserva sigue pendiente",
              status == 400 and "rechaz" in cuerpo and len(pagos) == 1 and pagos[0].estado == EstadoPago.FALLIDO
              and reserva_en(e["C02"]).estado == EstadoReserva.PENDIENTE and not facturas_de(r_tar),
              f"status={status} pagos={[(p.estado.value) for p in pagos]}")

    clave_ok = f"ok-{SUFIJO}"
    status, location, _ = pagar_tarjeta(r_tar, TARJETA_OK, clave_ok)
    r_tar = reserva_en(e["C02"])
    pagados = [p for p in pagos_de(r_tar) if p.estado == EstadoPago.PAGADO]
    facturas = facturas_de(r_tar)
    verificar("S1 · Pago aprobado -> reserva confirmada y redirige a la factura",
              status == 303 and r_tar.estado == EstadoReserva.CONFIRMADA and r_tar.pendiente_expira_en is None
              and len(facturas) == 1 and location == f"/comprobantes/{facturas[0].id}?pagado=1",
              f"status={status} location={location}")
    verificar("S1 · El pago queda como anticipado, con tarjeta, $6000 y referencia de la pasarela",
              len(pagados) == 1 and pagados[0].tipo == TipoPago.ANTICIPADO and pagados[0].metodo == MetodoPago.TARJETA
              and pagados[0].monto == 6000 and (pagados[0].referencia_externa or "").startswith("SIM-"))
    guardado = json.dumps([facturas[0].datos if facturas else {}, [
        (p.clave_idempotencia, p.referencia_externa) for p in pagos_de(r_tar)]])
    verificar("S1 · Los datos de la tarjeta no se guardan (ni número ni CVV)",
              "4242424242424242" not in guardado and "4242 4242" not in guardado and "cvv" not in guardado)

    pagar_tarjeta(r_tar, TARJETA_OK, clave_ok)   # doble clic / reintento
    verificar("S1 · Repetir el mismo envío no cobra dos veces ni duplica la factura",
              len([p for p in pagos_de(r_tar) if p.estado == EstadoPago.PAGADO]) == 1 and len(facturas_de(r_tar)) == 1)
    status, _, cuerpo = pagar_tarjeta(r_tar, TARJETA_OK, f"otra-{SUFIJO}")
    verificar("S1 · Pagar otra vez una reserva ya pagada -> 400", status == 400 and "ya está pagada" in cuerpo,
              f"status={status}")

    # ================= S3 y S7. Factura del pago digital =================
    if facturas:
        f1 = facturas[0]
        verificar("S3 · La factura del anticipo se vincula al pago y guarda el concepto y el valor",
                  f1.pago_id == pagados[0].id and f1.datos["concepto"] == "anticipado"
                  and f1.datos["valor_factura"] == 6000 and f1.datos["vehiculo"]["placa"] == carro.placa
                  and f1.datos["plaza"] == "C02", json.dumps(f1.datos)[:300])
        status, _, cuerpo = nav.pedir(f"/comprobantes/{f1.id}?pagado=1")
        verificar("S7 · La factura se ve como 'Factura N.º' interna, no electrónica DIAN, con el pago aprobado",
                  status == 200 and f"Factura N.º {f1.numero}" in cuerpo and "no es factura electrónica" in cuerpo
                  and "DIAN" in cuerpo and "Pago aprobado" in cuerpo and "$6.000" in cuerpo, f"status={status}")

    status, _, cuerpo = nav.pedir("/mis-reservas")
    verificar("S3 · Mis reservas enlaza la factura y la reserva en efectivo dice que se paga en el parqueadero",
              status == 200 and facturas and f'href="/comprobantes/{facturas[0].id}"' in cuerpo
              and "Pago en el parqueadero" in cuerpo)

    # ================= S1. Transferencia =================
    reservar(parq, e["C03"], "10:00", "11:00", "digital")
    r_tra = reserva_en(e["C03"])
    status, _, cuerpo = nav.pedir("/mis-reservas")
    verificar("S1 · Una reserva pendiente muestra el botón para pagarla",
              f'href="/reservas/{r_tra.id}/pagar"' in cuerpo)
    status, _, cuerpo = pagar_transferencia(r_tra, "rechazo", f"trr-{SUFIJO}")
    verificar("S1 · Transferencia rechazada por el banco de prueba -> 400 y reserva pendiente",
              status == 400 and "rechaz" in cuerpo and reserva_en(e["C03"]).estado == EstadoReserva.PENDIENTE)
    status, _, _ = pagar_transferencia(r_tra, "bancolombia", f"tra-{SUFIJO}")
    r_tra = reserva_en(e["C03"])
    facturas_tra = facturas_de(r_tra)
    verificar("S1 · Transferencia aprobada -> reserva confirmada y factura con método transferencia",
              status == 303 and r_tra.estado == EstadoReserva.CONFIRMADA and len(facturas_tra) == 1
              and facturas_tra[0].datos["metodo_pago"] == "transferencia", f"status={status}")

    # ================= S1. Plazo de pago vencido =================
    reservar(parq, e["C01"], "14:00", "15:00", "digital")
    r_ven = db.query(Reserva).filter(Reserva.espacio_id == e["C01"].id, Reserva.estado == EstadoReserva.PENDIENTE).one()
    r_ven.pendiente_expira_en = ahora_negocio() - timedelta(minutes=1)
    db.flush()
    status, _, cuerpo = pagar_tarjeta(r_ven, TARJETA_OK, f"ven-{SUFIJO}")
    db.expire_all()
    verificar("S1 · Pagar después del plazo -> 400, reserva cancelada y sin cobro",
              status == 400 and "venció" in cuerpo and db.get(Reserva, r_ven.id).estado == EstadoReserva.CANCELADA
              and not pagos_de(r_ven), f"status={status}")

    # ================= S4. Salidas =================
    # Anticipo que cubre todo: no se emite otra factura
    est, _ = registrar_entrada_reserva(db, parq, usuario_prop, r_tra.id, t("10:00"))
    cuenta = registrar_salida(db, parq, usuario_prop, est.id, t("10:55"))
    verificar("S4 · Anticipo que cubre todo -> saldo 0 y ninguna factura nueva al salir",
              saldo(cuenta) == 0 and len(facturas_de(cuenta=cuenta)) == 1)

    # Anticipo + tiempo extra: la factura de cierre cubre solo el extra
    est, _ = registrar_entrada_reserva(db, parq, usuario_prop, r_tar.id, t("10:00"))
    cuenta = registrar_salida(db, parq, usuario_prop, est.id, t("12:20"))
    verificar("S4 · Salida 20 min tarde con anticipo -> saldo $2250 y todavía solo la factura del anticipo",
              saldo(cuenta) == 2250 and len(facturas_de(cuenta=cuenta)) == 1, f"saldo={saldo(cuenta)}")
    status, _, _ = prop.pedir(f"/operacion/parqueaderos/{parq.id}/cuentas/{cuenta.id}/pagos",
                              {"metodo": "efectivo", "monto": "2250", "clave_idempotencia": f"x-{SUFIJO}"})
    facturas = facturas_de(cuenta=cuenta)
    cierre = [f for f in facturas if f.pago_id is None]
    verificar("S4 · Cobro del extra en efectivo -> factura de cierre por $2250 que cita la del anticipo",
              status == 303 and len(facturas) == 2 and len(cierre) == 1
              and cierre[0].datos["concepto"] == "cierre" and cierre[0].datos["valor_factura"] == 2250
              and cierre[0].datos["total"] == 8250
              and cierre[0].datos["facturas_previas"] == [facturas[0].numero], json.dumps(
                  cierre[0].datos if cierre else {})[:300])
    status, _, cuerpo = prop.pedir(f"/operacion/parqueaderos/{parq.id}/estancias/{est.id}/cuenta")
    verificar("S4 · El cobro de la salida lista las dos facturas",
              status == 200 and all(f"Factura N.º {f.numero}" in cuerpo for f in facturas), f"status={status}")

    # Reserva en efectivo: todo se factura al salir
    est, _ = registrar_entrada_reserva(db, parq, usuario_prop, r_efe.id, t("08:00"))
    cuenta = registrar_salida(db, parq, usuario_prop, est.id, t("09:55"))
    verificar("S4 · Reserva en efectivo -> al salir queda el saldo completo y sin factura",
              saldo(cuenta) == 6000 and not facturas_de(cuenta=cuenta))
    status, _, _ = prop.pedir(f"/operacion/parqueaderos/{parq.id}/cuentas/{cuenta.id}/pagos",
                              {"metodo": "efectivo", "monto": "6000", "clave_idempotencia": f"e-{SUFIJO}"})
    facturas = facturas_de(cuenta=cuenta)
    verificar("S4 · Pago en efectivo al salir -> pago confirmado y una factura de cierre por $6000",
              status == 303 and saldo(db.get(Cuenta, cuenta.id)) == 0 and len(facturas) == 1
              and facturas[0].pago_id is None and facturas[0].datos["valor_factura"] == 6000
              and facturas[0].datos["metodo_pago"] == "efectivo")

    # Pago anticipado en la caja (parqueadero que lo exige): también factura al pagar
    reservar(parq_ant, e_ant["C02"], "16:00", "17:00", "digital")
    r_caja = reserva_en(e_ant["C02"])
    status, _, _ = prop.pedir(f"/operacion/parqueaderos/{parq_ant.id}/reservas/{r_caja.id}/pago-anticipado",
                              {"metodo": "efectivo", "clave_idempotencia": f"caja-{SUFIJO}"})
    facturas = facturas_de(r_caja)
    verificar("S3 · Pago anticipado en efectivo en la caja -> confirma la reserva y emite su factura",
              status == 303 and reserva_en(e_ant["C02"]).estado == EstadoReserva.CONFIRMADA and len(facturas) == 1
              and facturas[0].pago_id is not None and facturas[0].datos["metodo_pago"] == "efectivo")

    # ================= S5. Ingreso sin reserva =================
    placa = f"WK{SUFIJO[:4]}".upper()
    status, _, _ = prop.pedir(f"/operacion/parqueaderos/{parq.id}/ingreso-directo",
                              {"placa": placa, "tipo": "carro", "nombre": "Conductor sin cuenta"})
    db.expire_all()
    estancia = db.query(Estancia).filter(Estancia.placa == placa, Estancia.salida_en.is_(None)).one_or_none()
    verificar("S5 · Ingreso sin reserva ni vehículo registrado -> estancia con plaza asignada",
              status == 303 and estancia is not None and estancia.reserva_id is None and estancia.espacio_id is not None
              and db.query(Vehiculo).filter(Vehiculo.placa == placa).count() == 0, f"status={status}")
    if estancia is not None:
        estancia.entrada_en = ahora_negocio() - timedelta(minutes=60) + timedelta(seconds=30)
        db.flush()
        prop.pedir(f"/operacion/parqueaderos/{parq.id}/estancias/{estancia.id}/salida", {})
        db.expire_all()
        cuenta = db.query(Cuenta).filter(Cuenta.estancia_id == estancia.id).one()
        prop.pedir(f"/operacion/parqueaderos/{parq.id}/cuentas/{cuenta.id}/pagos",
                   {"metodo": "efectivo", "monto": "3000", "clave_idempotencia": f"w-{SUFIJO}"})
        facturas = facturas_de(cuenta=cuenta)
        verificar("S5 · Salida y pago en efectivo del ingreso directo -> factura a nombre del conductor",
                  len(facturas) == 1 and facturas[0].datos["cliente"]["nombre"] == "Conductor sin cuenta")

    # Plaza elegida por el operario
    status, _, cuerpo = prop.pedir(f"/operacion/parqueaderos/{parq.id}")
    verificar("S5 · El ingreso sin reserva permite elegir la plaza o asignarla automáticamente",
              'name="espacio_id"' in cuerpo and "Asignar automáticamente" in cuerpo)
    placa2 = f"WE{SUFIJO[:4]}".upper()
    prop.pedir(f"/operacion/parqueaderos/{parq.id}/ingreso-directo",
               {"placa": placa2, "tipo": "carro", "espacio_id": e["C03"].id})
    db.expire_all()
    elegida = db.query(Estancia).filter(Estancia.placa == placa2, Estancia.salida_en.is_(None)).one_or_none()
    verificar("S5 · Elegir la plaza C03 -> el vehículo entra a C03",
              elegida is not None and elegida.espacio_id == e["C03"].id)
    placa3 = f"WM{SUFIJO[:4]}".upper()
    status, _, _ = prop.pedir(f"/operacion/parqueaderos/{parq.id}/ingreso-directo",
                              {"placa": placa3, "tipo": "carro", "espacio_id": e["M01"].id})
    _, _, cuerpo = prop.pedir(f"/operacion/parqueaderos/{parq.id}")
    db.expire_all()
    verificar("S5 · Elegir una plaza de moto para un carro -> se rechaza con explicación",
              db.query(Estancia).filter(Estancia.placa == placa3).count() == 0
              and "no está disponible para ese vehículo" in cuerpo)

    # ================= S6. Restricciones de la base (migración 006) =================
    f_cierre = cierre[0] if cierre else None
    if f_cierre is not None:
        try:
            with db.begin_nested():
                db.add(Comprobante(parqueadero_id=parq.id, cuenta_id=f_cierre.cuenta_id, numero=9000, datos={}))
            ok = False
        except IntegrityError:
            ok = True
        verificar("S6 · La base rechaza una segunda factura de cierre para la misma cuenta", ok)
        otro_pago = pagos_de(r_efe)[0]
        try:
            with db.begin_nested():
                db.add(Comprobante(parqueadero_id=parq.id, cuenta_id=f_cierre.cuenta_id, pago_id=otro_pago.id,
                                   numero=9001, datos={}))
            ok = False
        except IntegrityError:
            ok = True
        verificar("S6 · La base rechaza facturar un pago de otra cuenta", ok)


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
