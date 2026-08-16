"""Defaults y helpers de la landing comercial."""
import re

COMMERCIAL_DEFAULTS = {
    'commercial_landing_title': 'Únete a la comunidad',
    'commercial_landing_text': '''## Una oportunidad para ti

Déjanos tu nombre y email y te enviaremos el acceso al grupo.''',
    'commercial_landing_text_after': '''Estaremos encantadas de acompañarte.''',
    'commercial_reply_subject': 'Tu enlace al grupo — {{academy_name}}',
    'commercial_reply_body': '''<p>Hola <strong>{{nombre}}</strong>,</p>
<p>Gracias por tu interés. Aquí tienes el enlace al grupo de WhatsApp:</p>
<p><a href="{{whatsapp_url}}" style="display:inline-block;background:#25D366;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600" target="_blank" rel="noopener noreferrer">Entrar al grupo</a></p>
<p style="color:#71717a;font-size:12px">Si el botón no funciona, copia este enlace:<br>{{whatsapp_url}}</p>''',
}


def normalize_https_url(raw):
    """Asegura URL absoluta http(s) para enlaces de email/WhatsApp."""
    url = (raw or '').strip()
    if not url or url == '#':
        return ''
    # Quita {{ }} si alguien pegó la URL como «variable»
    if url.startswith('{{') and url.endswith('}}'):
        url = url[2:-2].strip()
    if not url or url == '#':
        return ''
    if url.startswith(('http://', 'https://')):
        return url
    if url.startswith('//'):
        return 'https:' + url
    return 'https://' + url


def unwrap_braced_urls(text):
    """Convierte {{https://...}} en https://... (error frecuente al editar plantillas)."""
    return re.sub(r'\{\{\s*(https?://[^}\s]+)\s*\}\}', r'\1', text or '')


def sanitize_commercial_reply_body(body, whatsapp_url=''):
    """Deja {{whatsapp_url}} donde haya una URL entre llaves; opcionalmente rellena whatsapp_url."""
    body = body or ''
    found = ''

    def _repl(m):
        nonlocal found
        url = normalize_https_url(m.group(1))
        if url and not found:
            found = url
        return '{{whatsapp_url}}'

    body = re.sub(r'\{\{\s*(https?://[^}]+)\s*\}\}', _repl, body)
    body = unwrap_braced_urls(body)
    wa = normalize_https_url(whatsapp_url) or found
    return body, wa


RESERVED_SLUGS = frozenset({
    'login', 'logout', 'registro', 'admin', 'comunidad', 'cursos', 'formaciones',
    'calendario', 'biblioteca', 'recursos', 'empieza', 'cuenta', 'miembros',
    'clasificacion', 'checkout', 'webhooks', 'static', 'avatar', 'legal',
    'recuperar-password', 'lp', 'api', 'notificaciones', 'landing-comercial',
})


def normalize_slug(raw, default='oferta'):
    slug = re.sub(r'[^a-z0-9\-]', '', (raw or default).strip().lower().replace(' ', '-'))
    slug = re.sub(r'-+', '-', slug).strip('-')
    if not slug or slug in RESERVED_SLUGS:
        return default
    return slug[:80]
