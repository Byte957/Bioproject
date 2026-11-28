# Bioweb

Aplicación web de gestión clínica desarrollada con Django.

## Requisitos

- Python 3.12 o posterior, compatible con Django 6.
- PowerShell en Windows (los comandos siguientes se ejecutan desde la carpeta raíz del proyecto).

## Preparar y ejecutar el proyecto

1. Abra una terminal en la carpeta del proyecto, donde se encuentran `manage.py` y `requirements.txt`.
2. Cree y active un entorno virtual:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   Si PowerShell bloquea la activación, permita scripts solo para la sesión actual y vuelva a activarlo:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\.venv\Scripts\Activate.ps1
   ```

3. Instale las dependencias:

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. Compruebe que el archivo `.env` esté en la carpeta raíz del proyecto. El archivo incluido proporciona la configuración necesaria para esta revisión; contiene variables como `DJANGO_SECRET_KEY` y datos de correo. No publique ni reutilice sus valores fuera de este entorno académico. Para el envío de correos se configuran `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` y `DEFAULT_FROM_EMAIL`. (Nota adicional: La contraseña utilizada en `EMAIL_HOST_PASSWORD` será desactivada tras recibir la nota por la evaluación del proyecto).

5. Aplique las migraciones de la base de datos:

   ```powershell
   python manage.py migrate
   ```

6. Inicie el servidor de desarrollo:

   ```powershell
   python manage.py runserver
   ```

   Abra [http://127.0.0.1:8000/](http://127.0.0.1:8000/) en el navegador. Para detener el servidor, pulsa `Ctrl+C` en la terminal.

### Crear una cuenta administradora (opcional)

Si necesita acceder al panel de administración de Django, cree un usuario:

```powershell
python manage.py createsuperuser
```

El panel está disponible en [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/).

## Cuentas de prueba (No se incluye una cuenta de Paciente para evidenciar el funcionamiento del formulario de registro)

| Rol | Usuario o correo | Contraseña |
| --- | --- | --- |
| Administrador | admin.prueba@biotechnica.local | AdminDemo2026! |
| Recepcionista | recepcion.prueba@biotechnica.local | RecepcionDemo2026! |
| Médico | medico.prueba@biotechnica.local | MedicoDemo2026! |
