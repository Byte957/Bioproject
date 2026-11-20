from django.db import migrations


SQL_TRIGGERS = """
-- ============================================================================
-- 1. TRIGGERS PARA TABLA: Rol
-- ============================================================================
DROP TRIGGER IF EXISTS trg_rol_insert;
CREATE TRIGGER trg_rol_insert AFTER INSERT ON Rol
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Rol', 'INSERT', CAST(NEW.ID_Rol AS TEXT), 'Rol creado: ' || NEW.Nombre_Rol, datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_rol_update;
CREATE TRIGGER trg_rol_update AFTER UPDATE ON Rol
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Rol', 'UPDATE', CAST(NEW.ID_Rol AS TEXT), 'Rol modificado: ' || OLD.Nombre_Rol || ' -> ' || NEW.Nombre_Rol, datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_rol_delete;
CREATE TRIGGER trg_rol_delete AFTER DELETE ON Rol
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Rol', 'DELETE', CAST(OLD.ID_Rol AS TEXT), 'Rol eliminado: ' || OLD.Nombre_Rol, datetime('now', 'localtime'));
END;

-- ============================================================================
-- 2. TRIGGERS PARA TABLA: Usuario (Sin exponer contraseñas)
-- ============================================================================
DROP TRIGGER IF EXISTS trg_usuario_insert;
CREATE TRIGGER trg_usuario_insert AFTER INSERT ON Usuario
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Usuario', 'INSERT', CAST(NEW.ID_Usuario AS TEXT),
            'Usuario: ' || NEW.Nombre || ' ' || NEW.Apellido || ' | Correo: ' || NEW.Correo_Usuario || ' | Rol ID: ' || NEW.Rol || ' | Estado: ' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_usuario_update;
CREATE TRIGGER trg_usuario_update AFTER UPDATE ON Usuario
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Usuario', 'UPDATE', CAST(NEW.ID_Usuario AS TEXT),
            'Usuario: ' || NEW.Nombre || ' ' || NEW.Apellido || ' | Correo: ' || NEW.Correo_Usuario || ' | Rol: ' || OLD.Rol || '->' || NEW.Rol || ' | Estado: ' || OLD.Estado || '->' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_usuario_delete;
CREATE TRIGGER trg_usuario_delete AFTER DELETE ON Usuario
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Usuario', 'DELETE', CAST(OLD.ID_Usuario AS TEXT),
            'Usuario eliminado: ' || OLD.Nombre || ' ' || OLD.Apellido || ' (' || OLD.Correo_Usuario || ')',
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 3. TRIGGERS PARA TABLA: Paciente
-- ============================================================================
DROP TRIGGER IF EXISTS trg_paciente_insert;
CREATE TRIGGER trg_paciente_insert AFTER INSERT ON Paciente
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Paciente', 'INSERT', CAST(NEW.ID AS TEXT),
            'RUN: ' || NEW.RUN_Paciente || ' | Nombre: ' || NEW.Nombre_Paciente || ' ' || NEW.ApellidoP || ' | Estado: ' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_paciente_update;
CREATE TRIGGER trg_paciente_update AFTER UPDATE ON Paciente
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Paciente', 'UPDATE', CAST(NEW.ID AS TEXT),
            'RUN: ' || NEW.RUN_Paciente || ' | Nombre: ' || NEW.Nombre_Paciente || ' ' || NEW.ApellidoP || ' | Estado: ' || OLD.Estado || '->' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_paciente_delete;
CREATE TRIGGER trg_paciente_delete AFTER DELETE ON Paciente
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Paciente', 'DELETE', CAST(OLD.ID AS TEXT),
            'Paciente eliminado - RUN: ' || OLD.RUN_Paciente || ' (' || OLD.Nombre_Paciente || ' ' || OLD.ApellidoP || ')',
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 4. TRIGGERS PARA TABLA: Medico
-- ============================================================================
DROP TRIGGER IF EXISTS trg_medico_insert;
CREATE TRIGGER trg_medico_insert AFTER INSERT ON Medico
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Medico', 'INSERT', CAST(NEW.MatriculaProfesional AS TEXT),
            'Matrícula #' || NEW.MatriculaProfesional || ' | RUN: ' || NEW.RUN_Medico || ' | Dr(a). ' || NEW.Nombre_Medico || ' ' || NEW.ApellidoP_Medico || ' | Esp: ' || COALESCE(NEW.Especializacion, 'N/A'),
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_medico_update;
CREATE TRIGGER trg_medico_update AFTER UPDATE ON Medico
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Medico', 'UPDATE', CAST(NEW.MatriculaProfesional AS TEXT),
            'Matrícula #' || NEW.MatriculaProfesional || ' | Dr(a). ' || NEW.Nombre_Medico || ' ' || NEW.ApellidoP_Medico || ' | Esp: ' || COALESCE(NEW.Especializacion, 'N/A') || ' | Estado: ' || OLD.Estado || '->' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_medico_delete;
CREATE TRIGGER trg_medico_delete AFTER DELETE ON Medico
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Medico', 'DELETE', CAST(OLD.MatriculaProfesional AS TEXT),
            'Médico eliminado - Matrícula #' || OLD.MatriculaProfesional || ' (' || OLD.Nombre_Medico || ' ' || OLD.ApellidoP_Medico || ')',
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 5. TRIGGERS PARA TABLA: Horario
-- ============================================================================
DROP TRIGGER IF EXISTS trg_horario_insert;
CREATE TRIGGER trg_horario_insert AFTER INSERT ON Horario
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Horario', 'INSERT', CAST(NEW.ID_Horario AS TEXT),
            'Bloque horario: ' || NEW.Hora_Inicio || ' - ' || NEW.Hora_Fin,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_horario_update;
CREATE TRIGGER trg_horario_update AFTER UPDATE ON Horario
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Horario', 'UPDATE', CAST(NEW.ID_Horario AS TEXT),
            'Bloque actualizado: ' || OLD.Hora_Inicio || '-' || OLD.Hora_Fin || ' -> ' || NEW.Hora_Inicio || '-' || NEW.Hora_Fin,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_horario_delete;
CREATE TRIGGER trg_horario_delete AFTER DELETE ON Horario
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Horario', 'DELETE', CAST(OLD.ID_Horario AS TEXT),
            'Bloque eliminado: ' || OLD.Hora_Inicio || ' - ' || OLD.Hora_Fin,
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 6. TRIGGERS PARA TABLA: HorarioMedico
-- ============================================================================
DROP TRIGGER IF EXISTS trg_horariomedico_insert;
CREATE TRIGGER trg_horariomedico_insert AFTER INSERT ON HorarioMedico
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('HorarioMedico', 'INSERT', CAST(NEW.id AS TEXT),
            'Médico #' || NEW.ID_Medico || ' | Horario #' || NEW.ID_Horario || ' | Día: ' || NEW.Dia || ' | Estado: ' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_horariomedico_update;
CREATE TRIGGER trg_horariomedico_update AFTER UPDATE ON HorarioMedico
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('HorarioMedico', 'UPDATE', CAST(NEW.id AS TEXT),
            'Médico #' || NEW.ID_Medico || ' | Día: ' || NEW.Dia || ' | Horario #' || NEW.ID_Horario || ' | Estado: ' || OLD.Estado || '->' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_horariomedico_delete;
CREATE TRIGGER trg_horariomedico_delete AFTER DELETE ON HorarioMedico
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('HorarioMedico', 'DELETE', CAST(OLD.id AS TEXT),
            'Disponibilidad eliminada - Médico #' || OLD.ID_Medico || ' | Día: ' || OLD.Dia || ' | Horario #' || OLD.ID_Horario,
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 7. TRIGGERS PARA TABLA: Atencion
-- ============================================================================
DROP TRIGGER IF EXISTS trg_atencion_insert;
CREATE TRIGGER trg_atencion_insert AFTER INSERT ON Atencion
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Atencion', 'INSERT', CAST(NEW.ID_Atencion AS TEXT),
            'Paciente ID #' || NEW.Paciente || ' | Médico #' || NEW.ID_Medico || ' | Día: ' || NEW.Dia || ' | Horario #' || NEW.ID_Horario || ' | Estado: ' || NEW.Estado,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_atencion_update;
CREATE TRIGGER trg_atencion_update AFTER UPDATE ON Atencion
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Atencion', 'UPDATE', CAST(NEW.ID_Atencion AS TEXT),
            'Atención #' || NEW.ID_Atencion || ' | Día: ' || OLD.Dia || '->' || NEW.Dia || ' | Estado: ' || OLD.Estado || '->' || NEW.Estado || ' | Desc: ' || SUBSTR(NEW.Descripcion, 1, 100),
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_atencion_delete;
CREATE TRIGGER trg_atencion_delete AFTER DELETE ON Atencion
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Atencion', 'DELETE', CAST(OLD.ID_Atencion AS TEXT),
            'Atención eliminada - Paciente ID #' || OLD.Paciente || ' | Médico #' || OLD.ID_Medico || ' | Día: ' || OLD.Dia,
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 8. TRIGGERS PARA TABLA: Medicamento
-- ============================================================================
DROP TRIGGER IF EXISTS trg_medicamento_insert;
CREATE TRIGGER trg_medicamento_insert AFTER INSERT ON Medicamento
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Medicamento', 'INSERT', CAST(NEW.ID_Medicamento AS TEXT),
            'Medicamento creado: ' || NEW.Nombre_Medicamento || ' (' || NEW.Nombre_Comun || ')',
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_medicamento_update;
CREATE TRIGGER trg_medicamento_update AFTER UPDATE ON Medicamento
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Medicamento', 'UPDATE', CAST(NEW.ID_Medicamento AS TEXT),
            'Medicamento actualizado: ' || OLD.Nombre_Medicamento || ' -> ' || NEW.Nombre_Medicamento || ' (' || NEW.Nombre_Comun || ')',
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_medicamento_delete;
CREATE TRIGGER trg_medicamento_delete AFTER DELETE ON Medicamento
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Medicamento', 'DELETE', CAST(OLD.ID_Medicamento AS TEXT),
            'Medicamento eliminado: ' || OLD.Nombre_Medicamento || ' (' || OLD.Nombre_Comun || ')',
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 9. TRIGGERS PARA TABLA: Receta
-- ============================================================================
DROP TRIGGER IF EXISTS trg_receta_insert;
CREATE TRIGGER trg_receta_insert AFTER INSERT ON Receta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Receta', 'INSERT', CAST(NEW.ID_Receta AS TEXT),
            'Receta emitida para Atención #' || NEW.Atencion || ' | Fecha: ' || NEW.Fecha_Receta || ' | Institución: ' || NEW.Institucion,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_receta_update;
CREATE TRIGGER trg_receta_update AFTER UPDATE ON Receta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Receta', 'UPDATE', CAST(NEW.ID_Receta AS TEXT),
            'Receta #' || NEW.ID_Receta || ' actualizada (Atención #' || NEW.Atencion || ') | Institución: ' || NEW.Institucion,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_receta_delete;
CREATE TRIGGER trg_receta_delete AFTER DELETE ON Receta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Receta', 'DELETE', CAST(OLD.ID_Receta AS TEXT),
            'Receta #' || OLD.ID_Receta || ' eliminada (Atención #' || OLD.Atencion || ')',
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 10. TRIGGERS PARA TABLA: MedicamentoReceta
-- ============================================================================
DROP TRIGGER IF EXISTS trg_medicamentoreceta_insert;
CREATE TRIGGER trg_medicamentoreceta_insert AFTER INSERT ON MedicamentoReceta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('MedicamentoReceta', 'INSERT', 'Receta:' || NEW.ID_Receta || '-Med:' || NEW.ID_Medicamento,
            'Medicamento #' || NEW.ID_Medicamento || ' añadido a Receta #' || NEW.ID_Receta || ' | Ind: ' || NEW.Indicaciones,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_medicamentoreceta_update;
CREATE TRIGGER trg_medicamentoreceta_update AFTER UPDATE ON MedicamentoReceta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('MedicamentoReceta', 'UPDATE', 'Receta:' || NEW.ID_Receta || '-Med:' || NEW.ID_Medicamento,
            'Indicaciones actualizadas en Receta #' || NEW.ID_Receta || ' (Med #' || NEW.ID_Medicamento || '): ' || NEW.Indicaciones,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_medicamentoreceta_delete;
CREATE TRIGGER trg_medicamentoreceta_delete AFTER DELETE ON MedicamentoReceta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('MedicamentoReceta', 'DELETE', 'Receta:' || OLD.ID_Receta || '-Med:' || OLD.ID_Medicamento,
            'Medicamento #' || OLD.ID_Medicamento || ' quitado de Receta #' || OLD.ID_Receta,
            datetime('now', 'localtime'));
END;

-- ============================================================================
-- 11. TRIGGERS PARA TABLA: Boleta
-- ============================================================================
DROP TRIGGER IF EXISTS trg_boleta_insert;
CREATE TRIGGER trg_boleta_insert AFTER INSERT ON Boleta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Boleta', 'INSERT', CAST(NEW.ID_Boleta AS TEXT),
            'Boleta emitida - Atención #' || NEW.Atencion || ' | Método: ' || NEW.Metodo_Pago || ' | Neto: $' || NEW.Monto_Neto || ' | IVA: $' || NEW.IVA || ' | Total: $' || NEW.Total,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_boleta_update;
CREATE TRIGGER trg_boleta_update AFTER UPDATE ON Boleta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Boleta', 'UPDATE', CAST(NEW.ID_Boleta AS TEXT),
            'Boleta #' || NEW.ID_Boleta || ' actualizada - Método: ' || OLD.Metodo_Pago || '->' || NEW.Metodo_Pago || ' | Total: $' || OLD.Total || '->$' || NEW.Total,
            datetime('now', 'localtime'));
END;

DROP TRIGGER IF EXISTS trg_boleta_delete;
CREATE TRIGGER trg_boleta_delete AFTER DELETE ON Boleta
BEGIN
    INSERT INTO Auditoria (Tabla_Afectada, Accion, Registro_ID, Detalle, Fecha_Hora)
    VALUES ('Boleta', 'DELETE', CAST(OLD.ID_Boleta AS TEXT),
            'Boleta #' || OLD.ID_Boleta || ' anulada/eliminada - Atención #' || OLD.Atencion || ' | Total: $' || OLD.Total,
            datetime('now', 'localtime'));
END;
"""


SQL_REVERSE = """
DROP TRIGGER IF EXISTS trg_rol_insert;
DROP TRIGGER IF EXISTS trg_rol_update;
DROP TRIGGER IF EXISTS trg_rol_delete;
DROP TRIGGER IF EXISTS trg_usuario_insert;
DROP TRIGGER IF EXISTS trg_usuario_update;
DROP TRIGGER IF EXISTS trg_usuario_delete;
DROP TRIGGER IF EXISTS trg_paciente_insert;
DROP TRIGGER IF EXISTS trg_paciente_update;
DROP TRIGGER IF EXISTS trg_paciente_delete;
DROP TRIGGER IF EXISTS trg_medico_insert;
DROP TRIGGER IF EXISTS trg_medico_update;
DROP TRIGGER IF EXISTS trg_medico_delete;
DROP TRIGGER IF EXISTS trg_horario_insert;
DROP TRIGGER IF EXISTS trg_horario_update;
DROP TRIGGER IF EXISTS trg_horario_delete;
DROP TRIGGER IF EXISTS trg_horariomedico_insert;
DROP TRIGGER IF EXISTS trg_horariomedico_update;
DROP TRIGGER IF EXISTS trg_horariomedico_delete;
DROP TRIGGER IF EXISTS trg_atencion_insert;
DROP TRIGGER IF EXISTS trg_atencion_update;
DROP TRIGGER IF EXISTS trg_atencion_delete;
DROP TRIGGER IF EXISTS trg_medicamento_insert;
DROP TRIGGER IF EXISTS trg_medicamento_update;
DROP TRIGGER IF EXISTS trg_medicamento_delete;
DROP TRIGGER IF EXISTS trg_receta_insert;
DROP TRIGGER IF EXISTS trg_receta_update;
DROP TRIGGER IF EXISTS trg_receta_delete;
DROP TRIGGER IF EXISTS trg_medicamentoreceta_insert;
DROP TRIGGER IF EXISTS trg_medicamentoreceta_update;
DROP TRIGGER IF EXISTS trg_medicamentoreceta_delete;
DROP TRIGGER IF EXISTS trg_boleta_insert;
DROP TRIGGER IF EXISTS trg_boleta_update;
DROP TRIGGER IF EXISTS trg_boleta_delete;
"""


class Migration(migrations.Migration):

    dependencies = [
       ('AppAdAtM', '0002_auto_20261115_2000'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL_TRIGGERS, reverse_sql=SQL_REVERSE),
    ]