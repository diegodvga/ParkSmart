# Cobros y facturas · pasarela simulada

Objetivo: pasarela de pago simulada para el cliente (tarjeta o transferencia), efectivo al salir y una factura interna por cada cobro.
Rama: `main` (sin commits: el proyecto prohíbe commits sin pedido explícito). Entrega: un solo bloque. Pruebas: `venv\Scripts\python.exe scripts\probar_*.py` contra Supabase con ROLLBACK.

## Specs

- **S1 · Pasarela simulada.** "haz una pasarela de pago de simulacion, que el cliente pueda pagar por tarjeta o transferencia, y que cuando el cliente haga la simulacion del pago se confirme la reserva".
  - Diseño aprobado: al reservar, el cliente elige tarjeta, transferencia o efectivo. Con tarjeta o transferencia, la reserva queda `pendiente` 15 minutos y se abre la pantalla de pago simulada.
  - Los datos de tarjeta nunca se guardan; una tarjeta terminada en `0002` se rechaza para probar el fallo.
  - Si el pago es exitoso, la reserva pasa a `confirmada` y se genera la factura.
- **S2 · Efectivo.** "ayudame a escojer la logica para cuando el pago es en efectivo como se confirma la reserva".
  - Diseño aprobado: la reserva en efectivo nace `confirmada`, con el pago pendiente. El cliente paga al salir y el operario registra el efectivo.
  - Si el parqueadero exige pago anticipado, la opción de efectivo no se ofrece y el servidor la rechaza.
- **S3 · Factura por pago digital.** "que cuando paga por medios digitales en mi pasarela de pago de prueba, se genere la factura".
  - Diseño aprobado: una factura por cada cobro. El pago anticipado genera su factura en el momento del pago.
- **S4 · Factura al salir.** "pero que tambien cuando pague en efectivo al salir del parqueadero se pueda generar la factura y confirmar el pago".
  - Al registrar el cobro de la salida, se emite una factura de cierre que cubre solo los cobros que todavía no tienen factura.
  - Si el anticipo ya cubría todo, no se emite una factura nueva.
- **S5 · Ingreso sin reserva.** "en el panel del propietaria poder registrar vehiculos que ingresaron al parqueadero sin reserva, que simplemente llegaron, poder asignar espacios sin nesecidad de hacer una reservar o crear un vehiculo desde el panel de propietario".
  - Ya existía en Operación → "Entradas y salidas" (`registrar_ingreso_directo`), con asignación automática.
  - Se agrega un selector opcional de plaza ("Asignar automáticamente" por defecto). El servidor rechaza una plaza ocupada o de otro tipo.
- **S6 · Migración 006** (aprobada con "si"): quitar `comprobantes.cuenta_id UNIQUE` y agregar `comprobantes.pago_id`.
  - Restricciones: una factura por pago y una sola factura de cierre por cuenta.
  - Se ensaya con ROLLBACK y después se aplica.
- **S7 · Factura interna.** La factura impresa aclara que es un documento interno y no una factura electrónica DIAN.

## Tasks

| ID | Specs | Ruta | Estado | Evidencia |
|---|---|---|---|---|
| T1 | S6 | inline | hecha | Ensayo ROLLBACK + aplicada; verificar_modelos 0 diferencias |
| T2 | S3, S4, S7 | inline | hecha | RED observado → probar_cobros 38/38 |
| T3 | S1, S2 | inline | hecha | probar_cobros 38/38 |
| T4 | S5 | inline | hecha | probar_cobros (S5) |

## Log

- **L1 (2026-11-15, pedido original):** "Nesecito implementar el sistema de cobros y facturas, haz una pasarela de pago de simulacion, que el cliente pueda pagar por tarjeta o transferencia, y que cuando el cliente haga la simulacion del pago se confirme la reserva, y ayudame a escojer la logica para cuando el pago es en efectivo como se confirma la reserva y nesecito generar facturas, que cuando paga por medios digitales en mi pasarela de pago de prueba, se genere la factura, pero que tambien cuando pague en efectivo al salir del parqueadero se pueda generar la factura y confirmar el pago, tambien quiero hacer en el panel del propietaria poder registrar vehiculos que ingresaron al parqueadero sin reserva, que simplemente llegaron, poder asignar espacios sin nesecidad de hacer una reservar o crear un vehiculo desde el panel de propietario"
- **L2 (2026-11-15):** Se propuso el diseño de S1 a S4, S6 y S7, y el usuario respondió "si". S5 ya existía.
- **L3 (2026-11-15):** Migración 006 aplicada en Supabase: ensayo con ROLLBACK y luego COMMIT, 0 facturas previas.
- **L4:** RED: 17 pruebas nuevas fallaron antes de implementar. GREEN: `probar_cobros` 38/38.
  - `probar_pagos` se ajustó a la regla aprobada: la factura del anticipo existe antes de la salida; el texto "Comprobante N.º" pasa a "Factura N.º".
- **L5:** Límite conocido: cancelar una reserva prepagada genera el reembolso, pero no una nota crédito. La factura del anticipo queda como está.
