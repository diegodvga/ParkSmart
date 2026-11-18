"""
Pruebas del Módulo 1: crear un parqueadero con sus espacios en lote.

Usa la misma técnica que scripts/probar_etapa2.py: una transacción externa
contra la base real y ROLLBACK al final, así que NO deja datos.

Qué se prueba:
- Numeración automática por tipo: M01, C01, B01... (mínimo dos dígitos,
  después de C99 sigue C100).
- El formulario recibe cuántos espacios hay para motos, carros y buses;
  una categoría puede tener cero, pero el total debe ser al menos 1.
- El parqueadero y sus espacios se crean juntos (si algo falla, no se
  crea nada).
- El formulario de edición sigue funcionando como antes.

Ejecutar desde la raíz del proyecto:
    venv\\Scripts\\python.exe scripts\\probar_espacios_lote.py
"""

import sys
import threading
import time
from pathlib import Path

# Permite importar "app" y el arnés de probar_etapa2 al ejecutar desde la raíz
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

import uvicorn  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import probar_etapa2 as arnes  # noqa: E402
from probar_etapa2 import SUFIJO, Navegador, puerto_libre, verificar  # noqa: E402
from app.database import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Espacio, EstadoEspacio, EstadoParqueadero, Parqueadero, RolUsuario, Usuario,
)
from app.services.espacios import generar_codigos  # noqa: E402


def datos_creacion(nombre, **extra):
    """Formulario de creación válido: 2 motos, 3 carros y 0 buses."""
    datos = {
        "nombre": nombre,
        "direccion": "Calle 10 # 5-20",
        "descripcion": "Parqueadero de prueba",
        "ciudad": "Santa Marta",
        "cantidad_motos": "2",
        "cantidad_carros": "3",
        "cantidad_buses": "0",
        "tarifa_hora_moto": "1500",
        "tarifa_hora_carro": "3000",
        "tarifa_hora_bus": "",
        "horario_apertura": "06:00",
        "horario_cierre": "22:00",
    }
    datos.update(extra)
    return datos


def ejecutar_pruebas(base, db: Session):
    def buscar(nombre):
        db.expire_all()
        return db.query(Parqueadero).filter(Parqueadero.nombre == nombre).first()

    def codigos(parqueadero):
        return sorted(e.numero for e in db.query(Espacio).filter(Espacio.parqueadero_id == parqueadero.id))

    # --- 1. Generación de códigos (sin base de datos) ---
    verificar("generar_codigos('M', 2) -> M01, M02", generar_codigos("M", 2) == ["M01", "M02"])
    verificar("generar_codigos('C', 0) -> lista vacía", generar_codigos("C", 0) == [])
    codigos_100 = generar_codigos("C", 100)
    verificar("Después de C99 sigue C100", codigos_100[98:] == ["C99", "C100"], str(codigos_100[97:]))

    # --- Preparar un propietario con sesión ---
    correo = f"prop_lote_{SUFIJO}@parksmart-prueba.com"
    prop = Navegador(base)
    arnes.crear_propietario("Propietario Lote", correo)
    status, _, _ = prop.login(correo)
    verificar("Login del propietario de prueba", status == 302, f"status={status}")

    # --- 2. El formulario de creación pide cantidades, no capacidad ---
    status, _, cuerpo = prop.pedir("/admin/parqueaderos/crear")
    verificar("Formulario de creación tiene cantidades por tipo",
              status == 200 and all(f'name="cantidad_{t}"' in cuerpo for t in ("motos", "carros", "buses")),
              f"status={status}")
    verificar("  ...y ya no pide la capacidad a mano", 'name="capacidad"' not in cuerpo)
    verificar("  ...tiene el margen entre reservas con 15 min por defecto",
              'name="margen_minutos"' in cuerpo and 'value="15" selected' in cuerpo)

    # --- 3. Creación válida: 2 motos, 3 carros, 0 buses ---
    nombre = f"Lote {SUFIJO}"
    status, _, cuerpo = prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre))
    p = buscar(nombre)
    verificar("Crear con 2 motos y 3 carros -> 302 y parqueadero creado", status == 302 and p is not None,
              f"status={status}")
    if p is not None:
        verificar("  ...códigos M01, M02, C01, C02, C03", codigos(p) == ["C01", "C02", "C03", "M01", "M02"],
                  str(codigos(p)))
        espacios = db.query(Espacio).filter(Espacio.parqueadero_id == p.id).all()
        verificar("  ...cada código tiene su tipo (M=moto, C=carro)",
                  all({"M": "moto", "C": "carro"}[e.numero[0]] == e.tipo.value for e in espacios))
        verificar("  ...todos nacen disponibles", all(e.estado == EstadoEspacio.DISPONIBLE for e in espacios))
        verificar("  ...capacidad = 5 (suma de espacios)", p.capacidad == 5, f"capacidad={p.capacidad}")
        verificar("  ...acepta motos y carros, no buses",
                  p.acepta_motos and p.acepta_carros and not p.acepta_buses)
        verificar("  ...sin tarifa de bus (no tiene espacios de bus)", p.tarifa_hora_bus is None)
        verificar("  ...sin elegir margen, queda en 15 min", p.margen_minutos == 15, f"margen={p.margen_minutos}")

    # --- 3b. Margen de 10 minutos elegido en el formulario ---
    nombre_margen = f"Margen10 {SUFIJO}"
    status, _, _ = prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre_margen, margen_minutos="10"))
    p = buscar(nombre_margen)
    verificar("Crear con margen de 10 min -> 302 y se guarda 10",
              status == 302 and p is not None and p.margen_minutos == 10, f"status={status}")

    # --- 4. 100 carros y 1 bus: la secuencia pasa de C99 a C100 ---
    nombre_grande = f"Grande {SUFIJO}"
    status, _, _ = prop.pedir("/admin/parqueaderos/crear", datos_creacion(
        nombre_grande, cantidad_motos="0", cantidad_carros="100", cantidad_buses="1", tarifa_hora_bus="8000"))
    p = buscar(nombre_grande)
    verificar("Crear con 100 carros y 1 bus -> 302", status == 302 and p is not None, f"status={status}")
    if p is not None:
        lista = codigos(p)
        verificar("  ...101 espacios, con C99, C100 y B01, sin motos",
                  len(lista) == 101 and {"C99", "C100", "B01"} <= set(lista)
                  and not any(c.startswith("M") for c in lista), f"{len(lista)} espacios")
        verificar("  ...no acepta motos y su tarifa de moto queda vacía",
                  not p.acepta_motos and p.tarifa_hora_moto is None)

    # --- 4b. Botón "Ver" del listado y panel de un parqueadero pendiente ---
    p = buscar(nombre)
    if p is not None:
        _, _, cuerpo = prop.pedir("/admin/parqueaderos")
        verificar("Listado del propietario: 'Ver' lleva a su panel (no al catálogo público)",
                  f'href="/propietario/parqueaderos/{p.id}"' in cuerpo and f'href="/parqueaderos/{p.id}"' not in cuerpo)
        status, _, cuerpo = prop.pedir(f"/propietario/parqueaderos/{p.id}")
        verificar("Panel de un parqueadero pendiente -> 200 con sus plazas y el aviso de aprobación",
                  status == 200 and "C01" in cuerpo and "M02" in cuerpo and "Pendiente de aprobación" in cuerpo,
                  f"status={status}")

        correo_admin = f"admin_lote_{SUFIJO}@parksmart-prueba.com"
        admin = Navegador(base)
        admin.registrar("Admin Lote", correo_admin, "cliente")
        usuario_admin = db.query(Usuario).filter(Usuario.correo == correo_admin).one()
        usuario_admin.rol = RolUsuario.ADMIN_GLOBAL
        db.flush()
        admin.login(correo_admin)
        _, _, cuerpo = admin.pedir("/admin/parqueaderos")
        verificar("Listado del admin: un pendiente no enlaza al catálogo público (daría 404) pero sí a Editar",
                  f'href="/parqueaderos/{p.id}"' not in cuerpo and f"/admin/parqueaderos/{p.id}/editar" in cuerpo)
        p.estado = EstadoParqueadero.ACTIVO
        db.flush()
        _, _, cuerpo = admin.pedir("/admin/parqueaderos")
        verificar("  ...y uno activo sí enlaza al catálogo público", f'href="/parqueaderos/{p.id}"' in cuerpo)
        status, _, cuerpo = prop.pedir(f"/propietario/parqueaderos/{p.id}")
        verificar("  ...el panel de un activo no muestra el aviso de aprobación",
                  status == 200 and "Pendiente de aprobación" not in cuerpo)
        p.estado = EstadoParqueadero.PENDIENTE
        db.flush()

    # --- 5. Entradas inválidas: 400 y no se crea nada ---
    casos_invalidos = [
        ("Todo en cero", {"cantidad_motos": "0", "cantidad_carros": "0", "cantidad_buses": "0"},
         "Agrega al menos un espacio"),
        ("Cantidad negativa", {"cantidad_carros": "-1"}, "número entero entre 0 y 500"),
        ("Cantidad decimal", {"cantidad_carros": "2.5"}, "número entero entre 0 y 500"),
        ("Más de 500 de un tipo", {"cantidad_carros": "501"}, "número entero entre 0 y 500"),
        ("Carros sin tarifa", {"tarifa_hora_carro": ""}, "Indica la tarifa por hora para carros"),
        ("Margen de 20 min", {"margen_minutos": "20"}, "debe ser de 10 o 15 minutos"),
        ("Margen no numérico", {"margen_minutos": "abc"}, "debe ser de 10 o 15 minutos"),
    ]
    for i, (descripcion, cambios, mensaje) in enumerate(casos_invalidos):
        nombre_malo = f"Malo {i} {SUFIJO}"
        status, _, cuerpo = prop.pedir("/admin/parqueaderos/crear", datos_creacion(nombre_malo, **cambios))
        verificar(f"{descripcion} -> 400 sin crear nada",
                  status == 400 and buscar(nombre_malo) is None, f"status={status}")
        verificar(f"  ...mensaje: '{mensaje}'", mensaje in cuerpo)

    # El formulario conserva lo que el usuario escribió
    status, _, cuerpo = prop.pedir("/admin/parqueaderos/crear",
                                   datos_creacion(f"Refill {SUFIJO}", cantidad_carros="7", tarifa_hora_carro=""))
    verificar("Tras un error, el formulario conserva la cantidad escrita (7 carros)",
              status == 400 and 'id="cantidad_carros"' in cuerpo and 'value="7"' in cuerpo)

    # --- 6. Comportamiento previo: la edición sigue igual ---
    p = buscar(nombre)
    if p is not None:
        status, _, cuerpo = prop.pedir(f"/admin/parqueaderos/{p.id}/editar")
        verificar("Formulario de edición sigue mostrando capacidad y tipos aceptados",
                  status == 200 and 'name="capacidad"' in cuerpo and 'name="acepta_carros"' in cuerpo,
                  f"status={status}")
        verificar("  ...y muestra el margen actual (15)",
                  'name="margen_minutos"' in cuerpo and 'value="15" selected' in cuerpo)
        datos_edicion = {
            "nombre": nombre + " editado", "direccion": "Calle 10 # 5-20", "descripcion": "",
            "capacidad": "5", "ciudad": "Santa Marta", "acepta_carros": "true", "acepta_motos": "true",
            "tarifa_hora_carro": "3500", "tarifa_hora_moto": "1500", "tarifa_hora_bus": "",
            "horario_apertura": "06:00", "horario_cierre": "22:00",
        }
        status, _, _ = prop.pedir(f"/admin/parqueaderos/{p.id}/editar", datos_edicion)
        p = buscar(nombre + " editado")
        verificar("Editar el parqueadero (sin enviar margen) -> 302 y guarda la nueva tarifa",
                  status == 302 and p is not None and p.tarifa_hora_carro == 3500, f"status={status}")
        if p is not None:
            verificar("  ...la edición no toca los espacios", len(codigos(p)) == 5)
            verificar("  ...si no se envía margen, conserva el que tenía", p.margen_minutos == 15)
            status, _, _ = prop.pedir(f"/admin/parqueaderos/{p.id}/editar", {**datos_edicion, "margen_minutos": "10"})
            p = buscar(nombre + " editado")
            verificar("Editar el margen a 10 min -> 302 y se guarda",
                      status == 302 and p is not None and p.margen_minutos == 10, f"status={status}")
            status, _, cuerpo = prop.pedir(f"/admin/parqueaderos/{p.id}/editar", {**datos_edicion, "margen_minutos": "30"})
            p = buscar(nombre + " editado")
            verificar("Editar con margen de 30 min -> 400 y no cambia",
                      status == 400 and "10 o 15 minutos" in cuerpo and p.margen_minutos == 10, f"status={status}")

    # --- 7. Un cliente no puede crear parqueaderos ---
    correo_cliente = f"cli_lote_{SUFIJO}@parksmart-prueba.com"
    cliente = Navegador(base)
    cliente.registrar("Cliente Lote", correo_cliente, "cliente")
    cliente.login(correo_cliente)
    status, _, _ = cliente.pedir("/admin/parqueaderos/crear", datos_creacion(f"Cliente {SUFIJO}"))
    verificar("Cliente intenta crear -> 403 sin crear nada",
              status == 403 and buscar(f"Cliente {SUFIJO}") is None, f"status={status}")


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
