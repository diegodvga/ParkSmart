"""
Pruebas del Módulo 4: pagos y comprobantes.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- Pagos en sitio y parciales; repetir el mismo pago (misma clave) no cobra
  dos veces; no se puede pagar más que el saldo.
- Pago anticipado de una reserva: confirma la reserva y se descuenta del
  saldo final; el tiempo excedido se cobra aparte (tipo 'extra').
- Reembolsos por cancelación: 100 % con más de 2 h o sin plaza alternativa,
  0 % con menos de 2 h.
- Pasarela: un intento fallido no cambia el saldo; repetir la confirmación
  no duplica.
- Factura (comprobante): una por cada cobro (migración 006). El anticipo
  tiene la suya al pagar; la de cierre sale con la salida registrada y el
  pago completo, solo por lo no facturado. Numeración consecutiva por
  parqueadero; datos congelados.
- Historial, JSON y vista imprimible con aislamiento entre propietarios y
  clientes.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_pagos.py
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
from sqlalchemy.orm import Session  # noqa: E402

import probar_etapa2 as arnes  # noqa: E402
from probar_etapa2 import SUFIJO, Navegador, puerto_libre, verificar  # noqa: E402
from probar_espacios_lote import datos_creacion  # noqa: E402
from app.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Comprobante, Cuenta, Espacio, EstadoIncidencia, EstadoPago, EstadoPagoCuenta, EstadoParqueadero,
    EstadoReserva, Estancia, Incidencia, MetodoPago, Pago, Parqueadero, Reserva, TipoIncidencia, TipoPago,
    TipoVehiculo, Usuario, Vehiculo,
)
from app.services.ocupacion import registrar_entrada_reserva, registrar_salida  # noqa: E402
from app.services.pagos import (  # noqa: E402
    confirmar_pago_pasarela, iniciar_pago_pasarela, registrar_pago_anticipado, saldo,
)
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio  # noqa: E402


def ejecutar_pruebas(base, db: Session):
    ahora = ahora_negocio()

    def en(minutos):
        return ahora + timedelta(minutes=minutos)

    # --- Preparar: propietarios A y B, cada uno con un parqueadero activo ---
    def propietario(letra):
        correo = f"prop_{letra}_pag_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        arnes.crear_propietario(f"Propietario {letra}", correo)
        nav.login(correo)
        nombre = f"Pagos {letra} {SUFIJO}"
        nav.pedir("/admin/parqueaderos/crear", datos_creacion(nombre))
        db.expire_all()
        p = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
        p.estado = EstadoParqueadero.ACTIVO
        db.flush()
        u = db.query(Usuario).filter(Usuario.correo == correo).one()
        return nav, p, u

    prop_a, parq, usuario_a = propietario("A")
    prop_b, parq_b, usuario_b = propietario("B")
    espacios = {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == parq.id)}
    panel = f"/operacion/parqueaderos/{parq.id}"

    def cuenta_de(estancia_id):
        db.expire_all()
        return db.query(Cuenta).filter(Cuenta.estancia_id == estancia_id).one()

    def pagar(cuenta, monto, metodo="efectivo", clave=None, nav=None, parqueadero=None):
        clave = clave or f"k-{SUFIJO}-{time.time_ns()}"
        return (nav or prop_a).pedir(
            f"/operacion/parqueaderos/{(parqueadero or parq).id}/cuentas/{cuenta.id}/pagos",
            {"metodo": metodo, "monto": str(monto), "clave_idempotencia": clave},
        )

    def walk_in_y_salida(placa, minutos, nav=None, parqueadero=None):
        """Registra un ingreso directo de hace `minutos` y su salida ahora."""
        nav = nav or prop_a
        parqueadero = parqueadero or parq
        nav.pedir(f"/operacion/parqueaderos/{parqueadero.id}/ingreso-directo", {"placa": placa, "tipo": "carro"})
        db.expire_all()
        e = db.query(Estancia).filter(Estancia.placa == placa, Estancia.salida_en.is_(None)).one()
        # 30 s de holgura: la salida se registra un instante después y no
        # debe pasar a la siguiente fracción de 15 min
        e.entrada_en = ahora_negocio() - timedelta(minutes=minutos) + timedelta(seconds=30)
        db.flush()
        nav.pedir(f"/operacion/parqueaderos/{parqueadero.id}/estancias/{e.id}/salida", {})
        return e, cuenta_de(e.id)

    def comprobantes_de(cuenta):
        db.expire_all()
        return db.query(Comprobante).filter(Comprobante.cuenta_id == cuenta.id).all()

    # ================= 1. Pago en sitio, parcial e idempotente =================
    e1, c1 = walk_in_y_salida(f"P1{SUFIJO[:4]}".upper(), 60)
    verificar("Salida de 60 min: cuenta $3000 pendiente y sin comprobante",
              c1.total == 3000 and c1.estado_pago == EstadoPagoCuenta.PENDIENTE and not comprobantes_de(c1))

    clave = f"parcial-{SUFIJO}"
    status, location, _ = pagar(c1, 1000, clave=clave)
    pagar(c1, 1000, clave=clave)   # doble clic: misma clave
    db.expire_all()
    c1 = db.get(Cuenta, c1.id)
    verificar("Pago parcial de $1000 -> 303 a la cuenta, estado parcial, saldo $2000",
              status == 303 and c1.estado_pago == EstadoPagoCuenta.PARCIAL and saldo(c1) == 2000,
              f"status={status} estado={c1.estado_pago} saldo={saldo(c1)}")
    verificar("  ...repetir el mismo pago (misma clave) no cobra dos veces",
              db.query(Pago).filter(Pago.cuenta_id == c1.id).count() == 1)
    verificar("  ...todavía sin comprobante (falta saldo)", not comprobantes_de(c1))

    status, _, _ = pagar(c1, 2500)
    db.expire_all()
    verificar("Pagar más que el saldo ($2500 > $2000) -> rechazado",
              db.query(Pago).filter(Pago.cuenta_id == c1.id).count() == 1)
    _, _, cuerpo = prop_a.pedir(f"{panel}/estancias/{e1.id}/cuenta")
    verificar("  ...con mensaje claro", "supera el saldo" in cuerpo)

    pagar(c1, 2000, metodo="tarjeta")
    db.expire_all()
    c1 = db.get(Cuenta, c1.id)
    comp1 = comprobantes_de(c1)
    verificar("Pago del saldo con tarjeta -> cuenta pagada y comprobante emitido",
              c1.estado_pago == EstadoPagoCuenta.PAGADO and saldo(c1) == 0 and len(comp1) == 1)
    if comp1:
        d = comp1[0].datos
        verificar("  ...comprobante n.º 1 del parqueadero", comp1[0].numero == 1, f"numero={comp1[0].numero}")
        verificar("  ...incluye desglose, total, vehículo y métodos de pago",
                  d["total"] == 3000 and d["tiempo_base"]["minutos"] == 60 and d["vehiculo"]["placa"] == e1.placa
                  and {p["metodo"] for p in d["pagos"]} == {"efectivo", "tarjeta"}, json.dumps(d)[:300])

    # ================= 2. Numeración consecutiva por parqueadero =================
    _, c2 = walk_in_y_salida(f"P2{SUFIJO[:4]}".upper(), 30)
    pagar(c2, c2.total)
    comp2 = comprobantes_de(c2)
    verificar("Segundo comprobante del mismo parqueadero -> n.º 2", comp2 and comp2[0].numero == 2)
    _, c3 = walk_in_y_salida(f"P3{SUFIJO[:4]}".upper(), 30, nav=prop_b, parqueadero=parq_b)
    pagar(c3, c3.total, nav=prop_b, parqueadero=parq_b)
    comp3 = comprobantes_de(c3)
    verificar("Otro parqueadero empieza su propia numeración -> n.º 1", comp3 and comp3[0].numero == 1)

    # ================= 3. Pago anticipado + tiempo excedido =================
    parq.pago_anticipado = True
    db.flush()
    correo_cli = f"cli_pag_{SUFIJO}@parksmart-prueba.com"
    cliente = Navegador(base)
    cliente.registrar("Cliente Pagos", correo_cli, "cliente")
    cliente.login(correo_cli)
    usuario_cli = db.query(Usuario).filter(Usuario.correo == correo_cli).one()
    carro = Vehiculo(usuario_id=usuario_cli.id, placa=f"CL{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
    db.add(carro)
    db.flush()

    manana = (ahora + timedelta(days=1)).date()
    cliente.pedir(f"/reservas/nueva/{espacios['C01'].id}", {
        "vehiculo_id": carro.id, "fecha": manana.isoformat(), "hora_entrada": "10:00", "hora_salida": "12:00"})
    db.expire_all()
    r = db.query(Reserva).filter(Reserva.vehiculo_id == carro.id).one()
    verificar("Reserva con pago anticipado nace pendiente ($6000)", r.estado == EstadoReserva.PENDIENTE and r.total == 6000)

    status, _, _ = prop_a.pedir(f"{panel}/reservas/{r.id}/pago-anticipado",
                                {"metodo": "transferencia", "clave_idempotencia": f"ant-{SUFIJO}"})
    prop_a.pedir(f"{panel}/reservas/{r.id}/pago-anticipado",
                 {"metodo": "transferencia", "clave_idempotencia": f"ant-{SUFIJO}"})
    db.expire_all()
    r = db.get(Reserva, r.id)
    cuenta_r = db.query(Cuenta).filter(Cuenta.reserva_id == r.id).one_or_none()
    pagos_r = db.query(Pago).filter(Pago.cuenta_id == cuenta_r.id).all() if cuenta_r else []
    verificar("Pago anticipado -> reserva confirmada y un solo pago de $6000 (aunque se repita)",
              status == 303 and r.estado == EstadoReserva.CONFIRMADA and len(pagos_r) == 1
              and pagos_r[0].monto == 6000 and pagos_r[0].tipo == TipoPago.ANTICIPADO, f"status={status}")

    # Entra a las 10:00 y sale 12:20: 20 min de exceso (30 min extra + recargo)
    t = lambda hora: datetime(manana.year, manana.month, manana.day, *map(int, hora.split(":")), tzinfo=ZONA_NEGOCIO)  # noqa: E731
    estancia_r, _ = registrar_entrada_reserva(db, parq, usuario_a, r.id, t("10:00"))
    cuenta_r = registrar_salida(db, parq, usuario_a, estancia_r.id, t("12:20"))
    verificar("Salida 20 min tarde: total $8250, el anticipo se descuenta -> saldo $2250",
              cuenta_r.total == 8250 and saldo(cuenta_r) == 2250 and cuenta_r.estado_pago == EstadoPagoCuenta.PARCIAL,
              f"total={cuenta_r.total} saldo={saldo(cuenta_r)}")
    verificar("  ...solo la factura del anticipo hasta pagar el exceso",
              len(comprobantes_de(cuenta_r)) == 1 and comprobantes_de(cuenta_r)[0].pago_id is not None)
    pagar(cuenta_r, 2250)
    db.expire_all()
    extra = db.query(Pago).filter(Pago.cuenta_id == cuenta_r.id, Pago.tipo == TipoPago.EXTRA).all()
    # comp_r: la factura de CIERRE (la del anticipo ya existía)
    comp_r = [c for c in comprobantes_de(cuenta_r) if c.pago_id is None]
    verificar("El cobro del exceso queda como tipo 'extra' y se emite la factura de cierre",
              len(extra) == 1 and extra[0].monto == 2250 and len(comp_r) == 1 and len(comprobantes_de(cuenta_r)) == 2)
    if comp_r:
        d = comp_r[0].datos
        verificar("  ...comprobante con tiempo extra, recargo, anticipo y datos del cliente",
                  d["tiempo_extra"]["minutos"] == 30 and d["penalizacion"] == 750
                  and d["cliente"]["nombre"] == "Cliente Pagos"
                  and [p["tipo"] for p in d["pagos"]] == ["anticipado", "extra"], json.dumps(d)[:300])

    # Datos congelados: cambiar la tarifa no altera cuentas ni comprobantes
    parq.tarifa_hora_carro = 9999
    db.flush()
    db.expire_all()
    verificar("Cambiar la tarifa después no altera la cuenta ni el comprobante",
              db.get(Cuenta, cuenta_r.id).total == 8250 and comp_r and comp_r[0].datos["total"] == 8250)
    parq.tarifa_hora_carro = 3000
    db.flush()

    # Anticipo que cubre todo: su factura sale al pagar y la salida no emite otra
    cliente.pedir(f"/reservas/nueva/{espacios['C02'].id}", {
        "vehiculo_id": carro.id, "fecha": manana.isoformat(), "hora_entrada": "14:00", "hora_salida": "15:00"})
    db.expire_all()
    r2 = db.query(Reserva).filter(Reserva.espacio_id == espacios["C02"].id, Reserva.vehiculo_id == carro.id).one()
    registrar_pago_anticipado(db, r2, MetodoPago.EFECTIVO, f"ant2-{SUFIJO}", usuario_a, ahora)
    e_r2, _ = registrar_entrada_reserva(db, parq, usuario_a, r2.id, t("14:00"))
    cuenta_r2 = registrar_salida(db, parq, usuario_a, e_r2.id, t("14:50"))
    verificar("Anticipo que cubre todo -> solo la factura del anticipo, sin otra al salir",
              saldo(cuenta_r2) == 0 and len(comprobantes_de(cuenta_r2)) == 1
              and comprobantes_de(cuenta_r2)[0].pago_id is not None)

    # ================= 4. Reembolsos por cancelación =================
    def reserva_pagada(espacio, inicio, fin):
        rr = Reserva(usuario_id=usuario_cli.id, vehiculo_id=carro.id, espacio_id=espacio.id, inicio=inicio,
                     fin=fin, margen_minutos=15, tarifa_hora=3000, total=3000, estado=EstadoReserva.PENDIENTE,
                     pendiente_expira_en=en(15))
        db.add(rr)
        db.flush()
        registrar_pago_anticipado(db, rr, MetodoPago.TARJETA, f"ant-{rr.id}-{SUFIJO}", usuario_a, ahora)
        return rr

    def reembolsos(rr):
        db.expire_all()
        cuenta = db.query(Cuenta).filter(Cuenta.reserva_id == rr.id).one()
        return cuenta, db.query(Pago).filter(Pago.cuenta_id == cuenta.id, Pago.tipo == TipoPago.REEMBOLSO).all()

    lejana = reserva_pagada(espacios["C03"], en(5 * 60), en(6 * 60))
    cliente.pedir(f"/reservas/{lejana.id}/cancelar", {})
    cuenta, devueltos = reembolsos(lejana)
    verificar("Cancelar con más de 2 h -> reembolso del 100 % ($3000) y cuenta reembolsada",
              len(devueltos) == 1 and devueltos[0].monto == 3000 and cuenta.estado_pago == EstadoPagoCuenta.REEMBOLSADO)

    cercana = reserva_pagada(espacios["C03"], en(60), en(120))
    cliente.pedir(f"/reservas/{cercana.id}/cancelar", {})
    cuenta, devueltos = reembolsos(cercana)
    verificar("Cancelar con menos de 2 h -> sin reembolso", not devueltos and cuenta.estado_pago == EstadoPagoCuenta.PAGADO)

    afectada = reserva_pagada(espacios["C03"], en(3 * 60), en(4 * 60))
    db.add(Incidencia(parqueadero_id=parq.id, reserva_id=afectada.id, espacio_id=espacios["C03"].id,
                      tipo=TipoIncidencia.SIN_ALTERNATIVA, estado=EstadoIncidencia.ABIERTA, detalle="prueba"))
    afectada.inicio, afectada.fin = en(40), en(100)   # ahora a menos de 2 h
    db.flush()
    cliente.pedir(f"/reservas/{afectada.id}/cancelar", {})
    cuenta, devueltos = reembolsos(afectada)
    verificar("Cancelar sin plaza alternativa -> reembolso del 100 % aunque falten menos de 2 h",
              len(devueltos) == 1 and devueltos[0].monto == 3000)
    verificar("  ...la cancelación queda con motivo sin_alternativa",
              db.get(Reserva, afectada.id).motivo_cancelacion.value == "sin_alternativa")

    # ================= 5. Pasarela: fallido e idempotencia =================
    _, c5 = walk_in_y_salida(f"P5{SUFIJO[:4]}".upper(), 60)
    ref = f"ref-{SUFIJO}"
    iniciar_pago_pasarela(db, c5, 3000, ref, f"pas1-{SUFIJO}", ahora)
    pago = confirmar_pago_pasarela(db, ref, False, ahora)
    db.expire_all()
    c5 = db.get(Cuenta, c5.id)
    verificar("Pasarela rechaza el pago -> pago fallido y el saldo no cambia",
              pago is not None and pago.estado == EstadoPago.FALLIDO and saldo(c5) == 3000)
    ref2 = f"ref2-{SUFIJO}"
    iniciar_pago_pasarela(db, c5, 3000, ref2, f"pas2-{SUFIJO}", ahora)
    confirmar_pago_pasarela(db, ref2, True, ahora)
    confirmar_pago_pasarela(db, ref2, True, ahora)   # la pasarela reenvía la confirmación
    db.expire_all()
    c5 = db.get(Cuenta, c5.id)
    verificar("Confirmación repetida de la pasarela -> un solo cobro y un solo comprobante",
              saldo(c5) == 0 and db.query(Pago).filter(Pago.cuenta_id == c5.id, Pago.estado == EstadoPago.PAGADO).count() == 1
              and len(comprobantes_de(c5)) == 1)

    # ================= 6. Historial, JSON, vista imprimible y aislamiento =================
    status, _, cuerpo = prop_a.pedir(f"{panel}/comprobantes")
    verificar("Historial de comprobantes del propietario lista los emitidos",
              status == 200 and "N.º 1" in cuerpo and "N.º 2" in cuerpo, f"status={status}")
    comp = comprobantes_de(c1)[0]
    status, _, cuerpo = prop_a.pedir(f"/comprobantes/{comp.id}")
    verificar("Vista imprimible del comprobante -> 200 con número y total",
              status == 200 and "Factura N.º 1" in cuerpo and "$3.000" in cuerpo, f"status={status}")
    status, _, cuerpo = prop_a.pedir(f"/comprobantes/{comp.id}.json")
    verificar("JSON del comprobante -> 200", status == 200 and json.loads(cuerpo)["numero"] == 1, f"status={status}")
    status, _, _ = prop_b.pedir(f"/comprobantes/{comp.id}")
    verificar("Otro propietario no ve el comprobante -> 404", status == 404, f"status={status}")
    status, _, _ = prop_b.pedir(f"/operacion/parqueaderos/{parq_b.id}/cuentas/{c1.id}/pagos",
                                {"metodo": "efectivo", "monto": "1", "clave_idempotencia": f"hack-{SUFIJO}"})
    verificar("Otro propietario no registra pagos en una cuenta ajena (usando SU parqueadero)",
              db.query(Pago).filter(Pago.clave_idempotencia == f"hack-{SUFIJO}").count() == 0, f"status={status}")
    status, _, _ = cliente.pedir(f"/comprobantes/{comp_r[0].id}" if comp_r else "/comprobantes/0")
    verificar("El cliente ve el comprobante de SU reserva -> 200", status == 200, f"status={status}")
    status, _, _ = cliente.pedir(f"/comprobantes/{comp.id}")
    verificar("El cliente no ve comprobantes ajenos -> 404", status == 404, f"status={status}")
    _, _, cuerpo = cliente.pedir("/mis-reservas")
    verificar("Mis reservas enlaza el comprobante", re.search(r'href="/comprobantes/\d+"', cuerpo) is not None)


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
