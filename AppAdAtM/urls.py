from django.urls import path
from . import views  # Importa el archivo views.py de esta misma carpeta

urlpatterns = [
    path('', views.iniciosesion, name='inicio_sesion'),
    path('Registrarse', views.registrousuario, name='registrarse_usuario'),
    path('RegistrarAtencion', views.registroatencion, name='registrar_atencion'),
    path('GestionarUsuario', views.gestionusuarios, name='gestionar_usuarios'),
    path('GestionarRecetasAtencion', views.gestionrecetas, name='gestionar_recetasatencion'),
    path('GestionarPacientes', views.gestionpacientes, name='gestionar_pacientes'),
    path('GestionarMedicos', views.gestionmedicos, name='gestionar_medicos'),
    path('GestionarDisponibilidad', views.gestiondisponibilidad, name='gestionar_disponibilidad'),
    path('GestionarAtencion', views.gestionatencion, name='gestionar_atencion'),
    path('Cobros', views.cobro, name='gestionar_cobros'),
    path('GestionarMedicamentos', views.gestionmedicamentos, name='gestionar_medicamentos'),
]