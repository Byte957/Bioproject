document.addEventListener('DOMContentLoaded', () => {
    const inputsRun = document.querySelectorAll('.validar-run');

    function calcularDV(cuerpo) {
        let suma = 0;
        let multiplicador = 2;

        for (let i = cuerpo.length - 1; i >= 0; i--) {
            suma += parseInt(cuerpo[i], 10) * multiplicador;
            multiplicador = multiplicador === 7 ? 2 : multiplicador + 1;
        }

        const resto = suma % 11;
        const dvEsperado = 11 - resto;

        if (dvEsperado === 11) return '0';
        if (dvEsperado === 10) return 'K';
        return dvEsperado.toString();
    }

    function validarRUN(runCompleto) {
        const limpio = runCompleto.replace(/[^0-9kK]/g, '').toUpperCase();
        if (limpio.length < 2) return false;

        const cuerpo = limpio.slice(0, -1);
        const dvIngresado = limpio.slice(-1);

        // Evita RUNs falsos con dígitos repetidos (ej: 11.111.111-1)
        if (/^(\d)\1+$/.test(cuerpo)) return false;

        return dvIngresado === calcularDV(cuerpo);
    }

    inputsRun.forEach((input) => {
        // Busca el elemento hermano con clase .mensaje-run si existe
        const mensaje = input.parentElement.querySelector('.mensaje-run');

        input.addEventListener('input', (e) => {
            let valor = e.target.value.replace(/[^0-9kK]/g, '').toUpperCase();

            // Formateo automático con puntos y guion
            if (valor.length > 1) {
                const cuerpo = valor.slice(0, -1);
                const dv = valor.slice(-1);
                const cuerpoFormateado = cuerpo.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
                e.target.value = `${cuerpoFormateado}-${dv}`;
            } else {
                e.target.value = valor;
            }

            // Validación HTML5 y visual
            if (valor.length >= 8) {
                if (validarRUN(e.target.value)) {
                    input.setCustomValidity('');
                    if (mensaje) {
                        mensaje.textContent = 'RUN válido';
                        mensaje.style.color = 'green';
                    }
                } else {
                    input.setCustomValidity('El RUN ingresado no es válido.');
                    if (mensaje) {
                        mensaje.textContent = 'RUN inválido';
                        mensaje.style.color = 'red';
                    }
                }
            } else {
                input.setCustomValidity('El RUN está incompleto.');
                if (mensaje) {
                    mensaje.textContent = '';
                }
            }
        });
    });
});

document.addEventListener('DOMContentLoaded', () => {
    const inputsNumericos = document.querySelectorAll('.solo-numeros');

    const teclasPermitidas = [
        'Backspace', 'Delete', 'Tab', 'Escape', 'Enter',
        'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'
    ];

    inputsNumericos.forEach((input) => {
        // Bloquea teclas inválidas al presionar
        input.addEventListener('keydown', (e) => {
            if (teclasPermitidas.includes(e.key) || e.ctrlKey || e.metaKey) {
                return;
            }
            if (!/^[0-9]$/.test(e.key)) {
                e.preventDefault();
            }
        });

        // Limpia cualquier carácter no numérico si se pega texto
        input.addEventListener('input', (e) => {
            e.target.value = e.target.value.replace(/[^0-9]/g, '');
        });
    });
});