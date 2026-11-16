from django.shortcuts import render, redirect
from django.db import transaction, IntegrityError
from django.contrib.auth.hashers import make_password
from .models import Rol, Usuario, Paciente, Medico
from django.contrib.auth.hashers import check_password
from django.contrib import messages
from datetime import date
# Create your views here.


def registroatencion(request):
  return render(request, "RegistroDeAtencion.html")

def gestionusuarios(request):
    if request.method == 'POST':
        usuario_id = request.POST.get('usuario_id', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        correo = request.POST.get('correo', '').strip()
        password = request.POST.get('password', '').strip()
        rol_id = request.POST.get('rol')
        estado = request.POST.get('estado', 'Activo')

        try:
            rol_obj = Rol.objects.get(ID_Rol=rol_id)

            if usuario_id:
                # --- MODO EDITAR USUARIO EXISTENTE ---
                usuario = Usuario.objects.get(ID_Usuario=usuario_id)

                # Validar que el correo no pertenezca a otro usuario distinto
                if Usuario.objects.filter(Correo_Usuario=correo).exclude(ID_Usuario=usuario_id).exists():
                    messages.error(request, f'El correo {correo} ya está siendo usado por otro usuario.')
                    return redirect('gestionar_usuarios')

                usuario.Nombre = nombre[:20]
                usuario.Apellido = apellido[:20]
                usuario.Correo_Usuario = correo[:50]
                usuario.Rol = rol_obj
                usuario.Estado = estado

                # Solo actualiza la contraseña si el administrador escribió una nueva
                if password:
                    usuario.Contraseña = make_password(password)

                usuario.save()
                messages.success(request, f'Usuario #{usuario.ID_Usuario} ({usuario.Nombre}) actualizado correctamente.')

            else:
                # --- MODO CREAR NUEVO USUARIO ---
                if Usuario.objects.filter(Correo_Usuario=correo).exists():
                    messages.error(request, f'No se pudo crear: El correo {correo} ya está registrado.')
                    return redirect('gestionar_usuarios')

                if not password or len(password) < 4:
                    messages.error(request, 'Debes ingresar una contraseña de al menos 4 caracteres para un usuario nuevo.')
                    return redirect('gestionar_usuarios')

                nuevo = Usuario.objects.create(
                    Nombre=nombre[:20],
                    Apellido=apellido[:20],
                    Correo_Usuario=correo[:50],
                    Contraseña=make_password(password),
                    Estado=estado,
                    Rol=rol_obj
                )
                messages.success(request, f'Usuario {nuevo.Nombre} {nuevo.Apellido} creado con éxito.')

        except Rol.DoesNotExist:
            messages.error(request, 'El rol seleccionado no es válido.')
        except Usuario.DoesNotExist:
            messages.error(request, 'El usuario que intentas editar no existe.')
        except Exception as e:
            messages.error(request, f'Error al guardar usuario: {str(e)}')

        return redirect('gestionar_usuarios')

    # --- PETICIÓN GET: LISTAR USUARIOS Y ROLES ---
    usuarios = Usuario.objects.select_related('Rol').all().order_by('ID_Usuario')
    roles = Rol.objects.all().order_by('ID_Rol')

    return render(request, 'GestionDeUsuarios.html', { # <-- VERIFICA EL NOMBRE REAL DE TU HTML
        'usuarios': usuarios,
        'roles': roles
    })

def gestionrecetas(request):
  return render(request, "GestionDeRecetasAtencion.html")



def gestionpacientes(request):
    if request.method == 'POST':
        paciente_id = request.POST.get('paciente_id', '').strip()
        run = request.POST.get('run-paciente', '').strip()
        nombre = request.POST.get('nombre-paciente', '').strip()
        seg_nombre = request.POST.get('segundo-nombre', '').strip()
        ter_nombre = request.POST.get('tercer-nombre', '').strip()
        apellido_p = request.POST.get('apellido-paterno', '').strip()
        apellido_m = request.POST.get('apellido-materno', '').strip()
        fecha_nac = request.POST.get('fecha-nacimiento')
        correo = request.POST.get('correo-contacto', '').strip()
        telefono = request.POST.get('telefono-contacto', '').strip()
        estado = request.POST.get('estado', 'Activo')

        telefono_limpio = telefono.replace('+56', '').strip()
        telefono_completo = f"+56{telefono_limpio}"[:15]

        try:
            if paciente_id:
                # --- MODO EDITAR PACIENTE EXISTENTE ---
                paciente = Paciente.objects.get(ID=paciente_id)

                if Paciente.objects.filter(RUN_Paciente=run).exclude(ID=paciente_id).exists() or Medico.objects.filter(RUN_Medico=run).exists():
                    messages.error(request, f'No se pudo actualizar: El RUN {run} ya pertenece a otra persona.')
                    return redirect('gestionar_pacientes')

                paciente.RUN_Paciente = run[:25]
                paciente.Nombre_Paciente = nombre[:20]
                paciente.SegNombre_Paciente = seg_nombre[:20]
                paciente.TerNombre_Paciente = ter_nombre[:20]
                paciente.ApellidoP = apellido_p[:20]
                paciente.ApellidoM = apellido_m[:20]
                paciente.Fecha_Nacimiento = fecha_nac
                paciente.CorreoContactoPaciente = correo[:50]
                paciente.NumeroContactoPaciente = telefono_completo
                paciente.Estado = estado
                paciente.save()

                if paciente.Usuario:
                    paciente.Usuario.Nombre = nombre[:20]
                    paciente.Usuario.Apellido = apellido_p[:20]
                    paciente.Usuario.Correo_Usuario = correo[:50]
                    paciente.Usuario.Estado = estado
                    paciente.Usuario.save()

                messages.success(request, f'Paciente {paciente.Nombre_Paciente} {paciente.ApellidoP} actualizado correctamente.')

            else:
                # --- MODO CREAR NUEVO PACIENTE ---
                if Paciente.objects.filter(RUN_Paciente=run).exists() or Medico.objects.filter(RUN_Medico=run).exists():
                    messages.error(request, f'No se pudo crear: El RUN {run} ya está registrado.')
                    return redirect('gestionar_pacientes')

                with transaction.atomic():
                    usuario_vinc = Usuario.objects.filter(Correo_Usuario=correo).first()
                    if not usuario_vinc:
                        rol_paciente = Rol.objects.get(ID_Rol=4)
                        usuario_vinc = Usuario.objects.create(
                            Nombre=nombre[:20],
                            Apellido=apellido_p[:20],
                            Correo_Usuario=correo[:50],
                            Contraseña=make_password(run),
                            Estado=estado,
                            Rol=rol_paciente
                        )

                    nuevo_pac = Paciente.objects.create(
                        RUN_Paciente=run[:25],
                        Nombre_Paciente=nombre[:20],
                        SegNombre_Paciente=seg_nombre[:20],
                        TerNombre_Paciente=ter_nombre[:20],
                        ApellidoP=apellido_p[:20],
                        ApellidoM=apellido_m[:20],
                        Fecha_Nacimiento=fecha_nac,
                        CorreoContactoPaciente=correo[:50],
                        NumeroContactoPaciente=telefono_completo,
                        Estado=estado,
                        Usuario=usuario_vinc
                    )

                messages.success(request, f'Paciente {nuevo_pac.Nombre_Paciente} {nuevo_pac.ApellidoP} registrado (clave inicial: su RUN).')

        except Paciente.DoesNotExist:
            messages.error(request, 'El paciente seleccionado no existe.')
        except Rol.DoesNotExist:
            messages.error(request, 'Error del sistema: No existe el Rol 4 (Paciente).')
        except Exception as e:
            messages.error(request, f'Error al guardar paciente: {str(e)}')

        return redirect('gestionar_pacientes')

    # --- PETICIÓN GET: LISTAR PACIENTES Y CALCULAR EDAD ---
    hoy = date.today()
    pacientes = Paciente.objects.all().order_by('ID')
    for p in pacientes:
        if p.Fecha_Nacimiento:
            p.edad = hoy.year - p.Fecha_Nacimiento.year - (
                (hoy.month, hoy.day) < (p.Fecha_Nacimiento.month, p.Fecha_Nacimiento.day)
            )
        else:
            p.edad = 'N/A'

    return render(request, 'GestionDePacientes.html', {
        'pacientes': pacientes
    })

def gestionmedicos(request):
    if request.method == 'POST':
        medico_id = request.POST.get('medico_id', '').strip()
        matricula_raw = request.POST.get('numero-matricula', '').strip()
        run = request.POST.get('run-medico', '').strip()
        nombre = request.POST.get('nombre-medico', '').strip()
        seg_nombre = request.POST.get('segundo-nombre-medico', '').strip()
        ter_nombre = request.POST.get('tercer-nombre-medico', '').strip()
        apellido_p = request.POST.get('apellido-paterno', '').strip()
        apellido_m = request.POST.get('apellido-materno', '').strip()
        especializacion = request.POST.get('especializacion', '').strip()
        correo = request.POST.get('correo-contacto', '').strip()
        password = request.POST.get('password-medico', '').strip()
        telefono = request.POST.get('numero-contacto', '').strip()
        estado = request.POST.get('estado', 'Activo')

        if not matricula_raw.isdigit():
            messages.error(request, 'El Número de Matrícula debe ser numérico.')
            return redirect('gestionar_medicos')

        matricula = int(matricula_raw)
        telefono_limpio = telefono.replace('+56', '').strip()
        telefono_completo = f"+56{telefono_limpio}"[:15]

        try:
            if medico_id:
                # --- MODO EDITAR MÉDICO EXISTENTE ---
                medico = Medico.objects.get(MatriculaProfesional=medico_id)

                if Medico.objects.filter(RUN_Medico=run).exclude(MatriculaProfesional=medico_id).exists() or Paciente.objects.filter(RUN_Paciente=run).exists():
                    messages.error(request, f'No se pudo actualizar: El RUN {run} ya pertenece a otra persona.')
                    return redirect('gestionar_medicos')

                if int(medico_id) != matricula and Medico.objects.filter(MatriculaProfesional=matricula).exists():
                    messages.error(request, f'La Matrícula Profesional {matricula} ya está registrada.')
                    return redirect('gestionar_medicos')

                if password and len(password) < 4:
                    messages.error(request, 'La nueva contraseña debe tener al menos 4 caracteres.')
                    return redirect('gestionar_medicos')

                qs_correo = Usuario.objects.filter(Correo_Usuario=correo)
                if medico.Usuario:
                    qs_correo = qs_correo.exclude(ID_Usuario=medico.Usuario.ID_Usuario)
                if qs_correo.exists():
                    messages.error(request, f'El correo {correo} ya está siendo usado por otro usuario.')
                    return redirect('gestionar_medicos')

                if int(medico_id) != matricula:
                    usuario_actual = medico.Usuario
                    medico.delete()
                    medico = Medico(MatriculaProfesional=matricula, Usuario=usuario_actual)

                medico.RUN_Medico = run[:25]
                medico.Nombre_Medico = nombre[:20]
                medico.SegNombre_Medico = seg_nombre[:20]
                medico.TerNombre_Medico = ter_nombre[:20]
                medico.ApellidoP_Medico = apellido_p[:20]
                medico.ApellidoM_Medico = apellido_m[:20]
                medico.Especializacion = especializacion[:50]
                medico.CorreoContactoMedico = correo[:50]
                medico.NumeroContactoMedico = telefono_completo
                medico.Estado = estado

                rol_medico = Rol.objects.get(ID_Rol=3)
                if medico.Usuario:
                    medico.Usuario.Nombre = nombre[:20]
                    medico.Usuario.Apellido = apellido_p[:20]
                    medico.Usuario.Correo_Usuario = correo[:50]
                    medico.Usuario.Estado = estado
                    medico.Usuario.Rol = rol_medico
                    if password:
                        medico.Usuario.Contraseña = make_password(password)
                    medico.Usuario.save()
                else:
                    clave_final = password if password else run
                    nuevo_usuario = Usuario.objects.create(
                        Nombre=nombre[:20],
                        Apellido=apellido_p[:20],
                        Correo_Usuario=correo[:50],
                        Contraseña=make_password(clave_final),
                        Estado=estado,
                        Rol=rol_medico
                    )
                    medico.Usuario = nuevo_usuario

                medico.save()
                messages.success(request, f'Dr(a). {medico.Nombre_Medico} {medico.ApellidoP_Medico} y su usuario vinculados fueron actualizados.')

            else:
                # --- MODO CREAR NUEVO MÉDICO + SU USUARIO ---
                if not password or len(password) < 4:
                    messages.error(request, 'Debes ingresar una contraseña de al menos 4 caracteres para crear el médico.')
                    return redirect('gestionar_medicos')

                if Medico.objects.filter(MatriculaProfesional=matricula).exists():
                    messages.error(request, f'No se pudo crear: La Matrícula Profesional {matricula} ya existe.')
                    return redirect('gestionar_medicos')

                if Medico.objects.filter(RUN_Medico=run).exists() or Paciente.objects.filter(RUN_Paciente=run).exists():
                    messages.error(request, f'No se pudo crear: El RUN {run} ya está registrado.')
                    return redirect('gestionar_medicos')

                if Usuario.objects.filter(Correo_Usuario=correo).exists():
                    messages.error(request, f'No se pudo crear: El correo {correo} ya está registrado en la tabla de Usuarios.')
                    return redirect('gestionar_medicos')

                with transaction.atomic():
                    rol_medico = Rol.objects.get(ID_Rol=3)

                    nuevo_usuario = Usuario.objects.create(
                        Nombre=nombre[:20],
                        Apellido=apellido_p[:20],
                        Correo_Usuario=correo[:50],
                        Contraseña=make_password(password),
                        Estado=estado,
                        Rol=rol_medico
                    )

                    nuevo_med = Medico.objects.create(
                        MatriculaProfesional=matricula,
                        RUN_Medico=run[:25],
                        Nombre_Medico=nombre[:20],
                        SegNombre_Medico=seg_nombre[:20],
                        TerNombre_Medico=ter_nombre[:20],
                        ApellidoP_Medico=apellido_p[:20],
                        ApellidoM_Medico=apellido_m[:20],
                        Especializacion=especializacion[:50],
                        CorreoContactoMedico=correo[:50],
                        NumeroContactoMedico=telefono_completo,
                        Estado=estado,
                        Usuario=nuevo_usuario
                    )

                messages.success(request, f'Dr(a). {nuevo_med.Nombre_Medico} {nuevo_med.ApellidoP_Medico} y su cuenta de acceso fueron creados correctamente.')

        except Medico.DoesNotExist:
            messages.error(request, 'El médico seleccionado no existe.')
        except Rol.DoesNotExist:
            messages.error(request, 'Error del sistema: No existe el Rol 3 (Médico) en la base de datos.')
        except Exception as e:
            messages.error(request, f'Error al guardar médico: {str(e)}')

        return redirect('gestionar_medicos')

    # --- PETICIÓN GET: LISTAR MÉDICOS ---
    medicos = Medico.objects.select_related('Usuario').all().order_by('MatriculaProfesional')
    return render(request, 'GestionDeMedicos.html', {
        'medicos': medicos
    })

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