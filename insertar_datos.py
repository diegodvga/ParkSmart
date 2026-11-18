"""
Script para insertar datos de prueba en ParkSmart.
Verifica si los datos ya existen antes de insertar.

Ejecutar con: python insertar_datos.py
"""

from app.database import SessionLocal
from app.models import (
    Usuario, Parqueadero, Espacio, Vehiculo, Reserva,
    RolUsuario, EstadoParqueadero, EstadoEspacio, EstadoReserva,
    TipoVehiculo
)
from app.auth import hash_password
from datetime import datetime, date, timedelta
import random


def insertar_datos():
    """
    Inserta datos de prueba en todas las tablas.
    """
    db = SessionLocal()

    try:
        print("=== INSERTANDO DATOS DE PRUEBA ===\n")

        # Verificar si ya existen datos
        parqueaderos_existentes = db.query(Parqueadero).count()
        if parqueaderos_existentes > 0:
            print(f"⚠️  Ya existen {parqueaderos_existentes} parqueaderos en la base de datos.")
            print("   No se insertarán nuevos datos.\n")
            print("   Si quieres insertar datos nuevos, primero elimina los existentes:")
            print("   python limpiar_datos.py")
            return

        # 1. Crear parqueaderos
        print("1. Creando parqueaderos...")
        parqueaderos = [
            Parqueadero(
                nombre="Parqueadero Central",
                direccion="Calle 100 #15-20",
                descripcion="Parqueadero cubierto con vigilancia 24/7",
                capacidad=50,
                tarifa_hora_carro=3000,
                tarifa_hora_moto=2000,
                horario_apertura="06:00",
                horario_cierre="22:00",
                estado=EstadoParqueadero.ACTIVO.value
            ),
            Parqueadero(
                nombre="Parqueadero Norte",
                direccion="Av. Carrera 7 #45-30",
                descripcion="Parqueadero al aire libre con cámaras de seguridad",
                capacidad=40,
                tarifa_hora_carro=2500,
                tarifa_hora_moto=1800,
                horario_apertura="07:00",
                horario_cierre="21:00",
                estado=EstadoParqueadero.ACTIVO.value
            ),
            Parqueadero(
                nombre="Parqueadero Sur",
                direccion="Calle 25 #10-15",
                descripcion="Parqueadero subterráneo con elevador",
                capacidad=60,
                tarifa_hora_carro=3500,
                tarifa_hora_moto=2500,
                horario_apertura="06:00",
                horario_cierre="23:00",
                estado=EstadoParqueadero.ACTIVO.value
            ),
            Parqueadero(
                nombre="Parqueadero Este",
                direccion="Carrera 15 #30-25",
                descripcion="Parqueadero con servicio de lavado",
                capacidad=35,
                tarifa_hora_carro=2800,
                tarifa_hora_moto=1900,
                horario_apertura="08:00",
                horario_cierre="20:00",
                estado=EstadoParqueadero.ACTIVO.value
            ),
            Parqueadero(
                nombre="Parqueadero Oeste",
                direccion="Av. 68 #20-10",
                descripcion="Parqueadero con techado y vigilancia",
                capacidad=45,
                tarifa_hora_carro=3200,
                tarifa_hora_moto=2200,
                horario_apertura="06:30",
                horario_cierre="22:30",
                estado=EstadoParqueadero.ACTIVO.value
            ),
        ]
        db.add_all(parqueaderos)
        db.commit()
        print(f"   ✓ {len(parqueaderos)} parqueaderos creados")

        # 2. Crear espacios para cada parqueadero
        print("\n2. Creando espacios...")
        espacios = []
        for parqueadero in parqueaderos:
            for i in range(1, 21):  # 20 espacios por parqueadero
                tipo = "CARRO" if i <= 15 else "MOTO"
                estado = random.choice([
                    EstadoEspacio.DISPONIBLE.value,
                    EstadoEspacio.DISPONIBLE.value,
                    EstadoEspacio.DISPONIBLE.value,
                    EstadoEspacio.OCUPADO.value,
                    EstadoEspacio.MANTENIMIENTO.value
                ])
                espacios.append(Espacio(
                    parqueadero_id=parqueadero.id,
                    numero=f"{chr(64 + i)}{i}",
                    tipo=tipo,
                    estado=estado
                ))
        db.add_all(espacios)
        db.commit()
        print(f"   ✓ {len(espacios)} espacios creados")

        # 3. Crear usuarios
        print("\n3. Creando usuarios...")
        usuarios = []
        nombres = [
            "Juan Pérez", "María García", "Carlos López", "Ana Martínez",
            "Pedro Rodríguez", "Laura Sánchez", "Miguel Torres", "Sofia Ramírez",
            "Diego Flores", "Valentina Ortiz", "Andrés Morales", "Camila Vargas",
            "Sebastián Castro", "Isabella Mendoza", "Nicolás Herrera", "Luciana Jiménez",
            "Gabriel Ríos", "Mariana Silva", "Alejandro Núñez", "Paula Gómez"
        ]
        for i, nombre in enumerate(nombres):
            usuarios.append(Usuario(
                nombre=nombre,
                correo=f"usuario{i+1}@ejemplo.com",
                password=hash_password("password123"),
                rol=RolUsuario.CLIENTE.value,
                fecha_registro=datetime.now() - timedelta(days=random.randint(1, 365))
            ))
        db.add_all(usuarios)
        db.commit()
        print(f"   ✓ {len(usuarios)} usuarios creados")

        # 4. Crear vehículos
        print("\n4. Creando vehículos...")
        vehiculos = []
        marcas_carro = ["Toyota", "Chevrolet", "Renault", "Mazda", "Hyundai", "Kia", "Nissan", "Honda"]
        marcas_moto = ["Yamaha", "Honda", "Suzuki", "Kawasaki", "Ducati", "BMW", "KTM", "Royal Enfield"]
        
        # Generar placas únicas
        letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        numeros = "0123456789"
        placas_usadas = set()
        
        def generar_placa_unica():
            while True:
                placa = f"{random.choice(letras)}{random.choice(letras)}{random.choice(letras)}{random.choice(numeros)}{random.choice(numeros)}{random.choice(numeros)}"
                if placa not in placas_usadas:
                    placas_usadas.add(placa)
                    return placa
        
        for i, usuario in enumerate(usuarios):
            # Cada usuario tiene 1-2 vehículos
            num_vehiculos = random.randint(1, 2)
            for j in range(num_vehiculos):
                if random.choice([True, False]):
                    vehiculos.append(Vehiculo(
                        usuario_id=usuario.id,
                        placa=generar_placa_unica(),
                        tipo="CARRO",
                        marca=random.choice(marcas_carro)
                    ))
                else:
                    vehiculos.append(Vehiculo(
                        usuario_id=usuario.id,
                        placa=generar_placa_unica(),
                        tipo="MOTO",
                        marca=random.choice(marcas_moto)
                    ))
        db.add_all(vehiculos)
        db.commit()
        print(f"   ✓ {len(vehiculos)} vehículos creados")

        # 5. Crear reservas
        print("\n5. Creando reservas...")
        reservas = []
        estados = [
            EstadoReserva.PENDIENTE.value,
            EstadoReserva.CONFIRMADA.value,
            EstadoReserva.COMPLETADA.value,
            EstadoReserva.CANCELADA.value
        ]
        pesos = [0.2, 0.3, 0.4, 0.1]  # Más completadas que canceladas
        
        for _ in range(100):
            usuario = random.choice(usuarios)
            vehiculo = random.choice([v for v in vehiculos if v.usuario_id == usuario.id])
            espacio = random.choice(espacios)
            parqueadero = next(p for p in parqueaderos if p.id == espacio.parqueadero_id)
            
            # Calcular tarifa según tipo de vehículo
            if vehiculo.tipo == "MOTO":
                tarifa = parqueadero.tarifa_hora_moto
            else:
                tarifa = parqueadero.tarifa_hora_carro
            
            # Fecha aleatoria en los últimos 30 días
            fecha = date.today() - timedelta(days=random.randint(0, 30))
            
            # Horas aleatorias
            hora_entrada = random.randint(6, 18)
            hora_salida = hora_entrada + random.randint(1, 4)
            if hora_salida > 22:
                hora_salida = 22
            
            horas = hora_salida - hora_entrada
            total = horas * tarifa
            
            reservas.append(Reserva(
                usuario_id=usuario.id,
                vehiculo_id=vehiculo.id,
                espacio_id=espacio.id,
                fecha=fecha,
                hora_entrada=f"{hora_entrada:02d}:00",
                hora_salida=f"{hora_salida:02d}:00",
                estado=random.choices(estados, weights=pesos)[0],
                total=total,
                fecha_reserva=datetime.now() - timedelta(days=random.randint(0, 30))
            ))
        db.add_all(reservas)
        db.commit()
        print(f"   ✓ {len(reservas)} reservas creadas")

        print("\n=== DATOS INSERTADOS EXITOSAMENTE ===")
        print("\nResumen:")
        print(f"  - Parqueaderos: {len(parqueaderos)}")
        print(f"  - Espacios: {len(espacios)}")
        print(f"  - Usuarios: {len(usuarios)}")
        print(f"  - Vehículos: {len(vehiculos)}")
        print(f"  - Reservas: {len(reservas)}")
        print("\n¡El sistema está listo para la presentación!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    insertar_datos()
