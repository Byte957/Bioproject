from django.db import migrations

def crear_roles(apps, schema_editor):
    Rol = apps.get_model('AppAdAtM', 'Rol')
    roles = [
        (1, 'Administrador'),
        (2, 'Recepcionista'),
        (3, 'Medico'),
        (4, 'Paciente'),
    ]
    for id_rol, nombre in roles:
        Rol.objects.get_or_create(ID_Rol=id_rol, defaults={'Nombre_Rol': nombre})

class Migration(migrations.Migration):

    dependencies = [
        ('AppAdAtM', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_roles),
    ]
