"""
Pruebas de la Etapa 4: panel del admin_global.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- Aprobar y rechazar parqueaderos pendientes (solo pendientes; solo admin).
- Suspender y activar usuarios (no a sí mismo ni a otro admin); un usuario
  suspendido no puede iniciar sesión.
- Estadísticas globales con datos reales de pagos y reservas (se comparan
  diferencias antes/después porque la base puede tener otros datos).
- Cancelar una reserva desde el admin no libera una plaza ocupada.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_admin.py
"""

import json
import sys
import threading
import time
from datetime import timedelta
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
    Espacio, EstadoEspacio, EstadoParqueadero, EstadoReserva, Estancia, MetodoPago, Parqueadero, Reserva,
    RolUsuario, TipoVehiculo, Usuario, Vehiculo,
)
from app.services.ocupacion import registrar_ingreso_directo, registrar_salida  # noqa: E402
from app.services.pagos import registrar_pago  # noqa: E402
from app.services.reservas import ahora_negocio  # noqa: E402


def ejecutar_pruebas(base, db: Session):
    ahora = ahora_negocio()

    def usuario(correo):
        db.expire_all()
        return db.query(Usuario).filter(Usuario.correo == correo).one()

    def parqueadero(nombre):
        db.expire_all()
        return db.query(Parqueadero).filter(Parqueadero.nombre == nombre).one()

    # --- Preparar: admin, propietario con dos parqueaderos pendientes, cliente ---
    correo_admin = f"admin_et4_{SUFIJO}@parksmart-prueba.com"
    admin = Navegador(base)
    admin.registrar("Admin Etapa4", correo_admin, "cliente")
    usuario(correo_admin).rol = RolUsuario.ADMIN_GLOBAL
    db.flush()
    admin.login(correo_admin)

    # ================= 0. Solo el admin crea cuentas de propietario =================
    correo_prop = f"prop_et4_{SUFIJO}@parksmart-prueba.com"
    prop = Navegador(base)
    status, _, cuerpo = prop.registrar("Propietario Etapa4", correo_prop, "propietario")
    verificar("Registro público como propietario -> 400 y no se crea la cuenta",
              status == 400 and db.query(Usuario).filter(Usuario.correo == correo_prop).first() is None,
              f"status={status}")

    status, _, cuerpo = admin.pedir("/admin/usuarios/crear")
    verificar("Admin abre el formulario 'Crear propietario'",
              status == 200 and 'name="correo"' in cuerpo and 'name="password"' in cuerpo, f"status={status}")
    verificar("  ...con el campo obligatorio 'Número de cédula' (solo dígitos)",
              'name="cedula"' in cuerpo and "Número de cédula" in cuerpo and 'pattern="[0-9]{4,15}"' in cuerpo)
    datos_prop = {"nombre": "Propietario Etapa4", "correo": correo_prop, "telefono": "301 555 0000",
                  "cedula": "1012345678", "password": "inicial123", "confirmar_password": "inicial123"}
    for descripcion, cedula in (("Sin cédula", ""), ("Cédula con letras", "10a2345"), ("Cédula muy corta", "123")):
        status, _, cuerpo = admin.pedir("/admin/usuarios/crear", {**datos_prop, "cedula": cedula})
        verificar(f"{descripcion} -> 400 y no se crea",
                  status == 400 and "cédula" in cuerpo
                  and db.query(Usuario).filter(Usuario.correo == correo_prop).first() is None, f"status={status}")
    status, location, _ = admin.pedir("/admin/usuarios/crear", {**datos_prop, "cedula": " 1012345678 "})
    nuevo = db.query(Usuario).filter(Usuario.correo == correo_prop).first()
    db.expire_all()
    verificar("Admin crea la cuenta -> 303 a Usuarios, rol propietario y activa",
              status == 303 and location == "/admin/usuarios" and nuevo is not None
              and nuevo.rol == RolUsuario.PROPIETARIO and nuevo.activo, f"status={status}")
    verificar("  ...con la cédula guardada como texto de dígitos (sin espacios)",
              nuevo is not None and nuevo.cedula == "1012345678", f"{nuevo.cedula if nuevo else None}")
    _, _, cuerpo = admin.pedir("/admin/usuarios")
    verificar("  ...con mensaje de confirmación", "creada" in cuerpo and correo_prop in cuerpo)
    status, location, _ = prop.pedir("/login", {"correo": correo_prop, "password": "inicial123"})
    verificar("  ...el propietario entra con la contraseña inicial y va a su panel",
              status == 302 and location == "/propietario/panel", f"status={status} location={location}")

    status, _, cuerpo = admin.pedir("/admin/usuarios/crear", datos_prop)
    verificar("Correo repetido -> 400 con mensaje claro",
              status == 400 and "ya está registrado" in cuerpo
              and db.query(Usuario).filter(Usuario.correo == correo_prop).count() == 1, f"status={status}")
    status, _, cuerpo = admin.pedir("/admin/usuarios/crear", {**datos_prop, "correo": f"x_{correo_prop}",
                                                               "confirmar_password": "otra123"})
    verificar("Contraseñas distintas -> 400 y no se crea",
              status == 400 and "no coinciden" in cuerpo
              and db.query(Usuario).filter(Usuario.correo == f"x_{correo_prop}").first() is None, f"status={status}")
    status, _, cuerpo = admin.pedir("/admin/usuarios/crear", {**datos_prop, "correo": f"y_{correo_prop}",
                                                               "rol": "admin_global"})
    creado = db.query(Usuario).filter(Usuario.correo == f"y_{correo_prop}").first()
    verificar("Enviar rol=admin_global en el formulario no sirve: siempre crea propietario",
              creado is not None and creado.rol == RolUsuario.PROPIETARIO, f"status={status}")

    prop.pedir("/admin/parqueaderos/crear", datos_creacion(f"Aprobable {SUFIJO}"))
    prop.pedir("/admin/parqueaderos/crear", datos_creacion(f"Rechazable {SUFIJO}"))
    p_ok, p_no = parqueadero(f"Aprobable {SUFIJO}"), parqueadero(f"Rechazable {SUFIJO}")

    correo_cli = f"cli_et4_{SUFIJO}@parksmart-prueba.com"
    cliente = Navegador(base)
    cliente.registrar("Cliente Etapa4", correo_cli, "cliente")
    cliente.login(correo_cli)
    for quien, nav in (("propietario", prop), ("cliente", cliente)):
        status, _, _ = nav.pedir("/admin/usuarios/crear", {**datos_prop, "correo": f"{quien}_{correo_prop}"})
        verificar(f"Un {quien} no puede crear cuentas de propietario -> 403",
                  status == 403 and db.query(Usuario).filter(Usuario.correo == f"{quien}_{correo_prop}").first() is None,
                  f"status={status}")

    # Estadísticas ANTES (para comparar diferencias)
    _, _, cuerpo = admin.pedir("/api/estadisticas/resumen")
    antes = json.loads(cuerpo) if cuerpo.startswith("{") else {}

    # ================= 1. Aprobar / rechazar =================
    status, _, cuerpo = admin.pedir("/admin")
    verificar("Dashboard del admin lista los parqueaderos pendientes con Aprobar/Rechazar",
              status == 200 and p_ok.nombre in cuerpo
              and f"/admin/parqueaderos/{p_ok.id}/aprobar" in cuerpo and f"/admin/parqueaderos/{p_ok.id}/rechazar" in cuerpo,
              f"status={status}")
    _, _, cuerpo = admin.pedir("/admin/parqueaderos")
    verificar("La lista de parqueaderos también tiene Aprobar/Rechazar en los pendientes",
              f"/admin/parqueaderos/{p_ok.id}/aprobar" in cuerpo)

    status, _, _ = prop.pedir(f"/admin/parqueaderos/{p_ok.id}/aprobar", {})
    verificar("El propietario no puede aprobarse a sí mismo -> 403",
              status == 403 and parqueadero(p_ok.nombre).estado == EstadoParqueadero.PENDIENTE, f"status={status}")
    status, _, _ = cliente.pedir(f"/admin/parqueaderos/{p_ok.id}/aprobar", {})
    verificar("Un cliente no puede aprobar -> 403", status == 403, f"status={status}")

    status, _, _ = admin.pedir(f"/admin/parqueaderos/{p_ok.id}/aprobar", {})
    verificar("Admin aprueba -> 303 y queda activo",
              status == 303 and parqueadero(p_ok.nombre).estado == EstadoParqueadero.ACTIVO, f"status={status}")
    status, _, _ = cliente.pedir(f"/parqueaderos/{p_ok.id}")
    verificar("  ...y aparece en el catálogo público", status == 200, f"status={status}")
    _, _, cuerpo = admin.pedir("/admin")
    verificar("  ...con mensaje de confirmación", "aprobado" in cuerpo)

    status, _, _ = admin.pedir(f"/admin/parqueaderos/{p_no.id}/rechazar", {})
    verificar("Admin rechaza -> 303 y queda rechazado",
              status == 303 and parqueadero(p_no.nombre).estado == EstadoParqueadero.RECHAZADO, f"status={status}")
    status, _, _ = cliente.pedir(f"/parqueaderos/{p_no.id}")
    verificar("  ...y no aparece en el catálogo", status == 404, f"status={status}")

    status, _, _ = admin.pedir(f"/admin/parqueaderos/{p_no.id}/aprobar", {})
    verificar("Aprobar uno que no está pendiente -> 400 y no cambia",
              status == 400 and parqueadero(p_no.nombre).estado == EstadoParqueadero.RECHAZADO, f"status={status}")

    # ================= 2. Suspender / activar usuarios =================
    status, _, cuerpo = admin.pedir("/admin/usuarios")
    verificar("Usuarios lista todos los roles (propietario y cliente) con su estado",
              status == 200 and correo_prop in cuerpo and correo_cli in cuerpo and "Activo" in cuerpo, f"status={status}")
    id_cli = usuario(correo_cli).id
    status, _, _ = admin.pedir(f"/admin/usuarios/{id_cli}/suspender", {})
    verificar("Suspender un cliente -> 303 y activo = False",
              status == 303 and usuario(correo_cli).activo is False, f"status={status}")
    status, _, cuerpo = Navegador(base).login(correo_cli)
    verificar("  ...el suspendido no puede iniciar sesión", status == 403 and "suspendida" in cuerpo, f"status={status}")
    status, location, _ = cliente.pedir("/mis-reservas")
    verificar("  ...y su sesión abierta se cierra", status == 303 and location == "/login", f"status={status}")
    status, _, _ = admin.pedir(f"/admin/usuarios/{id_cli}/activar", {})
    verificar("Activar de nuevo -> activo = True y puede iniciar sesión",
              status == 303 and usuario(correo_cli).activo is True and Navegador(base).login(correo_cli)[0] == 302)

    status, _, _ = admin.pedir(f"/admin/usuarios/{usuario(correo_admin).id}/suspender", {})
    verificar("El admin no puede suspenderse a sí mismo -> 400",
              status == 400 and usuario(correo_admin).activo is True, f"status={status}")
    correo_admin2 = f"admin2_et4_{SUFIJO}@parksmart-prueba.com"
    Navegador(base).registrar("Admin Dos", correo_admin2, "cliente")
    usuario(correo_admin2).rol = RolUsuario.ADMIN_GLOBAL
    db.flush()
    status, _, _ = admin.pedir(f"/admin/usuarios/{usuario(correo_admin2).id}/suspender", {})
    verificar("Tampoco a otro admin_global -> 400", status == 400 and usuario(correo_admin2).activo is True,
              f"status={status}")
    status, _, _ = prop.pedir(f"/admin/usuarios/{id_cli}/suspender", {})
    verificar("Un propietario no puede suspender usuarios -> 403", status == 403, f"status={status}")

    # ================= 2b. Buscar por nombre =================
    for nombre_buscado in (f"Zeta{SUFIJO} Ana", f"ZETA{SUFIJO} BETO"):
        Navegador(base).registrar(nombre_buscado, f"{nombre_buscado.split()[1].lower()}_{SUFIJO}@parksmart-prueba.com", "cliente")
    _, _, cuerpo = admin.pedir(f"/admin/usuarios?q=eta{SUFIJO.upper()}")
    verificar("Buscar por nombre: coincidencia parcial sin importar mayúsculas (encuentra a los dos)",
              f"Zeta{SUFIJO} Ana" in cuerpo and f"ZETA{SUFIJO} BETO" in cuerpo and correo_prop not in cuerpo)
    verificar("  ...el campo conserva lo buscado", f'value="eta{SUFIJO.upper()}"' in cuerpo)
    _, _, cuerpo = admin.pedir(f"/admin/usuarios?q=zeta{SUFIJO}+beto")
    verificar("  ...busca el texto completo ('zeta... beto' solo encuentra a Beto)",
              f"ZETA{SUFIJO} BETO" in cuerpo and f"Zeta{SUFIJO} Ana" not in cuerpo)
    _, _, cuerpo = admin.pedir(f"/admin/usuarios?q=zeta{SUFIJO}&rol=propietario")
    verificar("  ...se combina con el filtro de rol (ninguno es propietario)", f"Zeta{SUFIJO} Ana" not in cuerpo)
    _, _, cuerpo = admin.pedir("/admin/usuarios?q=%25")
    verificar("  ...un '%' se busca como texto, no como comodín", correo_prop not in cuerpo)
    _, _, cuerpo = admin.pedir("/admin/usuarios?q=")
    verificar("Limpiar la búsqueda vuelve al listado completo",
              f"Zeta{SUFIJO} Ana" in cuerpo and correo_prop in cuerpo)

    # ================= 2c. Cambiar contraseña desde Gestión de usuarios =================
    id_prop = usuario(correo_prop).id
    _, _, cuerpo = admin.pedir(f"/admin/usuarios?q=Propietario+Etapa4")
    verificar("Cada usuario tiene la acción 'Cambiar contraseña' que identifica la cuenta",
              f"/admin/usuarios/{id_prop}/contrasena" in cuerpo and "Nueva contraseña para" in cuerpo)
    verificar("  ...el listado no muestra contraseñas ni hashes", "$2b$" not in cuerpo and "inicial123" not in cuerpo)
    hash_antes = usuario(correo_prop).password
    for descripcion, datos, mensaje in (
        ("Confirmación distinta", {"password": "nueva456", "confirmar_password": "otra456"}, "no coinciden"),
        ("Contraseña muy corta", {"password": "abc", "confirmar_password": "abc"}, "al menos 6"),
    ):
        status, location, _ = admin.pedir(f"/admin/usuarios/{id_prop}/contrasena", datos)
        _, _, cuerpo = admin.pedir(location or "/admin/usuarios")
        verificar(f"{descripcion} -> vuelve a Gestión de usuarios con el error y no cambia",
                  status == 303 and mensaje in cuerpo and usuario(correo_prop).password == hash_antes,
                  f"status={status}")
    status, location, _ = admin.pedir(f"/admin/usuarios/{id_prop}/contrasena",
                                      {"password": "nueva456", "confirmar_password": "nueva456", "q": "Etapa4"})
    _, _, cuerpo = admin.pedir(location or "/admin/usuarios")
    verificar("Cambio válido -> vuelve a la misma vista (conserva la búsqueda) con el resultado",
              status == 303 and location == "/admin/usuarios?q=Etapa4"
              and "Contraseña actualizada" in cuerpo and "Propietario Etapa4" in cuerpo, f"location={location}")
    verificar("  ...la contraseña no aparece en la página", "nueva456" not in cuerpo)
    verificar("  ...la vieja ya no sirve y la nueva sí",
              Navegador(base).pedir("/login", {"correo": correo_prop, "password": "inicial123"})[0] == 401
              and Navegador(base).pedir("/login", {"correo": correo_prop, "password": "nueva456"})[0] == 302)
    status, _, _ = admin.pedir(f"/admin/usuarios/{usuario(correo_admin2).id}/contrasena",
                               {"password": "hackeo123", "confirmar_password": "hackeo123"})
    verificar("No puede cambiar la contraseña de otro admin_global -> 400", status == 400, f"status={status}")
    status, _, _ = prop.pedir(f"/admin/usuarios/{id_cli}/contrasena",
                              {"password": "hackeo123", "confirmar_password": "hackeo123"})
    verificar("Un propietario no puede cambiar contraseñas ajenas -> 403", status == 403, f"status={status}")

    # ================= 2d. Completar la cédula de un propietario anterior =================
    correo_viejo = f"prop_viejo_{SUFIJO}@parksmart-prueba.com"
    arnes.crear_propietario("Propietario Anterior", correo_viejo)   # sin cédula, como los de antes
    id_viejo = usuario(correo_viejo).id
    _, _, cuerpo = admin.pedir("/admin/usuarios?q=Propietario+Anterior")
    verificar("Un propietario sin cédula aparece marcado y con la acción para completarla",
              "Sin cédula" in cuerpo and f"/admin/usuarios/{id_viejo}/cedula" in cuerpo)
    status, location, _ = admin.pedir(f"/admin/usuarios/{id_viejo}/cedula", {"cedula": "12x45"})
    _, _, cuerpo = admin.pedir(location or "/admin/usuarios")
    verificar("Cédula inválida al completarla -> error en la misma vista y no se guarda",
              "cédula" in cuerpo and usuario(correo_viejo).cedula is None)
    status, _, _ = admin.pedir(f"/admin/usuarios/{id_viejo}/cedula", {"cedula": "79876543"})
    verificar("Completar la cédula -> se guarda", usuario(correo_viejo).cedula == "79876543", f"status={status}")
    status, _, _ = admin.pedir(f"/admin/usuarios/{id_cli}/cedula", {"cedula": "79876543"})
    verificar("La cédula solo se edita en cuentas de propietario -> 400 para un cliente",
              status == 400 and usuario(correo_cli).cedula is None, f"status={status}")

    # ================= 3. Estadísticas globales =================
    p_ok = parqueadero(p_ok.nombre)
    usuario_prop = usuario(correo_prop)
    estancia, _ = registrar_ingreso_directo(db, p_ok, usuario_prop, f"AD{SUFIJO[:4]}".upper(),
                                            TipoVehiculo.CARRO, ahora - timedelta(minutes=59))
    cuenta = registrar_salida(db, p_ok, usuario_prop, estancia.id, ahora - timedelta(seconds=10))
    registrar_pago(db, cuenta, MetodoPago.EFECTIVO, 3000, f"adm-{SUFIJO}", usuario_prop, ahora - timedelta(seconds=5))

    u_cli = usuario(correo_cli)
    carro = Vehiculo(usuario_id=u_cli.id, placa=f"AV{SUFIJO[:5]}".upper(), tipo=TipoVehiculo.CARRO)
    db.add(carro)
    db.flush()
    espacios = {e.numero: e for e in db.query(Espacio).filter(Espacio.parqueadero_id == p_ok.id)}
    inicio_hoy = ahora.replace(hour=23, minute=0, second=0, microsecond=0)
    reserva = Reserva(usuario_id=u_cli.id, vehiculo_id=carro.id, espacio_id=espacios["C02"].id,
                      inicio=inicio_hoy, fin=inicio_hoy + timedelta(minutes=30), margen_minutos=15,
                      tarifa_hora=3000, total=1500, estado=EstadoReserva.CONFIRMADA)
    db.add(reserva)
    db.flush()

    status, _, cuerpo = admin.pedir("/api/estadisticas/resumen")
    despues = json.loads(cuerpo) if status == 200 else {}
    verificar("Resumen global (JSON) -> 200", status == 200, f"status={status}")
    if antes and despues:
        verificar("Ingresos de hoy suben exactamente $3000 (pagos reales, no totales de reservas)",
                  despues["ingresos_dia"] - antes["ingresos_dia"] == 3000,
                  f"{antes['ingresos_dia']} -> {despues['ingresos_dia']}")
        verificar("Parqueaderos: un activo más, un rechazado más, dos pendientes menos",
                  despues["parqueaderos"]["activo"] - antes["parqueaderos"].get("activo", 0) == 1
                  and despues["parqueaderos"]["rechazado"] - antes["parqueaderos"].get("rechazado", 0) == 1
                  and despues["parqueaderos"]["pendiente"] == antes["parqueaderos"]["pendiente"] - 2,
                  f"{antes['parqueaderos']} -> {despues['parqueaderos']}")
        verificar("Usuarios por rol incluye propietarios y admins",
                  despues["usuarios"]["admin_global"] >= 2 and despues["usuarios"]["propietario"] >= 1)

    status, _, cuerpo = admin.pedir("/api/estadisticas/ingresos-por-mes")
    datos = json.loads(cuerpo) if status == 200 else {}
    verificar("Ingresos por mes: 6 meses con año en la etiqueta (no mezcla años)",
              len(datos.get("labels", [])) == 6 and str(ahora.year) in datos["labels"][-1], str(datos.get("labels")))
    status, _, cuerpo = admin.pedir("/api/estadisticas/reservas-por-dia")
    datos = json.loads(cuerpo) if status == 200 else {}
    verificar("Reservas por día: 7 días seguidos (con ceros) y la de hoy cuenta",
              len(datos.get("labels", [])) == 7 and datos["data"][-1] >= 1, str(datos))
    status, _, cuerpo = admin.pedir("/admin/estadisticas")
    verificar("Página de estadísticas -> 200", status == 200 and "chartIngresos" in cuerpo, f"status={status}")

    # ================= 4. Cancelar desde el admin no libera una plaza ocupada =================
    reserva.inicio, reserva.fin = ahora - timedelta(minutes=10), ahora + timedelta(minutes=50)
    db.add(Estancia(parqueadero_id=p_ok.id, espacio_id=espacios["C02"].id, placa=f"OT{SUFIJO[:4]}".upper(),
                    tipo_vehiculo=TipoVehiculo.CARRO, entrada_en=ahora - timedelta(minutes=5), tarifa_hora=3000,
                    registrado_por=usuario_prop.id))
    espacios["C02"].estado = EstadoEspacio.OCUPADO
    db.flush()
    status, _, _ = admin.pedir(f"/admin/reservas/{reserva.id}/estado", {"estado": "cancelada"})
    db.expire_all()
    r = db.get(Reserva, reserva.id)
    verificar("Admin cancela una reserva: queda cancelada con motivo admin y fecha",
              r.estado == EstadoReserva.CANCELADA and r.motivo_cancelacion is not None
              and r.motivo_cancelacion.value == "admin" and r.cancelada_en is not None, f"status={status}")
    verificar("  ...y NO marca disponible una plaza con un vehículo dentro",
              db.get(Espacio, espacios["C02"].id).estado == EstadoEspacio.OCUPADO)

    otra = Reserva(usuario_id=u_cli.id, vehiculo_id=carro.id, espacio_id=espacios["C03"].id,
                   inicio=ahora + timedelta(days=2), fin=ahora + timedelta(days=2, hours=1), margen_minutos=15,
                   tarifa_hora=3000, total=3000, estado=EstadoReserva.CONFIRMADA)
    db.add(otra)
    db.flush()
    status, _, _ = admin.pedir(f"/admin/reservas/{otra.id}/estado", {"estado": "completada"})
    db.expire_all()
    verificar("Admin no puede 'completar' a mano (eso lo hace la salida con su cobro) -> 400",
              status == 400 and db.get(Reserva, otra.id).estado == EstadoReserva.CONFIRMADA, f"status={status}")
    status, _, cuerpo = admin.pedir("/admin/reservas")
    verificar("Gestión de reservas abre aunque haya reservas sin las columnas viejas",
              status == 200 and f"AV{SUFIJO[:5]}".upper() in cuerpo, f"status={status}")


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
