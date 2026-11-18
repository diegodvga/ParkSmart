"""
Pruebas de la ETAPA 2 (autenticación y roles) contra la base real, SIN dejar datos.

Cómo funciona:
1. Abre UNA conexión a Supabase e inicia una transacción externa.
2. Reemplaza get_db para que todas las rutas usen una sesión dentro de esa
   transacción. join_transaction_mode="create_savepoint" hace que cada
   db.commit() de las rutas sea solo un SAVEPOINT, no un commit real.
3. Levanta la app con uvicorn en un hilo y le hace peticiones HTTP con urllib
   (librería estándar; no hace falta instalar httpx ni TestClient).
4. Al final hace ROLLBACK: nada de lo creado queda en la base.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_etapa2.py
"""

import socket
import sys
import threading
import time
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, build_opener

# Permite importar "app" al ejecutar el script desde la raíz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.models import EstadoParqueadero, Parqueadero, RolUsuario, Usuario  # noqa: E402

PASSWORD = "clave123"
SUFIJO = uuid.uuid4().hex[:8]
resultados = []

# Sesión de la transacción de prueba; la asigna main() de cada suite
SESION = None


def crear_propietario(nombre, correo):
    """
    Crea una cuenta de propietario como lo haría el admin_global.

    El registro público solo crea clientes; las suites que necesitan un
    propietario lo insertan directo (la pantalla del admin se prueba en
    scripts/probar_admin.py).
    """
    usuario = Usuario(nombre=nombre, correo=correo, password=hash_password(PASSWORD),
                      telefono="300 123 4567", rol=RolUsuario.PROPIETARIO)
    SESION.add(usuario)
    SESION.flush()
    return usuario


# ============================================
# HELPERS
# ============================================

class SinRedireccion(HTTPRedirectHandler):
    """Evita que urllib siga las redirecciones: queremos ver el 302/303."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Navegador:
    """Un "navegador" con sus propias cookies (su propia sesión)."""

    def __init__(self, base):
        self.base = base
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), SinRedireccion())

    def pedir(self, ruta, datos=None):
        """Hace GET (sin datos) o POST de formulario. Devuelve (status, location, cuerpo)."""
        cuerpo = urlencode(datos).encode() if datos is not None else None
        try:
            resp = self.opener.open(self.base + ruta, data=cuerpo, timeout=60)
            return resp.status, resp.headers.get("Location"), resp.read().decode("utf-8", "replace")
        except HTTPError as error:
            return error.code, error.headers.get("Location"), error.read().decode("utf-8", "replace")

    def registrar(self, nombre, correo, rol):
        return self.pedir("/registro", {
            "nombre": nombre,
            "correo": correo,
            "password": PASSWORD,
            "confirmar_password": PASSWORD,
            "rol": rol,
            "telefono": "300 123 4567",   # obligatorio desde la validación de teléfono
        })

    def login(self, correo):
        return self.pedir("/login", {"correo": correo, "password": PASSWORD})


def verificar(descripcion, condicion, detalle=""):
    """Registra el resultado de una prueba y lo imprime."""
    resultados.append(condicion)
    estado = "OK   " if condicion else "FALLA"
    print(f"[{estado}] {descripcion}" + (f"  -> {detalle}" if detalle and not condicion else ""))


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def datos_parqueadero(nombre, **extra):
    """
    Datos de formulario válidos tanto para crear como para editar:
    - crear usa cantidad_motos/cantidad_carros/cantidad_buses;
    - editar usa capacidad y acepta_*.
    FastAPI ignora los campos que una ruta no declara.
    """
    datos = {
        "nombre": nombre,
        "direccion": "Calle 10 # 5-20",
        "descripcion": "Parqueadero de prueba",
        "ciudad": "Santa Marta",   # obligatoria desde la lista de ciudades
        "capacidad": 10,
        "acepta_carros": "true",
        "acepta_motos": "true",
        "cantidad_motos": 4,
        "cantidad_carros": 6,
        "cantidad_buses": 0,
        "tarifa_hora_carro": 3000,
        "tarifa_hora_moto": 1500,
        "horario_apertura": "06:00",
        "horario_cierre": "22:00",
    }
    datos.update(extra)
    return datos


# ============================================
# PRUEBAS
# ============================================

def ejecutar_pruebas(base, db: Session):
    correo_cliente = f"cliente_{SUFIJO}@parksmart-prueba.com"
    correo_prop_a = f"prop_a_{SUFIJO}@parksmart-prueba.com"
    correo_prop_b = f"prop_b_{SUFIJO}@parksmart-prueba.com"
    correo_admin = f"admin_{SUFIJO}@parksmart-prueba.com"
    correo_hacker = f"hacker_{SUFIJO}@parksmart-prueba.com"

    anonimo = Navegador(base)
    cliente = Navegador(base)
    prop_a = Navegador(base)
    prop_b = Navegador(base)
    admin = Navegador(base)

    def usuario_por_correo(correo):
        db.expire_all()
        return db.query(Usuario).filter(Usuario.correo == correo).first()

    # --- 1. Registro ---
    status, location, _ = cliente.registrar("Cliente Prueba", correo_cliente, "cliente")
    u = usuario_por_correo(correo_cliente)
    verificar("Registro de cliente", status == 302 and u is not None and u.rol == RolUsuario.CLIENTE,
              f"status={status}")

    status, _, cuerpo = prop_a.registrar("Propietario A", correo_prop_a, "propietario")
    verificar("Registro público como propietario es rechazado (400 y no se crea)",
              status == 400 and usuario_por_correo(correo_prop_a) is None, f"status={status}")
    verificar("  ...explica que esas cuentas las crea el administrador", "las crea el administrador" in cuerpo)
    status, _, cuerpo = anonimo.pedir("/registro")
    verificar("El formulario de registro ya no ofrece la opción de propietario",
              status == 200 and 'value="propietario"' not in cuerpo)

    # Los propietarios de las pruebas siguientes los crea el "admin"
    crear_propietario("Propietario A", correo_prop_a)
    crear_propietario("Propietario B", correo_prop_b)

    status, _, cuerpo = anonimo.registrar("Hacker", correo_hacker, "admin_global")
    verificar("Registro como admin_global es rechazado (400 y no se crea)",
              status == 400 and usuario_por_correo(correo_hacker) is None, f"status={status}")
    verificar("  ...con mensaje claro", "Solo puedes registrarte" in cuerpo)

    status, _, _ = anonimo.registrar("Hacker", correo_hacker, "cualquier_cosa")
    verificar("Registro con rol inventado es rechazado",
              status == 400 and usuario_por_correo(correo_hacker) is None, f"status={status}")

    # --- 2. Login y redirección por rol ---
    status, location, _ = cliente.login(correo_cliente)
    verificar("Login cliente -> /parqueaderos", status == 302 and location == "/parqueaderos",
              f"status={status} location={location}")

    status, location, _ = prop_a.login(correo_prop_a)
    verificar("Login propietario -> /propietario/panel", location == "/propietario/panel",
              f"status={status} location={location}")
    prop_b.login(correo_prop_b)

    # Convertir un usuario en admin_global (como el UPDATE de Supabase, pero dentro de la transacción)
    admin.registrar("Admin Prueba", correo_admin, "cliente")
    u = usuario_por_correo(correo_admin)
    u.rol = RolUsuario.ADMIN_GLOBAL
    db.flush()
    status, location, _ = admin.login(correo_admin)
    verificar("Login admin_global -> /admin", location == "/admin", f"status={status} location={location}")
    status, _, cuerpo = admin.pedir("/admin")
    verificar("Menú del admin: 'Inicio' lleva a su panel (/admin)",
              status == 200 and '<a href="/admin">Inicio</a>' in cuerpo and '<a href="/">Inicio</a>' not in cuerpo)
    _, _, cuerpo = cliente.pedir("/mis-vehiculos")
    verificar("Menú del cliente: 'Inicio' sigue llevando a la portada (/)", '<a href="/">Inicio</a>' in cuerpo)

    # --- 3. El propietario crea un parqueadero: nace pendiente y es suyo ---
    id_prop_b = usuario_por_correo(correo_prop_b).id
    nombre_parq = f"Parqueadero Prueba {SUFIJO}"
    # Intento malicioso: mandar estado y propietario_id en el formulario
    status, _, _ = prop_a.pedir("/admin/parqueaderos/crear",
                                datos_parqueadero(nombre_parq, estado="activo", propietario_id=id_prop_b))
    db.expire_all()
    parq = db.query(Parqueadero).filter(Parqueadero.nombre == nombre_parq).first()
    id_prop_a = usuario_por_correo(correo_prop_a).id
    verificar("Propietario crea parqueadero", status == 302 and parq is not None, f"status={status}")
    verificar("  ...queda en estado pendiente (ignora 'estado' del formulario)",
              parq is not None and parq.estado == EstadoParqueadero.PENDIENTE)
    verificar("  ...propietario_id = usuario en sesión (ignora el del formulario)",
              parq is not None and parq.propietario_id == id_prop_a)

    pid = parq.id

    # --- 4. Otro propietario NO puede tocarlo ---
    status, _, _ = prop_b.pedir(f"/admin/parqueaderos/{pid}/editar", datos_parqueadero("Robado"))
    verificar("Propietario B edita parqueadero de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"/admin/parqueaderos/{pid}/editar")
    verificar("Propietario B abre formulario de edición de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"/admin/parqueaderos/{pid}/eliminar", {})
    verificar("Propietario B elimina parqueadero de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"/admin/parqueaderos/{pid}/espacios")
    verificar("Propietario B ve espacios de A -> 403", status == 403, f"status={status}")
    status, _, _ = prop_b.pedir(f"/admin/parqueaderos/{pid}/espacios/crear", {"numero": "Z-99", "tipo": "carro"})
    verificar("Propietario B crea espacio en parqueadero de A -> 403", status == 403, f"status={status}")
    status, _, cuerpo = prop_b.pedir("/admin/parqueaderos")
    verificar("Propietario B no ve el parqueadero de A en su lista", status == 200 and nombre_parq not in cuerpo,
              f"status={status}")

    # --- 5. El dueño sí edita, pero no puede auto-aprobarse ---
    status, _, _ = prop_a.pedir(f"/admin/parqueaderos/{pid}/editar",
                                datos_parqueadero(nombre_parq, estado="activo"))
    db.expire_all()
    parq = db.get(Parqueadero, pid)
    verificar("Propietario A edita su parqueadero", status == 302, f"status={status}")
    verificar("  ...pero no puede cambiarse el estado a activo", parq.estado == EstadoParqueadero.PENDIENTE)
    status, _, cuerpo = prop_a.pedir("/propietario/panel")
    verificar("Panel del propietario muestra su parqueadero", status == 200 and nombre_parq in cuerpo,
              f"status={status}")

    # --- 6. Catálogo público: solo activos ---
    status, _, cuerpo = anonimo.pedir("/parqueaderos")
    verificar("Catálogo no muestra parqueaderos pendientes", status == 200 and nombre_parq not in cuerpo,
              f"status={status}")
    status, _, _ = anonimo.pedir(f"/parqueaderos/{pid}")
    verificar("Detalle de parqueadero pendiente -> 404", status == 404, f"status={status}")

    # --- 7. Bloqueo por rol ---
    status, _, _ = cliente.pedir("/propietario/panel")
    verificar("Cliente entra a /propietario/panel -> 403", status == 403, f"status={status}")
    status, _, _ = cliente.pedir("/admin")
    verificar("Cliente entra a /admin -> 403", status == 403, f"status={status}")
    status, _, _ = cliente.pedir("/admin/parqueaderos")
    verificar("Cliente entra a /admin/parqueaderos -> 403", status == 403, f"status={status}")
    status, _, _ = cliente.pedir("/api/estadisticas/reservas-por-estado")
    verificar("Cliente pide API de estadísticas -> 403", status == 403, f"status={status}")
    status, _, _ = prop_a.pedir("/admin")
    verificar("Propietario entra a /admin -> 403", status == 403, f"status={status}")
    status, _, _ = prop_a.pedir("/mis-vehiculos")
    verificar("Propietario entra a /mis-vehiculos -> 403", status == 403, f"status={status}")
    status, _, _ = cliente.pedir("/mis-vehiculos")
    verificar("Cliente entra a /mis-vehiculos -> 200", status == 200, f"status={status}")
    status, location, _ = anonimo.pedir("/mis-reservas")
    verificar("Anónimo entra a /mis-reservas -> redirige al login", status == 303 and location == "/login",
              f"status={status} location={location}")

    # --- 8. admin_global ---
    status, _, cuerpo = admin.pedir("/admin/parqueaderos")
    verificar("Admin ve todos los parqueaderos (incluido el pendiente)", status == 200 and nombre_parq in cuerpo,
              f"status={status}")
    status, _, _ = admin.pedir("/admin")
    verificar("Admin entra al dashboard", status == 200, f"status={status}")

    # --- 8b. La ventana independiente de contraseñas se retiró (ahora la cambia el admin) ---
    status, _, _ = prop_a.pedir("/cuenta/contrasena")
    verificar("/cuenta/contrasena ya no existe -> 404", status == 404, f"status={status}")
    _, _, cuerpo = admin.pedir("/admin")
    verificar("  ...y el menú ya no tiene el acceso 'Contraseña'", "/cuenta/contrasena" not in cuerpo)

    # --- 9. Usuario suspendido (activo = False) ---
    u = usuario_por_correo(correo_cliente)
    u.activo = False
    db.flush()
    status, location, _ = cliente.pedir("/mis-vehiculos")
    verificar("Sesión abierta de usuario suspendido -> redirige al login", status == 303 and location == "/login",
              f"status={status}")
    status, _, cuerpo = cliente.login(correo_cliente)
    verificar("Usuario suspendido no puede iniciar sesión", status == 403 and "suspendida" in cuerpo,
              f"status={status}")


def main():
    conexion = engine.connect()
    transaccion = conexion.begin()
    db = Session(bind=conexion, join_transaction_mode="create_savepoint")
    global SESION
    SESION = db

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

    total = len(resultados)
    aprobadas = sum(resultados)
    print(f"Resultado: {aprobadas}/{total} pruebas OK")
    sys.exit(0 if aprobadas == total else 1)


if __name__ == "__main__":
    main()
