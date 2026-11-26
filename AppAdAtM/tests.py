import re
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.core import mail
from django.core import signing
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
    ActivacionPendiente,
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

    def registrar_usuario(
        self,
        run='12.345.678-5',
        correo='ana@example.com',
        telefono='912345678',
    ):
        return self.client.post(reverse('registrarse_usuario'), {
            'run': run,
            'nombre': 'Ana',
            'segundo-nombre': '',
            'tercer-nombre': '',
            'apellido-paterno': 'Prueba',
            'apellido-materno': '',
            'fecha-nacimiento': '1990-01-01',
            'telefono': telefono,
            'correo': correo,
            'password': 'clave1234',
        })

    def crear_ficha_sin_cuenta(self, estado='Activo', correo='ana@example.com'):
        return Paciente.objects.create(
            RUN_Paciente='12.345.678-5',
            Nombre_Paciente='Ana',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Prueba',
            ApellidoM='',
            Fecha_Nacimiento=date(1990, 1, 1),
            CorreoContactoPaciente=correo,
            NumeroContactoPaciente=None,
            Estado=estado,
        )

    def test_authentication_forms_render(self):
        for ruta in (
            'inicio_sesion',
            'registrarse_usuario',
            'reenviar_activacion',
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

    def test_registration_links_matching_unlinked_patient_after_email_activation(self):
        paciente = self.crear_ficha_sin_cuenta()

        response = self.registrar_usuario()
        usuario = Usuario.objects.get(Correo_Usuario='ana@example.com')
        paciente.refresh_from_db()

        self.assertRedirects(response, reverse('inicio_sesion'), fetch_redirect_response=False)
        self.assertEqual(Paciente.objects.count(), 1)
        self.assertIsNone(paciente.Usuario_id)
        self.assertEqual(usuario.Estado, 'Inactivo')
        self.assertEqual(mail.outbox[0].to, ['ana@example.com'])

        self.client.get(reverse('activar_cuenta', args=[self.token_del_correo()]))
        usuario.refresh_from_db()
        paciente.refresh_from_db()

        self.assertEqual(usuario.Estado, 'Activo')
        self.assertEqual(paciente.Usuario_id, usuario.ID_Usuario)
        self.assertEqual(paciente.Estado, 'Activo')

    def test_linking_does_not_reactivate_an_inactive_patient_record(self):
        paciente = self.crear_ficha_sin_cuenta(estado='Inactivo')
        self.registrar_usuario()
        usuario = Usuario.objects.get(Correo_Usuario='ana@example.com')

        self.client.get(reverse('activar_cuenta', args=[self.token_del_correo()]))
        usuario.refresh_from_db()
        paciente.refresh_from_db()
        self.assertEqual(usuario.Estado, 'Activo')
        self.assertEqual(paciente.Estado, 'Inactivo')
        self.assertEqual(paciente.Usuario_id, usuario.ID_Usuario)

        self.client.post(reverse('inicio_sesion'), {
            'modo': 'run',
            'identificador': '12.345.678-5',
            'password': 'clave1234',
        })
        self.assertNotIn('usuario_id', self.client.session)

    def test_linking_rejects_email_that_does_not_match_patient_record(self):
        self.crear_ficha_sin_cuenta(correo='contacto@example.com')

        response = self.registrar_usuario(correo='ana@example.com')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(Correo_Usuario='ana@example.com').exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_registration_rejects_patient_already_linked_to_an_account(self):
        paciente = self.crear_ficha_sin_cuenta()
        usuario = Usuario.objects.create(
            Nombre='Ana',
            Apellido='Prueba',
            Correo_Usuario='ana@example.com',
            Contraseña='existing-password-hash',
            Estado='Activo',
            Rol=Rol.objects.get(ID_Rol=4),
        )
        paciente.Usuario = usuario
        paciente.save(update_fields=['Usuario'])

        response = self.registrar_usuario()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Usuario.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_registration_accepts_missing_phone_and_stores_null(self):
        self.registrar_usuario(telefono='')

        paciente = Paciente.objects.get(RUN_Paciente='12.345.678-5')
        self.assertIsNone(paciente.NumeroContactoPaciente)

    def test_invalid_activation_link_does_not_activate_account(self):
        self.registrar_usuario()
        token = self.token_del_correo()

        self.client.get(reverse('activar_cuenta', args=[token + 'invalid']))

        self.assertEqual(
            Usuario.objects.get(Correo_Usuario='ana@example.com').Estado,
            'Inactivo',
        )

    def test_expired_activation_link_can_be_resent(self):
        self.registrar_usuario()
        usuario = Usuario.objects.get(Correo_Usuario='ana@example.com')
        pendiente = ActivacionPendiente.objects.get(Usuario=usuario)
        pendiente.UltimoEnvio = timezone.now() - timedelta(minutes=2)
        pendiente.save(update_fields=['UltimoEnvio'])

        with patch(
            'django.core.signing.time.time',
            return_value=(timezone.now() - timedelta(days=2)).timestamp(),
        ):
            token_expirado = signing.dumps(
                {
                    'usuario_id': usuario.pk,
                    'password_fingerprint': _huella_contrasena(usuario),
                    'paciente_id': pendiente.Paciente_id,
                    'vincular_paciente': pendiente.VincularPaciente,
                    'activar_ficha': pendiente.ActivarFicha,
                },
                salt='AppAdAtM.activacion_cuenta',
            )

        self.client.get(reverse('activar_cuenta', args=[token_expirado]))
        usuario.refresh_from_db()
        self.assertEqual(usuario.Estado, 'Inactivo')

        response = self.client.post(reverse('reenviar_activacion'), {
            'run': '12.345.678-5',
            'correo': 'ana@example.com',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)
        token_nuevo = re.search(r'/ActivarCuenta/([^/\s]+)', mail.outbox[1].body).group(1)
        self.client.get(reverse('activar_cuenta', args=[token_nuevo]))
        usuario.refresh_from_db()
        self.assertEqual(usuario.Estado, 'Activo')

    def test_resend_uses_pending_patient_and_rejects_another_run(self):
        paciente = self.crear_ficha_sin_cuenta(estado='Inactivo')
        self.registrar_usuario()
        usuario = Usuario.objects.get(Correo_Usuario='ana@example.com')
        pendiente = ActivacionPendiente.objects.get(Usuario=usuario)
        pendiente.UltimoEnvio = timezone.now() - timedelta(minutes=2)
        pendiente.save(update_fields=['UltimoEnvio'])
        Paciente.objects.create(
            RUN_Paciente='98.765.432-5',
            Nombre_Paciente='Otra',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Persona',
            ApellidoM='',
            Fecha_Nacimiento=date(1990, 1, 1),
            CorreoContactoPaciente='ana@example.com',
            Estado='Inactivo',
        )

        self.client.post(reverse('reenviar_activacion'), {
            'run': '98.765.432-5',
            'correo': 'ana@example.com',
        })
        self.assertEqual(len(mail.outbox), 1)

        self.client.post(reverse('reenviar_activacion'), {
            'run': '12.345.678-5',
            'correo': 'ana@example.com',
        })
        self.assertEqual(len(mail.outbox), 2)
        token_nuevo = re.search(r'/ActivarCuenta/([^/\s]+)', mail.outbox[1].body).group(1)
        self.client.get(reverse('activar_cuenta', args=[token_nuevo]))

        usuario.refresh_from_db()
        paciente.refresh_from_db()
        self.assertEqual(paciente.Usuario_id, usuario.ID_Usuario)
        self.assertEqual(paciente.Estado, 'Inactivo')
        self.assertIsNone(Paciente.objects.get(RUN_Paciente='98.765.432-5').Usuario_id)

    def test_resend_recovers_legacy_pending_patient_without_activation_metadata(self):
        paciente = self.crear_ficha_sin_cuenta(estado='Inactivo')
        self.registrar_usuario()
        usuario = Usuario.objects.get(Correo_Usuario='ana@example.com')
        ActivacionPendiente.objects.filter(Usuario=usuario).delete()

        self.client.post(reverse('reenviar_activacion'), {
            'run': '12.345.678-5',
            'correo': 'ana@example.com',
        })

        self.assertEqual(len(mail.outbox), 2)
        token_nuevo = re.search(r'/ActivarCuenta/([^/\s]+)', mail.outbox[1].body).group(1)
        self.client.get(reverse('activar_cuenta', args=[token_nuevo]))
        usuario.refresh_from_db()
        paciente.refresh_from_db()
        self.assertEqual(usuario.Estado, 'Activo')
        self.assertEqual(paciente.Usuario_id, usuario.ID_Usuario)
        self.assertEqual(paciente.Estado, 'Inactivo')

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

    def test_schedule_api_returns_only_future_available_slots_for_selected_doctor_and_date(self):
        fecha = date(2026, 11, 25)
        ahora = timezone.make_aware(datetime(2026, 11, 25, 16, 0))
        horarios = [
            Horario.objects.create(
                Hora_Inicio=inicio,
                Hora_Fin=(datetime.combine(date.min, inicio) + timedelta(minutes=20)).time(),
            )
            for inicio in (time(15, 0), time(16, 20), time(17, 0))
        ]
        for indice, horario in enumerate(horarios):
            HorarioMedico.objects.create(
                ID_Medico=self.medico,
                ID_Horario=horario,
                Dia=fecha,
                Estado='Ocupado' if indice == 2 else 'Disponible',
            )
        atencion_actual = Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Bloque ocupado',
            ID_Horario=horarios[2],
            Dia=fecha,
            Estado=EstadoAtencion.RESERVADA,
        )
        self.iniciar_sesion_de_prueba(self.recepcionista)

        with patch('AppAdAtM.views.timezone.now', return_value=ahora):
            response = self.client.get(reverse('gestionar_atencion'), {
                'disponibilidad': '1',
                'medico_id': str(self.medico.MatriculaProfesional),
                'dia': fecha.isoformat(),
            })
            response_edicion = self.client.get(reverse('gestionar_atencion'), {
                'disponibilidad': '1',
                'medico_id': str(self.medico.MatriculaProfesional),
                'dia': fecha.isoformat(),
                'atencion_id': str(atencion_actual.ID_Atencion),
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [bloque['id'] for bloque in response.json()['bloques']],
            [horarios[1].ID_Horario],
        )
        self.assertEqual(
            [bloque['id'] for bloque in response_edicion.json()['bloques']],
            [horarios[1].ID_Horario, horarios[2].ID_Horario],
        )
        self.assertIn('cita actual', response_edicion.json()['bloques'][1]['texto'])

    @patch('AppAdAtM.views._enviar_confirmacion_atencion')
    def test_reception_cannot_book_past_time_today_but_can_book_same_time_tomorrow(self, _send_email):
        hoy = date(2026, 11, 25)
        manana = hoy + timedelta(days=1)
        ahora = timezone.make_aware(datetime(2026, 11, 25, 16, 0))
        horario_pasado = Horario.objects.create(
            Hora_Inicio=time(15, 0),
            Hora_Fin=time(15, 20),
        )
        horario_futuro = Horario.objects.create(
            Hora_Inicio=time(15, 0),
            Hora_Fin=time(15, 20),
        )
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=horario_pasado,
            Dia=hoy,
            Estado='Disponible',
        )
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=horario_futuro,
            Dia=manana,
            Estado='Disponible',
        )
        self.iniciar_sesion_de_prueba(self.recepcionista)

        with patch('AppAdAtM.views.timezone.now', return_value=ahora):
            self.client.post(reverse('gestionar_atencion'), {
                'paciente': str(self.paciente.ID),
                'medico': str(self.medico.MatriculaProfesional),
                'dia': hoy.isoformat(),
                'horario': str(horario_pasado.ID_Horario),
                'descripcion': 'Prueba de horario pasado',
            })
            self.assertFalse(Atencion.objects.exists())

            self.client.post(reverse('gestionar_atencion'), {
                'paciente': str(self.paciente.ID),
                'medico': str(self.medico.MatriculaProfesional),
                'dia': manana.isoformat(),
                'horario': str(horario_futuro.ID_Horario),
                'descripcion': 'Prueba de horario futuro',
            })

        atencion = Atencion.objects.get()
        self.assertEqual(atencion.Dia, manana)
        self.assertEqual(atencion.ID_Horario_id, horario_futuro.ID_Horario)

    @patch('AppAdAtM.views._enviar_confirmacion_atencion')
    def test_reception_cannot_reschedule_to_past_start_time(self, _send_email):
        hoy = date(2026, 11, 25)
        manana = hoy + timedelta(days=1)
        ahora = timezone.make_aware(datetime(2026, 11, 25, 16, 0))
        atencion = self.crear_atencion(
            dia=date(2026, 11, 27),
        )
        horario_manana = Horario.objects.create(
            Hora_Inicio=time(15, 0),
            Hora_Fin=time(15, 20),
        )
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=horario_manana,
            Dia=manana,
            Estado='Disponible',
        )
        self.iniciar_sesion_de_prueba(self.recepcionista)

        with patch('AppAdAtM.views.timezone.now', return_value=ahora):
            self.client.post(reverse('gestionar_atencion'), {
                'atencion_id': str(atencion.ID_Atencion),
                'paciente': str(self.paciente.ID),
                'medico': str(self.medico.MatriculaProfesional),
                'dia': hoy.isoformat(),
                'horario': str(self.horario.ID_Horario),
                'descripcion': 'No debe reagendarse al pasado',
            })
            atencion.refresh_from_db()
            self.assertEqual(atencion.Dia, date(2026, 11, 27))

            self.client.post(reverse('gestionar_atencion'), {
                'atencion_id': str(atencion.ID_Atencion),
                'paciente': str(self.paciente.ID),
                'medico': str(self.medico.MatriculaProfesional),
                'dia': manana.isoformat(),
                'horario': str(horario_manana.ID_Horario),
                'descripcion': 'Reagendada a mañana',
            })

        atencion.refresh_from_db()
        self.assertEqual(atencion.Dia, manana)
        self.assertEqual(atencion.ID_Horario_id, horario_manana.ID_Horario)

    def test_registered_appointments_have_date_state_filters_and_consistent_cards(self):
        atencion = self.crear_atencion()
        self.iniciar_sesion_de_prueba(self.recepcionista)

        response = self.client.get(reverse('gestionar_atencion'))

        self.assertContains(response, 'filtrar-fecha-atencion')
        self.assertContains(response, 'filtrar-estado-atencion')
        self.assertContains(response, f'Atención #{atencion.ID_Atencion}')
        self.assertContains(response, 'Estado: Reservada')
        self.assertContains(response, 'RUN: 22.222.222-2')

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

    def test_cobro_lists_only_todays_reserved_appointments_and_shows_scheduled_details(self):
        hoy = timezone.localdate()
        atencion_hoy = self.crear_atencion(dia=hoy)
        atencion_futura = self.crear_atencion(dia=hoy + timedelta(days=1))
        horario_en_espera = Horario.objects.create(
            Hora_Inicio=time(10, 30),
            Hora_Fin=time(10, 50),
        )
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=horario_en_espera,
            Dia=hoy,
            Estado='Ocupado',
        )
        atencion_en_espera = Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Control de seguimiento',
            ID_Horario=horario_en_espera,
            Dia=hoy,
            Estado=EstadoAtencion.EN_ESPERA,
        )
        self.iniciar_sesion_de_prueba(self.recepcionista)

        response = self.client.get(reverse('gestionar_cobros'))

        self.assertContains(response, f'value="{atencion_hoy.ID_Atencion}"')
        self.assertNotContains(response, f'value="{atencion_futura.ID_Atencion}"')
        self.assertNotContains(response, f'value="{atencion_en_espera.ID_Atencion}"')
        self.assertContains(response, 'Buscar paciente por RUN o nombre')
        self.assertContains(response, 'data-search=')
        self.assertContains(response, '22.222.222-2')
        self.assertContains(response, 'Hora agendada:')
        self.assertContains(response, 'Motivo:')
        self.assertNotContains(response, 'Medicamentos Recetados')

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

    def test_user_management_only_lists_and_assigns_admin_or_reception_roles(self):
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)

        response = self.client.get(reverse('gestionar_usuarios'))

        self.assertEqual(
            set(response.context['usuarios'].values_list('ID_Usuario', flat=True)),
            {admin.ID_Usuario, self.recepcionista.ID_Usuario},
        )
        self.assertEqual(
            list(response.context['roles'].values_list('ID_Rol', flat=True)),
            [1, 2],
        )

        response = self.client.post(reverse('gestionar_usuarios'), {
            'usuario_id': str(self.usuario_paciente.ID_Usuario),
            'nombre': 'Pablo',
            'apellido': 'Ejemplo',
            'correo': 'paciente@example.com',
            'password': '',
            'rol': '4',
            'estado': 'Activo',
        })

        self.assertEqual(response.status_code, 302)
        self.usuario_paciente.refresh_from_db()
        self.assertEqual(self.usuario_paciente.Rol_id, 4)

    def test_patient_and_doctor_lists_have_name_or_date_and_state_filters(self):
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        Medico.objects.create(
            MatriculaProfesional=98765,
            RUN_Medico='88.888.888-8',
            Nombre_Medico='Elena',
            SegNombre_Medico='',
            TerNombre_Medico='',
            ApellidoP_Medico='Inactiva',
            ApellidoM_Medico='',
            CorreoContactoMedico='elena@example.com',
            NumeroContactoMedico='912345678',
            Estado='Inactivo',
        )
        Paciente.objects.create(
            RUN_Paciente='99.999.999-9',
            Nombre_Paciente='Paciente',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Inactivo',
            ApellidoM='',
            Fecha_Nacimiento=date(2000, 1, 1),
            CorreoContactoPaciente='inactivo@example.com',
            Estado='Inactivo',
        )
        self.iniciar_sesion_de_prueba(admin)

        response_medicos = self.client.get(reverse('gestionar_medicos'))
        self.assertContains(response_medicos, 'Buscar por nombre')
        self.assertContains(response_medicos, 'filtro-estado-medico')
        self.assertContains(response_medicos, 'Elena')
        self.assertContains(response_medicos, 'Estado: Inactivo')

        self.iniciar_sesion_de_prueba(self.recepcionista)
        response_pacientes = self.client.get(reverse('gestionar_pacientes'))
        self.assertContains(response_pacientes, 'filtro-estado')
        self.assertContains(response_pacientes, 'filtro-fecha-nac')
        self.assertContains(response_pacientes, 'Estado: Inactivo')

    def test_admin_can_create_doctor_and_login_account(self):
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)

        datos = {
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
        }

        datos['password-medico'] = '12345678'
        self.client.post(reverse('gestionar_medicos'), datos)
        self.assertFalse(Medico.objects.filter(MatriculaProfesional=54321).exists())

        datos['password-medico'] = 'clave1234'
        response = self.client.post(reverse('gestionar_medicos'), datos)

        medico = Medico.objects.get(MatriculaProfesional=54321)
        self.assertEqual(medico.Usuario.Rol_id, 3)
        self.assertEqual(medico.Usuario.Correo_Usuario, 'laura@example.com')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(reverse('gestionar_medicos'), {'medico_id': '54321'}).status_code,
            200,
        )

    def test_patient_management_creates_inactive_profile_without_account_and_allows_optional_phone(self):
        self.iniciar_sesion_de_prueba(self.recepcionista)
        self.assertEqual(self.client.get(reverse('gestionar_pacientes')).status_code, 200)
        datos = {
            'run-paciente': '12.345.678-5',
            'nombre-paciente': 'Claudia',
            'segundo-nombre': '',
            'tercer-nombre': '',
            'apellido-paterno': 'Nueva',
            'apellido-materno': '',
            'fecha-nacimiento': '1995-05-10',
            'correo-contacto': 'claudia@example.com',
            'telefono-contacto': '',
            'estado': 'Activo',
        }

        self.client.post(reverse('gestionar_pacientes'), datos)
        paciente = Paciente.objects.get(RUN_Paciente='12.345.678-5')

        self.assertEqual(paciente.Estado, 'Inactivo')
        self.assertIsNone(paciente.Usuario_id)
        self.assertIsNone(paciente.NumeroContactoPaciente)
        self.assertFalse(Usuario.objects.filter(Correo_Usuario='claudia@example.com').exists())

        datos.update({
            'paciente_id': str(paciente.ID),
            'estado': 'Activo',
        })
        self.client.post(reverse('gestionar_pacientes'), datos)
        paciente.refresh_from_db()

        self.assertEqual(paciente.Estado, 'Activo')
        self.assertIsNone(paciente.Usuario_id)
        self.assertIsNone(paciente.NumeroContactoPaciente)

    def test_doctor_management_starts_blank_and_selecting_doctor_loads_edit_form(self):
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)

        response = self.client.get(reverse('gestionar_medicos'))
        self.assertIsNone(response.context['medico'])

        response = self.client.get(
            reverse('gestionar_medicos'),
            {'medico_id': str(self.medico.MatriculaProfesional)},
        )
        self.assertEqual(response.context['medico'], self.medico)

    def test_doctor_password_change_uses_configured_password_validators(self):
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)
        password_anterior = self.usuario_medico.Contraseña

        self.client.post(reverse('gestionar_medicos'), {
            'medico_id': str(self.medico.MatriculaProfesional),
            'numero-matricula': str(self.medico.MatriculaProfesional),
            'run-medico': self.medico.RUN_Medico,
            'nombre-medico': self.medico.Nombre_Medico,
            'segundo-nombre-medico': self.medico.SegNombre_Medico,
            'tercer-nombre-medico': self.medico.TerNombre_Medico,
            'apellido-paterno': self.medico.ApellidoP_Medico,
            'apellido-materno': self.medico.ApellidoM_Medico,
            'especializacion': self.medico.Especializacion or 'Medicina General',
            'correo-contacto': self.medico.CorreoContactoMedico,
            'password-medico': '12345678',
            'numero-contacto': '912345678',
            'estado': self.medico.Estado,
        })

        self.usuario_medico.refresh_from_db()
        self.assertEqual(self.usuario_medico.Contraseña, password_anterior)

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
