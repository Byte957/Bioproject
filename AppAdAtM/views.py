from django.shortcuts import render, redirect
from django.db import transaction, IntegrityError
from django.contrib.auth.hashers import make_password
from .models import Rol, Usuario, Paciente, Medico, Horario, HorarioMedico, Atencion, Medicamento, Receta, MedicamentoReceta, Boleta, Auditoria
from django.contrib.auth.hashers import check_password
from django.contrib import messages
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from datetime import date, datetime, time, timedelta
import json
from django.http import HttpResponse
from django.template.loader import get_template
from django.db.models import Count, Sum
from django.urls import reverse
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from django.views.decorators.cache import never_cache
import reportlab
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from functools import wraps


class ErrorEnvioActivacion(Exception):
    pass


def sesion_requerida(view_func):
    @wraps(view_func)
    @never_cache
    def _wrapped_view(request, *args, **kwargs):
        if not request.session.get('usuario_id'):
            return redirect('inicio_sesion')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

@never_cache
@sesion_requerida
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

@never_cache
@sesion_requerida
def registroatencion(request):
    # Vista restringida para agendar citas únicamente en horarios previamente habilitados como 'Disponible'.
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

@never_cache
@sesion_requerida
def gestionusuarios(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    if not usuario_id:
        messages.error(request, 'Debes iniciar sesión para acceder.')
        return redirect('inicio_sesion')

    usuario_actual = Usuario.objects.select_related('Rol').filter(ID_Usuario=usuario_id).first()
    if not usuario_actual:
        request.session.flush()
        return redirect('inicio_sesion')

    es_admin = (rol_id == 1)

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        correo = request.POST.get('correo', '').strip()
        password = request.POST.get('password', '').strip()

        if not nombre or not apellido or not correo:
            messages.error(request, 'Nombre, apellido y correo son obligatorios.')
            return redirect('gestionar_usuarios')

        # --- MODO ADMINISTRADOR (Puede crear y editar cualquier usuario) ---
        if es_admin:
            target_id = request.POST.get('usuario_id', '').strip()
            rol_nuevo_id = request.POST.get('rol', '').strip()
            estado_nuevo = request.POST.get('estado', 'Activo').strip()

            try:
                with transaction.atomic():
                    if target_id:
                        u_obj = Usuario.objects.get(ID_Usuario=target_id)
                        if Usuario.objects.filter(Correo_Usuario__iexact=correo).exclude(ID_Usuario=target_id).exists():
                            messages.error(request, f'El correo {correo} ya está en uso por otro usuario.')
                            return redirect('gestionar_usuarios')

                        u_obj.Nombre = nombre
                        u_obj.Apellido = apellido
                        u_obj.Correo_Usuario = correo
                        if rol_nuevo_id:
                            u_obj.Rol = Rol.objects.get(ID_Rol=rol_nuevo_id)
                        u_obj.Estado = estado_nuevo
                        if password:
                            u_obj.Contraseña = make_password(password)
                        u_obj.save()

                        # Sincronizar si tiene ficha asociada
                        Paciente.objects.filter(Usuario=u_obj).update(
                            Nombre_Paciente=nombre, ApellidoP=apellido, CorreoContactoPaciente=correo, Estado=estado_nuevo
                        )
                        Medico.objects.filter(Usuario=u_obj).update(
                            Nombre_Medico=nombre, ApellidoP_Medico=apellido, CorreoContactoMedico=correo, Estado=estado_nuevo
                        )

                        if str(u_obj.ID_Usuario) == str(usuario_id):
                            request.session['usuario_nombre'] = f"{nombre} {apellido}"

                        messages.success(request, f'Usuario #{u_obj.ID_Usuario} ({nombre} {apellido}) actualizado correctamente.')
                    else:
                        if not password:
                            messages.error(request, 'Debes ingresar una contraseña para crear un usuario nuevo.')
                            return redirect('gestionar_usuarios')

                        if Usuario.objects.filter(Correo_Usuario__iexact=correo).exists():
                            messages.error(request, f'El correo {correo} ya se encuentra registrado.')
                            return redirect('gestionar_usuarios')

                        rol_obj = Rol.objects.get(ID_Rol=rol_nuevo_id)
                        nuevo_u = Usuario.objects.create(
                            Nombre=nombre,
                            Apellido=apellido,
                            Correo_Usuario=correo,
                            Contraseña=make_password(password),
                            Rol=rol_obj,
                            Estado=estado_nuevo
                        )
                        messages.success(request, f'Usuario "{nuevo_u.Nombre} {nuevo_u.Apellido}" creado correctamente.')

            except Exception as e:
                messages.error(request, f'Error al guardar usuario: {str(e)}')

        # --- MODO USUARIO ESTÁNDAR (Solo edita su propia cuenta) ---
        else:
            if Usuario.objects.filter(Correo_Usuario__iexact=correo).exclude(ID_Usuario=usuario_actual.ID_Usuario).exists():
                messages.error(request, f'El correo {correo} ya está registrado por otro usuario.')
                return redirect('gestionar_usuarios')

            try:
                with transaction.atomic():
                    usuario_actual.Nombre = nombre
                    usuario_actual.Apellido = apellido
                    usuario_actual.Correo_Usuario = correo
                    if password:
                        usuario_actual.Contraseña = make_password(password)
                    usuario_actual.save()

                    Paciente.objects.filter(Usuario=usuario_actual).update(
                        Nombre_Paciente=nombre, ApellidoP=apellido, CorreoContactoPaciente=correo
                    )
                    Medico.objects.filter(Usuario=usuario_actual).update(
                        Nombre_Medico=nombre, ApellidoP_Medico=apellido, CorreoContactoMedico=correo
                    )
                    request.session['usuario_nombre'] = f"{nombre} {apellido}"

                messages.success(request, 'Tus datos personales se actualizaron correctamente.')
            except Exception as e:
                messages.error(request, f'Error al actualizar tus datos: {str(e)}')

        return redirect('gestionar_usuarios')

    # --- PETICIÓN GET ---
    usuarios = Usuario.objects.select_related('Rol').all().order_by('ID_Usuario') if es_admin else []
    roles = Rol.objects.all().order_by('ID_Rol') if es_admin else []

    return render(request, 'GestionDeUsuarios.html', {  # <-- Verifica el nombre exacto de tu HTML
        'es_admin': es_admin,
        'usuario': usuario_actual,
        'usuarios': usuarios,
        'roles': roles,
    })

@never_cache
@sesion_requerida
def gestionrecetas(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    if request.method == 'POST':
        atencion_id = request.POST.get('atencion_id', '').strip()
        institucion = request.POST.get('institucion', '').strip() or 'Clínica Biotechnica'
        diagnostico = request.POST.get('diagnostico', '').strip()
        medicamentos_ids = request.POST.getlist('medicamento_id[]')
        indicaciones_list = request.POST.getlist('indicaciones[]')

        if not atencion_id:
            messages.error(request, 'Debes seleccionar una atención de la lista izquierda antes de emitir la receta.')
            return redirect('gestionar_recetasatencion')

        try:
            atencion_obj = Atencion.objects.get(ID_Atencion=atencion_id)

            # Validar medicamentos seleccionados (evitando duplicados por unique_together)
            items_receta = []
            vistos = set()
            for med_id, ind in zip(medicamentos_ids, indicaciones_list):
                med_id = med_id.strip()
                ind = ind.strip()
                if med_id and med_id not in vistos:
                    vistos.add(med_id)
                    items_receta.append((int(med_id), ind[:200] or 'Según indicación médica'))

            if not items_receta:
                messages.error(request, 'Debes seleccionar al menos un medicamento válido para emitir la receta.')
                return redirect('gestionar_recetasatencion')

            with transaction.atomic():
                # 1. Actualizar diagnóstico/descripción y marcar atención como Realizada
                if diagnostico:
                    atencion_obj.Descripcion = diagnostico[:500]
                atencion_obj.Estado = 'Realizada'
                atencion_obj.save()

                # 2. Crear o actualizar la Receta asociada a esta Atención
                receta_obj, _ = Receta.objects.update_or_create(
                    Atencion=atencion_obj,
                    defaults={
                        'Fecha_Receta': date.today(),
                        'Institucion': institucion[:50],
                    }
                )

                # 3. Reemplazar los detalles en MedicamentoReceta
                MedicamentoReceta.objects.filter(ID_Receta=receta_obj).delete()
                nuevos_detalles = []
                for med_id, ind in items_receta:
                    med_obj = Medicamento.objects.get(ID_Medicamento=med_id)
                    nuevos_detalles.append(
                        MedicamentoReceta(
                            ID_Medicamento=med_obj,
                            ID_Receta=receta_obj,
                            Indicaciones=ind
                        )
                    )
                MedicamentoReceta.objects.bulk_create(nuevos_detalles)

            messages.success(
                request,
                f'Receta #{receta_obj.ID_Receta} emitida correctamente para {atencion_obj.Paciente.Nombre_Paciente} {atencion_obj.Paciente.ApellidoP}.'
            )

        except Atencion.DoesNotExist:
            messages.error(request, 'La atención seleccionada no existe.')
        except Medicamento.DoesNotExist:
            messages.error(request, 'Uno de los medicamentos seleccionados no es válido.')
        except Exception as e:
            messages.error(request, f'Error al emitir receta: {str(e)}')

        return redirect('gestionar_recetasatencion')

    # --- PETICIÓN GET ---
    if rol_id == 3 and usuario_id:
        atenciones_qs = Atencion.objects.filter(ID_Medico__Usuario_id=usuario_id)
    else:
        atenciones_qs = Atencion.objects.all()

    atenciones = (
        atenciones_qs.exclude(Estado__iexact='Cancelada')
        .select_related('Paciente', 'ID_Medico', 'ID_Horario')
        .order_by('-Dia', 'ID_Horario__Hora_Inicio')
    )

    # Pre-cargar recetas existentes para mostrar sus medicamentos si el médico hace clic en una atención ya emitida
    recetas_map = {}
    for rec in Receta.objects.filter(Atencion__in=atenciones).prefetch_related('medicamentoreceta_set__ID_Medicamento'):
        recetas_map[str(rec.Atencion_id)] = {
            'id_receta': rec.ID_Receta,
            'institucion': rec.Institucion,
            'medicamentos': [
                {
                    'id': str(mr.ID_Medicamento_id),
                    'indicaciones': mr.Indicaciones
                }
                for mr in rec.medicamentoreceta_set.all()
            ]
        }

    medicamentos = Medicamento.objects.all().order_by('Nombre_Medicamento')
    pacientes = Paciente.objects.filter(Estado='Activo').order_by('ApellidoP')

    return render(request, 'GestionDeRecetasAtencion.html', {  # <-- VERIFICA EL NOMBRE DE TU ARCHIVO HTML
        'atenciones': atenciones,
        'medicamentos': medicamentos,
        'pacientes': pacientes,
        'recetas_json': json.dumps(recetas_map),
    })

def cerrarsesion(request):
    # 1. Eliminar explícitamente el rol y los datos de la sesión
    request.session.pop('usuario_rol', None)
    request.session.pop('usuario_id', None)
    request.session.pop('usuario_nombre', None)
    request.session.flush()

    messages.success(request, 'Sesión cerrada correctamente.')
    response = redirect('inicio_sesion')

    # 2. Borrar la cookie de sesión y limpiar la caché del navegador al instante
    response.delete_cookie('sessionid')
    response['Clear-Site-Data'] = '"cache", "storage"'
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'

    return response

@never_cache
@sesion_requerida
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

@never_cache
@sesion_requerida
def gestionmedicos(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    # 1. Prohibir el ingreso a cualquier rol distinto de Médico (Rol 3)
    if not usuario_id or rol_id != 3:
        messages.error(request, 'Acceso denegado: Esta sección es exclusiva para médicos.')
        if rol_id == 1:
            return redirect('gestionar_usuarios')
        elif rol_id == 2:
            return redirect('gestionar_atencion')
        elif rol_id == 4:
            return redirect('registrar_atencion')
        return redirect('inicio_sesion')

    # 2. Obtener únicamente el perfil del médico que inició sesión
    medico_actual = Medico.objects.select_related('Usuario').filter(Usuario_id=usuario_id).first()

    if not medico_actual:
        messages.error(request, 'No se encontró un perfil de médico vinculado a tu usuario.')
        return redirect('inicio_sesion')

    if request.method == 'POST':
        run_medico = request.POST.get('run-medico', '').strip()
        nombre = request.POST.get('nombre-medico', '').strip()
        seg_nombre = request.POST.get('segundo-nombre-medico', '').strip()
        ter_nombre = request.POST.get('tercer-nombre-medico', '').strip()
        apellido_p = request.POST.get('apellido-paterno', '').strip()
        apellido_m = request.POST.get('apellido-materno', '').strip()
        especializacion = request.POST.get('especializacion', '').strip()
        correo = request.POST.get('correo-contacto', '').strip()
        password = request.POST.get('password-medico', '').strip()
        telefono_raw = request.POST.get('numero-contacto', '').strip()
        estado = request.POST.get('estado', 'Activo').strip()

        telefono = f"+56{telefono_raw.replace('+56', '')}"

        # Validar que el RUN o correo no choque con otro médico distinto
        if Medico.objects.filter(RUN_Medico=run_medico).exclude(MatriculaProfesional=medico_actual.MatriculaProfesional).exists():
            messages.error(request, f'El RUN {run_medico} ya pertenece a otro médico.')
            return redirect('gestionar_medicos')

        if Usuario.objects.filter(Correo_Usuario__iexact=correo).exclude(ID_Usuario=usuario_id).exists():
            messages.error(request, f'El correo {correo} ya está registrado por otro usuario.')
            return redirect('gestionar_medicos')

        try:
            with transaction.atomic():
                # Actualizar datos en tabla Medico (manteniendo su MatriculaProfesional original)
                medico_actual.RUN_Medico = run_medico
                medico_actual.Nombre_Medico = nombre
                medico_actual.SegNombre_Medico = seg_nombre
                medico_actual.TerNombre_Medico = ter_nombre
                medico_actual.ApellidoP_Medico = apellido_p
                medico_actual.ApellidoM_Medico = apellido_m
                medico_actual.Especializacion = especializacion
                medico_actual.CorreoContactoMedico = correo
                medico_actual.NumeroContactoMedico = telefono
                medico_actual.Estado = estado
                medico_actual.save()

                # Sincronizar datos en su cuenta de Usuario asociada
                if medico_actual.Usuario:
                    usuario_obj = medico_actual.Usuario
                    usuario_obj.Nombre = nombre
                    usuario_obj.Apellido = apellido_p
                    usuario_obj.Correo_Usuario = correo
                    usuario_obj.Estado = estado
                    if password:
                        usuario_obj.Contraseña = make_password(password)
                    usuario_obj.save()

                    request.session['usuario_nombre'] = f"{nombre} {apellido_p}"

            messages.success(request, 'Tus datos profesionales se han actualizado correctamente.')
        except Exception as e:
            messages.error(request, f'Error al actualizar tus datos: {str(e)}')

        return redirect('gestionar_medicos')

    return render(request, 'GestionDeMedicos.html', {  # <-- Verifica que coincida con el nombre de tu archivo HTML
        'medico': medico_actual,
        'medicos': [medico_actual],
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

@never_cache
@sesion_requerida
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

@never_cache
@sesion_requerida
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

@never_cache
@sesion_requerida
def cobro(request):
    if request.method == 'POST':
        atencion_id = request.POST.get('atencion_id', '').strip()
        metodo_pago = request.POST.get('metodo-pago', '').strip()
        neto_str = request.POST.get('neto', '0').replace('$', '').replace('.', '').replace(',', '.').strip()
        iva_str = request.POST.get('iva', '0').replace('$', '').replace('.', '').replace(',', '.').strip()
        total_str = request.POST.get('total', '0').replace('$', '').replace('.', '').replace(',', '.').strip()
        estado_atencion = request.POST.get('estado-atencion', '').strip()

        if not atencion_id:
            messages.error(request, 'Debes seleccionar una atención médica para emitir el cobro.')
            return redirect('gestionar_cobros')

        try:
            atencion_obj = Atencion.objects.get(ID_Atencion=atencion_id)
            monto_neto = float(neto_str or 0)
            monto_iva = float(iva_str or round(monto_neto * 0.19, 2))
            monto_total = float(total_str or round(monto_neto + monto_iva, 2))

            if monto_neto <= 0 and monto_total <= 0:
                messages.error(request, 'El monto neto o total debe ser mayor a $0.')
                return redirect('gestionar_cobros')

            with transaction.atomic():
                boleta_obj, creada = Boleta.objects.update_or_create(
                    Atencion=atencion_obj,
                    defaults={
                        'Metodo_Pago': metodo_pago[:20],
                        'Monto_Neto': monto_neto,
                        'IVA': monto_iva,
                        'Total': monto_total,
                    }
                )

                if estado_atencion:
                    atencion_obj.Estado = estado_atencion[:20]
                    atencion_obj.save()

            accion_txt = 'emitida' if creada else 'actualizada'
            messages.success(
                request,
                f'Boleta #{boleta_obj.ID_Boleta} {accion_txt} correctamente por ${int(round(monto_total)):,} ({metodo_pago}).'
            )

        except Atencion.DoesNotExist:
            messages.error(request, 'La atención seleccionada no existe.')
        except ValueError:
            messages.error(request, 'Los valores numéricos de Neto, IVA o Total no son válidos.')
        except Exception as e:
            messages.error(request, f'Error al registrar el cobro: {str(e)}')

        return redirect('gestionar_cobros')

    # --- PETICIÓN GET ---
    atenciones = (
        Atencion.objects.exclude(Estado__iexact='Cancelada')
        .select_related('Paciente', 'ID_Medico', 'ID_Horario')
        .order_by('-Dia', 'ID_Horario__Hora_Inicio')
    )

    # Pre-cargar recetas/medicamentos asociados a cada atención
    medicamentos_por_atencion = {}
    for rec in Receta.objects.filter(Atencion__in=atenciones).prefetch_related('medicamentoreceta_set__ID_Medicamento'):
        lista_meds = [
            f"{mr.ID_Medicamento.Nombre_Medicamento} ({mr.ID_Medicamento.Nombre_Comun}) — {mr.Indicaciones}"
            for mr in rec.medicamentoreceta_set.all()
        ]
        medicamentos_por_atencion[str(rec.Atencion_id)] = lista_meds

    # Pre-cargar boletas ya emitidas por si se consulta una atención ya cobrada
    boletas_por_atencion = {}
    for bol in Boleta.objects.filter(Atencion__in=atenciones):
        boletas_por_atencion[str(bol.Atencion_id)] = {
            'id_boleta': bol.ID_Boleta,
            'metodo_pago': bol.Metodo_Pago,
            'neto': bol.Monto_Neto,
            'iva': bol.IVA,
            'total': bol.Total,
        }

    # Armar diccionario limpio para serializar con json_script sin errores de sintaxis en JS
    datos_atenciones = {}
    for a in atenciones:
        aid = str(a.ID_Atencion)
        datos_atenciones[aid] = {
            'id': a.ID_Atencion,
            'paciente_nombre': f"{a.Paciente.Nombre_Paciente} {a.Paciente.ApellidoP} {a.Paciente.ApellidoM}".strip(),
            'paciente_run': a.Paciente.RUN_Paciente,
            'medico_info': f"Dr(a). {a.ID_Medico.Nombre_Medico} {a.ID_Medico.ApellidoP_Medico} - {a.ID_Medico.Especializacion or 'Medicina General'} - RUN: {a.ID_Medico.RUN_Medico} / Mat. #{a.ID_Medico.MatriculaProfesional}",
            'paciente_info': f"{a.Paciente.Nombre_Paciente} {a.Paciente.ApellidoP} {a.Paciente.ApellidoM} - RUN: {a.Paciente.RUN_Paciente} - Tel: {a.Paciente.NumeroContactoPaciente}",
            'fecha_hora': f"{a.Dia.strftime('%d/%m/%Y')} {a.ID_Horario.Hora_Inicio.strftime('%H:%M')}",
            'descripcion': a.Descripcion,
            'estado': a.Estado,
            'medicamentos': medicamentos_por_atencion.get(aid, []),
            'boleta': boletas_por_atencion.get(aid, None),
        }

    return render(request, 'Cobro.html', {  # <-- VERIFICA EL NOMBRE EXACTO DE TU ARCHIVO HTML
        'atenciones': atenciones,
        'datos_atenciones': datos_atenciones,
    })

def redirigir_segun_rol(rol_id):
    if rol_id == 1:      # Administrador
        return redirect('gestionar_usuarios')
    elif rol_id == 2:    # Recepcionista
        return redirect('gestionar_atencion')
    elif rol_id == 3:    # Médico
        return redirect('gestionar_disponibilidad')
    else:                # Paciente (Rol 4)
        return redirect('registrar_atencion')


def iniciosesion(request):
    # Si ya tiene una sesión activa, impedir entrar al login y devolverlo a su panel
    rol_activo = request.session.get('usuario_rol')
    if rol_activo:
        return redirigir_segun_rol(rol_activo)

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
            messages.error(request, 'Credenciales de acceso incorrectas. Verifica tu RUN, correo o contraseña.')
            return redirect('inicio_sesion')

        if usuario.Estado != 'Activo':
            messages.error(request, 'Esta cuenta se encuentra Inactiva. Si acabas de registrarte, revisa tu correo para activarla.')
            return redirect('inicio_sesion')

        if not check_password(password, usuario.Contraseña):
            messages.error(request, 'Credenciales de acceso incorrectas. Verifica tu RUN, correo o contraseña')
            return redirect('inicio_sesion')

        # 5. Guardar datos en la sesión de Django
        request.session['usuario_id'] = usuario.ID_Usuario
        request.session['usuario_nombre'] = f"{usuario.Nombre} {usuario.Apellido}"
        request.session['usuario_rol'] = usuario.Rol.ID_Rol

        # 6. Redirigir según el Rol del usuario
        return redirigir_segun_rol(usuario.Rol.ID_Rol)

    return render(request, 'InicioSesion.html')


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
                    Estado='Inactivo',
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

                token = signing.dumps(
                    nuevo_usuario.pk,
                    salt='AppAdAtM.activacion_cuenta'
                )
                url_activacion = request.build_absolute_uri(
                    reverse('activar_cuenta', args=[token])
                )
                try:
                    enviados = send_mail(
                        'Activa tu cuenta de Biotechnica',
                        f'Hola {nuevo_usuario.Nombre},\n\n'
                        'Para activar tu cuenta, visita el siguiente enlace:\n'
                        f'{url_activacion}\n\n'
                        'Si no solicitaste esta cuenta, puedes ignorar este correo.',
                        settings.DEFAULT_FROM_EMAIL,
                        [nuevo_usuario.Correo_Usuario],
                        fail_silently=False,
                    )
                except Exception as error:
                    raise ErrorEnvioActivacion from error
                if enviados != 1:
                    raise ErrorEnvioActivacion

            messages.success(request, 'Registro exitoso. Revisa tu correo para activar tu cuenta antes de iniciar sesión.')
            return redirect('inicio_sesion')

        except Rol.DoesNotExist:
            messages.error(request, 'Error del sistema: No existe el Rol de Paciente (ID 4) en la base de datos.')
        except ErrorEnvioActivacion:
            messages.error(request, 'No se pudo enviar el correo de activación. La cuenta no fue creada; inténtalo nuevamente más tarde.')
        except Exception as e:
            messages.error(request, f'Ocurrió un error inesperado al guardar: {str(e)}')

    return render(request, 'Registrarse.html') # <-- PON AQUÍ EL NOMBRE REAL DE TU HTML DE REGISTRO


def activar_cuenta(request, token):
    try:
        usuario_id = signing.loads(token, salt='AppAdAtM.activacion_cuenta')
    except signing.BadSignature:
        messages.error(request, 'El enlace de activación no es válido.')
        return redirect('inicio_sesion')

    usuario = Usuario.objects.filter(pk=usuario_id).first()
    if not usuario:
        messages.error(request, 'El enlace de activación no es válido.')
    elif usuario.Estado == 'Inactivo':
        usuario.Estado = 'Activo'
        usuario.save(update_fields=['Estado'])
        messages.success(request, 'Tu cuenta ha sido activada. Ya puedes iniciar sesión.')
    else:
        messages.info(request, 'Esta cuenta ya se encuentra activa.')

    return redirect('inicio_sesion')


@never_cache
@sesion_requerida
def verauditoria(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    # Acceso exclusivo para Administrador (Rol 1)
    if not usuario_id or rol_id != 1:
        messages.error(request, 'Acceso denegado: Solo el Administrador puede consultar el registro de auditoría.')
        if rol_id == 2:
            return redirect('gestionar_atencion')
        elif rol_id == 3:
            return redirect('gestionar_disponibilidad')
        elif rol_id == 4:
            return redirect('registrar_atencion')
        return redirect('inicio_sesion')

    registros = Auditoria.objects.all().order_by('-ID_Auditoria')
    tablas = (
        Auditoria.objects.values_list('Tabla_Afectada', flat=True)
        .distinct()
        .order_by('Tabla_Afectada')
    )

    return render(request, 'Auditoria.html', {
        'registros': registros,
        'tablas': tablas,
    })


def _obtener_datos_reportes(fecha_inicio, fecha_fin):
    # --- 1. REPORTE DE ATENCIONES POR ESTADO ---
    qs_atenciones = Atencion.objects.select_related('Paciente', 'ID_Medico', 'ID_Horario').all()
    if fecha_inicio:
        qs_atenciones = qs_atenciones.filter(Dia__gte=fecha_inicio)
    if fecha_fin:
        qs_atenciones = qs_atenciones.filter(Dia__lte=fecha_fin)
    qs_atenciones = qs_atenciones.order_by('-Dia', 'ID_Horario__Hora_Inicio')

    resumen_estados = list(
        qs_atenciones.values('Estado')
        .annotate(cantidad=Count('ID_Atencion'))
        .order_by('Estado')
    )
    total_atenciones = qs_atenciones.count()

    # --- 2. REPORTE DE INGRESOS (BOLETAS) ---
    qs_boletas = Boleta.objects.select_related('Atencion', 'Atencion__Paciente', 'Atencion__ID_Medico').all()
    if fecha_inicio:
        qs_boletas = qs_boletas.filter(Atencion__Dia__gte=fecha_inicio)
    if fecha_fin:
        qs_boletas = qs_boletas.filter(Atencion__Dia__lte=fecha_fin)
    qs_boletas = qs_boletas.order_by('-Atencion__Dia', '-ID_Boleta')

    totales_ingresos = qs_boletas.aggregate(
        suma_neto=Sum('Monto_Neto'),
        suma_iva=Sum('IVA'),
        suma_total=Sum('Total')
    )
    resumen_metodos = list(
        qs_boletas.values('Metodo_Pago')
        .annotate(cantidad=Count('ID_Boleta'), total_metodo=Sum('Total'))
        .order_by('-total_metodo')
    )

    return {
        'atenciones': qs_atenciones,
        'resumen_estados': resumen_estados,
        'total_atenciones': total_atenciones,
        'boletas': qs_boletas,
        'suma_neto': totales_ingresos['suma_neto'] or 0,
        'suma_iva': totales_ingresos['suma_iva'] or 0,
        'suma_total': totales_ingresos['suma_total'] or 0,
        'resumen_metodos': resumen_metodos,
    }

@never_cache
@sesion_requerida
def reportes_clinica(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    # Solo Administrador (1) y Recepcionista (2)
    if not usuario_id or rol_id not in (1, 2):
        messages.error(request, 'Acceso denegado: No tienes permisos para ver reportes.')
        return redirect('inicio_sesion')

    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1).strftime('%Y-%m-%d')
    fecha_inicio = request.GET.get('fecha_inicio', primer_dia_mes)
    fecha_fin = request.GET.get('fecha_fin', hoy.strftime('%Y-%m-%d'))

    datos = _obtener_datos_reportes(fecha_inicio, fecha_fin)
    datos['fecha_inicio'] = fecha_inicio
    datos['fecha_fin'] = fecha_fin

    return render(request, 'Reportes.html', datos)


def exportar_reporte(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    if not usuario_id or rol_id not in (1, 2):
        return redirect('inicio_sesion')

    tipo = request.GET.get('tipo', 'atenciones')      # 'atenciones' o 'ingresos'
    formato = request.GET.get('formato', 'excel')     # 'excel' o 'pdf'
    fecha_inicio = request.GET.get('fecha_inicio', '')
    fecha_fin = request.GET.get('fecha_fin', '')

    datos = _obtener_datos_reportes(fecha_inicio, fecha_fin)
    rango_txt = f"{fecha_inicio or 'Inicio'}_a_{fecha_fin or 'Hoy'}"

    # =========================================================================
    # EXPORTACIÓN A EXCEL (.xlsx)
    # =========================================================================
    if formato == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active

        header_fill = PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)

        if tipo == 'atenciones':
            ws.title = "Reporte Atenciones"
            ws.append(["REPORTE DE HORAS Y ESTADOS DE ATENCIÓN - BIOTECHNICA"])
            ws.append([f"Rango: {fecha_inicio or 'Todos'} hasta {fecha_fin or 'Todos'}"])
            ws.append([])

            # Resumen por Estado
            ws.append(["RESUMEN POR ESTADO", "CANTIDAD"])
            for col in range(1, 3):
                ws.cell(row=4, column=col).fill = header_fill
                ws.cell(row=4, column=col).font = header_font

            for item in datos['resumen_estados']:
                ws.append([item['Estado'], item['cantidad']])
            ws.append(["TOTAL ATENCIONES", datos['total_atenciones']])
            ws.append([])

            # Detalle de Atenciones
            fila_cabecera = ws.max_row + 1
            cabeceras = ["ID", "Fecha", "Horario", "Paciente", "RUN Paciente", "Médico", "Estado", "Descripción"]
            ws.append(cabeceras)
            for col in range(1, len(cabeceras) + 1):
                ws.cell(row=fila_cabecera, column=col).fill = header_fill
                ws.cell(row=fila_cabecera, column=col).font = header_font

            for a in datos['atenciones']:
                ws.append([
                    a.ID_Atencion,
                    a.Dia.strftime('%d/%m/%Y'),
                    f"{a.ID_Horario.Hora_Inicio.strftime('%H:%M')} - {a.ID_Horario.Hora_Fin.strftime('%H:%M')}",
                    f"{a.Paciente.Nombre_Paciente} {a.Paciente.ApellidoP}",
                    a.Paciente.RUN_Paciente,
                    f"Dr(a). {a.ID_Medico.Nombre_Medico} {a.ID_Medico.ApellidoP_Medico}",
                    a.Estado,
                    a.Descripcion,
                ])

            nombre_archivo = f"Reporte_Atenciones_{rango_txt}.xlsx"

        else:
            ws.title = "Reporte Ingresos"
            ws.append(["REPORTE DE INGRESOS - BIOTECHNICA"])
            ws.append([f"Rango: {fecha_inicio or 'Todos'} hasta {fecha_fin or 'Todos'}"])
            ws.append([])

            # Resumen Financiero
            ws.append(["CONCEPTO", "MONTO ($)"])
            for col in range(1, 3):
                ws.cell(row=4, column=col).fill = header_fill
                ws.cell(row=4, column=col).font = header_font

            ws.append(["Total Monto Neto", round(datos['suma_neto'], 2)])
            ws.append(["Total IVA (19%)", round(datos['suma_iva'], 2)])
            ws.append(["Total Ingresos Brutos", round(datos['suma_total'], 2)])
            ws.append([])

            # Detalle de Boletas
            fila_cabecera = ws.max_row + 1
            cabeceras = ["N° Boleta", "Fecha Atención", "ID Atención", "Paciente", "Médico", "Método Pago", "Neto ($)", "IVA ($)", "Total ($)"]
            ws.append(cabeceras)
            for col in range(1, len(cabeceras) + 1):
                ws.cell(row=fila_cabecera, column=col).fill = header_fill
                ws.cell(row=fila_cabecera, column=col).font = header_font

            for b in datos['boletas']:
                ws.append([
                    b.ID_Boleta,
                    b.Atencion.Dia.strftime('%d/%m/%Y'),
                    b.Atencion.ID_Atencion,
                    f"{b.Atencion.Paciente.Nombre_Paciente} {b.Atencion.Paciente.ApellidoP}",
                    f"Dr(a). {b.Atencion.ID_Medico.Nombre_Medico} {b.Atencion.ID_Medico.ApellidoP_Medico}",
                    b.Metodo_Pago,
                    b.Monto_Neto,
                    b.IVA,
                    b.Total,
                ])

            nombre_archivo = f"Reporte_Ingresos_{rango_txt}.xlsx"

        # Ajustar ancho automático de columnas
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 45)

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
        wb.save(response)
        return response

    # =========================================================================
    # EXPORTACIÓN A PDF (.pdf) CON REPORTLAB
    # =========================================================================
    response = HttpResponse(content_type='application/pdf')
    nombre_pdf = f"Reporte_{tipo.capitalize()}_{rango_txt}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{nombre_pdf}"'

    doc = SimpleDocTemplate(response, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elementos = []

    titulo_texto = "Biotechnica Clínica - Reporte de Atenciones" if tipo == 'atenciones' else "Biotechnica Clínica - Reporte de Ingresos"
    elementos.append(Paragraph(f"<b>{titulo_texto}</b>", styles['Title']))
    elementos.append(Paragraph(f"Período: {fecha_inicio or 'Inicio'} al {fecha_fin or 'Hoy'} | Emitido: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
    elementos.append(Spacer(1, 14))

    estilo_tabla = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2e7d32')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f1f8e9')]),
    ])

    if tipo == 'atenciones':
        resumen_data = [["Estado", "Cantidad"]] + [[i['Estado'], str(i['cantidad'])] for i in datos['resumen_estados']] + [["TOTAL", str(datos['total_atenciones'])]]
        t_resumen = Table(resumen_data, colWidths=[180, 100])
        t_resumen.setStyle(estilo_tabla)
        elementos.append(t_resumen)
        elementos.append(Spacer(1, 14))

        filas = [["ID", "Fecha", "Horario", "Paciente", "RUN", "Médico", "Estado"]]
        for a in datos['atenciones']:
            filas.append([
                f"#{a.ID_Atencion}",
                a.Dia.strftime('%d/%m/%Y'),
                f"{a.ID_Horario.Hora_Inicio.strftime('%H:%M')}-{a.ID_Horario.Hora_Fin.strftime('%H:%M')}",
                f"{a.Paciente.Nombre_Paciente} {a.Paciente.ApellidoP}",
                a.Paciente.RUN_Paciente,
                f"Dr(a). {a.ID_Medico.Nombre_Medico} {a.ID_Medico.ApellidoP_Medico}",
                a.Estado,
            ])
        t_detalle = Table(filas, repeatRows=1)
        t_detalle.setStyle(estilo_tabla)
        elementos.append(t_detalle)
    else:
        resumen_data = [
            ["Concepto", "Monto"],
            ["Boletas Emitidas", str(datos['boletas'].count())],
            ["Total Neto", f"${int(round(datos['suma_neto'])):,}"],
            ["Total IVA (19%)", f"${int(round(datos['suma_iva'])):,}"],
            ["TOTAL INGRESOS", f"${int(round(datos['suma_total'])):,}"],
        ]
        t_resumen = Table(resumen_data, colWidths=[180, 130])
        t_resumen.setStyle(estilo_tabla)
        elementos.append(t_resumen)
        elementos.append(Spacer(1, 14))

        filas = [["Boleta", "Fecha", "Paciente", "Médico", "Método Pago", "Neto", "IVA", "Total"]]
        for b in datos['boletas']:
            filas.append([
                f"#{b.ID_Boleta}",
                b.Atencion.Dia.strftime('%d/%m/%Y'),
                f"{b.Atencion.Paciente.Nombre_Paciente} {b.Atencion.Paciente.ApellidoP}",
                f"Dr(a). {b.Atencion.ID_Medico.Nombre_Medico} {b.Atencion.ID_Medico.ApellidoP_Medico}",
                b.Metodo_Pago,
                f"${int(round(b.Monto_Neto)):,}",
                f"${int(round(b.IVA)):,}",
                f"${int(round(b.Total)):,}",
            ])
        t_detalle = Table(filas, repeatRows=1)
        t_detalle.setStyle(estilo_tabla)
        elementos.append(t_detalle)

    doc.build(elementos)
    return response