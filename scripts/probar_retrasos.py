"""
Pruebas del Módulo 3: protocolo de retraso, reasignación y extensiones.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

El protocolo se prueba llamando a procesar_retrasos(db, ahora) con horas
fijas: así se simula el paso del tiempo (T-30, T, T+15...) sin esperar.

Escenarios (T = fin de la reserva A a las 17:00):
1. Con plaza alternativa: A sigue dentro; B tiene C01 a las 18:00.
   - 16:30 recordatorio a A · 17:00 A excedida · 17:15 alerta al propietario
   - 17:30 (30 min antes de B) B pasa a C02, sin costo; C03 no sirve porque
     su agenda choca con B.
   - Repetir cualquier paso no duplica nada.
2. Sin plaza alternativa: la otra plaza está físicamente ocupada.
   - Incidencia sin_alternativa y aviso a B; si A sale a tiempo, la
     incidencia se resuelve y B conserva su plaza.
3. Extensión pedida por el cliente: solo si respeta la siguiente reserva
   y su margen.
4. Endpoint /tareas/retrasos protegido con clave.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_retrasos.py
"""

import os
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
    Cuenta, Espacio, EstadoIncidencia, EstadoParqueadero, EstadoReserva, Estancia, Incidencia,
    Notificacion, Parqueadero, Reserva, ReservaReasignacion, TipoIncidencia, TipoVehiculo, Usuario, Vehiculo,
)
from app.services.ocupacion import registrar_entrada_reserva, registrar_salida  # noqa: E402
from app.services.reservas import ZONA_NEGOCIO, ahora_negocio  # noqa: E402
from app.services.retrasos import procesar_retrasos  # noqa: E402


def ejecutar_pruebas(base, db: Session):
    # Día de prueba: pasado mañana, para no chocar con datos reales de hoy
    dia = (ahora_negocio() + timedelta(days=2)).date()

    def h(hora):
        horas, minutos = map(int, hora.split(":"))
        return datetime(dia.year, dia.month, dia.day, horas, minutos, tzinfo=ZONA_NEGOCIO)

    def correr(hora, parqueadero=None):
        resumen = procesar_retrasos(db, h(hora), parqueadero.id if parqueadero else None)
        db.expire_all()
        return resumen

    # --- Preparar propietario, parqueadero y clientes ---
    correo_prop = f"prop_ret_{SUFIJO}@parksmart-prueba.com"
    prop = Navegador(base)
    arnes.crear_propietario("Propietario Retrasos", correo_prop)
    prop.login(correo_prop)
    usuario_prop = db.query(Usuario).filter(Usuario.correo == correo_prop).one()

    def parqueadero(nombre, carros):
        prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre, cantidad_carros=str(carros)))
        db.expire_all()
        p = db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()
        p.estado = EstadoParqueadero.ACTIVO
        db.flush()
        return p, {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == p.id)}

    clientes = {}
    for letra in ("A", "B", "C"):
        correo = f"cli_{letra.lower()}_ret_{SUFIJO}@parksmart-prueba.com"
        nav = Navegador(base)
        nav.registrar(f"Cliente {letra}", correo, "cliente")
        nav.login(correo)
        u = db.query(Usuario).filter(Usuario.correo == correo).one()
        v = Vehiculo(usuario_id=u.id, placa=f"{letra}R{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
        db.add(v)
        db.flush()
        clientes[letra] = (nav, u, v)

    def reserva(letra, espacio, inicio, fin, estado=EstadoReserva.CONFIRMADA):
        _, u, v = clientes[letra]
        r = Reserva(usuario_id=u.id, vehiculo_id=v.id, espacio_id=espacio.id, inicio=h(inicio), fin=h(fin),
                    margen_minutos=15, tarifa_hora=3000, total=6000, estado=estado)
        db.add(r)
        db.flush()
        return r

    def dentro(parq, espacio, r=None, placa=None, desde="15:00"):
        e = Estancia(parqueadero_id=parq.id, espacio_id=espacio.id, reserva_id=r.id if r else None,
                     placa=placa or clientes["A"][2].placa, tipo_vehiculo=TipoVehiculo.CARRO,
                     entrada_en=h(desde), tarifa_hora=3000, registrado_por=usuario_prop.id)
        db.add(e)
        db.flush()
        return e

    def notifs(reserva_id=None, tipo=None, usuario_id=None):
        q = db.query(Notificacion)
        if reserva_id:
            q = q.filter(Notificacion.reserva_id == reserva_id)
        if tipo:
            q = q.filter(Notificacion.tipo == tipo)
        if usuario_id:
            q = q.filter(Notificacion.usuario_id == usuario_id)
        return q.all()

    def incidencias(reserva_id, tipo=None):
        q = db.query(Incidencia).filter(Incidencia.reserva_id == reserva_id)
        if tipo:
            q = q.filter(Incidencia.tipo == tipo)
        return q.all()

    # ================= ESCENARIO 1: con plaza alternativa =================
    p1, e1 = parqueadero(f"Retrasos 1 {SUFIJO}", 3)
    a = reserva("A", e1["C01"], "15:00", "17:00", EstadoReserva.EN_CURSO)
    estancia_a = dentro(p1, e1["C01"], a)
    b = reserva("B", e1["C01"], "18:00", "19:00")
    reserva("C", e1["C03"], "18:30", "19:30")       # C03 libre ahora, pero su agenda choca con B

    correr("16:20", p1)
    verificar("16:20: todavía no hay recordatorio (T-30 = 16:30)", not notifs(a.id, "recordatorio_fin"))
    correr("16:30", p1)
    correr("16:31", p1)
    recordatorios = notifs(a.id, "recordatorio_fin")
    verificar("16:30: un recordatorio al cliente A (repetir no lo duplica)",
              len(recordatorios) == 1 and recordatorios[0].usuario_id == clientes["A"][1].id,
              f"{len(recordatorios)}")

    correr("17:00", p1)
    a = db.get(Reserva, a.id)
    verificar("17:00 (T): A sigue dentro -> excedida, con inicio del tiempo extra",
              a.estado == EstadoReserva.EXCEDIDA and a.exceso_desde == h("17:00"), f"{a.estado}")
    verificar("  ...incidencia de exceso y aviso al cliente A",
              len(incidencias(a.id, "exceso")) == 1 and len(notifs(a.id, "exceso")) == 1)
    verificar("  ...todavía sin alerta al propietario (T+15 = 17:15)", not notifs(tipo="alerta_retraso", usuario_id=usuario_prop.id))

    correr("17:15", p1)
    correr("17:16", p1)
    alertas = notifs(b.id, "alerta_retraso", usuario_prop.id)
    verificar("17:15: alerta prioritaria al propietario (una sola)",
              len(alertas) == 1 and alertas[0].prioridad == "alta", f"{len(alertas)}")
    verificar("  ...incidencia de retraso vinculada a la reserva afectada (B)",
              len(incidencias(b.id, "retraso")) == 1)
    verificar("  ...B sigue en C01 (la búsqueda empieza 30 min antes de B)", db.get(Reserva, b.id).espacio_id == e1["C01"].id)

    total_b = db.get(Reserva, b.id).total
    correr("17:30", p1)
    b = db.get(Reserva, b.id)
    verificar("17:30: B se reasigna a C02 (C03 choca por agenda)", b.espacio_id == e1["C02"].id,
              f"espacio={b.espacio.numero}")
    verificar("  ...sin costo adicional y sigue confirmada", b.total == total_b and b.estado == EstadoReserva.CONFIRMADA)
    historial = db.query(ReservaReasignacion).filter(ReservaReasignacion.reserva_id == b.id).all()
    verificar("  ...historial con plaza original y nueva",
              len(historial) == 1 and historial[0].espacio_origen_id == e1["C01"].id
              and historial[0].espacio_destino_id == e1["C02"].id)
    aviso_b = notifs(b.id, "reasignacion")
    verificar("  ...aviso a B con el nuevo código", len(aviso_b) == 1 and "C02" in aviso_b[0].mensaje)
    verificar("  ...incidencia de reasignación y la de retraso resuelta",
              len(incidencias(b.id, "reasignacion")) == 1
              and incidencias(b.id, "retraso")[0].estado == EstadoIncidencia.RESUELTA)

    antes = (len(notifs()), db.query(Incidencia).count(), db.query(ReservaReasignacion).count())
    correr("17:31", p1)
    correr("17:31", p1)
    despues = (len(notifs()), db.query(Incidencia).count(), db.query(ReservaReasignacion).count())
    verificar("Reintentos (17:31 dos veces): no duplican avisos, incidencias ni reasignaciones",
              antes == despues, f"{antes} -> {despues}")

    estancia_b, _ = registrar_entrada_reserva(db, p1, usuario_prop, b.id, h("17:50"))
    verificar("B puede hacer check-in en su nueva plaza", estancia_b.espacio_id == e1["C02"].id)

    cuenta_a = registrar_salida(db, p1, usuario_prop, estancia_a.id, h("17:40"))
    verificar("A sale 17:40: cobra 45 min extra ($2250) + recargo ($1125) sobre lo pactado ($6000)",
              cuenta_a.minutos_extra == 45 and cuenta_a.total == 9375, f"total={cuenta_a.total}")
    db.expire_all()
    verificar("  ...la incidencia de exceso de A queda resuelta",
              incidencias(a.id, "exceso")[0].estado == EstadoIncidencia.RESUELTA)

    # ================= ESCENARIO 2: sin plaza alternativa =================
    p2, e2 = parqueadero(f"Retrasos 2 {SUFIJO}", 2)
    a2 = reserva("A", e2["C01"], "15:00", "17:00", EstadoReserva.EN_CURSO)
    estancia_a2 = dentro(p2, e2["C01"], a2)
    b2 = reserva("B", e2["C01"], "18:00", "19:00")
    dentro(p2, e2["C02"], placa=f"WK{SUFIJO[:5]}".upper(), desde="16:00")   # C02 ocupada físicamente

    correr("17:30", p2)
    correr("17:31", p2)
    b2 = db.get(Reserva, b2.id)
    sin_alt = incidencias(b2.id, "sin_alternativa")
    verificar("Sin plaza libre: B2 se queda en C01 con incidencia sin_alternativa (una sola)",
              b2.espacio_id == e2["C01"].id and len(sin_alt) == 1)
    aviso = notifs(b2.id, "sin_alternativa", clientes["B"][1].id)
    verificar("  ...aviso a B2 ofreciendo cambio de horario o reembolso",
              len(aviso) == 1 and "reembolso" in aviso[0].mensaje)
    verificar("  ...alerta al propietario", len(notifs(b2.id, "sin_alternativa", usuario_prop.id)) == 1)

    registrar_salida(db, p2, usuario_prop, estancia_a2.id, h("17:40"))
    correr("17:41", p2)
    b2 = db.get(Reserva, b2.id)
    verificar("A2 sale a tiempo: incidencias de B2 resueltas y conserva su plaza",
              b2.espacio_id == e2["C01"].id and b2.estado == EstadoReserva.CONFIRMADA
              and all(i.estado == EstadoIncidencia.RESUELTA for i in incidencias(b2.id)))
    verificar("  ...B2 recibe el aviso de que se resolvió", len(notifs(b2.id, "incidencia_resuelta")) == 1)

    # ================= ESCENARIO 3: extensiones =================
    p3, e3 = parqueadero(f"Retrasos 3 {SUFIJO}", 1)
    nav_a = clientes["A"][0]
    ext = reserva("A", e3["C01"], "10:00", "11:00")
    reserva("B", e3["C01"], "12:00", "13:00")

    def extender(r, hora, nav=nav_a):
        return nav.pedir(f"/reservas/{r.id}/extender", {"nueva_hora_fin": hora})

    status, _, _ = extender(ext, "11:30")
    db.expire_all()
    ext = db.get(Reserva, ext.id)
    verificar("Extender hasta 11:30 (respeta B a las 12:00 con margen 15) -> 302",
              status == 302 and ext.fin == h("11:30") and ext.fin_original == h("11:00"), f"status={status}")
    verificar("  ...cobra 30 min más a $3000/h ($6000 + $1500)", ext.total == 7500, f"total={ext.total}")
    status, _, cuerpo = extender(ext, "11:50")
    db.expire_all()
    verificar("Extender hasta 11:50 (11:50 + 15 > 12:00) -> 400 y no cambia",
              status == 400 and db.get(Reserva, ext.id).fin == h("11:30"), f"status={status}")
    status, _, _ = extender(ext, "11:00")
    verificar("Extender a una hora anterior al fin actual -> 400", status == 400, f"status={status}")
    status, _, _ = extender(ext, "11:45", nav=clientes["C"][0])
    verificar("Otro cliente no puede extender la reserva -> 404", status == 404, f"status={status}")
    status, _, cuerpo = nav_a.pedir("/mis-reservas")
    verificar("Mis reservas muestra los avisos del cliente (recordatorio)", status == 200 and "Te quedan 30 minutos" in cuerpo)

    # ================= ESCENARIO 4: endpoint protegido =================
    anonimo = Navegador(base)
    os.environ.pop("TAREAS_CLAVE", None)
    status, _, _ = anonimo.pedir("/tareas/retrasos", {})
    verificar("Sin TAREAS_CLAVE configurada -> 503", status == 503, f"status={status}")
    os.environ["TAREAS_CLAVE"] = f"clave-{SUFIJO}"
    status, _, _ = anonimo.pedir("/tareas/retrasos", {})
    verificar("Sin cabecera de clave -> 401", status == 401, f"status={status}")
    anonimo.opener.addheaders = [("X-Tarea-Clave", "incorrecta")]
    status, _, _ = anonimo.pedir("/tareas/retrasos", {})
    verificar("Clave incorrecta -> 401", status == 401, f"status={status}")
    anonimo.opener.addheaders = [("X-Tarea-Clave", f"clave-{SUFIJO}")]
    status, _, cuerpo = anonimo.pedir("/tareas/retrasos", {})
    verificar("Clave correcta -> 200 con resumen JSON", status == 200 and "reasignaciones" in cuerpo, f"status={status}")
    os.environ.pop("TAREAS_CLAVE", None)

    # El panel del propietario muestra las incidencias abiertas
    status, _, cuerpo = prop.pedir(f"/operacion/parqueaderos/{p1.id}")
    verificar("Panel de operación abre y muestra la sección de alertas", status == 200 and "Alertas" in cuerpo)


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
