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

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.navbar').forEach((navbar, index) => {
        const navbarContent = navbar.querySelector('.navbar-content');
        const navList = navbarContent ? navbarContent.querySelector('.nav-ul') : null;
        const brandSection = navbar.querySelector('.brand-section');

        if (!navbarContent || !navList || !brandSection) return;

        navbarContent.id = navbarContent.id || `navbar-content-${index}`;
        let navToggle = navbar.querySelector('.nav-toggle');

        if (!navToggle) {
            navToggle = document.createElement('button');
            navToggle.type = 'button';
            navToggle.className = 'nav-toggle';
            navToggle.innerHTML = '<span></span><span></span><span></span>';
            navbar.insertBefore(navToggle, navbarContent);
        }

        navToggle.setAttribute('aria-label', 'Abrir menú');
        navToggle.setAttribute('aria-expanded', 'false');
        navToggle.setAttribute('aria-controls', navbarContent.id);

        const items = Array.from(navList.querySelectorAll(':scope > li'));
        const accountItem = items.find((item) => {
            const text = item.textContent.trim().toLowerCase();
            return text.includes('mi cuenta') || text.includes('gestionar usuarios');
        });
        const logoutItem = items.find((item) => item.textContent.trim().toLowerCase().includes('cerrar'));
        const roleItems = items.filter((item) => item !== accountItem && item !== logoutItem);
        const initialPrimaryItems = roleItems.slice(0, 3);

        items.forEach((item) => {
            item.classList.remove('nav-primary', 'nav-secondary', 'nav-overflowed');
            const link = item.querySelector('a');
            if (link && item === logoutItem) link.classList.add('logout-link');
        });

        initialPrimaryItems.forEach((item) => item.classList.add('nav-primary'));
        if (accountItem) accountItem.classList.add('nav-primary');

        const allItemsFitByDefault = items.length <= 4;
        roleItems.slice(3).forEach((item) => {
            item.classList.add(allItemsFitByDefault ? 'nav-primary' : 'nav-secondary');
        });
        if (logoutItem) {
            logoutItem.classList.add(allItemsFitByDefault ? 'nav-primary' : 'nav-secondary');
        }

        navToggle.addEventListener('click', () => {
            const isOpen = navbarContent.classList.toggle('active');
            navbar.classList.toggle('menu-open', isOpen);
            navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            navToggle.setAttribute('aria-label', isOpen ? 'Cerrar menú' : 'Abrir menú');
        });

        navbarContent.querySelectorAll('a').forEach((link) => {
            link.addEventListener('click', () => {
                navbarContent.classList.remove('active');
                navbar.classList.remove('menu-open');
                navToggle.setAttribute('aria-expanded', 'false');
                navToggle.setAttribute('aria-label', 'Abrir menú');
            });
        });

        function actualizarModoNavbar() {
            navbar.classList.remove('navbar-collapsed', 'navbar-partial', 'navbar-measuring', 'menu-open');
            navbarContent.classList.remove('active');
            navList.querySelectorAll('.nav-overflowed').forEach((item) => {
                item.classList.remove('nav-overflowed');
            });
            navToggle.setAttribute('aria-expanded', 'false');
            navToggle.setAttribute('aria-label', 'Abrir menú');

            if (window.innerWidth <= 992) {
                navbar.classList.add('navbar-collapsed');
                return;
            }

            navbar.classList.add('navbar-measuring');

            const estilosNavbar = getComputedStyle(navbar);
            const estilosLista = getComputedStyle(navList);
            const anchoDisponible = navbar.clientWidth
                - parseFloat(estilosNavbar.paddingLeft)
                - parseFloat(estilosNavbar.paddingRight);
            const anchoMarca = brandSection.getBoundingClientRect().width;
            const anchoBoton = navToggle.getBoundingClientRect().width;
            const anchoGap = parseFloat(estilosNavbar.columnGap) || 0;
            const gapEnlaces = parseFloat(estilosLista.columnGap) || 0;
            const medirAncho = (elementos) => elementos.reduce(
                (total, item) => total + item.getBoundingClientRect().width,
                0
            ) + Math.max(0, elementos.length - 1) * gapEnlaces;
            const visibles = items.filter((item) => item.classList.contains('nav-primary'));
            const elementosSecundarios = items.filter((item) => item.classList.contains('nav-secondary'));
            const necesitaMenu = elementosSecundarios.length > 0;

            function cabe(conMenu) {
                return anchoMarca + medirAncho(visibles)
                    + (conMenu ? anchoBoton + anchoGap * 2 : anchoGap)
                    <= anchoDisponible;
            }

            if (!necesitaMenu && cabe(false)) {
                navbar.classList.remove('navbar-measuring');
                return;
            }

            navbar.classList.add('navbar-partial');
            while (visibles.length > 1 && !cabe(true)) {
                const removable = visibles.slice().reverse().find((item) => item !== accountItem);
                if (!removable) break;
                removable.classList.add('nav-overflowed');
                visibles.splice(visibles.indexOf(removable), 1);
            }
            navbar.classList.remove('navbar-measuring');
        }

        actualizarModoNavbar();
        window.addEventListener('resize', actualizarModoNavbar);
    });
});