from django.shortcuts import render, redirect
from django.db import transaction, IntegrityError
from django.contrib.auth.hashers import make_password
from .models import Rol, Usuario, Paciente, Medico, Horario, HorarioMedico, Atencion, Medicamento
from django.contrib.auth.hashers import check_password
from django.contrib import messages
from datetime import date, datetime, time, timedelta
import json



# Create your views here.
def gestionmedicamentos(request):
    if request.method == 'POST':
        accion = request.POST.get('accion', 'guardar')
        medicamento_id = request.POST.get('medicamento_id', '').strip()

        # --- ELIMINAR MEDICAMENTO ---
        if accion == 'eliminar' and medicamento_id:
            try:
                med = Medicamento.objects.get(ID_Medicamento=medicamento_id)
                nombre_borrado = med.Nombre_Medicamento
                med.delete()
                messages.success(request, f'Medicamento "{nombre_borrado}" eliminado correctamente.')
            except Medicamento.DoesNotExist:
                messages.error(request, 'El medicamento seleccionado no existe.')
            except Exception as e:
                messages.error(request, f'No se puede eliminar (puede estar asociado a recetas médicas): {str(e)}')
            return redirect('gestionar_medicamentos')

        # --- CREAR O EDITAR MEDICAMENTO ---
        nombre_med = request.POST.get('nombre-medicamento', '').strip()
        nombre_comun = request.POST.get('nombre-comun', '').strip()

        if not nombre_med or not nombre_comun:
            messages.error(request, 'Debes completar tanto el Nombre del Medicamento como su Nombre Común.')
            return redirect('gestionar_medicamentos')

        try:
            if medicamento_id:
                med = Medicamento.objects.get(ID_Medicamento=medicamento_id)
                if Medicamento.objects.filter(Nombre_Medicamento__iexact=nombre_med).exclude(ID_Medicamento=medicamento_id).exists():
                    messages.error(request, f'Ya existe otro medicamento registrado como "{nombre_med}".')
                    return redirect('gestionar_medicamentos')

                med.Nombre_Medicamento = nombre_med[:50]
                med.Nombre_Comun = nombre_comun[:50]
                med.save()
                messages.success(request, f'Medicamento #{med.ID_Medicamento} ({med.Nombre_Medicamento}) actualizado correctamente.')
            else:
                if Medicamento.objects.filter(Nombre_Medicamento__iexact=nombre_med).exists():
                    messages.error(request, f'El medicamento "{nombre_med}" ya se encuentra registrado.')
                    return redirect('gestionar_medicamentos')

                nuevo_med = Medicamento.objects.create(
                    Nombre_Medicamento=nombre_med[:50],
                    Nombre_Comun=nombre_comun[:50]
                )
                messages.success(request, f'Medicamento "{nuevo_med.Nombre_Medicamento}" agregado correctamente.')

        except Medicamento.DoesNotExist:
            messages.error(request, 'El medicamento seleccionado no existe.')
        except Exception as e:
            messages.error(request, f'Error al guardar medicamento: {str(e)}')

        return redirect('gestionar_medicamentos')

    medicamentos = Medicamento.objects.all().order_by('Nombre_Medicamento')
    return render(request, 'GestionarMedicamentos.html', {
        'medicamentos': medicamentos
    })

def registroatencion(request):
    """Vista restringida para agendar citas únicamente en horarios previamente habilitados como 'Disponible'."""
    _asegurar_bloques_horario()

    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    # Si es Paciente (Rol 4), forzamos que solo pueda agendar para sí mismo
    paciente_sesion = None
    if rol_id == 4 and usuario_id:
        paciente_sesion = Paciente.objects.filter(Usuario_id=usuario_id).first()

    if request.method == 'POST':
        medico_id = request.POST.get('medico_id', '').strip()
        horario_id = request.POST.get('horario_id', '').strip()
        fecha_str = request.POST.get('fecha', '').strip()
        run_paciente = paciente_sesion.RUN_Paciente if paciente_sesion else request.POST.get('run-paciente', '').strip()
        descripcion = request.POST.get('descripcion', '').strip() or 'Consulta médica agendada'

        if not medico_id or not horario_id or not fecha_str:
            messages.error(request, 'Debes seleccionar un bloque disponible de la lista izquierda.')
            return redirect('registrar_atencion')

        try:
            medico_obj = Medico.objects.get(MatriculaProfesional=medico_id)
            horario_obj = Horario.objects.get(ID_Horario=horario_id)
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()

            paciente_obj = Paciente.objects.filter(RUN_Paciente=run_paciente).first()
            if not paciente_obj:
                messages.error(request, f'No existe un paciente registrado con el RUN {run_paciente}.')
                return redirect('registrar_atencion')

            usuario_obj = Usuario.objects.filter(ID_Usuario=usuario_id).first() or paciente_obj.Usuario or Usuario.objects.first()

            # En Registrar Atención EXIGIMOS que el bloque esté explícitamente 'Disponible'
            bloque_habilitado = HorarioMedico.objects.filter(
                ID_Medico=medico_obj,
                Dia=fecha_obj,
                ID_Horario=horario_obj,
                Estado__iexact='Disponible'
            ).exists()

            ya_ocupado = Atencion.objects.filter(
                ID_Medico=medico_obj,
                Dia=fecha_obj,
                ID_Horario=horario_obj
            ).exclude(Estado__iexact='Cancelada').exists()

            if not bloque_habilitado or ya_ocupado:
                messages.error(request, 'Este horario ya no se encuentra disponible. Selecciona otro bloque.')
                return redirect('registrar_atencion')

            with transaction.atomic():
                nueva_atencion = Atencion.objects.create(
                    Paciente=paciente_obj,
                    Usuario=usuario_obj,
                    ID_Medico=medico_obj,
                    Descripcion=descripcion[:500],
                    ID_Horario=horario_obj,
                    Dia=fecha_obj,
                    Estado='Programada'
                )

                HorarioMedico.objects.filter(
                    ID_Medico=medico_obj,
                    Dia=fecha_obj,
                    ID_Horario=horario_obj
                ).update(Estado='Ocupado')

            messages.success(
                request,
                f'¡Cita #{nueva_atencion.ID_Atencion} agendada con Dr(a). {medico_obj.Nombre_Medico} {medico_obj.ApellidoP_Medico} para el {fecha_obj.strftime("%d/%m/%Y")} a las {horario_obj.Hora_Inicio.strftime("%H:%M")}.'
            )

        except (Medico.DoesNotExist, Horario.DoesNotExist):
            messages.error(request, 'El médico o bloque horario seleccionado no es válido.')
        except ValueError:
            messages.error(request, 'Fecha inválida.')
        except Exception as e:
            messages.error(request, f'Error al registrar la atención: {str(e)}')

        return redirect('registrar_atencion')

    # --- PETICIÓN GET ---
    hoy = date.today()
    disponibles_raw = HorarioMedico.objects.filter(
        Estado__iexact='Disponible',
        Dia__gte=hoy,
        ID_Medico__Estado='Activo'
    ).select_related('ID_Medico', 'ID_Horario').order_by('Dia', 'ID_Horario__Hora_Inicio')

    ocupados_set = set(
        Atencion.objects.filter(Dia__gte=hoy)
        .exclude(Estado__iexact='Cancelada')
        .values_list('ID_Medico_id', 'Dia', 'ID_Horario_id')
    )

    bloques_disponibles = [
        hm for hm in disponibles_raw
        if (hm.ID_Medico_id, hm.Dia, hm.ID_Horario_id) not in ocupados_set
    ]

    especialidades = (
        Medico.objects.filter(Estado='Activo')
        .exclude(Especializacion__isnull=True)
        .exclude(Especializacion__exact='')
        .values_list('Especializacion', flat=True)
        .distinct()
        .order_by('Especializacion')
    )

    pacientes_lista = Paciente.objects.filter(Estado='Activo').order_by('ApellidoP') if rol_id != 4 else []

    return render(request, 'RegistroDeAtencion.html', {
        'bloques_disponibles': bloques_disponibles,
        'especialidades': especialidades,
        'pacientes_lista': pacientes_lista,
        'paciente_sesion': paciente_sesion,
    })

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

def _asegurar_bloques_horario():
    """Crea los 51 bloques de 20 minutos (07:00 a 24:00) si la tabla Horario está vacía."""
    if Horario.objects.exists():
        return
    bloques = []
    for min_totales in range(7 * 60, 24 * 60, 20):
        h_ini, m_ini = divmod(min_totales, 60)
        fin_totales = min_totales + 20
        # 24:00 se guarda como 23:59:59 en TimeField de SQLite/Python
        if fin_totales >= 24 * 60:
            t_fin = time(23, 59, 59)
        else:
            h_fin, m_fin = divmod(fin_totales, 60)
            t_fin = time(h_fin, m_fin)
        bloques.append(Horario(Hora_Inicio=time(h_ini, m_ini), Hora_Fin=t_fin))
    Horario.objects.bulk_create(bloques)


def _str_a_time(hora_str):
    if hora_str == '24:00':
        return time(23, 59, 59)
    h, m = map(int, hora_str.split(':'))
    return time(h, m)


def gestiondisponibilidad(request):
    _asegurar_bloques_horario()

    # 1. Identificar el Médico activo (si inició sesión como Médico, o elegido por filtro si es Admin/Recepcionista)
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    medico_actual = None
    if rol_id == 3 and usuario_id:
        medico_actual = Medico.objects.filter(Usuario_id=usuario_id).first()

    medico_param = request.GET.get('medico') or request.POST.get('medico_id')
    if not medico_actual and medico_param:
        medico_actual = Medico.objects.filter(MatriculaProfesional=medico_param).first()

    if not medico_actual:
        medico_actual = Medico.objects.filter(Estado='Activo').first()

    # 2. Calcular la semana (Lunes a Domingo) según la fecha seleccionada en el filtro
    fecha_str = request.GET.get('fecha') or request.POST.get('fecha_semana')
    try:
        fecha_ref = datetime.strptime(fecha_str, '%Y-%m-%d').date() if fecha_str else date.today()
    except ValueError:
        fecha_ref = date.today()

    lunes_semana = fecha_ref - timedelta(days=fecha_ref.weekday())
    dias_semana = [lunes_semana + timedelta(days=i) for i in range(7)]
    nombres_dias = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    mapa_dia_fecha = dict(zip(nombres_dias, dias_semana))

    # 3. Guardar o actualizar bloques en HorarioMedico (POST)
    if request.method == 'POST':
        if not medico_actual:
            messages.error(request, 'No hay un médico seleccionado para gestionar disponibilidad.')
            return redirect('gestionar_disponibilidad')

        dia_nombre = request.POST.get('dia-semana', '').strip()
        inicio_str = request.POST.get('inicio-reserva', '').strip()
        fin_str = request.POST.get('fin-reserva', '').strip()
        accion_estado = request.POST.get('estado-bloque', '').strip()  # Disponible | Bloqueado | Eliminar

        fecha_objetivo = mapa_dia_fecha.get(dia_nombre)
        if not fecha_objetivo or not inicio_str or not fin_str:
            messages.error(request, 'Debes seleccionar día, bloque de inicio y bloque de término.')
            return redirect(f"{request.path}?fecha={fecha_ref.isoformat()}&medico={medico_actual.MatriculaProfesional}")

        t_ini = _str_a_time(inicio_str)
        t_fin = _str_a_time(fin_str)

        if t_fin <= t_ini:
            messages.error(request, 'La hora de término debe ser posterior a la hora de inicio.')
            return redirect(f"{request.path}?fecha={fecha_ref.isoformat()}&medico={medico_actual.MatriculaProfesional}")

        # Buscar todos los bloques de 20 min comprendidos en el rango seleccionado
        bloques_rango = Horario.objects.filter(Hora_Inicio__gte=t_ini, Hora_Fin__lte=t_fin).order_by('Hora_Inicio')

        conflictos = 0
        aplicados = 0

        for bloque in bloques_rango:
            # Verificar si ese bloque ya tiene una Atención agendada (no Cancelada)
            tiene_atencion = Atencion.objects.filter(
                ID_Medico=medico_actual,
                Dia=fecha_objetivo,
                ID_Horario=bloque
            ).exclude(Estado__iexact='Cancelada').exists()

            if tiene_atencion:
                conflictos += 1
                continue

            if accion_estado == 'Eliminar':
                HorarioMedico.objects.filter(
                    ID_Medico=medico_actual,
                    Dia=fecha_objetivo,
                    ID_Horario=bloque
                ).delete()
                aplicados += 1
            else:
                HorarioMedico.objects.update_or_create(
                    ID_Medico=medico_actual,
                    Dia=fecha_objetivo,
                    ID_Horario=bloque,
                    defaults={'Estado': accion_estado}
                )
                aplicados += 1

        if aplicados > 0:
            messages.success(request, f'Se actualizaron {aplicados} bloque(s) el {dia_nombre} ({fecha_objetivo.strftime("%d/%m/%Y")}).')
        if conflictos > 0:
            messages.warning(request, f'{conflictos} bloque(s) no se modificaron porque ya tienen una atención médica reservada.')

        return redirect(f"{request.path}?fecha={fecha_ref.isoformat()}&medico={medico_actual.MatriculaProfesional}")

    # 4. Consultar disponibilidad (HorarioMedico) y citas (Atencion) de toda la semana
    eventos_semana = []

    if medico_actual:
        disponibilidades = HorarioMedico.objects.filter(
            ID_Medico=medico_actual,
            Dia__range=(dias_semana[0], dias_semana[6])
        ).select_related('ID_Horario')

        for hm in disponibilidades:
            dia_nombre = nombres_dias[hm.Dia.weekday()]
            ini_str = hm.ID_Horario.Hora_Inicio.strftime('%H:%M')
            eventos_semana.append({
                'dia': dia_nombre,
                'inicio': ini_str,
                'tipo': hm.Estado.lower(),  # 'disponible' o 'bloqueado'
                'texto': hm.Estado
            })

        # Las atenciones agendadas sobrescriben visualmente la celda como ocupada
        atenciones = Atencion.objects.filter(
            ID_Medico=medico_actual,
            Dia__range=(dias_semana[0], dias_semana[6])
        ).exclude(Estado__iexact='Cancelada').select_related('ID_Horario', 'Paciente')

        for at in atenciones:
            dia_nombre = nombres_dias[at.Dia.weekday()]
            ini_str = at.ID_Horario.Hora_Inicio.strftime('%H:%M')
            nombre_pac = f"{at.Paciente.Nombre_Paciente} {at.Paciente.ApellidoP}"
            eventos_semana.append({
                'dia': dia_nombre,
                'inicio': ini_str,
                'tipo': 'ocupado',
                'texto': f"Atención: {nombre_pac}",
                'detalle': f"{nombre_pac} ({at.Estado}) - {at.Descripcion}"
            })

    medicos_lista = Medico.objects.filter(Estado='Activo').order_by('ApellidoP_Medico')

    return render(request, 'GestionDeDisponibilidad.html', {
        'medico': medico_actual,
        'medicos': medicos_lista,
        'rol_id': rol_id,
        'fecha_seleccionada': fecha_ref.isoformat(),
        'fechas_columnas': [
            {'nombre': nombres_dias[i], 'fecha': dias_semana[i].strftime('%d/%m')}
            for i in range(7)
        ],
        'eventos_json': json.dumps(eventos_semana),
    })

def gestionatencion(request):
    """Vista con privilegios administrativos para Recepcionistas (Rol 2) y Administradores (Rol 1)."""
    _asegurar_bloques_horario()

    rol_id = request.session.get('usuario_rol')
    usuario_id = request.session.get('usuario_id')

    # Control de privilegios: bloquear acceso si un Paciente (Rol 4) intenta entrar por URL
    if rol_id == 4:
        messages.error(request, 'Acceso denegado: No tienes privilegios para administrar atenciones.')
        return redirect('registrar_atencion')

    if request.method == 'POST':
        accion = request.POST.get('accion', 'guardar')
        atencion_id = request.POST.get('atencion_id', '').strip()

        # --- PRIVILEGIO EXCLUSIVO DE RECEPCIÓN/ADMIN: ELIMINAR ATENCIÓN ---
        if accion == 'eliminar' and atencion_id:
            if rol_id not in (1, 2):
                messages.error(request, 'Solo Recepción o Administración pueden eliminar atenciones.')
                return redirect('gestionar_atencion')

            try:
                with transaction.atomic():
                    atencion = Atencion.objects.get(ID_Atencion=atencion_id)
                    # Liberar el bloque en HorarioMedico si estaba marcado como Ocupado
                    HorarioMedico.objects.filter(
                        ID_Medico=atencion.ID_Medico,
                        Dia=atencion.Dia,
                        ID_Horario=atencion.ID_Horario,
                        Estado__iexact='Ocupado'
                    ).update(Estado='Disponible')
                    atencion.delete()
                messages.success(request, f'Atención #{atencion_id} eliminada y bloque horario liberado.')
            except Atencion.DoesNotExist:
                messages.error(request, 'La atención que intentas eliminar no existe.')
            except Exception as e:
                messages.error(request, f'No se pudo eliminar (puede tener cobros o recetas asociadas): {str(e)}')

            return redirect('gestionar_atencion')

        # --- CREAR O EDITAR / REAGENDAR ATENCIÓN ---
        paciente_id = request.POST.get('paciente')
        medico_id = request.POST.get('medico')
        dia_str = request.POST.get('dia')
        horario_id = request.POST.get('horario')
        descripcion = request.POST.get('descripcion', '').strip()
        estado = request.POST.get('estado', 'Programada')

        try:
            paciente_obj = Paciente.objects.get(ID=paciente_id)
            medico_obj = Medico.objects.get(MatriculaProfesional=medico_id)
            horario_obj = Horario.objects.get(ID_Horario=horario_id)
            fecha_obj = datetime.strptime(dia_str, '%Y-%m-%d').date()

            usuario_obj = Usuario.objects.filter(ID_Usuario=usuario_id).first() or Usuario.objects.first()

            # Validar que no exista doble reserva en el mismo bloque (salvo si la cita está Cancelada)
            if estado.lower() != 'cancelada':
                qs_choque = Atencion.objects.filter(
                    ID_Medico=medico_obj,
                    Dia=fecha_obj,
                    ID_Horario=horario_obj
                ).exclude(Estado__iexact='Cancelada')

                if atencion_id:
                    qs_choque = qs_choque.exclude(ID_Atencion=atencion_id)

                if qs_choque.exists():
                    messages.error(request, 'Conflicto de horario: Ese médico ya tiene otro paciente asignado en ese bloque.')
                    return redirect('gestionar_atencion')

                bloqueado = HorarioMedico.objects.filter(
                    ID_Medico=medico_obj,
                    Dia=fecha_obj,
                    ID_Horario=horario_obj,
                    Estado__iexact='Bloqueado'
                ).exists()

                if bloqueado:
                    messages.error(request, 'El médico tiene este bloque marcado como Bloqueado.')
                    return redirect('gestionar_atencion')

            with transaction.atomic():
                if atencion_id:
                    atencion = Atencion.objects.get(ID_Atencion=atencion_id)

                    # Si se reagendó (cambió médico, día o bloque) o se canceló, liberar el bloque anterior
                    cambio_bloque = (
                        atencion.ID_Medico_id != medico_obj.MatriculaProfesional or
                        atencion.Dia != fecha_obj or
                        atencion.ID_Horario_id != horario_obj.ID_Horario
                    )
                    if cambio_bloque or estado.lower() == 'cancelada':
                        HorarioMedico.objects.filter(
                            ID_Medico=atencion.ID_Medico,
                            Dia=atencion.Dia,
                            ID_Horario=atencion.ID_Horario,
                            Estado__iexact='Ocupado'
                        ).update(Estado='Disponible')

                    atencion.Paciente = paciente_obj
                    atencion.ID_Medico = medico_obj
                    atencion.Dia = fecha_obj
                    atencion.ID_Horario = horario_obj
                    atencion.Descripcion = descripcion[:500]
                    atencion.Estado = estado
                    atencion.save()

                    if estado.lower() != 'cancelada':
                        HorarioMedico.objects.update_or_create(
                            ID_Medico=medico_obj,
                            Dia=fecha_obj,
                            ID_Horario=horario_obj,
                            defaults={'Estado': 'Ocupado'}
                        )

                    messages.success(request, f'Atención #{atencion.ID_Atencion} actualizada y agenda sincronizada.')
                else:
                    # Recepción puede agendar directamente en cualquier bloque libre (sobrecupo / asignación directa)
                    nueva = Atencion.objects.create(
                        Paciente=paciente_obj,
                        Usuario=usuario_obj,
                        ID_Medico=medico_obj,
                        Descripcion=descripcion[:500],
                        ID_Horario=horario_obj,
                        Dia=fecha_obj,
                        Estado=estado
                    )

                    if estado.lower() != 'cancelada':
                        HorarioMedico.objects.update_or_create(
                            ID_Medico=medico_obj,
                            Dia=fecha_obj,
                            ID_Horario=horario_obj,
                            defaults={'Estado': 'Ocupado'}
                        )

                    messages.success(request, f'Atención #{nueva.ID_Atencion} creada por Recepción para {paciente_obj.Nombre_Paciente} {paciente_obj.ApellidoP}.')

        except (Paciente.DoesNotExist, Medico.DoesNotExist, Horario.DoesNotExist, Atencion.DoesNotExist):
            messages.error(request, 'Alguno de los registros seleccionados no es válido.')
        except ValueError:
            messages.error(request, 'La fecha ingresada no es válida.')
        except Exception as e:
            messages.error(request, f'Error al procesar la atención: {str(e)}')

        return redirect('gestionar_atencion')

    # --- PETICIÓN GET ---
    # Si es Médico (Rol 3), ve solo sus atenciones; si es Admin (1) o Recepcionista (2), ve TODAS
    if rol_id == 3 and usuario_id:
        atenciones = Atencion.objects.filter(ID_Medico__Usuario_id=usuario_id).select_related('Paciente', 'ID_Medico', 'ID_Horario').order_by('-Dia', 'ID_Horario__Hora_Inicio')
    else:
        atenciones = Atencion.objects.select_related('Paciente', 'ID_Medico', 'ID_Horario').all().order_by('-Dia', 'ID_Horario__Hora_Inicio')

    pacientes = Paciente.objects.filter(Estado='Activo').order_by('ApellidoP')
    medicos = Medico.objects.filter(Estado='Activo').order_by('ApellidoP_Medico')
    horarios = Horario.objects.all().order_by('Hora_Inicio')

    return render(request, 'GestionDeAtencion.html', {
        'atenciones': atenciones,
        'pacientes': pacientes,
        'medicos': medicos,
        'horarios': horarios,
        'rol_id': rol_id,
    })

def cobro(request):
  return render(request, "Cobro.html")

from django.contrib.auth.hashers import check_password

def iniciosesion(request):
    if request.method == 'POST':
        identificador = request.POST.get('run', '').strip()  # Puede ser RUN o Correo
        password = request.POST.get('password', '').strip()

        usuario = None

        # 1. Buscar si corresponde al RUN de un Paciente
        paciente = Paciente.objects.select_related('Usuario').filter(RUN_Paciente=identificador).first()
        if paciente and paciente.Usuario:
            usuario = paciente.Usuario

        # 2. Si no es Paciente, buscar si corresponde al RUN de un Médico
        if not usuario:
            medico = Medico.objects.select_related('Usuario').filter(RUN_Medico=identificador).first()
            if medico and medico.Usuario:
                usuario = medico.Usuario

        # 3. Si no tiene Paciente ni Médico asignado (Admin / Recepcionista), buscar por Correo
        if not usuario:
            usuario = Usuario.objects.filter(Correo_Usuario__iexact=identificador).first()

        # 4. Verificar existencia, estado Activo y contraseña encriptada
        if not usuario:
            messages.error(request, 'Usuario no encontrado. Verifica tu RUN o Correo.')
            return redirect('inicio_sesion')

        if usuario.Estado != 'Activo':
            messages.error(request, 'Esta cuenta se encuentra Inactiva. Contacta al administrador.')
            return redirect('inicio_sesion')

        if not check_password(password, usuario.Contraseña):
            messages.error(request, 'Contraseña incorrecta.')
            return redirect('inicio_sesion')

        # 5. Guardar datos en la sesión de Django
        request.session['usuario_id'] = usuario.ID_Usuario
        request.session['usuario_nombre'] = f"{usuario.Nombre} {usuario.Apellido}"
        request.session['usuario_rol'] = usuario.Rol.ID_Rol

        # 6. Redirigir según el Rol del usuario
        rol_id = usuario.Rol.ID_Rol
        if rol_id == 1:      # Administrador
            return redirect('gestionar_usuarios')
        elif rol_id == 2:    # Recepcionista
            return redirect('gestionar_atencion')
        elif rol_id == 3:    # Médico
            return redirect('gestionar_disponibilidad')
        else:                # Paciente (Rol 4)
            return redirect('registrar_atencion')

    return render(request, 'InicioSesion.html')  # <-- Verifica el nombre de tu template de login


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