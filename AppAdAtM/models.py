from django.db import models

# Create your models here.
from django.db import models

class Rol(models.Model):
    ID_Rol = models.AutoField(primary_key=True)
    Nombre_Rol = models.TextField()

    class Meta:
        db_table = 'Rol'

    def __str__(self):
        return self.Nombre_Rol


class Usuario(models.Model):
    ID_Usuario = models.AutoField(primary_key=True)
    Nombre = models.CharField(max_length=20)
    Apellido = models.CharField(max_length=20)
    Correo_Usuario = models.CharField(max_length=50, unique=True)
    Contraseña = models.CharField(max_length=128)
    Estado = models.CharField(max_length=10, default='Activo')
    Rol = models.ForeignKey(Rol, on_delete=models.RESTRICT, db_column='Rol')

    class Meta:
        db_table = 'Usuario'


class Paciente(models.Model):
    ID = models.AutoField(primary_key=True)
    RUN_Paciente = models.CharField(max_length=25)
    Nombre_Paciente = models.CharField(max_length=20)
    SegNombre_Paciente = models.CharField(max_length=20)
    TerNombre_Paciente = models.CharField(max_length=20)
    ApellidoP = models.CharField(max_length=20)
    ApellidoM = models.CharField(max_length=20)
    Fecha_Nacimiento = models.DateField()
    CorreoContactoPaciente = models.CharField(max_length=50, null=True, blank=True)
    NumeroContactoPaciente = models.CharField(max_length=15, null=True, blank=True)
    Estado = models.CharField(max_length=10, default='Activo')
    Usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, db_column='Usuario')

    class Meta:
        db_table = 'Paciente'


class Medico(models.Model):
    MatriculaProfesional = models.IntegerField(primary_key=True)
    RUN_Medico = models.CharField(max_length=25)
    Nombre_Medico = models.CharField(max_length=20)
    SegNombre_Medico = models.CharField(max_length=20)
    TerNombre_Medico = models.CharField(max_length=20)
    ApellidoP_Medico = models.CharField(max_length=20)
    ApellidoM_Medico = models.CharField(max_length=20)
    Especializacion = models.CharField(max_length=50, null=True, blank=True)
    CorreoContactoMedico = models.CharField(max_length=50)
    NumeroContactoMedico = models.CharField(max_length=15)
    Estado = models.CharField(max_length=10, default='Activo')
    Usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, db_column='Usuario')

    class Meta:
        db_table = 'Medico'


class Horario(models.Model):
    ID_Horario = models.AutoField(primary_key=True)
    Hora_Inicio = models.TimeField()
    Hora_Fin = models.TimeField()

    class Meta:
        db_table = 'Horario'


class HorarioMedico(models.Model):
    ID_Medico = models.ForeignKey(Medico, on_delete=models.CASCADE, db_column='ID_Medico')
    ID_Horario = models.ForeignKey(Horario, on_delete=models.CASCADE, db_column='ID_Horario')
    Dia = models.DateField()
    Estado = models.CharField(max_length=20, default='Disponible')

    class Meta:
        db_table = 'HorarioMedico'
        unique_together = (('ID_Medico', 'ID_Horario', 'Dia'),)


class Atencion(models.Model):
    ID_Atencion = models.AutoField(primary_key=True)
    Paciente = models.ForeignKey(Paciente, on_delete=models.RESTRICT, db_column='Paciente')
    Usuario = models.ForeignKey(Usuario, on_delete=models.RESTRICT, db_column='Usuario')
    ID_Medico = models.ForeignKey(Medico, on_delete=models.RESTRICT, db_column='ID_Medico')
    Descripcion = models.CharField(max_length=500)
    ID_Horario = models.ForeignKey(Horario, on_delete=models.RESTRICT, db_column='ID_Horario')
    Dia = models.DateField()
    Estado = models.CharField(max_length=20, default='Programada')

    class Meta:
        db_table = 'Atencion'


class Medicamento(models.Model):
    ID_Medicamento = models.AutoField(primary_key=True)
    Nombre_Medicamento = models.CharField(max_length=50)
    Nombre_Comun = models.CharField(max_length=50)

    class Meta:
        db_table = 'Medicamento'


class Receta(models.Model):
    ID_Receta = models.AutoField(primary_key=True)
    Fecha_Receta = models.DateField()
    Atencion = models.ForeignKey(Atencion, on_delete=models.CASCADE, db_column='Atencion')
    Institucion = models.CharField(max_length=50)

    class Meta:
        db_table = 'Receta'


class MedicamentoReceta(models.Model):
    ID_Medicamento = models.ForeignKey(Medicamento, on_delete=models.CASCADE, db_column='ID_Medicamento')
    ID_Receta = models.ForeignKey(Receta, on_delete=models.CASCADE, db_column='ID_Receta')
    Indicaciones = models.CharField(max_length=200)

    class Meta:
        db_table = 'MedicamentoReceta'
        unique_together = (('ID_Medicamento', 'ID_Receta'),)


class Boleta(models.Model):
    ID_Boleta = models.AutoField(primary_key=True)
    Atencion = models.ForeignKey(Atencion, on_delete=models.RESTRICT, db_column='Atencion')
    Metodo_Pago = models.CharField(max_length=20)
    Monto_Neto = models.FloatField()
    IVA = models.FloatField()
    Total = models.FloatField()

    class Meta:
        db_table = 'Boleta'


class Auditoria(models.Model):
    ID_Auditoria = models.AutoField(primary_key=True)
    Tabla_Afectada = models.TextField()
    Accion = models.TextField()
    Registro_ID = models.TextField()
    Detalle = models.TextField(null=True, blank=True)
    Fecha_Hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'Auditoria'