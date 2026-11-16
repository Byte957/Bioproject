from django.shortcuts import render, redirect
from django.db import transaction, IntegrityError
from django.contrib.auth.hashers import make_password
from .models import Rol, Usuario, Paciente, Medico
from django.contrib.auth.hashers import check_password
from django.contrib import messages

# Create your views here.


def registroatencion(request):
  return render(request, "RegistroDeAtencion.html")

def gestionusuarios(request):
  return render(request, "GestionDeUsuarios.html")

def gestionrecetas(request):
  return render(request, "GestionDeRecetasAtencion.html")

def gestionpacientes(request):
  return render(request, "GestionDePacientes.html")

def gestionmedicos(request):
  return render(request, "GestionDeMedicos.html")

def gestiondisponibilidad(request):
  return render(request, "GestionDeDisponibilidad.html")

def gestionatencion(request):
  return render(request, "GestionDeAtencion.html")

def cobro(request):
  return render(request, "Cobro.html")

def iniciosesion(request):
    if request.method == 'POST':
        run_ingresado = request.POST.get('usuario', '').strip()
        password_ingresada = request.POST.get('password', '')

        usuario_encontrado = None

        # 1. Buscar si el RUN pertenece a un Paciente
        paciente = Paciente.objects.filter(RUN_Paciente=run_ingresado).select_related('Usuario').first()
        if paciente and paciente.Usuario:
            usuario_encontrado = paciente.Usuario

        # 2. Si no es paciente, buscar si el RUN pertenece a un Médico
        if not usuario_encontrado:
            medico = Medico.objects.filter(RUN_Medico=run_ingresado).select_related('Usuario').first()
            if medico and medico.Usuario:
                usuario_encontrado = medico.Usuario

        # 3. Respaldo por Correo_Usuario (para Admin o Recepcionista)
        if not usuario_encontrado:
            usuario_encontrado = Usuario.objects.filter(Correo_Usuario=run_ingresado).first()

        # 4. Validar estado y contraseña
        if usuario_encontrado:
            if usuario_encontrado.Estado != 'Activo':
                messages.error(request, 'Tu cuenta se encuentra inactiva.')
                return render(request, 'InicioSesion.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE LOGIN

            if check_password(password_ingresada, usuario_encontrado.Contraseña) or password_ingresada == usuario_encontrado.Contraseña:
                request.session['usuario_id'] = usuario_encontrado.ID_Usuario
                request.session['usuario_nombre'] = f"{usuario_encontrado.Nombre} {usuario_encontrado.Apellido}"
                request.session['usuario_rol'] = usuario_encontrado.Rol.ID_Rol

                return redirect('registrar_atencion') # <-- Coincide con name='registrar_atencion' de tu urls.py

        messages.error(request, 'RUN o contraseña incorrectos.')

    return render(request, 'InicioSesion.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE LOGIN


def registrousuario(request):
    if request.method == 'POST':
        run = request.POST.get('run', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        seg_nombre = request.POST.get('segundo-nombre', '').strip()
        ter_nombre = request.POST.get('tercer-nombre', '').strip()
        apellido_p = request.POST.get('apellido-paterno', '').strip()
        apellido_m = request.POST.get('apellido-materno', '').strip()
        fecha_nac = request.POST.get('fecha-nacimiento')
        telefono = request.POST.get('telefono', '').strip()
        correo = request.POST.get('correo', '').strip()
        password = request.POST.get('password', '')

        # Validar RUN duplicado
        if Paciente.objects.filter(RUN_Paciente=run).exists() or Medico.objects.filter(RUN_Medico=run).exists():
            messages.error(request, f'No se pudo registrar: El RUN {run} ya está registrado en el sistema.')
            return render(request, 'Registrarse.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE REGISTRO

        # Validar Correo duplicado
        if Usuario.objects.filter(Correo_Usuario=correo).exists():
            messages.error(request, f'No se pudo registrar: El correo {correo} ya está en uso.')
            return render(request, 'Registrarse.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE REGISTRO

        # Validar longitud mínima de contraseña
        if len(password) < 4:
            messages.error(request, 'La contraseña debe tener al menos 4 caracteres.')
            return render(request, 'Registrarse.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE REGISTRO

        telefono_completo = f"+56{telefono}"[:15]

        try:
            with transaction.atomic():
                rol_paciente = Rol.objects.get(ID_Rol=4)

                nuevo_usuario = Usuario.objects.create(
                    Nombre=nombre[:20],
                    Apellido=apellido_p[:20],
                    Correo_Usuario=correo[:50],
                    Contraseña=make_password(password),
                    Estado='Activo',
                    Rol=rol_paciente
                )

                Paciente.objects.create(
                    RUN_Paciente=run[:25],
                    Nombre_Paciente=nombre[:20],
                    SegNombre_Paciente=seg_nombre[:20],
                    TerNombre_Paciente=ter_nombre[:20],
                    ApellidoP=apellido_p[:20],
                    ApellidoM=apellido_m[:20],
                    Fecha_Nacimiento=fecha_nac,
                    CorreoContactoPaciente=correo[:50],
                    NumeroContactoPaciente=telefono_completo,
                    Estado='Activo',
                    Usuario=nuevo_usuario
                )

            messages.success(request, '¡Registro exitoso! Ya puedes iniciar sesión con tu RUN y contraseña.')
            return redirect('inicio_sesion') # <-- Coincide con name='inicio_sesion' de tu urls.py

        except Rol.DoesNotExist:
            messages.error(request, 'Error del sistema: No existe el Rol de Paciente (ID 4) en la base de datos.')
        except Exception as e:
            messages.error(request, f'Ocurrió un error inesperado al guardar: {str(e)}')

    return render(request, 'Registrarse.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE REGISTRO