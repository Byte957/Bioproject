from django.urls import path
from . import views  # Importa el archivo views.py de esta misma carpeta

urlpatterns = [
    path('', views.iniciosesion, name='inicio_sesion'),
    path('Registrarse', views.registrousuario, name='registrarse_usuario'),
    path('ActivarCuenta/<str:token>', views.activar_cuenta, name='activar_cuenta'),
    path('ReenviarActivacion', views.reenviar_activacion, name='reenviar_activacion'),
    path('RecuperarContrasena', views.solicitar_restauracion_contrasena, name='solicitar_restauracion_contrasena'),
    path('RestablecerContrasena/<str:token>', views.restablecer_contrasena, name='restablecer_contrasena'),
    path('RegistrarAtencion', views.registroatencion, name='registrar_atencion'),
    path('GestionarUsuario', views.gestionusuarios, name='gestionar_usuarios'),
    path('GestionarRecetasAtencion', views.gestionrecetas, name='gestionar_recetasatencion'),
    path('GestionarPacientes', views.gestionpacientes, name='gestionar_pacientes'),
    path('GestionarMedicos', views.gestionmedicos, name='gestionar_medicos'),
    path('GestionarDisponibilidad', views.gestiondisponibilidad, name='gestionar_disponibilidad'),
    path('GestionarAtencion', views.gestionatencion, name='gestionar_atencion'),
    path('HistorialAtenciones', views.historial_atenciones, name='historial_atenciones'),
    path('Cobros', views.cobro, name='gestionar_cobros'),
    path('GestionarMedicamentos', views.gestionmedicamentos, name='gestionar_medicamentos'),
    path('CerrarSesion', views.cerrarsesion, name='cerrar_sesion'),
    path('Auditoria', views.verauditoria, name='ver_auditoria'),
    path('Reportes', views.reportes_clinica, name='reportes_clinica'),
    path('Reportes/Exportar', views.exportar_reporte, name='exportar_reporte'),
]