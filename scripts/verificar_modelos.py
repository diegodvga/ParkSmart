"""
Script TEMPORAL de verificación de modelos (Etapa 1).

Hace dos cosas:
1. Compara las columnas de cada modelo SQLAlchemy con las columnas reales
   de la base de datos (usando sqlalchemy.inspect) y reporta diferencias.
2. Inserta, DENTRO DE UNA TRANSACCIÓN, un usuario propietario, un parqueadero
   y un espacio de prueba; los lee usando las relaciones y hace ROLLBACK.
   No queda ningún dato guardado.

Uso (desde la raíz del proyecto):
    venv\\Scripts\\python.exe scripts\\verificar_modelos.py
"""

import os
import sys
import uuid

# Permite importar el paquete "app" al ejecutar el script desde la raíz
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta, timezone  # noqa: E402

from sqlalchemy import (  # noqa: E402
    inspect, String, Numeric, Integer, SmallInteger, Boolean, DateTime, Text, Enum,
)
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.models import (  # noqa: E402
    Espacio,
    EstadoEspacio,
    EstadoParqueadero,
    Parqueadero,
    Reserva,
    RolUsuario,
    TipoEspacio,
    TipoVehiculo,
    Usuario,
    Vehiculo,
)


def describir_tipo(tipo):
    """Resume un tipo de SQLAlchemy como texto comparable (ej. 'VARCHAR(20)')."""
    if isinstance(tipo, Enum):
        return f"VARCHAR({tipo.length})"
    if isinstance(tipo, String) and not isinstance(tipo, Text):
        return f"VARCHAR({tipo.length})"
    if isinstance(tipo, Text):
        return "TEXT"
    if isinstance(tipo, Numeric):
        return f"NUMERIC({tipo.precision},{tipo.scale})"
    if isinstance(tipo, Boolean):
        return "BOOLEAN"
    if isinstance(tipo, DateTime):
        return "TIMESTAMPTZ" if tipo.timezone else "TIMESTAMP"
    if isinstance(tipo, SmallInteger):
        return "SMALLINT"
    if isinstance(tipo, Integer):
        return "INTEGER"
    return str(tipo)


def comparar_columnas(inspector):
    """Compara nombre, tipo y nullable de cada columna. Devuelve el número de diferencias."""
    diferencias = 0
    print("=" * 60)
    print("1. COMPARACIÓN DE COLUMNAS (modelo vs. base de datos)")
    print("=" * 60)

    for tabla in Base.metadata.sorted_tables:
        if not inspector.has_table(tabla.name):
            print(f"\n[X] La tabla '{tabla.name}' NO existe en la base de datos")
            diferencias += 1
            continue

        reales = {c["name"]: c for c in inspector.get_columns(tabla.name)}
        modelo = {c.name: c for c in tabla.columns}
        problemas = []

        for nombre in sorted(set(modelo) - set(reales)):
            problemas.append(f"columna '{nombre}' está en el modelo pero NO en la BD")
        for nombre in sorted(set(reales) - set(modelo)):
            problemas.append(f"columna '{nombre}' está en la BD pero NO en el modelo")

        for nombre in sorted(set(modelo) & set(reales)):
            col_modelo, col_real = modelo[nombre], reales[nombre]
            tipo_modelo = describir_tipo(col_modelo.type)
            tipo_real = describir_tipo(col_real["type"])
            if tipo_modelo != tipo_real:
                problemas.append(f"'{nombre}': tipo modelo={tipo_modelo} / BD={tipo_real}")
            if col_modelo.nullable != col_real["nullable"]:
                problemas.append(
                    f"'{nombre}': nullable modelo={col_modelo.nullable} / BD={col_real['nullable']}"
                )

        # Llaves foráneas: comparar columna, tabla destino y ON DELETE
        fks_reales = {
            (tuple(fk["constrained_columns"]), fk["referred_table"]): (fk.get("options") or {}).get("ondelete")
            for fk in inspector.get_foreign_keys(tabla.name)
        }
        for fk in tabla.foreign_key_constraints:
            clave = (tuple(c.name for c in fk.columns), fk.referred_table.name)
            if clave not in fks_reales:
                problemas.append(f"FK {clave} no existe en la BD")
            elif (fk.ondelete or "NO ACTION").upper() != (fks_reales[clave] or "NO ACTION").upper():
                problemas.append(
                    f"FK {clave}: ondelete modelo={fk.ondelete} / BD={fks_reales[clave]}"
                )

        if problemas:
            print(f"\n[X] {tabla.name}")
            for p in problemas:
                print(f"    - {p}")
            diferencias += len(problemas)
        else:
            print(f"[OK] {tabla.name}: {len(modelo)} columnas y {len(tabla.foreign_key_constraints)} FK coinciden")

    return diferencias


def prueba_insercion():
    """Inserta datos de prueba dentro de una transacción y hace rollback."""
    print()
    print("=" * 60)
    print("2. PRUEBA DE INSERCIÓN (con ROLLBACK)")
    print("=" * 60)

    sufijo = uuid.uuid4().hex[:8]
    with engine.connect() as conexion:
        transaccion = conexion.begin()
        sesion = Session(bind=conexion)
        try:
            propietario = Usuario(
                nombre="Propietario Prueba",
                correo=f"prueba_{sufijo}@parksmart.test",
                password="hash_de_prueba",
                telefono="3000000000",
                rol=RolUsuario.PROPIETARIO,
            )
            parqueadero = Parqueadero(
                propietario=propietario,
                nombre="Parqueadero Prueba",
                direccion="Calle 1 # 2-3",
                barrio="El Rodadero",
                capacidad=10,
                tarifa_hora_carro=3500.50,
                tarifa_hora_moto=1500,
                horario_apertura="08:00",
                horario_cierre="20:00",
            )
            espacio = Espacio(parqueadero=parqueadero, numero="A-01", tipo=TipoEspacio.CARRO)
            sesion.add_all([propietario, parqueadero, espacio])
            sesion.flush()  # Envía los INSERT a la BD sin confirmar

            # Leer el valor guardado en crudo para confirmar que se guarda el
            # VALOR del enum ('propietario') y no el NOMBRE ('PROPIETARIO')
            from sqlalchemy import text
            fila = conexion.execute(
                text("SELECT u.rol, p.estado, p.ciudad, e.estado AS estado_espacio "
                     "FROM usuarios u JOIN parqueaderos p ON p.propietario_id = u.id "
                     "JOIN espacios e ON e.parqueadero_id = p.id WHERE u.id = :id"),
                {"id": propietario.id},
            ).one()

            sesion.expire_all()  # Fuerza a releer desde la BD usando las relaciones
            usuario_leido = sesion.get(Usuario, propietario.id)

            print(f"Usuario:      {usuario_leido.nombre} | rol={usuario_leido.rol.value} | activo={usuario_leido.activo}")
            print(f"              fecha_registro={usuario_leido.fecha_registro}")
            for p in usuario_leido.parqueaderos:
                print(f"Parqueadero:  {p.nombre} | estado={p.estado.value} | ciudad={p.ciudad}")
                print(f"              tarifa_carro={p.tarifa_hora_carro!r} ({type(p.tarifa_hora_carro).__name__})")
                print(f"              propietario_nombre={p.propietario_nombre}")
                for e in p.espacios:
                    print(f"Espacio:      {e.numero} | tipo={e.tipo.value} | estado={e.estado.value}")
            print(f"Valores crudos en BD: rol='{fila.rol}', estado='{fila.estado}', "
                  f"ciudad='{fila.ciudad}', estado_espacio='{fila.estado_espacio}'")

            assert fila.rol == "propietario"
            assert fila.estado == EstadoParqueadero.PENDIENTE.value
            assert fila.estado_espacio == EstadoEspacio.DISPONIBLE.value
            assert isinstance(usuario_leido.parqueaderos[0].tarifa_hora_carro, float)
            assert usuario_leido.parqueaderos[0].margen_minutos == 15

            # Reserva: el trigger de la base calcula bloqueo = [inicio, fin + margen)
            colombia = timezone(timedelta(hours=-5))
            vehiculo = Vehiculo(usuario=propietario, placa=f"V{sufijo[:6]}".upper(), tipo=TipoVehiculo.CARRO)
            reserva = Reserva(
                usuario=propietario, vehiculo=vehiculo, espacio=espacio, total=6000,
                inicio=datetime(2030, 1, 1, 15, 0, tzinfo=colombia),
                fin=datetime(2030, 1, 1, 17, 0, tzinfo=colombia),
                margen_minutos=15, tarifa_hora=3000,
            )
            sesion.add_all([vehiculo, reserva])
            sesion.flush()
            sesion.refresh(reserva)
            print(f"Reserva:      {reserva.inicio} a {reserva.fin} | bloqueo={reserva.bloqueo}")
            assert reserva.bloqueo.upper == datetime(2030, 1, 1, 17, 15, tzinfo=colombia)
            print("\n[OK] Inserción y lectura con relaciones correctas")
            return True
        except Exception as error:
            print(f"\n[X] Error en la prueba de inserción: {type(error).__name__}: {error}")
            return False
        finally:
            sesion.close()
            transaccion.rollback()
            print("[OK] ROLLBACK ejecutado: no quedó ningún dato guardado")


def main():
    inspector = inspect(engine)
    diferencias = comparar_columnas(inspector)
    insercion_ok = prueba_insercion()

    print()
    print("=" * 60)
    print(f"RESULTADO: {diferencias} diferencia(s) de esquema | inserción {'OK' if insercion_ok else 'FALLÓ'}")
    print("=" * 60)
    sys.exit(0 if diferencias == 0 and insercion_ok else 1)


if __name__ == "__main__":
    main()
