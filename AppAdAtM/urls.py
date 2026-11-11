from django.urls import path
from . import views  # Importa el archivo views.py de esta misma carpeta

urlpatterns = [
    # Cuando alguien entre a /AppAdAtM/iniciosesion/, se ejecutará tu vista
    path('InicioSesion', views.iniciosesion, name='inicio_sesion'),
    path('Registrarse', views.registrousuario, name='registrarse_usuario'),
    path('', views.registroatencion, name='registrar_atencion'),
    path('GestionarUsuario', views.gestionusuarios, name='gestionar_usuarios'),
    path('GestionarRecetasAtencion', views.gestionrecetas, name='gestionar_recetasatencion'),
]