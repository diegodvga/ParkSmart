/**
 * ParkSmart - JavaScript principal
 *
 * Este archivo contiene funciones generales que se usan
 * en toda la aplicación.
 */

// Cuando el documento esté listo
document.addEventListener('DOMContentLoaded', function() {
    console.log('ParkSmart cargado correctamente');

    // Cerrar mensajes de alerta automáticamente después de 5 segundos
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            alert.style.opacity = '0';
            setTimeout(function() {
                alert.remove();
            }, 300);
        }, 5000);
    });
});

/**
 * Muestra un mensaje de confirmación antes de ejecutar una acción.
 * @param {string} mensaje - Mensaje a mostrar
 * @returns {boolean} - true si el usuario confirma, false si cancela
 */
function confirmarAccion(mensaje) {
    return confirm(mensaje || '¿Estás seguro de que deseas continuar?');
}

/**
 * Formatea un número como moneda colombiana.
 * @param {number} valor - Valor a formatear
 * @returns {string} - Valor formateado
 */
function formatMoneda(valor) {
    return new Intl.NumberFormat('es-CO', {
        style: 'currency',
        currency: 'COP',
        minimumFractionDigits: 0
    }).format(valor);
}

/**
 * Valida que un campo de formulario no esté vacío.
 * @param {string} campo - Valor del campo
 * @returns {boolean} - true si es válido
 */
function campoValido(campo) {
    return campo !== null && campo !== undefined && campo.trim() !== '';
}
