import re
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
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
    MedicamentoReceta,
    Medico,
    Paciente,
    ActivacionPendiente,
    Receta,
    Rol,
    Usuario,
    Auditoria,
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
        auditoria_registro = Auditoria.objects.get(
            Tabla_Afectada='Usuario',
            Accion='INSERT',
            Registro_ID=str(usuario.ID_Usuario),
        )
        self.assertEqual(auditoria_registro.Usuario_id, usuario.ID_Usuario)
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
        auditoria_activacion = Auditoria.objects.get(
            Tabla_Afectada='Usuario',
            Accion='UPDATE',
            Registro_ID=str(usuario.ID_Usuario),
        )
        self.assertEqual(auditoria_activacion.Usuario_id, usuario.ID_Usuario)

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


class LoginLockoutTests(TestCase):
    def setUp(self):
        rol_admin, _ = Rol.objects.get_or_create(
            ID_Rol=1,
            defaults={'Nombre_Rol': 'Administrador'},
        )
        self.usuario = Usuario.objects.create(
            Nombre='Admin',
            Apellido='Prueba',
            Correo_Usuario='admin@example.com',
            Contraseña=make_password('clave1234'),
            Rol=rol_admin,
        )

    def iniciar_sesion(self, password):
        return self.client.post(reverse('inicio_sesion'), {
            'modo': 'correo',
            'identificador': 'admin@example.com',
            'password': password,
        }, follow=True)

    def test_fifth_failed_password_locks_account_and_rejects_correct_password(self):
        for _ in range(4):
            self.iniciar_sesion('incorrecta')

        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.Intentos_Fallidos, 4)
        inicio_quinto_intento = timezone.now()
        response = self.iniciar_sesion('incorrecta')

        self.usuario.refresh_from_db()
        self.assertContains(
            response,
            'Cuenta bloqueada por múltiples intentos fallidos. Intente nuevamente en 15 minutos',
        )
        self.assertEqual(self.usuario.Intentos_Fallidos, 5)
        self.assertGreaterEqual(
            self.usuario.Bloqueada_Hasta,
            inicio_quinto_intento + timedelta(minutes=15),
        )
        self.assertNotIn('usuario_id', self.client.session)

        response = self.iniciar_sesion('clave1234')
        self.assertContains(
            response,
            'Cuenta bloqueada por múltiples intentos fallidos. Intente nuevamente en 15 minutos',
        )
        self.assertNotIn('usuario_id', self.client.session)

    def test_correct_password_after_lock_expires_allows_login_and_resets_counter(self):
        self.usuario.Intentos_Fallidos = 5
        self.usuario.Bloqueada_Hasta = timezone.now() - timedelta(seconds=1)
        self.usuario.save(update_fields=['Intentos_Fallidos', 'Bloqueada_Hasta'])

        response = self.iniciar_sesion('clave1234')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session['usuario_id'], self.usuario.ID_Usuario)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.Intentos_Fallidos, 0)
        self.assertIsNone(self.usuario.Bloqueada_Hasta)

    def test_successful_login_resets_failed_attempts(self):
        for _ in range(4):
            self.iniciar_sesion('incorrecta')

        response = self.iniciar_sesion('clave1234')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session['usuario_id'], self.usuario.ID_Usuario)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.Intentos_Fallidos, 0)
        self.assertIsNone(self.usuario.Bloqueada_Hasta)


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

    def test_registration_can_search_available_doctors_by_name(self):
        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        dia = timezone.localdate() + timedelta(days=2)
        HorarioMedico.objects.create(
            ID_Medico=self.medico,
            ID_Horario=self.horario,
            Dia=dia,
            Estado='Disponible',
        )

        response = self.client.get(reverse('registrar_atencion'))

        self.assertContains(response, 'id="filtro-medico"')
        self.assertContains(response, 'Nombre o apellido del médico')
        self.assertContains(response, 'data-medico-busqueda="Marta')
        self.assertContains(response, 'filtrarDisponibles')

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
        self.assertContains(response, f'Cita #{atencion.ID_Atencion}')
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
        self.assertEqual(self.client.get(reverse('gestionar_atencion')).status_code, 302)
        self.assertEqual(self.client.get(reverse('gestionar_disponibilidad')).status_code, 200)
        inicio_response = self.client.post(reverse('gestionar_recetasatencion'), {
            'accion': 'iniciar',
            'atencion_id': str(atencion.ID_Atencion),
        }, follow=True)
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.EN_ATENCION)
        self.assertEqual(
            inicio_response.redirect_chain[0][0],
            f'{reverse("gestionar_recetasatencion")}?atencion_id={atencion.ID_Atencion}',
        )
        self.assertEqual(
            inicio_response.context['atencion_seleccionada_id'],
            str(atencion.ID_Atencion),
        )

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

    def test_doctor_can_finish_without_medicine_but_not_with_an_empty_medicine_row(self):
        atencion = self.crear_atencion(estado=EstadoAtencion.EN_ATENCION)
        medicamento = Medicamento.objects.create(
            Nombre_Medicamento='Medicamento opcional',
            Nombre_Comun='Opcional',
        )
        self.iniciar_sesion_de_prueba(self.usuario_medico)
        url = reverse('gestionar_recetasatencion')

        self.client.post(url, {
            'atencion_id': str(atencion.ID_Atencion),
            'medicamento_id[]': [''],
            'indicaciones[]': [''],
        })
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.EN_ATENCION)
        self.assertFalse(Receta.objects.filter(Atencion=atencion).exists())

        self.client.post(url, {
            'atencion_id': str(atencion.ID_Atencion),
            'medicamento_id[]': [str(medicamento.ID_Medicamento)],
            'indicaciones[]': ['   '],
        })
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.EN_ATENCION)
        self.assertFalse(Receta.objects.filter(Atencion=atencion).exists())

        self.client.post(url, {
            'atencion_id': str(atencion.ID_Atencion),
            'diagnostico': 'Atención sin medicamentos',
        })
        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.FINALIZADA)
        receta = Receta.objects.get(Atencion=atencion)
        self.assertFalse(MedicamentoReceta.objects.filter(ID_Receta=receta).exists())

        detalle_sin_medicamento = MedicamentoReceta.objects.create(
            ID_Medicamento=None,
            ID_Receta=receta,
            Indicaciones='Sin medicamento asociado',
        )
        self.assertIsNone(detalle_sin_medicamento.ID_Medicamento_id)
        response = self.client.get(reverse('historial_atenciones'))
        self.assertContains(response, 'Atención sin medicamentos')
        self.assertContains(response, 'Descargar Receta')

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

    def test_patient_cancellation_requires_at_least_24_hours(self):
        manana = date(2026, 11, 26)
        dia_siguiente = date(2026, 11, 27)
        menos_de_24_horas = self.crear_atencion(dia=manana)
        exactamente_24_horas = self.crear_atencion(dia=dia_siguiente)
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        with patch('AppAdAtM.views.timezone', wraps=timezone) as mocked_timezone:
            mocked_timezone.now.return_value = timezone.make_aware(
                datetime(2026, 11, 25, 11, 0),
            )
            self.client.post(reverse('gestionar_atencion'), {
                'accion': 'cancelar',
                'atencion_id': str(menos_de_24_horas.ID_Atencion),
            })

        menos_de_24_horas.refresh_from_db()
        self.assertEqual(menos_de_24_horas.Estado, EstadoAtencion.RESERVADA)
        inicio_exactamente_24_horas = timezone.make_aware(datetime.combine(
            exactamente_24_horas.Dia,
            exactamente_24_horas.ID_Horario.Hora_Inicio,
        ))
        ahora_exactamente_24_horas = timezone.make_aware(datetime(2026, 11, 26, 10, 0))
        self.assertEqual(
            inicio_exactamente_24_horas - ahora_exactamente_24_horas,
            timedelta(hours=24),
        )

        with patch('AppAdAtM.views.timezone', wraps=timezone) as mocked_timezone:
            mocked_timezone.now.return_value = timezone.make_aware(
                datetime(2026, 11, 26, 10, 0),
            )
            response = self.client.post(reverse('gestionar_atencion'), {
                'accion': 'cancelar',
                'atencion_id': str(exactamente_24_horas.ID_Atencion),
            }, follow=True)

        self.assertContains(response, f'La cita #{exactamente_24_horas.ID_Atencion} fue cancelada.')
        exactamente_24_horas.refresh_from_db()
        self.assertEqual(exactamente_24_horas.Estado, EstadoAtencion.CANCELADA)

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
        en_espera = self.crear_atencion(
            estado=EstadoAtencion.EN_ESPERA,
            dia=propia.Dia + timedelta(days=3),
        )
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        response = self.client.get(reverse('gestionar_atencion'))

        ids = set(response.context['atenciones'].values_list('ID_Atencion', flat=True))
        self.assertEqual(ids, {propia.ID_Atencion, con_otro_medico.ID_Atencion})
        self.assertNotIn(en_espera.ID_Atencion, ids)
        self.assertContains(response, 'Buscar por médico')
        self.assertContains(response, 'data-busqueda="Marta Doctora')
        self.assertContains(response, 'Cancelar cita')
        self.assertContains(response, 'id="btn-eliminar" disabled')
        self.assertContains(response, 'Importante: Solo se pueden cancelar citas con 24 horas de anticipación')
        self.assertContains(response, 'data-inicio-timestamp=')
        self.assertNotContains(response, 'Filtrar por estado')
        self.assertNotContains(response, 'id="btn-abandonar"')
        self.assertNotContains(response, 'tab-medicos')
        self.assertNotContains(response, 'tab-pacientes')

    def test_patient_atenciones_exclude_past_date_and_time(self):
        hoy = timezone.localdate()
        pasada = self.crear_atencion(dia=hoy - timedelta(days=1))
        pasada_hoy = self.crear_atencion(dia=hoy)
        horario_futuro = Horario.objects.create(
            Hora_Inicio=time(13, 0),
            Hora_Fin=time(13, 20),
        )
        futura_hoy = Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Consulta futura de hoy',
            ID_Horario=horario_futuro,
            Dia=hoy,
            Estado=EstadoAtencion.RESERVADA,
        )
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        with patch('AppAdAtM.views.timezone', wraps=timezone) as mocked_timezone:
            mocked_timezone.now.return_value = timezone.make_aware(
                datetime.combine(hoy, time(12, 0)),
            )
            response = self.client.get(reverse('gestionar_atencion'))

        ids = set(response.context['atenciones'].values_list('ID_Atencion', flat=True))
        self.assertEqual(ids, {futura_hoy.ID_Atencion})
        self.assertNotIn(pasada.ID_Atencion, ids)
        self.assertNotIn(pasada_hoy.ID_Atencion, ids)

    def test_doctors_are_blocked_from_reservation_management_and_menu_link(self):
        esperando = self.crear_atencion(estado=EstadoAtencion.EN_ESPERA)
        self.iniciar_sesion_de_prueba(self.usuario_medico)

        response = self.client.get(reverse('gestionar_atencion'))
        self.assertRedirects(
            response,
            reverse('gestionar_disponibilidad'),
            fetch_redirect_response=False,
        )

        self.client.post(reverse('gestionar_atencion'), {
            'accion': 'iniciar',
            'atencion_id': str(esperando.ID_Atencion),
        })
        esperando.refresh_from_db()
        self.assertEqual(esperando.Estado, EstadoAtencion.EN_ESPERA)

        menu_response = self.client.get(reverse('gestionar_disponibilidad'))
        self.assertContains(menu_response, 'Iniciar Cita')
        self.assertNotContains(menu_response, 'Gestionar Atenciones y Citas')

    def test_doctor_recetas_view_lists_only_waiting_and_in_progress_appointments(self):
        esperando = self.crear_atencion(
            estado=EstadoAtencion.EN_ESPERA,
            dia=date.today() + timedelta(days=1),
        )
        en_atencion = self.crear_atencion(
            estado=EstadoAtencion.EN_ATENCION,
            dia=date.today() + timedelta(days=2),
        )
        reservada = self.crear_atencion(
            estado=EstadoAtencion.RESERVADA,
            dia=date.today() + timedelta(days=3),
        )
        self.iniciar_sesion_de_prueba(self.usuario_medico)

        response = self.client.get(reverse('gestionar_recetasatencion'))

        ids = set(response.context['atenciones'].values_list('ID_Atencion', flat=True))
        self.assertEqual(ids, {esperando.ID_Atencion, en_atencion.ID_Atencion})
        self.assertNotIn(reservada.ID_Atencion, ids)
        self.assertTrue(response.context['hay_atencion_en_curso'])
        self.assertNotContains(response, 'id="filtro-fecha"')
        self.assertContains(response, 'Buscar paciente por nombre o RUN')
        self.assertContains(response, 'type="search" id="filtro-paciente"')
        self.assertContains(response, 'Iniciar Cita')
        self.assertContains(response, 'Continuar Cita')
        self.assertContains(response, 'disabled title="Ya tienes una cita en curso.')
        self.assertContains(response, 'window.confirm')
        self.assertNotContains(response, '<select id="filtro-paciente"')

        html = response.content.decode()
        continuar_index = html.index('Continuar Cita')
        boton_continuar = html[html.rfind('<button', 0, continuar_index):]
        self.assertNotIn('disabled', boton_continuar.split('>', 1)[0])

    def test_doctor_can_return_own_in_progress_appointment_to_waiting(self):
        atencion = self.crear_atencion(estado=EstadoAtencion.EN_ATENCION)
        self.iniciar_sesion_de_prueba(self.usuario_medico)

        response = self.client.post(reverse('gestionar_recetasatencion'), {
            'accion': 'devolver_espera',
            'atencion_id': str(atencion.ID_Atencion),
        }, follow=True)

        atencion.refresh_from_db()
        self.assertEqual(atencion.Estado, EstadoAtencion.EN_ESPERA)
        self.assertEqual(response.context['atencion_seleccionada_id'], '')
        self.assertContains(response, 'La cita')
        self.assertContains(response, 'volvió a estado En espera')

    def test_doctor_cannot_start_another_appointment_while_one_is_in_progress(self):
        esperando = self.crear_atencion(
            estado=EstadoAtencion.EN_ESPERA,
            dia=date.today() + timedelta(days=1),
        )
        en_atencion = self.crear_atencion(
            estado=EstadoAtencion.EN_ATENCION,
            dia=date.today() + timedelta(days=2),
        )
        self.iniciar_sesion_de_prueba(self.usuario_medico)

        response = self.client.post(reverse('gestionar_recetasatencion'), {
            'accion': 'iniciar',
            'atencion_id': str(esperando.ID_Atencion),
        }, follow=True)

        esperando.refresh_from_db()
        en_atencion.refresh_from_db()
        self.assertEqual(esperando.Estado, EstadoAtencion.EN_ESPERA)
        self.assertEqual(en_atencion.Estado, EstadoAtencion.EN_ATENCION)
        self.assertContains(response, 'Ya tienes una cita en curso')

    def test_doctor_cannot_start_another_doctors_waiting_appointment(self):
        esperando = self.crear_atencion(estado=EstadoAtencion.EN_ESPERA)
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
        esperando.ID_Medico = otro_medico
        esperando.save(update_fields=['ID_Medico'])
        self.iniciar_sesion_de_prueba(self.usuario_medico)

        self.client.post(reverse('gestionar_recetasatencion'), {
            'accion': 'iniciar',
            'atencion_id': str(esperando.ID_Atencion),
        })

        esperando.refresh_from_db()
        self.assertEqual(esperando.Estado, EstadoAtencion.EN_ESPERA)

        esperando.Estado = EstadoAtencion.EN_ATENCION
        esperando.save(update_fields=['Estado'])
        self.client.post(reverse('gestionar_recetasatencion'), {
            'accion': 'devolver_espera',
            'atencion_id': str(esperando.ID_Atencion),
        })
        esperando.refresh_from_db()
        self.assertEqual(esperando.Estado, EstadoAtencion.EN_ATENCION)

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

    def test_history_loads_responsive_navbar_script_for_patients_and_doctors(self):
        for usuario in (self.usuario_paciente, self.usuario_medico):
            self.iniciar_sesion_de_prueba(usuario)
            response = self.client.get(reverse('historial_atenciones'))

            self.assertContains(response, 'AppAdAtM/js/Filtros.js')
            self.assertContains(response, 'class="navbar-content"')
            self.assertContains(response, 'class="nav-ul"')

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

        registro_auditoria = Auditoria.objects.get(
            Tabla_Afectada='Paciente',
            Accion='INSERT',
            Registro_ID=str(paciente.ID),
        )
        self.assertEqual(registro_auditoria.Usuario_id, self.recepcionista.ID_Usuario)

        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)
        response = self.client.get(reverse('ver_auditoria'))
        self.assertContains(response, 'data-usuario="Usuario Prueba"')
        self.assertContains(response, 'Sistema / Sin usuario autenticado')

        self.iniciar_sesion_de_prueba(self.recepcionista)
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
        registro_actualizacion = Auditoria.objects.get(
            Tabla_Afectada='Paciente',
            Accion='UPDATE',
            Registro_ID=str(paciente.ID),
        )

        self.assertEqual(paciente.Estado, 'Activo')
        self.assertIsNone(paciente.Usuario_id)
        self.assertIsNone(paciente.NumeroContactoPaciente)
        self.assertEqual(registro_actualizacion.Usuario_id, self.recepcionista.ID_Usuario)

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

    def test_patient_history_contains_only_its_finalized_appointments(self):
        atencion_con_receta = self.crear_atencion(estado=EstadoAtencion.FINALIZADA)
        Receta.objects.create(
            Atencion=atencion_con_receta,
            Fecha_Receta=date.today(),
            Institucion='Biotechnica',
        )
        self.crear_atencion(
            estado=EstadoAtencion.FINALIZADA,
            dia=date.today() + timedelta(days=6),
        )
        self.crear_atencion(
            estado=EstadoAtencion.RESERVADA,
            dia=date.today() + timedelta(days=3),
        )
        self.crear_atencion(
            estado=EstadoAtencion.EN_ESPERA,
            dia=date.today() + timedelta(days=4),
        )
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
            Dia=date.today() + timedelta(days=5),
            Estado=EstadoAtencion.FINALIZADA,
        )
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        response = self.client.get(reverse('historial_atenciones'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Consulta')
        self.assertNotContains(response, 'RESERVADA')
        self.assertNotContains(response, 'EN ESPERA')
        self.assertNotContains(response, 'Historia ajena')
        self.assertContains(response, 'Descargar Receta')
        self.assertContains(response, 'Sin recetas asignadas')

    def test_history_filters_by_date_and_orders_appointments(self):
        atencion_antigua = self.crear_atencion(
            estado=EstadoAtencion.FINALIZADA,
            dia=date.today() + timedelta(days=2),
        )
        atencion_reciente = self.crear_atencion(
            estado=EstadoAtencion.FINALIZADA,
            dia=date.today() + timedelta(days=5),
        )
        self.iniciar_sesion_de_prueba(self.usuario_paciente)

        response_reciente = self.client.get(reverse('historial_atenciones'))
        self.assertEqual(
            list(response_reciente.context['atenciones'].values_list('ID_Atencion', flat=True)),
            [atencion_reciente.ID_Atencion, atencion_antigua.ID_Atencion],
        )

        response_fecha = self.client.get(
            reverse('historial_atenciones'),
            {'fecha': atencion_antigua.Dia.isoformat()},
        )
        self.assertEqual(
            list(response_fecha.context['atenciones'].values_list('ID_Atencion', flat=True)),
            [atencion_antigua.ID_Atencion],
        )

        response_antigua = self.client.get(
            reverse('historial_atenciones'),
            {'orden': 'antigua'},
        )
        self.assertEqual(
            list(response_antigua.context['atenciones'].values_list('ID_Atencion', flat=True)),
            [atencion_antigua.ID_Atencion, atencion_reciente.ID_Atencion],
        )

    def test_admin_reports_filter_by_date_and_sort_appointments(self):
        atencion_antigua = self.crear_atencion(
            estado=EstadoAtencion.FINALIZADA,
            dia=date.today() - timedelta(days=2),
        )
        atencion_reciente = self.crear_atencion(
            estado=EstadoAtencion.FINALIZADA,
            dia=date.today() - timedelta(days=1),
        )
        admin = self.crear_usuario('admin@example.com', self.rol_admin)
        self.iniciar_sesion_de_prueba(admin)

        response_fecha = self.client.get(
            reverse('reportes_clinica'),
            {
                'fecha': atencion_antigua.Dia.isoformat(),
                'orden': 'reciente',
            },
        )
        self.assertEqual(
            list(response_fecha.context['atenciones'].values_list('ID_Atencion', flat=True)),
            [atencion_antigua.ID_Atencion],
        )

        parametros_rango = {
            'fecha_inicio': atencion_antigua.Dia.isoformat(),
            'fecha_fin': atencion_reciente.Dia.isoformat(),
        }
        response_reciente = self.client.get(
            reverse('reportes_clinica'),
            {**parametros_rango, 'orden': 'reciente'},
        )
        self.assertEqual(
            list(response_reciente.context['atenciones'].values_list('ID_Atencion', flat=True)),
            [atencion_reciente.ID_Atencion, atencion_antigua.ID_Atencion],
        )

        response_antigua = self.client.get(
            reverse('reportes_clinica'),
            {**parametros_rango, 'orden': 'antigua'},
        )
        self.assertEqual(
            list(response_antigua.context['atenciones'].values_list('ID_Atencion', flat=True)),
            [atencion_antigua.ID_Atencion, atencion_reciente.ID_Atencion],
        )

        response_pdf = self.client.get(
            reverse('exportar_reporte'),
            {
                'tipo': 'atenciones',
                'formato': 'pdf',
                'fecha': atencion_antigua.Dia.isoformat(),
                'orden': 'antigua',
            },
        )
        self.assertEqual(response_pdf.status_code, 200)
        self.assertEqual(response_pdf['Content-Type'], 'application/pdf')
        self.assertIn(b'%PDF-', response_pdf.content)

    def test_history_recipe_download_is_pdf_and_restricted_to_owner_and_finalized_appointments(self):
        atencion = self.crear_atencion(estado=EstadoAtencion.FINALIZADA)
        receta = Receta.objects.create(
            Atencion=atencion,
            Fecha_Receta=date.today(),
            Institucion='Biotechnica',
        )
        medicamento = Medicamento.objects.create(
            Nombre_Medicamento='Paracetamol',
            Nombre_Comun='Acetaminofén',
        )
        MedicamentoReceta.objects.create(
            ID_Medicamento=medicamento,
            ID_Receta=receta,
            Indicaciones='1 comprimido cada 8 horas',
        )
        otra_atencion = self.crear_atencion(
            estado=EstadoAtencion.RESERVADA,
            dia=date.today() + timedelta(days=3),
        )
        otro_paciente = Paciente.objects.create(
            RUN_Paciente='55.555.555-5',
            Nombre_Paciente='Otra',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Persona',
            ApellidoM='',
            Fecha_Nacimiento=date(1991, 1, 1),
        )
        atencion_ajena = Atencion.objects.create(
            Paciente=otro_paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Atención ajena',
            ID_Horario=self.horario,
            Dia=date.today() + timedelta(days=4),
            Estado=EstadoAtencion.FINALIZADA,
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
        atencion_otro_medico = Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=otro_medico,
            Descripcion='Atención de otro médico',
            ID_Horario=self.horario,
            Dia=date.today() + timedelta(days=5),
            Estado=EstadoAtencion.FINALIZADA,
        )
        url_descarga = reverse(
            'descargar_receta_historial',
            args=[atencion.ID_Atencion],
        )

        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        response_paciente = self.client.get(url_descarga)
        self.assertEqual(response_paciente.status_code, 200)
        self.assertEqual(response_paciente['Content-Type'], 'application/pdf')
        self.assertIn(b'%PDF-', response_paciente.content)
        self.assertIn(
            f'Receta_Cita_{atencion.ID_Atencion}.pdf',
            response_paciente['Content-Disposition'],
        )
        self.assertEqual(
            self.client.get(reverse(
                'descargar_receta_historial',
                args=[atencion_ajena.ID_Atencion],
            )).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse(
                'descargar_receta_historial',
                args=[otra_atencion.ID_Atencion],
            )).status_code,
            404,
        )

        self.iniciar_sesion_de_prueba(self.usuario_medico)
        self.assertEqual(self.client.get(url_descarga).status_code, 200)
        self.assertEqual(
            self.client.get(reverse(
                'descargar_receta_historial',
                args=[atencion_otro_medico.ID_Atencion],
            )).status_code,
            404,
        )

    def test_history_searches_doctor_for_patients_and_patient_name_or_run_for_doctors(self):
        propia = self.crear_atencion(
            estado=EstadoAtencion.FINALIZADA,
            dia=date.today() + timedelta(days=2),
        )
        propia.Descripcion = 'Control de Pablo'
        propia.save(update_fields=['Descripcion'])
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
        control_otro_medico = Atencion.objects.create(
            Paciente=self.paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=otro_medico,
            Descripcion='Control con Diego',
            ID_Horario=self.horario,
            Dia=date.today() + timedelta(days=3),
            Estado=EstadoAtencion.FINALIZADA,
        )
        otro_paciente = Paciente.objects.create(
            RUN_Paciente='55.555.555-5',
            Nombre_Paciente='Elena',
            SegNombre_Paciente='',
            TerNombre_Paciente='',
            ApellidoP='Ejemplo',
            ApellidoM='',
            Fecha_Nacimiento=date(1990, 1, 1),
        )
        control_otro_paciente = Atencion.objects.create(
            Paciente=otro_paciente,
            Usuario=self.usuario_paciente,
            ID_Medico=self.medico,
            Descripcion='Control RUN',
            ID_Horario=self.horario,
            Dia=date.today() + timedelta(days=4),
            Estado=EstadoAtencion.FINALIZADA,
        )

        self.iniciar_sesion_de_prueba(self.usuario_paciente)
        response_paciente = self.client.get(
            reverse('historial_atenciones'),
            {'q': 'Diego Segundo'},
        )
        self.assertContains(response_paciente, 'Control con Diego')
        self.assertNotContains(response_paciente, 'Control de Pablo')
        self.assertContains(response_paciente, 'Buscar médico por nombre')

        self.iniciar_sesion_de_prueba(self.usuario_medico)
        response_medico = self.client.get(
            reverse('historial_atenciones'),
            {'q': '555555555'},
        )
        self.assertContains(response_medico, 'Control RUN')
        self.assertNotContains(response_medico, 'Control de Pablo')
        self.assertContains(response_medico, 'Buscar paciente por nombre o RUN')

        response_por_nombre = self.client.get(
            reverse('historial_atenciones'),
            {'q': 'Elena Ejemplo'},
        )
        self.assertContains(response_por_nombre, 'Control RUN')
        self.assertNotIn(control_otro_medico.ID_Atencion, response_medico.context['atenciones'].values_list(
            'ID_Atencion',
            flat=True,
        ))
        self.assertNotIn(propia.ID_Atencion, response_paciente.context['atenciones'].values_list(
            'ID_Atencion',
            flat=True,
        ))
