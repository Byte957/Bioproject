from django.shortcuts import render, redirect
from django.db import transaction, IntegrityError
from django.db.models import Count, F, Q, Sum, Value
from django.db.models.functions import Replace, Upper
from django.contrib.auth.hashers import make_password
from .models import Rol, Usuario, Paciente, ActivacionPendiente, Medico, Horario, HorarioMedico, Atencion, EstadoAtencion, Medicamento, Receta, MedicamentoReceta, Boleta, Auditoria
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib import messages
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from datetime import date, datetime, time, timedelta
import json
import hashlib
import hmac
import logging
import re
import smtplib
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.urls import reverse
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from django.views.decorators.cache import never_cache
from django.utils import timezone
import reportlab
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from functools import wraps


logger = logging.getLogger(__name__)


class ErrorEnvioActivacion(Exception):
    pass


def _token_activacion(usuario, paciente_id, vincular_paciente, activar_ficha):
    return signing.dumps(
        {
            'usuario_id': usuario.pk,
            'password_fingerprint': _huella_contrasena(usuario),
            'paciente_id': paciente_id,
            'vincular_paciente': vincular_paciente,
            'activar_ficha': activar_ficha,
        },
        salt='AppAdAtM.activacion_cuenta',
    )


def _enviar_correo_activacion(request, usuario, paciente_id, vincular_paciente, activar_ficha):
    token = _token_activacion(
        usuario,
        paciente_id,
        vincular_paciente,
        activar_ficha,
    )
    url_activacion = request.build_absolute_uri(
        reverse('activar_cuenta', args=[token])
    )
    enviados = send_mail(
        'Activa tu cuenta de Biotechnica',
        f'Hola {usuario.Nombre},\n\n'
        'Para activar tu cuenta, visita el siguiente enlace (válido por 24 horas):\n'
        f'{url_activacion}\n\n'
        'Si no solicitaste esta cuenta, puedes ignorar este correo.',
        settings.DEFAULT_FROM_EMAIL,
        [usuario.Correo_Usuario],
        fail_silently=False,
    )
    if enviados != 1:
        raise ErrorEnvioActivacion


def _validar_run(run):
    run = (run or '').strip().upper()
    if not re.fullmatch(r'[0-9.\-\s]*[0-9K]', run):
        raise ValidationError('Ingresa un RUN válido.')

    digitos = re.sub(r'[.\-\s]', '', run)
    cuerpo, digito_verificador = digitos[:-1], digitos[-1]
    if not 7 <= len(cuerpo) <= 8:
        raise ValidationError('Ingresa un RUN válido.')

    suma = 0
    multiplicador = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1

    resto = 11 - suma % 11
    esperado = '0' if resto == 11 else 'K' if resto == 10 else str(resto)
    if digito_verificador != esperado:
        raise ValidationError('El dígito verificador del RUN no es válido.')

    return f'{int(cuerpo):,}'.replace(',', '.') + f'-{digito_verificador}'


def _run_clave_expression(field_name):
    expression = Upper(F(field_name))
    for caracter in ('.', '-', ' '):
        expression = Replace(expression, Value(caracter), Value(''))
    return expression


def _pacientes_con_run(run):
    clave = re.sub(r'[.\-\s]', '', run).upper()
    return Paciente.objects.annotate(
        _run_clave=_run_clave_expression('RUN_Paciente'),
    ).filter(_run_clave=clave)


def _medicos_con_run(run):
    clave = re.sub(r'[.\-\s]', '', run).upper()
    return Medico.objects.annotate(
        _run_clave=_run_clave_expression('RUN_Medico'),
    ).filter(_run_clave=clave)


def _run_ya_registrado(run, *, excluir_paciente_id=None, excluir_matricula=None):
    pacientes = _pacientes_con_run(run)
    medicos = _medicos_con_run(run)
    if excluir_paciente_id is not None:
        pacientes = pacientes.exclude(pk=excluir_paciente_id)
    if excluir_matricula is not None:
        medicos = medicos.exclude(pk=excluir_matricula)
    return pacientes.exists() or medicos.exists()


def _validar_correo(correo):
    correo = (correo or '').strip().lower()
    if not correo or len(correo) > 50:
        raise ValidationError('Ingresa un correo válido de hasta 50 caracteres.')
    validate_email(correo)
    return correo


def _validar_nombre(valor, etiqueta, max_length=20, requerido=True):
    valor = (valor or '').strip()
    if requerido and not valor:
        raise ValidationError(f'{etiqueta} es obligatorio.')
    if len(valor) > max_length:
        raise ValidationError(f'{etiqueta} no puede superar los {max_length} caracteres.')
    if any(ord(caracter) < 32 for caracter in valor):
        raise ValidationError(f'{etiqueta} contiene caracteres no válidos.')
    return valor


def _validar_telefono_chileno(telefono):
    telefono = (telefono or '').strip()
    if not re.fullmatch(r'[\d\s()+.-]+', telefono):
        raise ValidationError('Ingresa un teléfono móvil chileno válido de 9 dígitos.')
    digitos = re.sub(r'\D', '', telefono or '')
    if digitos.startswith('56') and len(digitos) == 11:
        digitos = digitos[2:]
    if not re.fullmatch(r'9\d{8}', digitos):
        raise ValidationError('Ingresa un teléfono móvil chileno válido de 9 dígitos.')
    return f'+56{digitos}'


def _validar_password(password, usuario=None):
    try:
        validate_password(password, user=usuario)
    except ValidationError as error:
        raise ValidationError(error.messages) from error


def _usuario_tiene_perfil_activo(usuario):
    if usuario.Rol_id == 4:
        return Paciente.objects.filter(Usuario=usuario, Estado='Activo').exists()
    if usuario.Rol_id == 3:
        return Medico.objects.filter(Usuario=usuario, Estado='Activo').exists()
    return True


def _huella_contrasena(usuario):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        usuario.Contraseña.encode(),
        hashlib.sha256,
    ).hexdigest()


def sesion_requerida(view_func):
    @wraps(view_func)
    @never_cache
    def _wrapped_view(request, *args, **kwargs):
        usuario = Usuario.objects.select_related('Rol').filter(
            pk=request.session.get('usuario_id'),
        ).first()
        if (
            not usuario
            or usuario.Estado != 'Activo'
            or not _usuario_tiene_perfil_activo(usuario)
            or request.session.get('usuario_password_fingerprint') != _huella_contrasena(usuario)
        ):
            request.session.flush()
            return redirect('inicio_sesion')
        request.session['usuario_rol'] = usuario.Rol_id
        request.session['usuario_nombre'] = f'{usuario.Nombre} {usuario.Apellido}'
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def roles_requeridos(*roles_permitidos):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.session.get('usuario_rol') not in roles_permitidos:
                messages.error(request, 'No tienes permisos para acceder a esta sección.')
                return redirigir_segun_rol(request.session.get('usuario_rol'))
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


def _enviar_confirmacion_atencion(request, atencion):
    paciente = atencion.Paciente
    destinatario = paciente.CorreoContactoPaciente
    if not destinatario and paciente.Usuario_id:
        destinatario = paciente.Usuario.Correo_Usuario
    if not destinatario:
        messages.warning(request, 'La cita quedó reservada, pero el paciente no tiene un correo de contacto.')
        return

    try:
        enviado = send_mail(
            'Confirmación de cita médica',
            (
                f'Hola {paciente.Nombre_Paciente}, tu cita quedó reservada '
                f'para el {atencion.Dia.strftime("%d/%m/%Y")} a las '
                f'{atencion.ID_Horario.Hora_Inicio.strftime("%H:%M")} con '
                f'Dr(a). {atencion.ID_Medico.Nombre_Medico} '
                f'{atencion.ID_Medico.ApellidoP_Medico}.'
            ),
            settings.DEFAULT_FROM_EMAIL,
            [destinatario],
            fail_silently=False,
        )
        if not enviado:
            messages.warning(request, 'La cita quedó reservada, pero no se pudo enviar el correo de confirmación.')
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        messages.warning(request, f'La cita quedó reservada, pero falló el correo de confirmación: {exc}')


def _cancelar_atencion_y_liberar_bloque(atencion):
    with transaction.atomic():
        atencion.Estado = EstadoAtencion.CANCELADA
        atencion.save(update_fields=['Estado'])
        HorarioMedico.objects.filter(
            ID_Medico=atencion.ID_Medico,
            Dia=atencion.Dia,
            ID_Horario=atencion.ID_Horario,
            Estado__iexact='Ocupado',
        ).update(Estado='Disponible')


@never_cache
@sesion_requerida
@roles_requeridos(1)
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
@roles_requeridos(2, 4)
def registroatencion(request):
    # Vista restringida para agendar citas únicamente en horarios previamente habilitados como 'Disponible'.
    _asegurar_bloques_horario()

    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    # Si es Paciente (Rol 4), forzamos que solo pueda agendar para sí mismo
    paciente_sesion = None
    if rol_id == 4 and usuario_id:
        paciente_sesion = Paciente.objects.filter(Usuario_id=usuario_id).first()
        if not paciente_sesion:
            messages.error(request, 'No se encontró un perfil de paciente vinculado a tu cuenta.')
            return redirect('inicio_sesion')

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
            medico_obj = Medico.objects.get(MatriculaProfesional=medico_id, Estado='Activo')
            horario_obj = Horario.objects.get(ID_Horario=horario_id)
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            inicio_atencion = timezone.make_aware(datetime.combine(fecha_obj, horario_obj.Hora_Inicio))
            if inicio_atencion <= timezone.now():
                messages.error(request, 'No se pueden reservar horarios que ya comenzaron.')
                return redirect('registrar_atencion')

            paciente_obj = Paciente.objects.filter(
                RUN_Paciente=run_paciente,
                Estado='Activo',
            ).first()
            if not paciente_obj:
                messages.error(request, f'No existe un paciente registrado con el RUN {run_paciente}.')
                return redirect('registrar_atencion')

            usuario_obj = Usuario.objects.filter(ID_Usuario=usuario_id).first()
            if not usuario_obj:
                messages.error(request, 'La cuenta de usuario ya no está disponible.')
                return redirect('inicio_sesion')

            with transaction.atomic():
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
                ).exclude(Estado=EstadoAtencion.CANCELADA).exists()

                if not bloque_habilitado or ya_ocupado:
                    messages.error(request, 'Este horario ya no se encuentra disponible. Selecciona otro bloque.')
                    return redirect('registrar_atencion')

                nueva_atencion = Atencion.objects.create(
                    Paciente=paciente_obj,
                    Usuario=usuario_obj,
                    ID_Medico=medico_obj,
                    Descripcion=descripcion[:500],
                    ID_Horario=horario_obj,
                    Dia=fecha_obj,
                    Estado=EstadoAtencion.RESERVADA
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
            _enviar_confirmacion_atencion(request, nueva_atencion)

        except IntegrityError:
            messages.error(request, 'Este horario acaba de ser reservado. Selecciona otro bloque.')
        except (Medico.DoesNotExist, Horario.DoesNotExist):
            messages.error(request, 'El médico o bloque horario seleccionado no es válido.')
        except ValueError:
            messages.error(request, 'Fecha inválida.')
        except Exception as e:
            messages.error(request, f'Error al registrar la atención: {str(e)}')

        return redirect('registrar_atencion')

    # --- PETICIÓN GET ---
    ahora_local = timezone.localtime()
    hoy = ahora_local.date()
    disponibles_raw = HorarioMedico.objects.filter(
        Estado__iexact='Disponible',
        ID_Medico__Estado='Activo'
    ).filter(
        Q(Dia__gt=hoy)
        | Q(Dia=hoy, ID_Horario__Hora_Inicio__gt=ahora_local.time())
    ).select_related(
        'ID_Medico',
        'ID_Horario',
    ).order_by(
        'Dia',
        'ID_Horario__Hora_Inicio',
        'ID_Medico__ApellidoP_Medico',
        'ID_Medico__Nombre_Medico',
    )

    ocupados_set = set(
        Atencion.objects.filter(Dia__gte=hoy)
        .exclude(Estado=EstadoAtencion.CANCELADA)
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
@roles_requeridos(1, 2, 3, 4)
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
        password = request.POST.get('password', '')

        try:
            nombre = _validar_nombre(nombre, 'El nombre')
            apellido = _validar_nombre(apellido, 'El apellido')
            correo = _validar_correo(correo)
            if password:
                usuario_validar = Usuario(
                    Nombre=nombre,
                    Apellido=apellido,
                    Correo_Usuario=correo,
                )
                _validar_password(password, usuario_validar)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('gestionar_usuarios')

        # --- MODO ADMINISTRADOR (Puede crear y editar cualquier usuario) ---
        if es_admin:
            target_id = request.POST.get('usuario_id', '').strip()
            rol_nuevo_id = request.POST.get('rol', '').strip()
            estado_nuevo = request.POST.get('estado', 'Activo').strip()

            try:
                if rol_nuevo_id not in ('1', '2'):
                    raise ValidationError('Solo puedes asignar los roles Administrador o Recepcionista.')
                if estado_nuevo not in ('Activo', 'Inactivo'):
                    raise ValidationError('Selecciona un estado válido.')

                with transaction.atomic():
                    if target_id:
                        u_obj = Usuario.objects.get(
                            ID_Usuario=target_id,
                            Rol_id__in=(1, 2),
                        )
                        if Usuario.objects.filter(Correo_Usuario__iexact=correo).exclude(ID_Usuario=target_id).exists():
                            messages.error(request, f'El correo {correo} ya está en uso por otro usuario.')
                            return redirect('gestionar_usuarios')

                        u_obj.Nombre = nombre
                        u_obj.Apellido = apellido
                        u_obj.Correo_Usuario = correo
                        u_obj.Rol = Rol.objects.get(ID_Rol=rol_nuevo_id)
                        u_obj.Estado = estado_nuevo
                        if password:
                            u_obj.Contraseña = make_password(password)
                        u_obj.save()
                        if str(u_obj.ID_Usuario) == str(usuario_id):
                            request.session['usuario_password_fingerprint'] = _huella_contrasena(u_obj)

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

            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
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
                    request.session['usuario_password_fingerprint'] = _huella_contrasena(usuario_actual)

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
    usuarios = Usuario.objects.select_related('Rol').filter(
        Rol_id__in=(1, 2),
    ).order_by('ID_Usuario') if es_admin else []
    roles = Rol.objects.filter(
        ID_Rol__in=(1, 2),
    ).order_by('ID_Rol') if es_admin else []

    return render(request, 'GestionDeUsuarios.html', {  # <-- Verifica el nombre exacto de tu HTML
        'es_admin': es_admin,
        'usuario': usuario_actual,
        'usuarios': usuarios,
        'roles': roles,
    })

@never_cache
@sesion_requerida
@roles_requeridos(3)
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
                if atencion_obj.ID_Medico.Usuario_id != usuario_id:
                    messages.error(request, 'Solo el médico asignado puede emitir la receta.')
                    return redirect('gestionar_recetasatencion')
                if atencion_obj.Estado != EstadoAtencion.EN_ATENCION:
                    messages.error(request, 'Solo se puede finalizar una atención que esté en curso.')
                    return redirect('gestionar_recetasatencion')

                # 1. Actualizar diagnóstico/descripción y finalizar la atención
                if diagnostico:
                    atencion_obj.Descripcion = diagnostico[:500]
                atencion_obj.Estado = EstadoAtencion.FINALIZADA
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
    atenciones_qs = Atencion.objects.filter(ID_Medico__Usuario_id=usuario_id)

    atenciones = (
        atenciones_qs.exclude(Estado=EstadoAtencion.CANCELADA)
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


@never_cache
@sesion_requerida
@roles_requeridos(3, 4)
def historial_atenciones(request):
    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')

    atenciones = Atencion.objects.select_related(
        'Paciente',
        'ID_Medico',
        'ID_Horario',
    ).prefetch_related(
        'receta_set__medicamentoreceta_set__ID_Medicamento',
    )
    if rol_id == 3:
        atenciones = atenciones.filter(ID_Medico__Usuario_id=usuario_id)
    else:
        atenciones = atenciones.filter(Paciente__Usuario_id=usuario_id)

    return render(request, 'HistorialAtenciones.html', {
        'atenciones': atenciones.order_by('-Dia', '-ID_Horario__Hora_Inicio'),
        'rol_id': rol_id,
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
@roles_requeridos(1, 2)
def gestionpacientes(request):
    if request.method == 'POST':
        paciente_id = request.POST.get('paciente_id', '').strip()
        run = request.POST.get('run-paciente', '')
        nombre = request.POST.get('nombre-paciente', '').strip()
        seg_nombre = request.POST.get('segundo-nombre', '').strip()
        ter_nombre = request.POST.get('tercer-nombre', '').strip()
        apellido_p = request.POST.get('apellido-paterno', '').strip()
        apellido_m = request.POST.get('apellido-materno', '').strip()
        fecha_nac = request.POST.get('fecha-nacimiento')
        correo = request.POST.get('correo-contacto', '')
        telefono = request.POST.get('telefono-contacto', '').strip()
        estado = request.POST.get('estado', '').strip()

        try:
            run = _validar_run(run)
            correo = _validar_correo(correo)
            telefono_limpio = telefono.replace('+56', '').strip()
            telefono_completo = (
                _validar_telefono_chileno(telefono_limpio)
                if telefono_limpio else None
            )

            if paciente_id:
                # --- MODO EDITAR PACIENTE EXISTENTE ---
                paciente = Paciente.objects.get(ID=paciente_id)

                if estado not in ('Activo', 'Inactivo'):
                    raise ValidationError('Selecciona un estado válido para el paciente.')
                if _run_ya_registrado(run, excluir_paciente_id=paciente.ID):
                    raise ValidationError(f'El RUN {run} ya pertenece a otra persona.')

                paciente.RUN_Paciente = run
                paciente.Nombre_Paciente = nombre[:20]
                paciente.SegNombre_Paciente = seg_nombre[:20]
                paciente.TerNombre_Paciente = ter_nombre[:20]
                paciente.ApellidoP = apellido_p[:20]
                paciente.ApellidoM = apellido_m[:20]
                paciente.Fecha_Nacimiento = fecha_nac
                paciente.CorreoContactoPaciente = correo
                paciente.NumeroContactoPaciente = telefono_completo
                paciente.Estado = estado
                paciente.save()

                if paciente.Usuario:
                    paciente.Usuario.Nombre = nombre[:20]
                    paciente.Usuario.Apellido = apellido_p[:20]
                    paciente.Usuario.Correo_Usuario = correo
                    paciente.Usuario.Estado = estado
                    paciente.Usuario.save()

                messages.success(request, f'Paciente {paciente.Nombre_Paciente} {paciente.ApellidoP} actualizado correctamente.')

            else:
                # --- MODO CREAR NUEVO PACIENTE ---
                if _run_ya_registrado(run):
                    raise ValidationError(f'El RUN {run} ya está registrado.')

                nuevo_pac = Paciente.objects.create(
                    RUN_Paciente=run,
                    Nombre_Paciente=nombre[:20],
                    SegNombre_Paciente=seg_nombre[:20],
                    TerNombre_Paciente=ter_nombre[:20],
                    ApellidoP=apellido_p[:20],
                    ApellidoM=apellido_m[:20],
                    Fecha_Nacimiento=fecha_nac,
                    CorreoContactoPaciente=correo,
                    NumeroContactoPaciente=telefono_completo,
                    Estado='Inactivo',
                    Usuario=None,
                )

                messages.success(
                    request,
                    f'Paciente {nuevo_pac.Nombre_Paciente} {nuevo_pac.ApellidoP} '
                    'registrado como Inactivo y sin cuenta de acceso.',
                )

        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        except Paciente.DoesNotExist:
            messages.error(request, 'El paciente seleccionado no existe.')
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

    return render(request, 'GestiondePacientes.html', {
        'pacientes': pacientes
    })

@never_cache
@sesion_requerida
@roles_requeridos(1)
def gestionmedicos(request):
    if request.method == 'POST':
        medico_id = request.POST.get('medico_id', '').strip()
        matricula_str = request.POST.get('numero-matricula', '').strip()
        run_medico = request.POST.get('run-medico', '').strip()
        nombre = request.POST.get('nombre-medico', '').strip()
        seg_nombre = request.POST.get('segundo-nombre-medico', '').strip()
        ter_nombre = request.POST.get('tercer-nombre-medico', '').strip()
        apellido_p = request.POST.get('apellido-paterno', '').strip()
        apellido_m = request.POST.get('apellido-materno', '').strip()
        especializacion = request.POST.get('especializacion', '').strip()
        correo = request.POST.get('correo-contacto', '').strip()
        password = request.POST.get('password-medico', '')
        telefono_raw = request.POST.get('numero-contacto', '').strip()
        estado = request.POST.get('estado', 'Activo').strip()
        telefono = f"+56{telefono_raw.replace('+56', '')}"

        if not matricula_str or not run_medico or not nombre or not apellido_p or not especializacion or not correo or not telefono_raw:
            messages.error(request, 'Completa matrícula, RUN, nombre, apellido, especialidad, correo y teléfono.')
            return redirect('gestionar_medicos')

        try:
            matricula = int(matricula_str)
        except ValueError:
            messages.error(request, 'La matrícula profesional debe ser numérica.')
            return redirect('gestionar_medicos')
        if matricula <= 0 or estado not in ('Activo', 'Inactivo'):
            messages.error(request, 'La matrícula o el estado del médico no son válidos.')
            return redirect('gestionar_medicos')

        try:
            medico_actual = Medico.objects.select_related('Usuario').filter(
                MatriculaProfesional=medico_id,
            ).first() if medico_id else None
            if medico_id and not medico_actual:
                messages.error(request, 'El médico seleccionado ya no existe.')
                return redirect('gestionar_medicos')

            if medico_actual and medico_actual.MatriculaProfesional != matricula:
                messages.error(request, 'No se puede cambiar la matrícula de un médico existente.')
                return redirect(f"{reverse('gestionar_medicos')}?medico_id={medico_id}")
            elif not medico_actual and Medico.objects.filter(MatriculaProfesional=matricula).exists():
                messages.error(request, 'La matrícula ya está asociada a otro médico.')
                return redirect('gestionar_medicos')

            run_duplicado = Medico.objects.filter(RUN_Medico=run_medico)
            if medico_actual:
                run_duplicado = run_duplicado.exclude(MatriculaProfesional=medico_actual.MatriculaProfesional)
            if run_duplicado.exists() or Paciente.objects.filter(RUN_Paciente=run_medico).exists():
                messages.error(request, f'El RUN {run_medico} ya pertenece a otra persona.')
                return redirect(
                    f"{reverse('gestionar_medicos')}?medico_id={medico_id}"
                    if medico_id else 'gestionar_medicos'
                )

            usuario_actual = medico_actual.Usuario if medico_actual else None
            usuario_duplicado = Usuario.objects.filter(Correo_Usuario__iexact=correo)
            if usuario_actual:
                usuario_duplicado = usuario_duplicado.exclude(ID_Usuario=usuario_actual.ID_Usuario)
            if usuario_duplicado.exists():
                messages.error(request, f'El correo {correo} ya está registrado por otro usuario.')
                return redirect(
                    f"{reverse('gestionar_medicos')}?medico_id={medico_id}"
                    if medico_id else 'gestionar_medicos'
                )
            if not usuario_actual and not password:
                messages.error(request, 'Debes asignar una contraseña inicial al crear un médico.')
                return redirect('gestionar_medicos')

            if password:
                usuario_validar = Usuario(
                    Nombre=nombre[:20],
                    Apellido=apellido_p[:20],
                    Correo_Usuario=correo[:50],
                )
                _validar_password(password, usuario_validar)

            with transaction.atomic():
                rol_medico = Rol.objects.get(ID_Rol=3)
                if usuario_actual:
                    usuario_obj = usuario_actual
                else:
                    usuario_obj = Usuario.objects.create(
                        Nombre=nombre[:20],
                        Apellido=apellido_p[:20],
                        Correo_Usuario=correo[:50],
                        Contraseña=make_password(password),
                        Estado=estado,
                        Rol=rol_medico,
                    )

                if medico_actual:
                    medico_obj = medico_actual
                else:
                    medico_obj = Medico(MatriculaProfesional=matricula)
                medico_obj.Usuario = usuario_obj

                medico_obj.RUN_Medico = run_medico[:25]
                medico_obj.Nombre_Medico = nombre[:20]
                medico_obj.SegNombre_Medico = seg_nombre[:20]
                medico_obj.TerNombre_Medico = ter_nombre[:20]
                medico_obj.ApellidoP_Medico = apellido_p[:20]
                medico_obj.ApellidoM_Medico = apellido_m[:20]
                medico_obj.Especializacion = especializacion[:50]
                medico_obj.CorreoContactoMedico = correo[:50]
                medico_obj.NumeroContactoMedico = telefono[:15]
                medico_obj.Estado = estado
                medico_obj.save()

                usuario_obj.Nombre = nombre[:20]
                usuario_obj.Apellido = apellido_p[:20]
                usuario_obj.Correo_Usuario = correo[:50]
                usuario_obj.Estado = estado
                usuario_obj.Rol = rol_medico
                if password and usuario_actual:
                    usuario_obj.Contraseña = make_password(password)
                usuario_obj.save()

            messages.success(
                request,
                f'Médico {nombre} {apellido_p} '
                f'{"registrado" if not medico_id else "actualizado"} correctamente.',
            )
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        except Exception as e:
            messages.error(request, f'Error al guardar médico: {str(e)}')

        return redirect(
            f"{reverse('gestionar_medicos')}?medico_id={medico_obj.MatriculaProfesional}"
            if 'medico_obj' in locals() else 'gestionar_medicos'
        )

    medicos = Medico.objects.select_related('Usuario').all().order_by('ApellidoP_Medico', 'Nombre_Medico')
    if request.GET.get('nuevo') == '1':
        medico_actual = None
    elif request.GET.get('medico_id'):
        medico_actual = medicos.filter(
            MatriculaProfesional=request.GET.get('medico_id'),
        ).first()
        if not medico_actual:
            messages.error(request, 'El médico seleccionado no existe.')
    else:
        medico_actual = None
    return render(request, 'GestionDeMedicos.html', {
        'medico': medico_actual,
        'medicos': medicos,
        'es_admin': True,
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
@roles_requeridos(3)
def gestiondisponibilidad(request):
    _asegurar_bloques_horario()

    usuario_id = request.session.get('usuario_id')
    rol_id = request.session.get('usuario_rol')
    medico_actual = Medico.objects.filter(Usuario_id=usuario_id, Estado='Activo').first()
    if not medico_actual:
        messages.error(request, 'No se encontró un perfil médico activo vinculado a tu cuenta.')
        return redirect('gestionar_atencion')

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

    medicos_lista = [medico_actual]

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
@roles_requeridos(2, 3, 4)
def gestionatencion(request):
    """Recepción administra reservas; cada médico solo inicia sus propias atenciones."""
    _asegurar_bloques_horario()

    rol_id = request.session.get('usuario_rol')
    usuario_id = request.session.get('usuario_id')

    if request.method == 'GET' and request.GET.get('disponibilidad') == '1':
        if rol_id != 2:
            return JsonResponse({'error': 'No autorizado.'}, status=403)

        medico_id = request.GET.get('medico_id', '').strip()
        dia_str = request.GET.get('dia', '').strip()
        if not medico_id or not dia_str:
            return JsonResponse({'error': 'Indica médico y fecha.'}, status=400)

        try:
            fecha_obj = date.fromisoformat(dia_str)
            medico_obj = Medico.objects.get(
                MatriculaProfesional=medico_id,
                Estado='Activo',
            )
        except (ValueError, Medico.DoesNotExist):
            return JsonResponse({'error': 'Médico o fecha no válidos.'}, status=400)

        inicio_minimo = timezone.now()
        atencion_id_actual = request.GET.get('atencion_id', '').strip()
        atencion_actual = None
        if atencion_id_actual:
            atencion_actual = Atencion.objects.filter(
                ID_Atencion=atencion_id_actual,
                Estado=EstadoAtencion.RESERVADA,
            ).first()
            if not atencion_actual:
                return JsonResponse({'error': 'La atención que se está editando no es válida.'}, status=400)

        atenciones_activas = Atencion.objects.filter(
            ID_Medico=medico_obj,
            Dia=fecha_obj,
        ).exclude(Estado=EstadoAtencion.CANCELADA)
        if atencion_actual and (
            atencion_actual.ID_Medico_id == medico_obj.MatriculaProfesional
            and atencion_actual.Dia == fecha_obj
        ):
            atenciones_activas = atenciones_activas.exclude(
                ID_Atencion=atencion_actual.ID_Atencion,
            )
        horarios_ocupados = set(
            atenciones_activas.values_list('ID_Horario_id', flat=True)
        )

        bloques = []
        ids_bloques_disponibles = set()
        disponibilidades = HorarioMedico.objects.filter(
            ID_Medico=medico_obj,
            Dia=fecha_obj,
            Estado__iexact='Disponible',
        ).select_related('ID_Horario').order_by('ID_Horario__Hora_Inicio')
        for disponibilidad in disponibilidades:
            horario = disponibilidad.ID_Horario
            inicio_bloque = timezone.make_aware(
                datetime.combine(fecha_obj, horario.Hora_Inicio)
            )
            if (
                inicio_bloque > inicio_minimo
                and horario.ID_Horario not in horarios_ocupados
            ):
                bloques.append({
                    'id': horario.ID_Horario,
                    'texto': (
                        f'{horario.Hora_Inicio.strftime("%H:%M")} - '
                        f'{horario.Hora_Fin.strftime("%H:%M")}'
                    ),
                })
                ids_bloques_disponibles.add(horario.ID_Horario)

        if (
            atencion_actual
            and atencion_actual.ID_Medico_id == medico_obj.MatriculaProfesional
            and atencion_actual.Dia == fecha_obj
            and atencion_actual.ID_Horario_id not in ids_bloques_disponibles
        ):
            horario_actual = atencion_actual.ID_Horario
            inicio_actual = timezone.make_aware(
                datetime.combine(fecha_obj, horario_actual.Hora_Inicio)
            )
            if inicio_actual > inicio_minimo:
                bloques.append({
                    'id': horario_actual.ID_Horario,
                    'texto': (
                        f'{horario_actual.Hora_Inicio.strftime("%H:%M")} - '
                        f'{horario_actual.Hora_Fin.strftime("%H:%M")} (cita actual)'
                    ),
                })
                bloques.sort(key=lambda bloque: bloque['texto'])

        return JsonResponse({'bloques': bloques})

    if request.method == 'POST':
        accion = request.POST.get('accion', 'guardar')
        atencion_id = request.POST.get('atencion_id', '').strip()

        if rol_id == 4:
            paciente = Paciente.objects.filter(Usuario_id=usuario_id).first()
            if accion == 'abandonar' and atencion_id and paciente:
                try:
                    atencion = Atencion.objects.get(
                        ID_Atencion=atencion_id,
                        Paciente=paciente,
                        Estado=EstadoAtencion.EN_ESPERA,
                    )
                    _cancelar_atencion_y_liberar_bloque(atencion)
                    messages.success(request, f'Abandonaste la espera de la atención #{atencion_id}.')
                except Atencion.DoesNotExist:
                    messages.error(request, 'La atención no está en espera o no te pertenece.')
                return redirect('gestionar_atencion')

            if accion not in ('cancelar', 'eliminar') or not atencion_id or not paciente:
                messages.error(request, 'Solo puedes cancelar tus propias citas reservadas.')
                return redirect('gestionar_atencion')
            try:
                atencion = Atencion.objects.get(
                    ID_Atencion=atencion_id,
                    Paciente=paciente,
                    Estado=EstadoAtencion.RESERVADA,
                )
                inicio = timezone.make_aware(datetime.combine(atencion.Dia, atencion.ID_Horario.Hora_Inicio))
                if inicio - timezone.now() < timedelta(hours=24):
                    messages.error(request, 'Solo puedes cancelar una cita con al menos 24 horas de anticipación.')
                else:
                    _cancelar_atencion_y_liberar_bloque(atencion)
                    messages.success(request, f'La cita #{atencion_id} fue cancelada.')
            except Atencion.DoesNotExist:
                messages.error(request, 'La cita no existe, ya no está reservada o no te pertenece.')
            return redirect('gestionar_atencion')

        if accion == 'abandonar' and atencion_id:
            try:
                atencion = Atencion.objects.get(
                    ID_Atencion=atencion_id,
                    Estado=EstadoAtencion.EN_ESPERA,
                )
                _cancelar_atencion_y_liberar_bloque(atencion)
                messages.success(request, f'La espera de la atención #{atencion_id} fue abandonada.')
            except Atencion.DoesNotExist:
                messages.error(request, 'La atención no está en espera.')
            return redirect('gestionar_atencion')

        if rol_id == 3:
            if accion in ('cancelar', 'eliminar') and atencion_id:
                try:
                    atencion = Atencion.objects.get(
                        ID_Atencion=atencion_id,
                        ID_Medico__Usuario_id=usuario_id,
                        Estado=EstadoAtencion.RESERVADA,
                    )
                    inicio = timezone.make_aware(datetime.combine(atencion.Dia, atencion.ID_Horario.Hora_Inicio))
                    if inicio - timezone.now() < timedelta(hours=24):
                        messages.error(request, 'Solo puedes cancelar una cita con al menos 24 horas de anticipación.')
                    else:
                        _cancelar_atencion_y_liberar_bloque(atencion)
                        messages.success(request, f'La cita #{atencion_id} fue cancelada.')
                except Atencion.DoesNotExist:
                    messages.error(request, 'La cita no existe, ya no está reservada o no está asignada a tu agenda.')
                return redirect('gestionar_atencion')

            if accion != 'iniciar' or not atencion_id:
                messages.error(request, 'El médico solo puede iniciar una atención asignada.')
                return redirect('gestionar_atencion')

            try:
                atencion = Atencion.objects.get(
                    ID_Atencion=atencion_id,
                    ID_Medico__Usuario_id=usuario_id,
                    Estado=EstadoAtencion.EN_ESPERA,
                )
                atencion.Estado = EstadoAtencion.EN_ATENCION
                atencion.save(update_fields=['Estado'])
                messages.success(request, f'La atención #{atencion_id} comenzó.')
            except Atencion.DoesNotExist:
                messages.error(request, 'La atención no existe, no está en espera o no está asignada a tu agenda.')

            return redirect('gestionar_atencion')

        if accion in ('eliminar', 'cancelar') and atencion_id:
            try:
                atencion = Atencion.objects.get(ID_Atencion=atencion_id)
                inicio = timezone.make_aware(datetime.combine(atencion.Dia, atencion.ID_Horario.Hora_Inicio))
                if atencion.Estado != EstadoAtencion.RESERVADA or inicio - timezone.now() < timedelta(hours=24):
                    messages.error(request, 'Solo puedes cancelar una cita reservada con al menos 24 horas de anticipación.')
                else:
                    _cancelar_atencion_y_liberar_bloque(atencion)
                    messages.success(request, f'La atención #{atencion_id} fue cancelada.')
            except Atencion.DoesNotExist:
                messages.error(request, 'La atención que intentas cancelar no existe.')
            return redirect('gestionar_atencion')

        if accion == 'no_asistio' and atencion_id:
            try:
                atencion = Atencion.objects.get(ID_Atencion=atencion_id)
                inicio = timezone.make_aware(datetime.combine(atencion.Dia, atencion.ID_Horario.Hora_Inicio))
                if atencion.Estado != EstadoAtencion.RESERVADA or timezone.now() < inicio:
                    messages.error(request, 'Solo puedes registrar inasistencia cuando una cita reservada ya comenzó.')
                else:
                    atencion.Estado = EstadoAtencion.NO_ASISTIO
                    atencion.save(update_fields=['Estado'])
                    messages.success(request, f'La atención #{atencion_id} quedó como no asistida.')
            except Atencion.DoesNotExist:
                messages.error(request, 'La atención seleccionada no existe.')
            return redirect('gestionar_atencion')

        if rol_id != 2:
            messages.error(request, 'No tienes permisos para modificar la agenda.')
            return redirect('gestionar_atencion')

        # Recepción puede crear o reagendar una reserva; el estado no proviene del formulario.
        paciente_id = request.POST.get('paciente')
        medico_id = request.POST.get('medico')
        dia_str = request.POST.get('dia')
        horario_id = request.POST.get('horario')
        descripcion = request.POST.get('descripcion', '').strip()

        try:
            paciente_obj = Paciente.objects.get(ID=paciente_id, Estado='Activo')
            medico_obj = Medico.objects.get(MatriculaProfesional=medico_id, Estado='Activo')
            horario_obj = Horario.objects.get(ID_Horario=horario_id)
            fecha_obj = date.fromisoformat(dia_str)
            inicio_atencion = timezone.make_aware(datetime.combine(fecha_obj, horario_obj.Hora_Inicio))
            if inicio_atencion <= timezone.now():
                messages.error(request, 'No se pueden reservar horarios que ya comenzaron.')
                return redirect('gestionar_atencion')

            usuario_obj = Usuario.objects.filter(ID_Usuario=usuario_id).first()
            if not usuario_obj:
                messages.error(request, 'La cuenta de usuario ya no está disponible.')
                return redirect('inicio_sesion')

            with transaction.atomic():
                atencion = None
                if atencion_id:
                    atencion = Atencion.objects.get(ID_Atencion=atencion_id)
                    if atencion.Estado != EstadoAtencion.RESERVADA:
                        messages.error(request, 'Solo se pueden reagendar citas que aún estén reservadas.')
                        return redirect('gestionar_atencion')
                    inicio_actual = timezone.make_aware(datetime.combine(
                        atencion.Dia,
                        atencion.ID_Horario.Hora_Inicio,
                    ))
                    if inicio_actual - timezone.now() < timedelta(hours=24):
                        messages.error(request, 'Solo se pueden reagendar citas con al menos 24 horas de anticipación.')
                        return redirect('gestionar_atencion')
                    cambio_bloque = (
                        atencion.ID_Medico_id != medico_obj.MatriculaProfesional
                        or atencion.Dia != fecha_obj
                        or atencion.ID_Horario_id != horario_obj.ID_Horario
                    )
                else:
                    cambio_bloque = True

                if cambio_bloque:
                    disponible = HorarioMedico.objects.filter(
                        ID_Medico=medico_obj,
                        Dia=fecha_obj,
                        ID_Horario=horario_obj,
                        Estado__iexact='Disponible',
                    ).exists()
                    if not disponible:
                        messages.error(request, 'El bloque seleccionado no está disponible.')
                        return redirect('gestionar_atencion')

                    qs_choque = Atencion.objects.filter(
                        ID_Medico=medico_obj,
                        Dia=fecha_obj,
                        ID_Horario=horario_obj,
                    ).exclude(Estado=EstadoAtencion.CANCELADA)
                    if atencion:
                        qs_choque = qs_choque.exclude(ID_Atencion=atencion.ID_Atencion)
                    if qs_choque.exists():
                        messages.error(request, 'Conflicto de horario: ese médico ya tiene una atención reservada en ese bloque.')
                        return redirect('gestionar_atencion')

                if atencion_id:
                    medico_anterior = atencion.ID_Medico
                    dia_anterior = atencion.Dia
                    horario_anterior = atencion.ID_Horario
                    atencion.Paciente = paciente_obj
                    atencion.ID_Medico = medico_obj
                    atencion.Dia = fecha_obj
                    atencion.ID_Horario = horario_obj
                    atencion.Descripcion = descripcion[:500]
                    atencion.save()
                    if cambio_bloque:
                        HorarioMedico.objects.filter(
                            ID_Medico=medico_anterior,
                            Dia=dia_anterior,
                            ID_Horario=horario_anterior,
                            Estado__iexact='Ocupado',
                        ).update(Estado='Disponible')
                        HorarioMedico.objects.filter(
                            ID_Medico=medico_obj,
                            Dia=fecha_obj,
                            ID_Horario=horario_obj,
                        ).update(Estado='Ocupado')

                    mensaje_exito = f'Atención #{atencion.ID_Atencion} actualizada y agenda sincronizada.'
                    atencion_para_confirmar = atencion
                else:
                    nueva = Atencion.objects.create(
                        Paciente=paciente_obj,
                        Usuario=usuario_obj,
                        ID_Medico=medico_obj,
                        Descripcion=descripcion[:500],
                        ID_Horario=horario_obj,
                        Dia=fecha_obj,
                        Estado=EstadoAtencion.RESERVADA
                    )

                    HorarioMedico.objects.filter(
                        ID_Medico=medico_obj,
                        Dia=fecha_obj,
                        ID_Horario=horario_obj,
                    ).update(Estado='Ocupado')

                    mensaje_exito = f'Atención #{nueva.ID_Atencion} creada por Recepción para {paciente_obj.Nombre_Paciente} {paciente_obj.ApellidoP}.'
                    atencion_para_confirmar = nueva

            messages.success(request, mensaje_exito)
            _enviar_confirmacion_atencion(request, atencion_para_confirmar)
        except (Paciente.DoesNotExist, Medico.DoesNotExist, Horario.DoesNotExist, Atencion.DoesNotExist):
            messages.error(request, 'Alguno de los registros seleccionados no es válido.')
        except ValueError:
            messages.error(request, 'La fecha ingresada no es válida.')
        except IntegrityError:
            messages.error(request, 'Este bloque acaba de ser reservado. Selecciona otro horario.')
        except Exception as e:
            messages.error(request, f'Error al procesar la atención: {str(e)}')

        return redirect('gestionar_atencion')

    # --- PETICIÓN GET ---
    # Si es Médico (Rol 3), ve solo sus atenciones; si es Admin (1) o Recepcionista (2), ve TODAS
    if rol_id == 3 and usuario_id:
        atenciones = Atencion.objects.filter(ID_Medico__Usuario_id=usuario_id).select_related('Paciente', 'ID_Medico', 'ID_Horario').order_by('-Dia', 'ID_Horario__Hora_Inicio')
        pacientes = Paciente.objects.filter(
            atencion__in=atenciones,
        ).distinct().order_by('ApellidoP')
        medicos = Medico.objects.filter(Usuario_id=usuario_id)
    elif rol_id == 4 and usuario_id:
        atenciones = Atencion.objects.filter(Paciente__Usuario_id=usuario_id).select_related('Paciente', 'ID_Medico', 'ID_Horario').order_by('-Dia', 'ID_Horario__Hora_Inicio')
        pacientes = Paciente.objects.none()
        medicos = Medico.objects.none()
    else:
        atenciones = Atencion.objects.select_related('Paciente', 'ID_Medico', 'ID_Horario').all().order_by('-Dia', 'ID_Horario__Hora_Inicio')
        pacientes = Paciente.objects.filter(Estado='Activo').order_by('ApellidoP')
        medicos = Medico.objects.filter(Estado='Activo').order_by('ApellidoP_Medico')

    return render(request, 'GestionDeAtencion.html', {
        'atenciones': atenciones,
        'pacientes': pacientes,
        'medicos': medicos,
        'rol_id': rol_id,
        'fecha_minima': timezone.localdate().isoformat(),
        'atencion_estados': EstadoAtencion.choices,
    })

@never_cache
@sesion_requerida
@roles_requeridos(2)
def cobro(request):
    if request.method == 'POST':
        atencion_id = request.POST.get('atencion_id', '').strip()
        metodo_pago = request.POST.get('metodo-pago', '').strip()
        neto_str = request.POST.get('neto', '0').replace('$', '').replace('.', '').replace(',', '.').strip()
        iva_str = request.POST.get('iva', '0').replace('$', '').replace('.', '').replace(',', '.').strip()
        total_str = request.POST.get('total', '0').replace('$', '').replace('.', '').replace(',', '.').strip()

        if not atencion_id:
            messages.error(request, 'Debes seleccionar una atención médica para emitir el cobro.')
            return redirect('gestionar_cobros')

        try:
            atencion_obj = Atencion.objects.get(ID_Atencion=atencion_id)
            if atencion_obj.Estado not in (
                EstadoAtencion.RESERVADA,
                EstadoAtencion.EN_ESPERA,
            ):
                messages.error(request, 'Solo se pueden cobrar atenciones reservadas o en espera.')
                return redirect('gestionar_cobros')
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

                if atencion_obj.Estado == EstadoAtencion.RESERVADA:
                    atencion_obj.Estado = EstadoAtencion.EN_ESPERA
                    atencion_obj.save(update_fields=['Estado'])

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
        Atencion.objects.filter(
            Estado=EstadoAtencion.RESERVADA,
            Dia=timezone.localdate(),
        )
        .select_related('Paciente', 'ID_Medico', 'ID_Horario')
        .order_by('ID_Horario__Hora_Inicio')
    )

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
            'hora_agendada': a.ID_Horario.Hora_Inicio.strftime('%H:%M'),
            'motivo': a.Descripcion,
            'estado': a.Estado,
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
    rol_activo = request.session.get('usuario_rol')
    if rol_activo:
        usuario_activo = Usuario.objects.filter(
            pk=request.session.get('usuario_id'),
            Estado='Activo',
        ).first()
        if (
            usuario_activo
            and _usuario_tiene_perfil_activo(usuario_activo)
            and request.session.get('usuario_password_fingerprint') == _huella_contrasena(usuario_activo)
        ):
            return redirigir_segun_rol(usuario_activo.Rol_id)
        request.session.flush()

    if request.method == 'POST':
        modo = request.POST.get('modo', 'run')
        identificador = request.POST.get('identificador', request.POST.get('run', '')).strip()
        password = request.POST.get('password', '')

        usuario = None
        try:
            if modo == 'run':
                run = _validar_run(identificador)
                clave_run = re.sub(r'[.\-\s]', '', run)
                pacientes = list(
                    Paciente.objects.annotate(
                        _run_clave=_run_clave_expression('RUN_Paciente'),
                    ).filter(_run_clave=clave_run).select_related('Usuario')[:2]
                )
                medicos = list(
                    Medico.objects.annotate(
                        _run_clave=_run_clave_expression('RUN_Medico'),
                    ).filter(_run_clave=clave_run).select_related('Usuario')[:2]
                )
                perfiles = [perfil for perfil in pacientes + medicos if perfil.Usuario_id]
                usuarios = {perfil.Usuario_id: perfil.Usuario for perfil in perfiles}
                if len(usuarios) == 1 and len(perfiles) == 1:
                    usuario = next(iter(usuarios.values()))
            elif modo == 'correo':
                correo = _validar_correo(identificador)
                usuario = Usuario.objects.filter(Correo_Usuario__iexact=correo).first()
            else:
                raise ValidationError('Selecciona iniciar sesión con RUN o correo.')
        except ValidationError:
            usuario = None

        if not usuario or not check_password(password, usuario.Contraseña):
            messages.error(request, 'Credenciales de acceso incorrectas.')
            return redirect('inicio_sesion')

        if usuario.Estado != 'Activo' or not _usuario_tiene_perfil_activo(usuario):
            messages.error(request, 'Esta cuenta se encuentra inactiva. Si acabas de registrarte, revisa tu correo para activarla.')
            return redirect('inicio_sesion')

        request.session.cycle_key()
        request.session['usuario_id'] = usuario.ID_Usuario
        request.session['usuario_nombre'] = f'{usuario.Nombre} {usuario.Apellido}'
        request.session['usuario_rol'] = usuario.Rol_id
        request.session['usuario_password_fingerprint'] = _huella_contrasena(usuario)
        return redirigir_segun_rol(usuario.Rol_id)

    return render(request, 'InicioSesion.html')


def registrousuario(request):
    if request.method == 'POST':
        run = request.POST.get('run', '')
        nombre = request.POST.get('nombre', '')
        seg_nombre = request.POST.get('segundo-nombre', '')
        ter_nombre = request.POST.get('tercer-nombre', '')
        apellido_p = request.POST.get('apellido-paterno', '')
        apellido_m = request.POST.get('apellido-materno', '')
        fecha_nac = request.POST.get('fecha-nacimiento', '')
        telefono = request.POST.get('telefono', '')
        correo = request.POST.get('correo', '')
        password = request.POST.get('password', '')

        try:
            run = _validar_run(run)
            nombre = _validar_nombre(nombre, 'El nombre')
            seg_nombre = _validar_nombre(seg_nombre, 'El segundo nombre', requerido=False)
            ter_nombre = _validar_nombre(ter_nombre, 'El tercer nombre', requerido=False)
            apellido_p = _validar_nombre(apellido_p, 'El apellido paterno')
            apellido_m = _validar_nombre(apellido_m, 'El apellido materno', requerido=False)
            correo = _validar_correo(correo)
            telefono_limpio = telefono.strip()
            telefono_completo = (
                _validar_telefono_chileno(telefono_limpio)
                if telefono_limpio else None
            )
            try:
                fecha_nacimiento = date.fromisoformat(fecha_nac)
            except ValueError as error:
                raise ValidationError('Ingresa una fecha de nacimiento válida.') from error
            if fecha_nacimiento >= timezone.localdate():
                raise ValidationError('La fecha de nacimiento debe ser anterior a hoy.')

            with transaction.atomic():
                pacientes_coincidentes = list(
                    _pacientes_con_run(run).select_for_update()[:2]
                )
                if len(pacientes_coincidentes) > 1:
                    raise ValidationError(
                        'No se pudo identificar una ficha única para ese RUN. Contacta al personal.'
                    )
                if _medicos_con_run(run).exists():
                    raise ValidationError('El RUN ya está asociado a un perfil médico.')

                paciente_existente = (
                    pacientes_coincidentes[0] if pacientes_coincidentes else None
                )
                if paciente_existente:
                    correo_ficha = (
                        paciente_existente.CorreoContactoPaciente or ''
                    ).strip().lower()
                    if paciente_existente.Usuario_id:
                        raise ValidationError(
                            'La ficha ya está vinculada a una cuenta. Inicia sesión o recupera tu contraseña.'
                        )
                    if not correo_ficha:
                        raise ValidationError(
                            'La ficha no tiene un correo de contacto. Contacta al personal para actualizarla.'
                        )
                    if correo != correo_ficha:
                        raise ValidationError(
                            'El correo ingresado no coincide con el registrado en la ficha. Contacta al personal.'
                        )

                    nombre_usuario = paciente_existente.Nombre_Paciente
                    apellido_usuario = paciente_existente.ApellidoP
                    paciente_id_activacion = paciente_existente.ID
                    vincular_paciente = True
                    activar_ficha = False
                else:
                    nombre_usuario = nombre
                    apellido_usuario = apellido_p
                    paciente_id_activacion = None
                    vincular_paciente = False
                    activar_ficha = True

                if Usuario.objects.filter(Correo_Usuario__iexact=correo).exists():
                    raise ValidationError(
                        'El correo ya está asociado a una cuenta. Inicia sesión o recupera tu contraseña.'
                    )

                usuario_temporal = Usuario(
                    Nombre=nombre_usuario,
                    Apellido=apellido_usuario,
                    Correo_Usuario=correo,
                )
                _validar_password(password, usuario_temporal)
                rol_paciente = Rol.objects.get(ID_Rol=4)

                nuevo_usuario = Usuario.objects.create(
                    Nombre=nombre_usuario,
                    Apellido=apellido_usuario,
                    Correo_Usuario=correo,
                    Contraseña=make_password(password),
                    Estado='Inactivo',
                    Rol=rol_paciente
                )

                if not paciente_existente:
                    nuevo_paciente = Paciente.objects.create(
                        RUN_Paciente=run,
                        Nombre_Paciente=nombre,
                        SegNombre_Paciente=seg_nombre,
                        TerNombre_Paciente=ter_nombre,
                        ApellidoP=apellido_p,
                        ApellidoM=apellido_m,
                        Fecha_Nacimiento=fecha_nacimiento,
                        CorreoContactoPaciente=correo,
                        NumeroContactoPaciente=telefono_completo,
                        Estado='Inactivo',
                        Usuario=nuevo_usuario
                    )
                    paciente_id_activacion = nuevo_paciente.ID

                pendiente = ActivacionPendiente.objects.create(
                    Usuario=nuevo_usuario,
                    Paciente_id=paciente_id_activacion,
                    VincularPaciente=vincular_paciente,
                    ActivarFicha=activar_ficha,
                )
                try:
                    _enviar_correo_activacion(
                        request,
                        nuevo_usuario,
                        paciente_id_activacion,
                        vincular_paciente,
                        activar_ficha,
                    )
                except Exception as error:
                    raise ErrorEnvioActivacion from error
                pendiente.UltimoEnvio = timezone.now()
                pendiente.save(update_fields=['UltimoEnvio'])

            messages.success(
                request,
                'Registro exitoso. Revisa el correo registrado para activar tu cuenta y completar la vinculación.',
            )
            return redirect('inicio_sesion')

        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        except Rol.DoesNotExist:
            messages.error(request, 'Error del sistema: No existe el Rol de Paciente (ID 4) en la base de datos.')
        except ErrorEnvioActivacion:
            messages.error(request, 'No se pudo enviar el correo de activación. La cuenta no fue creada; inténtalo nuevamente más tarde.')
        except Exception:
            logger.exception('Error inesperado durante el registro de usuario.')
            messages.error(request, 'Ocurrió un error inesperado al guardar. Inténtalo nuevamente más tarde.')

    return render(request, 'Registrarse.html', {
        'fecha_maxima': timezone.localdate().isoformat(),
    })


def reenviar_activacion(request):
    mensaje_generico = (
        'Si existe una cuenta pendiente que coincide con esos datos, enviaremos '
        'un nuevo enlace al correo registrado. Revisa también la carpeta de spam.'
    )
    if request.method == 'POST':
        try:
            run = _validar_run(request.POST.get('run', ''))
            correo = _validar_correo(request.POST.get('correo', ''))
            clave_run = re.sub(r'[.\-\s]', '', run).upper()
            with transaction.atomic():
                usuario = Usuario.objects.select_for_update().filter(
                    Correo_Usuario__iexact=correo,
                    Estado='Inactivo',
                    Rol_id=4,
                ).first()
                if usuario:
                    pendiente = ActivacionPendiente.objects.select_for_update().filter(
                        Usuario=usuario,
                    ).select_related('Paciente').first()
                    if pendiente:
                        paciente = pendiente.Paciente
                        run_paciente = re.sub(
                            r'[.\-\s]',
                            '',
                            paciente.RUN_Paciente,
                        ).upper()
                        correo_ficha = (
                            (paciente.CorreoContactoPaciente or '').strip().lower()
                        )
                        vincular_paciente = pendiente.VincularPaciente
                        activar_ficha = pendiente.ActivarFicha
                        relacion_valida = (
                            run_paciente == clave_run
                            and correo_ficha == correo
                            and (
                                paciente.Usuario_id is None
                                if vincular_paciente
                                else paciente.Usuario_id == usuario.ID_Usuario
                            )
                        )
                    else:
                        pacientes = list(
                            _pacientes_con_run(run).select_for_update()[:2]
                        )
                        paciente = (
                            pacientes[0]
                            if len(pacientes) == 1
                            and (
                                (pacientes[0].CorreoContactoPaciente or '')
                                .strip()
                                .lower()
                                == correo
                            )
                            else None
                        )
                        relacion_valida = bool(
                            paciente
                            and paciente.Nombre_Paciente.casefold() == usuario.Nombre.casefold()
                            and paciente.ApellidoP.casefold() == usuario.Apellido.casefold()
                        )
                        vincular_paciente = False
                        activar_ficha = False
                        if relacion_valida and paciente.Usuario_id is None:
                            vincular_paciente = True
                        elif relacion_valida and paciente.Usuario_id == usuario.ID_Usuario:
                            activar_ficha = True
                        else:
                            relacion_valida = False
                        if relacion_valida and vincular_paciente:
                            fichas_ambiguas = Paciente.objects.filter(
                                Usuario__isnull=True,
                                CorreoContactoPaciente__iexact=correo,
                                Nombre_Paciente__iexact=usuario.Nombre,
                                ApellidoP__iexact=usuario.Apellido,
                            ).count()
                            relacion_valida = fichas_ambiguas == 1
                        if relacion_valida:
                            pendiente = ActivacionPendiente.objects.create(
                                Usuario=usuario,
                                Paciente=paciente,
                                VincularPaciente=vincular_paciente,
                                ActivarFicha=activar_ficha,
                            )

                    ultimo_envio = pendiente.UltimoEnvio if relacion_valida else None
                    puede_reenviar = (
                        ultimo_envio is None
                        or timezone.now() - ultimo_envio >= timedelta(minutes=1)
                    )
                    if relacion_valida and puede_reenviar:
                        try:
                            _enviar_correo_activacion(
                                request,
                                usuario,
                                pendiente.Paciente_id,
                                pendiente.VincularPaciente,
                                pendiente.ActivarFicha,
                            )
                        except Exception as error:
                            raise ErrorEnvioActivacion from error
                        pendiente.UltimoEnvio = timezone.now()
                        pendiente.save(update_fields=['UltimoEnvio'])
        except ValidationError:
            pass
        except ErrorEnvioActivacion:
            logger.exception('No se pudo reenviar el correo de activación.')
        except Exception:
            logger.exception('Error inesperado al reenviar el correo de activación.')

        messages.success(request, mensaje_generico)
    return render(request, 'ReenviarActivacion.html')


def activar_cuenta(request, token):
    try:
        datos = signing.loads(token, salt='AppAdAtM.activacion_cuenta', max_age=60 * 60 * 24)
    except signing.SignatureExpired:
        messages.error(request, 'El enlace de activación expiró. Solicita uno nuevo.')
        return redirect('inicio_sesion')
    except signing.BadSignature:
        messages.error(request, 'El enlace de activación no es válido.')
        return redirect('inicio_sesion')

    if isinstance(datos, int) and not isinstance(datos, bool):
        usuario_id = datos
        huella = None
        token_legacy = True
        paciente_id = None
        vincular_paciente = False
        activar_ficha = True
    elif isinstance(datos, dict):
        usuario_id = datos.get('usuario_id')
        huella = datos.get('password_fingerprint')
        token_legacy = False
        paciente_id = datos.get('paciente_id')
        vincular_paciente = datos.get('vincular_paciente', False)
        activar_ficha = datos.get('activar_ficha', True)
    else:
        messages.error(request, 'El enlace de activación no es válido.')
        return redirect('inicio_sesion')

    activacion_valida = True
    cuenta_ya_activa = False
    ficha_inactiva = False
    try:
        with transaction.atomic():
            usuario = Usuario.objects.select_for_update().filter(pk=usuario_id).first()
            if not usuario or (
                not token_legacy
                and (
                    not isinstance(huella, str)
                    or not hmac.compare_digest(huella, _huella_contrasena(usuario))
                )
            ):
                activacion_valida = False
            elif usuario.Estado != 'Inactivo':
                cuenta_ya_activa = True
                ActivacionPendiente.objects.filter(Usuario=usuario).delete()
            else:
                paciente = None
                if paciente_id is not None:
                    if not isinstance(paciente_id, int) or isinstance(paciente_id, bool):
                        activacion_valida = False
                    else:
                        paciente = Paciente.objects.select_for_update().filter(
                            pk=paciente_id,
                        ).first()
                        correo_ficha = (
                            (paciente.CorreoContactoPaciente or '').strip().lower()
                            if paciente else ''
                        )
                        if (
                            not paciente
                            or correo_ficha != usuario.Correo_Usuario.lower()
                            or (
                                vincular_paciente
                                and (usuario.Rol_id != 4 or paciente.Usuario_id is not None)
                            )
                            or (
                                not vincular_paciente
                                and paciente.Usuario_id != usuario.ID_Usuario
                            )
                        ):
                            activacion_valida = False
                        else:
                            if vincular_paciente:
                                paciente.Usuario = usuario
                            if activar_ficha:
                                paciente.Estado = 'Activo'
                            if vincular_paciente or activar_ficha:
                                campos_actualizados = []
                                if vincular_paciente:
                                    campos_actualizados.append('Usuario')
                                if activar_ficha:
                                    campos_actualizados.append('Estado')
                                paciente.save(update_fields=campos_actualizados)
                            ficha_inactiva = paciente.Estado != 'Activo'
                elif activar_ficha:
                    Paciente.objects.filter(Usuario=usuario).update(Estado='Activo')

                if activacion_valida:
                    usuario.Estado = 'Activo'
                    usuario.save(update_fields=['Estado'])
                    ActivacionPendiente.objects.filter(Usuario=usuario).delete()
    except (TypeError, ValueError):
        activacion_valida = False

    if not activacion_valida:
        messages.error(request, 'El enlace de activación no es válido.')
    elif cuenta_ya_activa:
        messages.info(request, 'Esta cuenta ya se encuentra activa.')
    elif ficha_inactiva:
        messages.success(
            request,
            'Tu cuenta fue activada y vinculada. La ficha sigue Inactiva; el personal debe activarla antes de que puedas iniciar sesión.',
        )
    else:
        messages.success(request, 'Tu cuenta ha sido activada. Ya puedes iniciar sesión.')

    return redirect('inicio_sesion')


def solicitar_restauracion_contrasena(request):
    if request.method == 'POST':
        try:
            correo = _validar_correo(request.POST.get('correo', ''))
            usuario = Usuario.objects.filter(
                Correo_Usuario__iexact=correo,
                Estado='Activo',
            ).first()
            if usuario:
                token = signing.dumps(
                    {
                        'usuario_id': usuario.pk,
                        'password_fingerprint': _huella_contrasena(usuario),
                    },
                    salt='AppAdAtM.restaurar_contrasena',
                )
                url = request.build_absolute_uri(
                    reverse('restablecer_contrasena', args=[token])
                )
                try:
                    enviados = send_mail(
                        'Recuperación de contraseña',
                        f'Hola {usuario.Nombre},\n\n'
                        'Para elegir una nueva contraseña, visita este enlace (válido por una hora):\n'
                        f'{url}\n\n'
                        'Si no solicitaste este cambio, puedes ignorar este correo.',
                        settings.DEFAULT_FROM_EMAIL,
                        [usuario.Correo_Usuario],
                        fail_silently=False,
                    )
                    if enviados != 1:
                        raise OSError('El backend de correo no confirmó el envío.')
                except Exception:
                    logger.exception('No se pudo enviar el correo de recuperación de contraseña.')
            messages.success(
                request,
                'Si existe una cuenta activa con ese correo, recibirás instrucciones para recuperar la contraseña.',
            )
            return redirect('solicitar_restauracion_contrasena')
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))

    return render(request, 'RecuperarContrasena.html')


def restablecer_contrasena(request, token):
    try:
        datos = signing.loads(
            token,
            salt='AppAdAtM.restaurar_contrasena',
            max_age=60 * 60,
        )
    except signing.SignatureExpired:
        messages.error(request, 'El enlace de recuperación expiró. Solicita uno nuevo.')
        return redirect('solicitar_restauracion_contrasena')
    except signing.BadSignature:
        messages.error(request, 'El enlace de recuperación no es válido.')
        return redirect('solicitar_restauracion_contrasena')

    if not isinstance(datos, dict):
        messages.error(request, 'El enlace de recuperación no es válido.')
        return redirect('solicitar_restauracion_contrasena')

    usuario = Usuario.objects.filter(
        pk=datos.get('usuario_id'),
        Estado='Activo',
    ).first()
    huella = datos.get('password_fingerprint')
    if (
        not usuario
        or not isinstance(huella, str)
        or not hmac.compare_digest(huella, _huella_contrasena(usuario))
    ):
        messages.error(request, 'El enlace ya no es válido. Solicita uno nuevo.')
        return redirect('solicitar_restauracion_contrasena')

    if request.method == 'POST':
        password = request.POST.get('password', '')
        confirmacion = request.POST.get('confirmacion', '')
        if password != confirmacion:
            messages.error(request, 'Las contraseñas no coinciden.')
        else:
            try:
                _validar_password(password, usuario)
                usuario.Contraseña = make_password(password)
                usuario.save(update_fields=['Contraseña'])
                messages.success(request, 'Tu contraseña fue actualizada. Ya puedes iniciar sesión.')
                return redirect('inicio_sesion')
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))

    return render(request, 'RestablecerContrasena.html')


@never_cache
@sesion_requerida
@roles_requeridos(1)
def verauditoria(request):
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
@roles_requeridos(1)
def reportes_clinica(request):
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1).strftime('%Y-%m-%d')
    fecha_inicio = request.GET.get('fecha_inicio', primer_dia_mes)
    fecha_fin = request.GET.get('fecha_fin', hoy.strftime('%Y-%m-%d'))

    datos = _obtener_datos_reportes(fecha_inicio, fecha_fin)
    datos['fecha_inicio'] = fecha_inicio
    datos['fecha_fin'] = fecha_fin

    return render(request, 'Reportes.html', datos)


@never_cache
@sesion_requerida
@roles_requeridos(1)
def exportar_reporte(request):
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