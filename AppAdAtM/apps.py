from django.apps import AppConfig
from django.db.backends.signals import connection_created

from .audit_context import current_audit_user_id


def registrar_funcion_actor_auditoria(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        connection.connection.create_function(
            'current_audit_user_id',
            0,
            current_audit_user_id,
        )


class AppadatmConfig(AppConfig):
    name = 'AppAdAtM'

    def ready(self):
        connection_created.connect(
            registrar_funcion_actor_auditoria,
            dispatch_uid='AppAdAtM.registrar_funcion_actor_auditoria',
        )
