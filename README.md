
# Configuración local

El proyecto requiere estas variables de entorno antes de iniciar Django:

- `DJANGO_SECRET_KEY`: clave privada de firma de Django.
- `EMAIL_HOST_USER`: cuenta SMTP que enviará los correos de activación y recuperación.
- `EMAIL_HOST_PASSWORD`: contraseña de aplicación SMTP de esa cuenta.
- `DEFAULT_FROM_EMAIL` (opcional): dirección mostrada como remitente; por defecto usa `EMAIL_HOST_USER`.

En Windows PowerShell, configura las variables en la terminal donde ejecutarás `manage.py`:

```powershell
$env:DJANGO_SECRET_KEY = "<clave aleatoria privada>"
$env:EMAIL_HOST_USER = "<cuenta SMTP>"
$env:EMAIL_HOST_PASSWORD = "<contraseña de aplicación SMTP>"
$env:DEFAULT_FROM_EMAIL = $env:EMAIL_HOST_USER
```

No guardes claves ni contraseñas en el repositorio. Usa secretos del entorno de despliegue en producción. Si el proyecto ya estuvo publicado con las credenciales anteriores, revoca y rota la contraseña SMTP expuesta y configura una nueva clave `DJANGO_SECRET_KEY`; los enlaces de activación emitidos con la clave anterior dejarán de ser válidos.
