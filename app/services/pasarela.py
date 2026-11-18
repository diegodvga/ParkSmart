"""
Pasarela de pago SIMULADA para ParkSmart (solo pruebas, sin dinero real).

Valida lo que escribe el cliente como lo haría una pasarela real y decide
la respuesta del "banco" con reglas fijas, para poder probar los dos casos:
- Tarjeta: el número debe pasar el algoritmo de Luhn (el dígito de control
  que usan todas las tarjetas) y no estar vencida. Las tarjetas terminadas
  en 0002 se RECHAZAN (por ejemplo 4000 0000 0000 0002); el resto se
  aprueba (por ejemplo 4242 4242 4242 4242).
- Transferencia: el cliente elige su banco; el banco "rechazo" simula una
  transferencia rechazada.

Seguridad: los datos de la tarjeta solo se validan en memoria. NUNCA se
guardan en la base, ni en la factura, ni en los registros (logs).
"""

import re
from datetime import datetime

from app.services.ocupacion import OperacionError

# (valor, nombre visible). "rechazo" existe solo para probar el fallo.
BANCOS = [
    ("bancolombia", "Bancolombia (prueba)"),
    ("davivienda", "Davivienda (prueba)"),
    ("bbva", "BBVA (prueba)"),
    ("nequi", "Nequi (prueba)"),
    ("rechazo", "Banco de prueba que rechaza"),
]
SUFIJO_RECHAZO = "0002"


def luhn_valido(numero: str) -> bool:
    """
    Algoritmo de Luhn: desde la derecha, se duplica un dígito de cada dos
    (restando 9 si pasa de 9) y la suma total debe ser múltiplo de 10.
    Detecta casi cualquier error al escribir un dígito.
    """
    suma = 0
    for posicion, caracter in enumerate(reversed(numero)):
        digito = int(caracter)
        if posicion % 2 == 1:
            digito *= 2
            if digito > 9:
                digito -= 9
        suma += digito
    return suma % 10 == 0


def respuesta_tarjeta(titular: str, numero: str, vencimiento: str, cvv: str, ahora: datetime) -> bool:
    """
    Valida los datos de la tarjeta y devuelve la respuesta simulada del
    banco: True = aprobado, False = rechazado.

    Raises:
        OperacionError: si los datos están mal escritos (no hay cobro).
    """
    if not (titular or "").strip():
        raise OperacionError("Escribe el nombre del titular de la tarjeta.")
    numero = re.sub(r"[\s-]", "", numero or "")
    if not re.fullmatch(r"\d{13,19}", numero) or not luhn_valido(numero):
        raise OperacionError("El número de tarjeta no es válido. Revisa los dígitos.")
    coincidencia = re.fullmatch(r"(\d{2})\s*/\s*(\d{2})", (vencimiento or "").strip())
    if not coincidencia or not 1 <= int(coincidencia.group(1)) <= 12:
        raise OperacionError("Escribe el vencimiento como MM/AA, por ejemplo 08/29.")
    mes, anio = int(coincidencia.group(1)), 2000 + int(coincidencia.group(2))
    # La tarjeta sirve hasta el último día de su mes de vencimiento
    if (anio, mes) < (ahora.year, ahora.month):
        raise OperacionError("La tarjeta está vencida.")
    if not re.fullmatch(r"\d{3,4}", (cvv or "").strip()):
        raise OperacionError("El código de seguridad (CVV) tiene 3 o 4 dígitos.")
    return not numero.endswith(SUFIJO_RECHAZO)


def respuesta_transferencia(banco: str, documento: str) -> bool:
    """
    Valida el banco y el documento del titular y devuelve la respuesta
    simulada: True = aprobada, False = rechazada.
    """
    if banco not in {valor for valor, _ in BANCOS}:
        raise OperacionError("Elige tu banco.")
    if not re.fullmatch(r"\d{4,15}", (documento or "").strip()):
        raise OperacionError("Escribe el número de documento del titular (solo dígitos).")
    return banco != "rechazo"
