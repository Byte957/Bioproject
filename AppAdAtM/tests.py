import re
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Atencion,
    Boleta,
    EstadoAtencion,
    Horario,
    HorarioMedico,
    Medicamento,
    Medico,
    Paciente,
    Rol,
    Usuario,
)
from .views import _huella_contrasena


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@example.com',
)
class RegistroActivacionTests(TestCase):
    def setUp(self):
        Rol.objects.get_or_create(ID_Rol=4, defaults={'Nombre_Rol': 'Paciente'})

    def registrar_usuario(self):
        return self.client.post(reverse('registrarse_usuario'), {
            'run': '12.345.678-5',
            'nombre': 'Ana',
            'segundo-nombre': '',
            'tercer-nombre': '',
            'apellido-paterno': 'Prueba',
            'apellido-materno': '',
            'fecha-nacimiento': '1990-01-01',
            'telefono': '912345678',
            'correo': 'ana@example.com',
            'password': 'clave1234',
        })

    def test_authentication_forms_render(self):
        for ruta in (
            'inicio_sesion',
            'registrarse_usuario',
            'solicitar_restauracion_contrasena',
        ):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(reverse(ruta)).status_code, 200)

    def token_del_correo(self):
        match = re.search(r'/ActivarCuenta/([^/\s]+)', mail.outbox[0].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_registration_requires_email_activation_before_login(self):
        response = self.registrar_usuario()
        usuario = Usuario.objects.get(Correo_Usuario='ana@example.com')

        self.assertRedirects(response, reverse('inicio_sesion'), fetch_redirect_response=False)
        self.assertEqual(usuario.Estado, 'Inactivo')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ana@example.com'])
        token = self.token_del_correo()

        self.client.post(reverse('inicio_sesion'), {
            'modo': 'run',
            'identificador': '12.345.678-5',
            'password': 'clave1234',
        })
        self.assertNotIn('usuario_id', self.client.session)

        self.client.get(reverse('activar_cuenta', args=[token]))
        usuario.refresh_from_db()
        self.assertEqual(usuario.Estado, 'Activo')

        self.client.post(reverse('inicio_sesion'), {
            'modo': 'correo',
            'identificador': 'ANA@example.com',
            'password': 'clave1234',
        })
        self.assertEqual(self.client.session['usuario_id'], usuario.ID_Usuario)

    def test_invalid_activation_link_does_not_activate_account(self):
        self.registrar_usuario()
        token = self.token_del_correo()

        self.client.get(reverse('activar_cuenta', args=[token + 'invalid']))

        self.assertEqual(
            Usuario.objects.get(Correo_Usuario='ana@example.com').Estado,
            'Inactivo',
        )

    @patch('AppAdAtM.views.send_mail', side_effect=OSError('SMTP unavailable'))
    def test_email_failure_does_not_leave_an_unactivated_account(self, _send_mail):
        self.registrar_usuario()

        self.assertFalse(Usuario.objects.filter(Correo_Usuario='ana@example.com').exists())
        self.assertFalse(Paciente.objects.filter(RUN_Paciente='12.345.678-5').exists())


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@example.com',
)
class RecuperacionContrasenaTests(TestCase):
    def setUp(self):
        self.rol_paciente, _ = Rol.objects.get_or_create(
            ID_Rol=4,
            defaults={'Nombre_Rol': 'Paciente'},
        )

    def registrar_y_activar(self):
        self.client.post(reverse('registrarse_usuario'), {
            'run': '12.345.678-5',
            'nombre': 'Ana',
            'apellido-paterno': 'Prueba',
            'fecha-nacimiento': '1990-01-01',
            'telefono': '912345678',
            'correo': 'ana@example.com',
            'password': 'clave1234',
        })
        token = re.search(r'/ActivarCuenta/([^/\s]+)', mail.outbox[0].body).group(1)
        self.client.get(reverse('activar_cuenta', args=[token]))
        return Usuario.objects.get(Correo_Usuario='ana@example.com')

    def test_password_reset_link_is_one_time_and_allows_login_with_new_password(self):
        usuario = self.registrar_y_activar()

        response = self.client.post(reverse('solicitar_restauracion_contrasena'), {
            'correo': 'ANA@example.com',
        })

        self.assertRedirects(
            response,
            reverse('solicitar_restauracion_contrasena'),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 2)
        token = re.search(r'/RestablecerContrasena/([^/\s]+)', mail.outbox[1].body).group(1)
        self.assertEqual(
            self.client.get(reverse('restablecer_contrasena', args=[token])).status_code,
            200,
        )
        response = self.client.post(
            reverse('restablecer_contrasena', args=[token]),
            {'password': 'NuevaClave!2026', 'confirmacion': 'NuevaClave!2026'},
        )

        self.assertRedirects(response, reverse('inicio_sesion'), fetch_redirect_response=False)
        usuario.refresh_from_db()
        self.assertNotEqual(usuario.Contraseña, 'clave1234')
        self.assertRedirects(
            self.client.get(reverse('restablecer_contrasena', args=[token])),
            reverse('solicitar_restauracion_contrasena'),
            fetch_redirect_response=False,
        )
        self.client.post(reverse('inicio_sesion'), {
            'modo': 'correo',
            'identificador': 'ana@example.com',
            'password': 'NuevaClave!2026',
        })
        self.assertEqual(self.client.session['usuario_id'], usuario.ID_Usuario)

    def test_unknown_account_gets_same_response_without_sending_email(self):
        response = self.client.post(reverse('solicitar_restauracion_contrasena'), {
            'correo': 'desconocido@example.com',
        })

        self.assertRedirects(
            response,
            reverse('solicitar_restauracion_contrasena'),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_registration_rejects_invalid_run_and_weak_password(self):
        response = self.client.post(reverse('registrarse_usuario'), {
            'run': '12.345.678-9',
            'nombre': 'Ana',
            'apellido-paterno': 'Prueba',
            'fecha-nacimiento': '1990-01-01',
            'telefono': '912345678',
            'correo': 'ana@example.com',
            'password': '12345678',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(Correo_Usuario='ana@example.com').exists())

        response = self.client.post(reverse('registrarse_usuario'), {
            'run': '12.345.678-5',
            'nombre': 'Ana',
            'apellido-paterno': 'Prueba',
            'fecha-nacimiento': '1990-01-01',
            'telefono': '912345678',
            'correo': 'ana@example.com',
            'password': '12345678',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(Correo_Usuario='ana@example.com').exists())

    def test_existing_session_is_invalidated_after_password_change(self):
        rol_recepcion, _ = Rol.objects.get_or_create(
            ID_Rol=2,
            defaults={'Nombre_Rol': 'Recepcionista'},
        )
        usuario = Usuario.objects.create(
            Nombre='Recepcionista',
            Apellido='Prueba',
            Correo_Usuario='recepcion@example.com',
            Contraseña='original-hash',
            Rol=rol_recepcion,
        )
        session = self.client.session
        session['usuario_id'] = usuario.ID_Usuario
        session['usuario_rol'] = usuario.Rol_id
        session['usuario_password_fingerprint'] = _huella_contrasena(usuario)
        session.save()

        usuario.Contraseña = 'nuevo-hash'
        usuario.save(update_fields=['Contraseña'])
        response = self.client.get(reverse('gestionar_usuarios'))

        self.assertRedirects(response, reverse('inicio_sesion'), fetch_redirect_response=False)
        self.assertNotIn('usuario_id', self.client.session)

    def test_user_management_rejects_password_matching_new_email(self):
        rol_admin, _ = Rol.objects.get_or_create(
            ID_Rol=1,
            defaults={'Nombre_Rol': 'Administrador'},
        )
        admin = Usuario.objects.create(
            Nombre='Admin',
            Apellido='Prueba',
            Correo_Usuario='admin@example.com',
            Contraseña='admin-hash',
            Rol=rol_admin,
        )
        session = self.client.session
        session['usuario_id'] = admin.ID_Usuario
        session['usuario_rol'] = admin.Rol_id
        session['usuario_password_fingerprint'] = _huella_contrasena(admin)
        session.save()

        response = self.client.post(reverse('gestionar_usuarios'), {
            'nombre': 'Ana',
            'apellido': 'Prueba',
            'correo': 'ana@example.com',
            'password': 'ana@example.com',
            'rol': '2',
            'estado': 'Activo',
        })

        self.assertRedirects(response, reverse('gestionar_usuarios'), fetch_redirect_response=False)
        self.assertFalse(Usuario.objects.filter(Correo_Usuario='ana@example.com').exists())


class FlujoAtencionesTests(TestCase):
    def setUp(self):
        self.rol_admin, _ = Rol.objects.get_or_create(
            ID_Rol=1,
            defaults={'Nombre_Rol': 'Administrador'},
        )
        self.rol_recepcion, _ = Rol.objects.get_or_create(
            ID_Rol=2,
            defaults={'Nombre_Rol': 'Recepcionista'},
        )
        self.rol_medico, _ = Rol.objects.get_or_create(
            ID_Rol=3,
            defaults={'Nombre_Rol': 'Médico'},
        )
        self.rol_paciente, _ = Rol.objects.get_or_create(
            ID_Rol=4,
            defaults={'Nombre_Rol': 'Paciente'},
        )
        self.recepcionista = self.crear_usuario('recepcion@example.com', self.rol_recepcion)
        self.usuario_medico = self.crear_usuario('medico@example.com', self.rol_medico)
        self.usuario_paciente = self.crear_usuario('paciente@example.com', self.rol_paciente)
        self.paciente = Paciente.objects.create(
            RUN_Paciente='22.222.222-2',
            Nombre_Paciente='Pablo',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Ejemplo',
            ApellidoM='',
            Fecha_Nacimiento=date(1990, 1, 1),
            CorreoContactoPaciente='paciente@example.com',
            Usuario=self.usuario_paciente,
        )
        self.medico = Medico.objects.create(
            MatriculaProfesional=12345,
            RUN_Medico='33.333.333-3',
            Nombre_Medico='Marta',
            SegNombre_Medico='',
            TerNombre_Medico='',
            ApellidoP_Medico='Doctora',
            ApellidoM_Medico='',
            CorreoContactoMedico='medico@example.com',
            NumeroContactoMedico='912345678',
            Usuario=self.usuario_medico,
        )
        self.horario = Horario.objects.create(
            Hora_Inicio=time(10, 0),
            Hora_Fin=time(10, 20),
        )

    def crear_usuario(self, correo, rol):
        return Usuario.objects.create(
            Nombre='Usuario',
            Apellido='Prueba',
            Correo_Usuario=correo,
            Contraseña='hashed',
            Rol=rol,
        )

    def iniciar_sesion_de_prueba(self, usuario):
        session = self.client.session
        session['usuario_id'] = usuario.ID_Usuario
        session['usuario_rol'] = usuario.Rol_id
        session['usuario_password_fingerprint'] = _huella_contrasena(usuario)
        session.save()

    def crear_atencion(self, estado=EstadoAtencion.RESERVADA, dia=None):
        dia = dia or date.today() + timedelta(days=2)
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=self.horario,
            Dia=dia,
            Estado='Disponible',
        )
        return Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Consulta',
            ID_Horario=self.horario,
            Dia=dia,
            Estado=estado,
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_booking_creates_reserved_appointment_sends_email_and_rejects_duplicate(self):
        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        self.assertEqual(self.client.get(reverse('registrar_atencion')).status_code, 200)
        dia = date.today() + timedelta(days=2)
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=self.horario,
            Dia=dia,
            Estado='Disponible',
        )
        payload = {
            'medico_id': str(self.medico.MatriculaProfesional),
            'horario_id': str(self.horario.ID_Horario),
            'fecha': dia.isoformat(),
            'descripcion': 'Consulta de prueba',
        }
        self.client.post(reverse('registrar_atencion'), payload)
        atencion = Atencion.objects.get()
        self.assertEqual(atencion.Estado, EstadoAtencion.RESERVADA)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['paciente@example.com'])
        self.assertEqual(
            HorarioMedico.objects.get(ID_Medico=self.medico, Dia=dia).Estado,
            'Ocupado',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Atencion.objects.create(
                    Paciente=self.paciente,
                    Usuario=self.usuario_paciente,
                    ID_Medico=self.medico,
                    Descripcion='Duplicada',
                    ID_Horario=self.horario,
                    Dia=dia,
                    Estado=EstadoAtencion.RESERVADA,
                )

    def test_registration_lists_only_future_slots_in_chronological_order(self):
        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        hoy = date(2026, 11, 23)
        ahora = timezone.make_aware(datetime(2026, 11, 23, 13, 38))
        bloques = [
            (hoy - timedelta(days=1), time(16, 0)),
            (hoy, time(10, 0)),
            (hoy, time(13, 38)),
            (hoy, time(14, 0)),
            (hoy + timedelta(days=1), time(9, 0)),
        ]
        for dia, inicio in bloques:
            horario = Horario.objects.create(
                Hora_Inicio=inicio,
                Hora_Fin=(datetime.combine(date.min, inicio) + timedelta(minutes=20)).time(),
            )
            HorarioMedico.objects.create(
                ID_Medico=self.medico,
                ID_Horario=horario,
                Dia=dia,
                Estado='Disponible',
            )

        with patch('AppAdAtM.views.timezone.localtime', return_value=ahora):
            response = self.client.get(reverse('registrar_atencion'))

        bloques_visibles = list(response.context['bloques_disponibles'])
        self.assertEqual(
            [(bloque.Dia, bloque.ID_Horario.Hora_Inicio) for bloque in bloques_visibles],
            [
                (hoy, time(14, 0)),
                (hoy + timedelta(days=1), time(9, 0)),
            ],
        )

    def test_cobro_starts_waiting_doctor_starts_and_recipe_finishes(self):
        atencion = self.crear_atencion()
        self.iniciar_sesion_de_prueba(self.recepcionista)
        self.assertEqual(self.client.get(reverse('gestionar_cobros')).status_code, 200)
        self.client.post(reverse('gestionar_cobros'), {
            'atencion_id': str(atencion.ID_Atencion),
            'metodo-pago': 'Efectivo',
            'neto': '1000',
            'iva': '190',
            'total': '1190',
        })
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.EN_ESPERA)
        self.assertTrue(Boleta.objects.filter(Atencion=atencion).exists())

        self.iniciar_sesion_de_prueba(self.usuario_medico)
        self.assertEqual(self.client.get(reverse('gestionar_atencion')).status_code, 200)
        self.assertEqual(self.client.get(reverse('gestionar_disponibilidad')).status_code, 200)
        self.client.post(reverse('gestionar_atencion'), {
            'accion': 'iniciar',
            'atencion_id': str(atencion.ID_Atencion),
        })
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.EN_ATENCION)

        medicamento = Medicamento.objects.create(
            Nombre_Medicamento='Medicamento de prueba',
            Nombre_Comun='Prueba',
        )
        self.assertEqual(self.client.get(reverse('gestionar_recetasatencion')).status_code, 200)
        self.client.post(reverse('gestionar_recetasatencion'), {
            'atencion_id': str(atencion.ID_Atencion),
            'diagnostico': 'Diagnóstico de prueba',
            'medicamento_id[]': [str(medicamento.ID_Medicamento)],
            'indicaciones[]': ['Una dosis'],
        })
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.FINALIZADA)

    def test_patient_can_cancel_only_own_reserved_appointment_in_advance(self):
        atencion = self.crear_atencion()
        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        self.assertEqual(self.client.get(reverse('gestionar_atencion')).status_code, 200)

        self.client.post(reverse('gestionar_atencion'), {
            'accion': 'cancelar',
            'atencion_id': str(atencion.ID_Atencion),
        })

        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.CANCELADA)
        self.assertEqual(
            HorarioMedico.objects.get(ID_Medico=self.medico, Dia=atencion.Dia).Estado,
            'Disponible',
        )

    def test_patient_atenciones_include_all_doctors_but_exclude_other_patients(self):
        propia = self.crear_atencion()
        otro_medico = Medico.objects.create(
            MatriculaProfesional=67890,
            RUN_Medico='66.666.666-6',
            Nombre_Medico='Diego',
            SegNombre_Medico='',
            TerNombre_Medico='',
            ApellidoP_Medico='Segundo',
            ApellidoM_Medico='',
            CorreoContactoMedico='segundo@example.com',
            NumeroContactoMedico='912345679',
        )
        horario_otro_medico = Horario.objects.create(
            Hora_Inicio=time(11, 0),
            Hora_Fin=time(11, 20),
        )
        fecha_otro_medico = propia.Dia + timedelta(days=1)
        con_otro_medico = Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=otro_medico,
            Descripcion='Consulta con segundo médico',
            ID_Horario=horario_otro_medico,
            Dia=fecha_otro_medico,
            Estado=EstadoAtencion.RESERVADA,
        )
        otro_paciente = Paciente.objects.create(
            RUN_Paciente='77.777.777-7',
            Nombre_Paciente='Paciente',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Distinto',
            ApellidoM='',
            Fecha_Nacimiento=date(1990, 1, 1),
        )
        Atencion.objects.create(
            Paciente=otro_paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Atención de otro paciente',
            ID_Horario=Horario.objects.create(
                Hora_Inicio=time(12, 0),
                Hora_Fin=time(12, 20),
            ),
            Dia=propia.Dia + timedelta(days=2),
            Estado=EstadoAtencion.RESERVADA,
        )
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        response = self.client.get(reverse('gestionar_atencion'))

        ids = set(response.context['atenciones'].values_list('ID_Atencion', flat=True))
        self.assertEqual(ids, {propia.ID_Atencion, con_otro_medico.ID_Atencion})
        self.assertContains(response, 'Buscar por médico')
        self.assertContains(response, 'data-busqueda="Marta Doctora')
        self.assertNotContains(response, 'tab-medicos')
        self.assertNotContains(response, 'tab-pacientes')

    def test_doctor_atenciones_include_all_patients_but_exclude_other_doctors(self):
        propia = self.crear_atencion()
        otro_paciente = Paciente.objects.create(
            RUN_Paciente='77.777.777-7',
            Nombre_Paciente='Paciente',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Distinto',
            ApellidoM='',
            Fecha_Nacimiento=date(1990, 1, 1),
        )
        con_otro_paciente = Atencion.objects.create(
            Paciente=otro_paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Consulta de otro paciente',
            ID_Horario=Horario.objects.create(
                Hora_Inicio=time(12, 0),
                Hora_Fin=time(12, 20),
            ),
            Dia=propia.Dia + timedelta(days=1),
            Estado=EstadoAtencion.RESERVADA,
        )
        otro_medico = Medico.objects.create(
            MatriculaProfesional=67890,
            RUN_Medico='66.666.666-6',
            Nombre_Medico='Diego',
            SegNombre_Medico='',
            TerNombre_Medico='',
            ApellidoP_Medico='Segundo',
            ApellidoM_Medico='',
            CorreoContactoMedico='segundo@example.com',
            NumeroContactoMedico='912345679',
        )
        Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=otro_medico,
            Descripcion='Atención de otro médico',
            ID_Horario=Horario.objects.create(
                Hora_Inicio=time(13, 0),
                Hora_Fin=time(13, 20),
            ),
            Dia=propia.Dia + timedelta(days=2),
            Estado=EstadoAtencion.RESERVADA,
        )
        self.iniciar_sesion_de_prueba(self.usuario_medico)

        response = self.client.get(reverse('gestionar_atencion'))

        ids = set(response.context['atenciones'].values_list('ID_Atencion', flat=True))
        self.assertEqual(ids, {propia.ID_Atencion, con_otro_paciente.ID_Atencion})
        self.assertContains(response, 'Buscar paciente por nombre o RUN')
        self.assertContains(response, 'data-busqueda="Pablo')
        self.assertContains(response, '22.222.222-2')

    def test_waiting_patient_can_abandon_and_reception_can_mark_no_show(self):
        waiting = self.crear_atencion(estado=EstadoAtencion.EN_ESPERA)
        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        self.client.post(reverse('gestionar_atencion'), {
            'accion': 'abandonar',
            'atencion_id': str(waiting.ID_Atencion),
        })
        waiting.refresh_from_db()
        self.assertEqual(waiting.Estado, EstadoAtencion.CANCELADA)

        no_show = self.crear_atencion(
            dia=date.today() - timedelta(days=1),
        )
        self.iniciar_sesion_de_prueba(self.recepcionista)
        self.client.post(reverse('gestionar_atencion'), {
            'accion': 'no_asistio',
            'atencion_id': str(no_show.ID_Atencion),
        })
        no_show.refresh_from_db()
        self.assertEqual(no_show.Estado, EstadoAtencion.NO_ASISTIO)

    def test_patient_cannot_open_admin_medicine_section(self):
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        response = self.client.get(reverse('gestionar_medicamentos'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse('registrar_atencion'),
            fetch_redirect_response=False,
        )

    def test_admin_can_create_doctor_and_login_account(self):
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)

        response = self.client.post(reverse('gestionar_medicos'), {
            'numero-matricula': '54321',
            'run-medico': '44.444.444-4',
            'nombre-medico': 'Laura',
            'segundo-nombre-medico': '',
            'tercer-nombre-medico': '',
            'apellido-paterno': 'Profesional',
            'apellido-materno': '',
            'especializacion': 'Medicina General',
            'correo-contacto': 'laura@example.com',
            'password-medico': 'clave1234',
            'numero-contacto': '912345678',
            'estado': 'Activo',
        })

        medico = Medico.objects.get(MatriculaProfesional=54321)
        self.assertEqual(medico.Usuario.Rol_id, 3)
        self.assertEqual(medico.Usuario.Correo_Usuario, 'laura@example.com')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(reverse('gestionar_medicos'), {'medico_id': '54321'}).status_code,
            200,
        )

    def test_patient_history_contains_only_its_appointments(self):
        self.crear_atencion()
        otro_paciente = Paciente.objects.create(
            RUN_Paciente='55.555.555-5',
            Nombre_Paciente='Otra',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Persona',
            ApellidoM='',
            Fecha_Nacimiento=date(1991, 1, 1),
            CorreoContactoPaciente='otra@example.com',
        )
        Atencion.objects.create(
            Paciente=otro_paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Historia ajena',
            ID_Horario=self.horario,
            Dia=date.today() + timedelta(days=3),
            Estado=EstadoAtencion.FINALIZADA,
        )
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        response = self.client.get(reverse('historial_atenciones'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Consulta')
        self.assertNotContains(response, 'Historia ajena')
