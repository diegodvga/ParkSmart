"""
Servicio de espacios para ParkSmart.

Genera los códigos de los espacios y los prepara para crearlos en lote:
- Motos:  M01, M02, ...
- Carros: C01, C02, ...
- Buses:  B01, B02, ...

Cada código tiene como mínimo dos dígitos; después de C99 sigue C100.
La base de datos garantiza que el código sea único dentro de su
parqueadero (UNIQUE parqueadero_id, numero).
"""

from app.models import Espacio, EstadoEspacio, TipoEspacio


# Prefijo del código de cada tipo de espacio
PREFIJOS = {
    TipoEspacio.MOTO: "M",
    TipoEspacio.CARRO: "C",
    TipoEspacio.BUS: "B",
}


def generar_codigos(prefijo: str, cantidad: int) -> list[str]:
    """
    Genera `cantidad` códigos consecutivos empezando en 01.

    Ejemplo: generar_codigos("C", 3) → ["C01", "C02", "C03"]
    """
    return [f"{prefijo}{numero:02d}" for numero in range(1, cantidad + 1)]


def construir_espacios(cantidades: dict) -> list[Espacio]:
    """
    Prepara (sin guardar) los espacios de un parqueadero nuevo.

    Args:
        cantidades: cuántos espacios hay de cada tipo, por ejemplo
            {TipoEspacio.MOTO: 2, TipoEspacio.CARRO: 3, TipoEspacio.BUS: 0}

    Returns:
        Lista de objetos Espacio en estado 'disponible'. Se asignan a
        parqueadero.espacios para que SQLAlchemy los inserte en lote en la
        MISMA transacción que el parqueadero.
    """
    espacios = []
    for tipo, prefijo in PREFIJOS.items():
        for codigo in generar_codigos(prefijo, cantidades.get(tipo, 0)):
            espacios.append(Espacio(numero=codigo, tipo=tipo, estado=EstadoEspacio.DISPONIBLE))
    return espacios
