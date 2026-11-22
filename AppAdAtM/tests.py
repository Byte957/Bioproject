import re
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from .models import Paciente, Rol, Usuario


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@example.com',
)
class RegistroActivacionTests(TestCase):
    def setUp(self):
        Rol.objects.get_or_create(ID_Rol=4, defaults={'Nombre_Rol': 'Paciente'})

    def registrar_usuario(self):
        return self.client.post(reverse('registrarse_usuario'), {
            'run': '11.111.111-1',
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
            'run': '11.111.111-1',
            'password': 'clave1234',
        })
        self.assertNotIn('usuario_id', self.client.session)

        self.client.get(reverse('activar_cuenta', args=[token]))
        usuario.refresh_from_db()
        self.assertEqual(usuario.Estado, 'Activo')

        self.client.post(reverse('inicio_sesion'), {
            'run': '11.111.111-1',
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
        self.assertFalse(Paciente.objects.filter(RUN_Paciente='11.111.111-1').exists())
