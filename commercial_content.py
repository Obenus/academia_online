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
    if url.startswith(('http://', 'https://')):
        return url
    if url.startswith('//'):
        return 'https:' + url
    return 'https://' + url

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
