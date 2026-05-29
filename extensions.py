"""Extensiones Flask: CSRF, rate limiting, cabeceras de seguridad."""
import os

from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])


def init_security(app):
    app.config.setdefault('WTF_CSRF_TIME_LIMIT', None)
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
    secure = _session_secure(app)
    app.config['SESSION_COOKIE_SECURE'] = secure

    csrf.init_app(app)
    limiter.init_app(app)
    _init_talisman(app, force_https=secure)


def _session_secure(app):
    if app.config.get('SESSION_COOKIE_SECURE') is not None:
        return bool(app.config['SESSION_COOKIE_SECURE'])
    return _as_bool(os.environ.get('SESSION_COOKIE_SECURE')) or _as_bool(
        os.environ.get('FORCE_HTTPS')
    )


def _as_bool(val):
    if val is None:
        return False
    return str(val).strip().lower() in ('1', 'true', 'yes', 'on')


def _init_talisman(app, force_https=False):
    try:
        from flask_talisman import Talisman
    except ImportError:
        return
    Talisman(
        app,
        force_https=force_https,
        session_cookie_secure=force_https,
        content_security_policy={
            'default-src': "'self'",
            'script-src': ["'self'", "'unsafe-inline'", 'https://js.stripe.com'],
            'style-src': ["'self'", "'unsafe-inline'"],
            'img-src': ["'self'", 'data:', 'https:', 'blob:'],
            'frame-src': ["'self'", 'https://www.youtube.com', 'https://player.vimeo.com', 'https://js.stripe.com'],
            'connect-src': ["'self'", 'https://api.stripe.com'],
            'font-src': ["'self'", 'data:'],
        },
        content_security_policy_nonce_in=[],
    )
