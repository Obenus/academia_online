import os

def _read_secret_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''

def _get_secret(name: str, default: str = '') -> str:
    file_var = os.environ.get(f'{name}_FILE')
    if file_var:
        value = _read_secret_file(file_var)
        if value:
            return value
    return os.environ.get(name, default)

def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')

SECRET_KEY = _get_secret('SECRET_KEY', 'cambiar-en-produccion-secret-key-aqui')

# Cookies de sesión (en producción con HTTPS: SESSION_COOKIE_SECURE=true)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = _as_bool(os.environ.get('SESSION_COOKIE_SECURE'), False)

# Usar PostgreSQL en Railway, SQLite en local
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///academy.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
SQLALCHEMY_DATABASE_URI = _db_url
SQLALCHEMY_TRACK_MODIFICATIONS = False

STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = _get_secret('STRIPE_SECRET_KEY', '')

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

ACADEMY_NAME = os.environ.get('ACADEMY_NAME', 'Marca Atractora')

# Email (configurar en Railway con variables de entorno)
MAIL_SERVER   = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT     = int(os.environ.get('MAIL_PORT', 587))
MAIL_USE_TLS  = _as_bool(os.environ.get('MAIL_USE_TLS'), True)
MAIL_USE_SSL  = _as_bool(os.environ.get('MAIL_USE_SSL'), False)
MAIL_USERNAME = _get_secret('MAIL_USERNAME', '')
MAIL_PASSWORD = _get_secret('MAIL_PASSWORD', '')
MAIL_DEFAULT_SENDER = MAIL_USERNAME
