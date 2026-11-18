"""
Pruebas del Módulo 5: paneles del propietario y aislamiento entre propietarios.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Escenario (propietario A con dos parqueaderos):
- P1 (2 motos, 3 carros): C01 ingreso directo, C02 con reserva en curso,
  C03 con reserva excedida (afectada por retraso), M01 en mantenimiento,
  M02 libre. Capacidad operativa = 5 - 1 en mantenimiento = 4; ocupadas 3
  -> 75 %.
- P2 (2 carros): C01 ingreso directo -> 50 %.
- Global de A: 4 ocupadas / 6 operativas = 66.67 %.
- Ingresos (hora de Colombia): los pagos cuentan el día en que se
  confirman y los reembolsos se RESTAN el día en que se hacen.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_paneles.py
"""

import json
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
    Cuenta, Espacio, EstadoEspacio, EstadoIncidencia, EstadoParqueadero, EstadoReserva, Estancia, Incidencia,
    MetodoPago, Parqueadero, ParqueaderoOperario, Reserva, RolUsuario, TipoIncidencia, TipoVehiculo, Usuario,
    Vehiculo,
)
from app.services.ocupacion import registrar_ingreso_directo, registrar_salida  # noqa: E402
from app.services.pagos import reembolsar, registrar_pago  # noqa: E402
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio  # noqa: E402


def ejecutar_pruebas(base, db: Session):
    ahora = ahora_negocio()
    inicio_mes = ahora.replace(day=1, hour=12, minute=0, second=0, microsecond=0)

    # --- Propietarios y parqueaderos ---
    def propietario(letra):
        correo = f"prop_{letra}_pan_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        arnes.crear_propietario(f"Propietario {letra}", correo)
        nav.login(correo)
        return nav, db.query(Usuario).filter(Usuario.correo == correo).one()

    def parqueadero(nav, nombre, **cantidades):
        nav.pedir("/admin/parqueaderos/crear", datos_creacion(nombre, **cantidades))
        db.expire_all()
        p = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
        p.estado = EstadoParqueadero.ACTIVO
        db.flush()
        return p, {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == p.id)}

    prop_a, usuario_a = propietario("A")
    prop_b, usuario_b = propietario("B")
    p1, e1 = parqueadero(prop_a, f"Panel Uno {SUFIJO}")                                   # 2 motos, 3 carros
    p2, e2 = parqueadero(prop_a, f"Panel Dos {SUFIJO}", cantidad_motos="0", cantidad_carros="2")
    pb, eb = parqueadero(prop_b, f"Panel B {SUFIJO}", cantidad_motos="0", cantidad_carros="1")

    # --- Ingresos: cobros cerrados (la plaza queda libre después) ---
    def cobro(parq, placa, monto_pago, cuando):
        estancia, _ = registrar_ingreso_directo(db, parq, usuario_a if parq.propietario_id == usuario_a.id else usuario_b,
                                                placa, TipoVehiculo.CARRO, cuando - timedelta(minutes=59))
        cuenta = registrar_salida(db, parq, usuario_a, estancia.id, cuando)
        registrar_pago(db, cuenta, MetodoPago.EFECTIVO, monto_pago, f"pan-{placa}-{SUFIJO}", usuario_a, cuando)
        return cuenta

    cuenta_hoy = cobro(p1, f"H1{SUFIJO[:4]}".upper(), 3000, ahora - timedelta(minutes=1))
    reembolsar(db, cuenta_hoy, 1000, MetodoPago.EFECTIVO, f"reemb-{SUFIJO}", usuario_a, ahora - timedelta(seconds=30))
    cobro(p1, f"H2{SUFIJO[:4]}".upper(), 3000, ahora - timedelta(days=40))             # fuera del mes
    hay_dia_anterior_del_mes = ahora.day > 1 and inicio_mes < ahora - timedelta(hours=2)
    if hay_dia_anterior_del_mes:
        cobro(p1, f"H3{SUFIJO[:4]}".upper(), 3000, inicio_mes)                          # mes sí, hoy no
    cobro(p2, f"H4{SUFIJO[:4]}".upper(), 3000, ahora - timedelta(minutes=1))
    cobro(pb, f"H5{SUFIJO[:4]}".upper(), 3000, ahora - timedelta(minutes=1))            # de B: no cuenta para A

    # --- Ocupación actual de P1 y P2 ---
    correo_cli = f"cli_pan_{SUFIJO}@parksmart-prueba.com"
    cliente = Navegador(base)
    cliente.registrar("Cliente Panel", correo_cli, "cliente")
    usuario_cli = db.query(Usuario).filter(Usuario.correo == correo_cli).one()
    carro = Vehiculo(usuario_id=usuario_cli.id, placa=f"PC{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
    db.add(carro)
    db.flush()

    def reserva(espacio, inicio, fin, estado):
        r = Reserva(usuario_id=usuario_cli.id, vehiculo_id=carro.id, espacio_id=espacio.id, inicio=inicio, fin=fin,
                    margen_minutos=15, tarifa_hora=3000, total=3000, estado=estado)
        db.add(r)
        db.flush()
        return r

    def dentro(parq, espacio, placa, r=None):
        db.add(Estancia(parqueadero_id=parq.id, espacio_id=espacio.id, reserva_id=r.id if r else None, placa=placa,
                        tipo_vehiculo=TipoVehiculo.CARRO, entrada_en=ahora - timedelta(minutes=30),
                        tarifa_hora=3000, registrado_por=usuario_a.id))
        espacio.estado = EstadoEspacio.OCUPADO
        db.flush()

    dentro(p1, e1["C01"], f"W1{SUFIJO[:4]}".upper())
    r_curso = reserva(e1["C02"], ahora - timedelta(minutes=30), ahora + timedelta(minutes=30), EstadoReserva.EN_CURSO)
    dentro(p1, e1["C02"], f"R1{SUFIJO[:4]}".upper(), r_curso)
    r_exc = reserva(e1["C03"], ahora - timedelta(minutes=90), ahora - timedelta(minutes=20), EstadoReserva.EXCEDIDA)
    dentro(p1, e1["C03"], f"R2{SUFIJO[:4]}".upper(), r_exc)
    db.add(Incidencia(parqueadero_id=p1.id, reserva_id=r_exc.id, espacio_id=e1["C03"].id,
                      tipo=TipoIncidencia.EXCESO, estado=EstadoIncidencia.ABIERTA, detalle="Sigue dentro"))
    e1["M01"].estado = EstadoEspacio.MANTENIMIENTO
    dentro(p2, e2["C01"], f"W2{SUFIJO[:4]}".upper())

    # Reservas próximas: dentro de 24 h sí, en 3 días no
    proxima = reserva(e1["M02"], ahora + timedelta(hours=3), ahora + timedelta(hours=4), EstadoReserva.CONFIRMADA)
    reserva(e1["M02"], ahora + timedelta(days=3), ahora + timedelta(days=3, hours=1), EstadoReserva.CONFIRMADA)
    db.flush()

    # ================= 1. Panel individual (JSON) =================
    status, _, cuerpo = prop_a.pedir(f"/propietario/api/parqueaderos/{p1.id}/metricas")
    m = json.loads(cuerpo) if status == 200 else {}
    verificar("JSON del panel individual -> 200", status == 200, f"status={status}")
    if m:
        situaciones = {pl["numero"]: pl["situacion"] for pl in m["plazas"]}
        verificar("Plazas: C01 directo, C02 reserva, C03 afectada, M01 mantenimiento, M02 libre",
                  situaciones == {"C01": "directo", "C02": "reserva", "C03": "afectada",
                                  "M01": "mantenimiento", "M02": "libre"}, str(situaciones))
        o = m["ocupacion"]
        verificar("Ocupación P1: 3 ocupadas / 4 operativas (sin mantenimiento) = 75 %",
                  o["ocupadas"] == 3 and o["operativas"] == 4 and o["porcentaje"] == 75.0, str(o))
        verificar("Ingresos de hoy P1 = $3000 - reembolso $1000 = $2000", m["ingresos_dia"] == 2000, str(m["ingresos_dia"]))
        esperado_mes = 2000 + (3000 if hay_dia_anterior_del_mes else 0)
        verificar(f"Ingresos del mes P1 = ${esperado_mes} (el cobro de hace 40 días no cuenta)",
                  m["ingresos_mes"] == esperado_mes, str(m["ingresos_mes"]))
        verificar("Reservas próximas (24 h): solo la de dentro de 3 h",
                  [r["id"] for r in m["reservas_proximas"]] == [proxima.id], str(m["reservas_proximas"]))
        verificar("Alertas activas: la incidencia de exceso de C03",
                  len(m["alertas"]) == 1 and m["alertas"][0]["tipo"] == "exceso", str(m["alertas"]))
        verificar("Documenta la zona horaria de los cortes", m["zona_horaria"] == "America/Bogota")

    # ================= 2. Panel consolidado (JSON) =================
    status, _, cuerpo = prop_a.pedir("/propietario/api/metricas")
    c = json.loads(cuerpo) if status == 200 else {}
    verificar("JSON del panel consolidado -> 200", status == 200, f"status={status}")
    if c:
        verificar("Incluye solo los parqueaderos de A (P1 y P2, no el de B)",
                  sorted(p["id"] for p in c["parqueaderos"]) == sorted([p1.id, p2.id]))
        verificar("Ingresos de hoy de A = $2000 (P1) + $3000 (P2) = $5000", c["ingresos_dia"] == 5000, str(c["ingresos_dia"]))
        verificar("Ocupación global = 4 ocupadas / 6 operativas = 66.67 %",
                  c["ocupacion"]["ocupadas"] == 4 and c["ocupacion"]["operativas"] == 6
                  and c["ocupacion"]["porcentaje"] == 66.67, str(c["ocupacion"]))
        verificar("Vehículos activos = 4", c["vehiculos_activos"] == 4, str(c["vehiculos_activos"]))
        verificar("Alertas pendientes = 1", c["alertas_pendientes"] == 1, str(c["alertas_pendientes"]))
        fila_p2 = next((p for p in c["parqueaderos"] if p["id"] == p2.id), {})
        verificar("Comparativa por parqueadero: P2 con 50 % y $3000 hoy",
                  fila_p2.get("porcentaje") == 50.0 and fila_p2.get("ingresos_dia") == 3000, str(fila_p2))

    # ================= 3. Páginas HTML =================
    status, _, cuerpo = prop_a.pedir("/propietario/panel")
    verificar("Panel consolidado (HTML) muestra totales y la comparativa",
              status == 200 and "$5.000" in cuerpo and "66.67" in cuerpo and p2.nombre in cuerpo, f"status={status}")
    status, _, cuerpo = prop_a.pedir(f"/propietario/parqueaderos/{p1.id}")
    verificar("Panel individual (HTML) muestra plazas, ingresos y alertas",
              status == 200 and "$2.000" in cuerpo and "75" in cuerpo and "Afectada" in cuerpo, f"status={status}")

    # ================= 4. Aislamiento: cambiar el id no da acceso =================
    for ruta in (f"/propietario/parqueaderos/{p1.id}", f"/propietario/api/parqueaderos/{p1.id}/metricas"):
        status, _, _ = prop_b.pedir(ruta)
        verificar(f"B pide {ruta.split('/')[-1] if 'api' in ruta else 'panel'} de A -> 403", status == 403, f"status={status}")
    status, _, cuerpo = prop_b.pedir("/propietario/api/metricas")
    verificar("El consolidado de B no incluye datos de A",
              status == 200 and [p["id"] for p in json.loads(cuerpo)["parqueaderos"]] == [pb.id]
              and json.loads(cuerpo)["ingresos_dia"] == 3000)
    status, _, _ = prop_b.pedir(f"/admin/parqueaderos/{p1.id}/espacios")
    verificar("B lista los espacios de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"/admin/espacios/{e1['M02'].id}/editar")
    verificar("B abre la edición de un espacio de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"/admin/espacios/{e1['M02'].id}/estado", {"estado": "mantenimiento"})
    db.expire_all()
    verificar("B cambia el estado de un espacio de A -> 403 y no cambia",
              status == 403 and db.get(Espacio, e1["M02"].id).estado == EstadoEspacio.DISPONIBLE, f"status={status}")
    status, _, _ = prop_b.pedir(f"/operacion/parqueaderos/{p1.id}/estancias/1/cuenta")
    verificar("B abre una cuenta de A -> 403", status == 403, f"status={status}")

    # Operario asignado: opera, pero no ve ingresos
    correo_op = f"oper_pan_{SUFIJO}@parksmart-prueba.com"
    operario = Navegador(base)
    operario.registrar("Operario Panel", correo_op, "cliente")
    u_op = db.query(Usuario).filter(Usuario.correo == correo_op).one()
    u_op.rol = RolUsuario.OPERARIO
    db.add(ParqueaderoOperario(parqueadero_id=p1.id, usuario_id=u_op.id))
    db.flush()
    operario.login(correo_op)
    status, _, _ = operario.pedir(f"/propietario/api/parqueaderos/{p1.id}/metricas")
    verificar("Operario asignado no ve las métricas con ingresos -> 403", status == 403, f"status={status}")
    status, _, _ = operario.pedir(f"/operacion/parqueaderos/{p1.id}")
    verificar("  ...pero sí opera su parqueadero -> 200", status == 200, f"status={status}")
    cliente.login(correo_cli)
    status, _, _ = cliente.pedir("/propietario/api/metricas")
    verificar("Cliente pide métricas -> 403", status == 403, f"status={status}")


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
