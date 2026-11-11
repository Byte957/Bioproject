from django.shortcuts import render

# Create your views here.
def iniciosesion(request):
  return render(request, "InicioSesion.html")

def registrousuario(request):
  return render(request, "Registrarse.html")

def registroatencion(request):
  return render(request, "RegistroDeAtencion.html")

def gestionusuarios(request):
  return render(request, "GestionDeUsuarios.html")

def gestionrecetas(request):
  return render(request, "GestionDeRecetasAtencion.html")

def gestionpacientes(request):
  return render(request, "GestionDePacientes.html")