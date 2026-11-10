from django.urls import path
from . import views  # Importa el archivo views.py de esta misma carpeta

urlpatterns = [
    # Cuando alguien entre a /AppAdAtM/iniciosesion/, se ejecutará tu vista
    path('', views.iniciosesion, name='inicio_sesion'),
]