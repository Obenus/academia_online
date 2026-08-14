"""Facturación: Stripe, emails y estado de suscripción."""
from datetime import datetime, timezone
from html import escape

from backup_manager import decrypt_value, encrypt_value

# Estados Stripe que permiten usar la plataforma
SUBSCRIPTION_ACTIVE_STATUSES = frozenset({'active', 'trialing'})

# Estados que bloquean el acceso (impago, cancelación, etc.)
SUBSCRIPTION_BLOCK_STATUSES = frozenset({
    'past_due', 'unpaid', 'canceled', 'incomplete', 'incomplete_expired', 'paused',
})


def get_stripe_secret(app):
    s = _payment_settings(app)
    if s and s.stripe_secret_key_enc:
        return decrypt_value(s.stripe_secret_key_enc, app.config.get('SECRET_KEY', ''))
    return app.config.get('STRIPE_SECRET_KEY', '')


def get_stripe_public(app):
    s = _payment_settings(app)
    if s and s.stripe_public_key:
        return s.stripe_public_key
    return app.config.get('STRIPE_PUBLIC_KEY', '')


def get_stripe_webhook_secret(app):
    s = _payment_settings(app)
    if s and s.stripe_webhook_secret_enc:
        return decrypt_value(s.stripe_webhook_secret_enc, app.config.get('SECRET_KEY', ''))
    return ''


def _payment_settings(app):
    from models import SiteSettings
    return SiteSettings.query.first()


def payments_enabled(app):
    s = _payment_settings(app)
    return bool(s and s.payments_enabled and get_stripe_secret(app))


def _mail_env_defaults(app):
    if '_MAIL_ENV' not in app.config:
        app.config['_MAIL_ENV'] = {
            'MAIL_SERVER': app.config.get('MAIL_SERVER') or 'smtp.gmail.com',
            'MAIL_PORT': int(app.config.get('MAIL_PORT') or 587),
            'MAIL_USE_TLS': bool(app.config.get('MAIL_USE_TLS', True)),
            'MAIL_USE_SSL': bool(app.config.get('MAIL_USE_SSL', False)),
            'MAIL_USERNAME': app.config.get('MAIL_USERNAME') or '',
            'MAIL_PASSWORD': app.config.get('MAIL_PASSWORD') or '',
            'MAIL_DEFAULT_SENDER': app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME') or '',
        }
    return app.config['_MAIL_ENV']


def format_smtp_sender(display_name, email, override=''):
    """From: «Nombre de la web <email>». override puede ser email, nombre o 'Nombre <email>'."""
    import re
    name = (display_name or '').strip()
    addr = (email or '').strip()
    raw = (override or '').strip()
    m = re.match(r'^(.+?)\s*<([^>]+)>$', raw)
    if m:
        name = m.group(1).strip().strip('"').strip("'")
        addr = m.group(2).strip()
    elif '@' in raw:
        addr = raw
    elif raw:
        name = raw
    if name and addr:
        return (name, addr)
    return addr


def _public_hostname(url):
    from urllib.parse import urlparse
    raw = (url or '').strip()
    if not raw:
        return ''
    if '://' not in raw:
        raw = f'https://{raw}'
    host = (urlparse(raw).hostname or '').strip().lower()
    if host.startswith('www.'):
        host = host[4:]
    return host


def _ipv4_from_route_hex(value):
    return '.'.join(str(int(value[i:i + 2], 16)) for i in (6, 4, 2, 0))


def _docker_host_ipv4_candidates():
    """IPs del host vistas desde el contenedor (pasarela Compose, no docker0)."""
    ips = []
    try:
        with open('/proc/net/route', encoding='utf-8') as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == '00000000':
                    gw = _ipv4_from_route_hex(parts[2])
                    if gw and not gw.startswith('0.') and gw != '127.0.0.1':
                        ips.append(gw)
                        break
    except OSError:
        pass
    try:
        import socket
        hip = socket.gethostbyname('host.docker.internal')
        if hip and hip not in ips and hip != '127.0.0.1':
            ips.append(hip)
    except OSError:
        pass
    for fallback in ('172.17.0.1', '172.18.0.1'):
        if fallback not in ips:
            ips.append(fallback)
    return ips or ['172.17.0.1']


def _smtp_tcp_ok(host, port, timeout=1.5):
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _local_smtp_endpoint(preferred_port):
    """Host local: primero el relé Compose; si no, pasarela/587/465."""
    if _smtp_tcp_ok('smtp-relay', 2525):
        print('[mail] SMTP vía relé smtp-relay:2525')
        return 'smtp-relay', 2525
    for ip in _docker_host_ipv4_candidates():
        for p in (preferred_port, 587, 465, 2525):
            if _smtp_tcp_ok(ip, p):
                print(f'[mail] SMTP del host alcanzable en {ip}:{p}')
                return ip, int(p)
    print('[mail] SMTP local no responde; se usará smtp-relay:2525')
    return 'smtp-relay', 2525


def _smtp_host_for_docker(server, public_base_url='', port=587):
    """Si el SMTP es el mismo VPS que la web, devolver (host, puerto) alcanzable."""
    server = (server or '').strip()
    low = server.lower()
    use_host = low in ('', 'localhost', '127.0.0.1', '::1', 'host.docker.internal')
    if not use_host:
        pub = _public_hostname(public_base_url)
        mail_host = low[4:] if low.startswith('www.') else low
        if pub and (mail_host == pub or mail_host.endswith('.' + pub) or pub.endswith('.' + mail_host)):
            use_host = True
        else:
            try:
                import socket
                mail_ips = {ai[4][0] for ai in socket.getaddrinfo(server, None, socket.AF_INET)}
                if mail_ips & {'127.0.0.1', '0.0.0.0'}:
                    use_host = True
            except OSError:
                pass
    if use_host:
        return _local_smtp_endpoint(port)
    return server, port


def _normalize_smtp_cfg(cfg, public_base_url=''):
    """Docker no puede usar localhost/IP pública del mismo VPS; 465=SSL, 587=TLS."""
    try:
        port = int(cfg.get('MAIL_PORT') or 587)
    except (TypeError, ValueError):
        port = 587
    cfg['MAIL_PORT'] = port
    cfg['MAIL_USE_TLS'] = bool(cfg.get('MAIL_USE_TLS'))
    cfg['MAIL_USE_SSL'] = bool(cfg.get('MAIL_USE_SSL'))
    if port == 465:
        cfg['MAIL_USE_SSL'] = True
        cfg['MAIL_USE_TLS'] = False
    elif port == 587:
        cfg['MAIL_USE_SSL'] = False
        cfg['MAIL_USE_TLS'] = True
    if cfg['MAIL_USE_SSL'] and cfg['MAIL_USE_TLS']:
        cfg['MAIL_USE_TLS'] = False
    server = (cfg.get('MAIL_SERVER') or '').strip()
    host, rport = _smtp_host_for_docker(server, public_base_url, port)
    cfg['MAIL_SERVER'] = host
    cfg['MAIL_PORT'] = int(rport)
    if int(rport) == 2525:
        cfg['MAIL_USE_SSL'] = False
        cfg['MAIL_USE_TLS'] = True
    elif int(rport) == 465:
        cfg['MAIL_USE_SSL'] = True
        cfg['MAIL_USE_TLS'] = False
    elif int(rport) == 587:
        cfg['MAIL_USE_SSL'] = False
        cfg['MAIL_USE_TLS'] = True
    return cfg


def apply_smtp_config(app, mail=None):
    """Aplica SMTP desde Ajustes (BD) si hay usuario; si no, usa .env/secrets."""
    env = dict(_mail_env_defaults(app))
    cfg = dict(env)
    try:
        from models import SiteSettings
        s = SiteSettings.query.first()
    except Exception:
        s = None
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass

    db_user = (getattr(s, 'mail_username', None) or '').strip() if s else ''
    academy = ''
    if s:
        academy = (s.academy_name or '').strip()
    academy = academy or (app.config.get('ACADEMY_NAME') or '').strip()

    if s and db_user:
        cfg['MAIL_SERVER'] = (s.mail_server or '').strip() or env['MAIL_SERVER']
        try:
            cfg['MAIL_PORT'] = int(s.mail_port or env['MAIL_PORT'] or 587)
        except (TypeError, ValueError):
            cfg['MAIL_PORT'] = env['MAIL_PORT']
        if s.mail_use_tls is not None:
            cfg['MAIL_USE_TLS'] = bool(s.mail_use_tls)
        if s.mail_use_ssl is not None:
            cfg['MAIL_USE_SSL'] = bool(s.mail_use_ssl)
        cfg['MAIL_USERNAME'] = db_user
        pwd = decrypt_value(s.mail_password_enc or '', app.config.get('SECRET_KEY', ''))
        cfg['MAIL_PASSWORD'] = pwd or env['MAIL_PASSWORD']
        cfg['MAIL_DEFAULT_SENDER'] = format_smtp_sender(
            academy, db_user, (s.mail_sender or '').strip(),
        )
    else:
        cfg['MAIL_DEFAULT_SENDER'] = format_smtp_sender(
            academy,
            env.get('MAIL_USERNAME') or '',
            env.get('MAIL_DEFAULT_SENDER') or '',
        )

    cfg = _normalize_smtp_cfg(cfg, app.config.get('PUBLIC_BASE_URL') or '')
    app.config.update(cfg)
    if mail is not None:
        mail.init_app(app)
    return cfg


def _mail_configured(app, mail=None):
    apply_smtp_config(app, mail)
    return bool(app.config.get('MAIL_USERNAME'))


def render_template_vars(text, **kwargs):
    for key, val in kwargs.items():
        text = text.replace('{{' + key + '}}', str(val or ''))
        text = text.replace('{{ ' + key + ' }}', str(val or ''))
    return text


def send_html_email(app, mail, recipients, subject, body_html):
    from flask_mail import Message as MailMessage
    if not recipients or not _mail_configured(app, mail):
        return False
    sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
    msg = MailMessage(subject=subject, recipients=recipients, html=body_html, sender=sender)
    try:
        mail.send(msg)
        return True
    except OSError as e:
        refused = getattr(e, 'errno', None) == 111 or 'Connection refused' in str(e)
        if refused:
            alt_host, alt_port = _local_smtp_endpoint(app.config.get('MAIL_PORT') or 587)
            cur = (app.config.get('MAIL_SERVER'), int(app.config.get('MAIL_PORT') or 0))
            if (alt_host, int(alt_port)) != cur:
                print(f'[mail] reintento SMTP en {alt_host}:{alt_port}')
                app.config['MAIL_SERVER'] = alt_host
                app.config['MAIL_PORT'] = int(alt_port)
                if int(alt_port) == 2525:
                    app.config['MAIL_USE_SSL'] = False
                    app.config['MAIL_USE_TLS'] = True
                mail.init_app(app)
                mail.send(msg)
                return True
        raise


def default_welcome_subject():
    return '¡Bienvenido/a a {{academy_name}}!'


def default_welcome_body():
    return """<p>Hola <strong>{{username}}</strong>,</p>
<p>¡Bienvenida a <strong>{{academy_name}}</strong>! Tu suscripción está activa.</p>
{{welcome_video_block}}
<p><strong>Tus datos de acceso:</strong></p>
<ul>
<li>Email: <strong>{{email}}</strong></li>
<li>Usuario: <strong>{{username}}</strong></li>
<li>Contraseña: <strong>{{password}}</strong></li>
</ul>
<p style="color:#b45309;font-size:13px">Te recomendamos cambiar la contraseña en Mi cuenta tras el primer acceso.</p>
<p><a href="{{login_url}}" style="display:inline-block;background:#7c3aed;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Entrar a la comunidad</a></p>
<p>Plan: <strong>{{plan_name}}</strong></p>
<p style="color:#71717a;font-size:12px">Si tienes dudas, responde a este correo.</p>"""


def default_billing_alert_subject():
    return 'Cuenta suspendida por impago: {{username}}'


def default_billing_alert_body():
    return """<p>La cuenta de <strong>{{username}}</strong> ha sido <strong>suspendida por impago</strong> y ya no puede acceder a la plataforma.</p>
<ul>
<li><strong>Usuario:</strong> {{username}}</li>
<li><strong>Email:</strong> {{email}}</li>
<li><strong>Plan:</strong> {{plan_name}}</li>
<li><strong>Motivo:</strong> {{reason}}</li>
<li><strong>Fecha:</strong> {{fecha}}</li>
</ul>
<p>La usuaria puede actualizar su método de pago desde Mi cuenta (portal Stripe). Cuando Stripe confirme el cobro, el acceso se reactivará automáticamente.</p>
<p style="color:#71717a;font-size:12px">Revisa <a href="/admin/suscripciones">Suscripciones</a> y el grupo WhatsApp VIP si aplica.</p>"""


def default_admin_reg_subject():
    return 'Nuevo registro: {{username}}'


def default_event_reminder_subject():
    return 'Recordatorio: {{event_title}} — {{reminder_label}}'


def default_event_reminder_body():
    return """<p>Hola <strong>{{username}}</strong>,</p>
<p>Te recordamos el evento <strong>{{event_title}}</strong> ({{reminder_label}}).</p>
<p>📅 <strong>{{event_datetime}}</strong></p>
<p>⏱ Duración aproximada: {{event_duration}} min</p>
{{meet_link_block}}
<p style="color:#71717a;font-size:12px">También puedes verlo en el <a href="{{calendar_url}}">calendario de la academia</a>.</p>"""


def reminder_label_for_type(reminder_type):
    if reminder_type == '24h':
        return 'en 24 horas'
    if reminder_type == '1h':
        return 'en 1 hora'
    return reminder_type


def send_event_reminder_email(app, mail, user, live_class, reminder_type, site=None, calendar_url=''):
    s = site or _payment_settings(app)
    subject_tpl = (
        (s.event_reminder_email_subject if s and s.event_reminder_email_subject else None)
        or default_event_reminder_subject()
    )
    body_tpl = (
        (s.event_reminder_email_body if s and s.event_reminder_email_body else None)
        or default_event_reminder_body()
    )
    academy = (s.academy_name if s and s.academy_name else None) or app.config.get('ACADEMY_NAME', 'Academia')
    when = live_class.scheduled_at.strftime('%d/%m/%Y %H:%M UTC')
    meet_block = ''
    if live_class.meet_url:
        meet_block = (
            f'<p><a href="{escape(live_class.meet_url)}" '
            f'style="display:inline-block;background:#7c3aed;color:#fff;padding:12px 24px;'
            f'border-radius:8px;text-decoration:none;font-weight:600">🎥 Unirse al evento</a></p>'
        )
    ctx = {
        'username': user.username,
        'email': user.email,
        'academy_name': academy,
        'event_title': live_class.title,
        'event_datetime': when,
        'event_duration': live_class.duration_min or '—',
        'meet_url': live_class.meet_url or '',
        'meet_link_block': meet_block,
        'reminder_type': reminder_type,
        'reminder_label': reminder_label_for_type(reminder_type),
        'calendar_url': calendar_url or '#',
        'instructor': live_class.instructor or '',
    }
    subject = render_template_vars(subject_tpl, **ctx)
    inner = render_template_vars(body_tpl, **ctx)
    return send_html_email(app, mail, [user.email], subject, email_wrapper(academy, inner))


def default_admin_reg_body():
    return """<p>Se ha registrado un nuevo usuario en la plataforma:</p>
<ul>
<li><strong>Usuario:</strong> {{username}}</li>
<li><strong>Email:</strong> {{email}}</li>
<li><strong>Fecha registro:</strong> {{created_at}}</li>
<li><strong>Plan:</strong> {{plan_name}} ({{plan_price}})</li>
<li><strong>Estado cuenta:</strong> {{status}}</li>
<li><strong>Qué necesito ahora:</strong> {{bio}}</li>
<li><strong>Ciudad:</strong> {{city}}</li>
</ul>
<p style="color:#71717a;font-size:12px">Revisa el panel de administración para aprobar o gestionar la cuenta.</p>"""


def email_wrapper(academy_name, inner_html):
    name = escape(academy_name or 'Academia')
    return f"""<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
<div style="background:#7c3aed;padding:24px;border-radius:12px 12px 0 0;text-align:center">
<h1 style="color:#fff;margin:0;font-size:20px">🎓 {name}</h1>
</div>
<div style="background:#fff;padding:32px;border:1px solid #e4e4e7;border-top:none;border-radius:0 0 12px 12px">
{inner_html}
</div></div>"""


def video_embed_block(url):
    if not url:
        return ''
    embed = url
    if 'youtu.be/' in url:
        vid = url.split('youtu.be/')[1].split('?')[0]
        embed = f'https://www.youtube.com/embed/{vid}'
    elif 'youtube.com' in url and 'embed' not in url:
        if 'v=' in url:
            vid = url.split('v=')[1].split('&')[0]
            embed = f'https://www.youtube.com/embed/{vid}'
    elif 'vimeo.com' in url and 'player.vimeo.com' not in url:
        path = url.split('vimeo.com/')[1].split('?')[0]
        parts = path.split('/')
        embed = f'https://player.vimeo.com/video/{parts[0]}'
    return (
        f'<div style="margin:16px 0;position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:8px">'
        f'<iframe src="{escape(embed)}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0" '
        f'allowfullscreen></iframe></div>'
    )


def get_admin_emails(app):
    admin_email = (app.config.get('ADMIN_EMAIL') or '').strip()
    if admin_email:
        return [admin_email]
    from models import User
    return [a.email for a in User.query.filter_by(role='admin').all() if a.email]


def send_welcome_email(app, mail, user, plan_name, login_url, pending_approval=False,
                       plain_password='', site=None):
    s = site or _payment_settings(app)
    subject = (s.welcome_email_subject if s and s.welcome_email_subject else default_welcome_subject())
    body = (s.welcome_email_body if s and s.welcome_email_body else default_welcome_body())
    academy = (s.academy_name if s and s.academy_name else None) or app.config.get('ACADEMY_NAME', 'Academia')
    approval_note = (
        '<p style="color:#b45309">Tu pago está confirmado. Un administrador revisará tu cuenta y te avisará cuando puedas entrar.</p>'
        if pending_approval else ''
    )
    video_block = video_embed_block(getattr(s, 'welcome_video_url', '') or '')
    ctx = {
        'username': user.username,
        'email': user.email,
        'password': plain_password or '—',
        'plan_name': plan_name or '—',
        'academy_name': academy,
        'login_url': login_url,
        'approval_note': approval_note,
        'welcome_video_block': video_block,
    }
    subject = render_template_vars(subject, **ctx)
    inner = render_template_vars(body, **ctx)
    return send_html_email(app, mail, [user.email], subject, email_wrapper(academy, inner))


def send_admin_registration_email(app, mail, user, plan_name, status_label, plan=None, region_label=''):
    s = _payment_settings(app)
    subject = (s.admin_reg_email_subject if s and s.admin_reg_email_subject else default_admin_reg_subject())
    body = (s.admin_reg_email_body if s and s.admin_reg_email_body else default_admin_reg_body())
    academy = (s.academy_name if s and s.academy_name else None) or app.config.get('ACADEMY_NAME', 'Academia')
    if plan:
        price = plan.price_for_region('intl' if 'Internacional' in (region_label or '') else 'es')
        plan_price = f'{price:.2f} €/mes'
    else:
        plan_price = '—'
    created = user.created_at.strftime('%d/%m/%Y %H:%M') if user.created_at else '—'
    ctx = {
        'username': user.username,
        'email': user.email,
        'bio': user.bio or '—',
        'city': user.city or '—',
        'plan_name': plan_name or '—',
        'plan_price': plan_price,
        'created_at': created,
        'status': status_label,
        'academy_name': academy,
    }
    subject = render_template_vars(subject, **ctx)
    inner = render_template_vars(body, **ctx)
    emails = get_admin_emails(app)
    if not emails:
        return False
    return send_html_email(app, mail, emails, subject, email_wrapper(academy, inner))


def send_admin_billing_alert_email(app, mail, user, reason, event_date=None):
    s = _payment_settings(app)
    subject_tpl = (s.billing_alert_email_subject if s and s.billing_alert_email_subject else None) or default_billing_alert_subject()
    body_tpl = (s.billing_alert_email_body if s and s.billing_alert_email_body else None) or default_billing_alert_body()
    academy = (s.academy_name if s and s.academy_name else None) or app.config.get('ACADEMY_NAME', 'Academia')
    plan_name = user.subscription_plan.name if user.subscription_plan else '—'
    fecha = (event_date or datetime.utcnow()).strftime('%d/%m/%Y %H:%M')
    ctx = {
        'username': user.username,
        'email': user.email,
        'plan_name': plan_name,
        'reason': reason,
        'fecha': fecha,
        'academy_name': academy,
    }
    subject = render_template_vars(subject_tpl, **ctx)
    inner = render_template_vars(body_tpl, **ctx)
    emails = get_admin_emails(app)
    if not emails:
        return False
    return send_html_email(app, mail, emails, subject, email_wrapper(academy, inner))


def mark_whatsapp_vip_pending(user):
    user.whatsapp_vip_pending = True


def suspend_user_for_nonpayment(user, reason, *, subscription_status=None,
                                db=None, notify_fn=None, app=None, mail=None):
    """
    Suspende por impago y avisa a administradores (notificación in-app + email).
    Devuelve True si la cuenta acaba de quedar suspendida en esta llamada.
    """
    if user.billing_type == 'free' or user.is_admin:
        return False

    newly_suspended = user.status != 'suspended'
    user.status = 'suspended'
    if subscription_status:
        user.subscription_status = subscription_status
    elif user.subscription_status in SUBSCRIPTION_ACTIVE_STATUSES:
        user.subscription_status = 'past_due'
    mark_whatsapp_vip_pending(user)

    if newly_suspended:
        if db is not None and notify_fn is not None:
            notify_admins_payment_failed(db, notify_fn, user, reason, app=app, mail=mail)
        elif app and mail:
            try:
                sent = send_admin_billing_alert_email(app, mail, user, reason)
                if not sent:
                    print(f'[billing] No se envió email de suspensión (SMTP). user={user.username}')
            except Exception as e:
                print(f'[billing] admin suspension email: {e}')
    return newly_suspended


def notify_admins_payment_failed(db, notify_fn, user, reason, app=None, mail=None):
    from models import User
    admins = User.query.filter_by(role='admin').all()
    for admin in admins:
        notify_fn(
            admin.id,
            'payment_failed',
            f'⚠️ {user.username} suspendida por impago ({reason}). Revisa suscripciones.',
            '/admin/suscripciones',
        )
    if app and mail:
        try:
            sent = send_admin_billing_alert_email(app, mail, user, reason)
            if not sent:
                print(f'[billing] No se envió email de suspensión a admins (SMTP no configurado). user={user.username}')
        except Exception as e:
            print(f'[billing] admin alert email: {e}')
    elif app:
        print(f'[billing] Email de suspensión omitido: mail no disponible. user={user.username}')


def user_payment_label(user):
    if user.billing_type == 'free':
        return 'Gratuito'
    st = user.subscription_status or 'none'
    if st == 'active':
        return 'Al día'
    if st == 'trialing':
        return 'Periodo de prueba'
    if st == 'past_due':
        return 'Pago pendiente'
    if st == 'unpaid':
        return 'Impago'
    if st == 'canceled':
        return 'Cancelado'
    if user.status == 'pending':
        return 'Pendiente registro/pago'
    if user.status == 'suspended':
        return 'Suspendido'
    return 'Sin suscripción'


def user_needs_paid_subscription(user, payments_on=True):
    """True si el usuario debe tener suscripción Stripe al día."""
    if not payments_on:
        return False
    if user.is_admin or user.is_free_billing:
        return False
    return True


def user_platform_access(user, payments_on=True, check_period_end=True):
    """
    Devuelve (permitido, mensaje_error).
    Bloquea cuentas suspendidas, sin suscripción activa o con periodo vencido sin pago.
    """
    if user.is_admin:
        return True, ''
    if user.is_free_billing:
        return True, ''
    if not user_needs_paid_subscription(user, payments_on):
        if user.status == 'pending':
            return False, 'Tu cuenta está pendiente de aprobación por un administrador.'
        if user.status == 'rejected':
            return False, 'Tu solicitud de acceso ha sido denegada. Contacta con el administrador.'
        if user.status == 'suspended':
            return False, 'Tu cuenta está suspendida. Contacta con soporte.'
        return True, ''

    if user.status == 'pending':
        return False, 'Tu cuenta está pendiente de aprobación por un administrador.'
    if user.status == 'rejected':
        return False, 'Tu solicitud de acceso ha sido denegada. Contacta con el administrador.'
    if user.status == 'suspended':
        return False, (
            'Tu cuenta está suspendida por impago o revisión administrativa. '
            'Actualiza tu método de pago en Stripe o contacta con soporte.'
        )

    st = user.subscription_status or 'none'
    if st not in SUBSCRIPTION_ACTIVE_STATUSES:
        if st == 'past_due':
            return False, (
                'Tu suscripción tiene un pago pendiente. '
                'Actualiza tu tarjeta desde Mi cuenta para recuperar el acceso.'
            )
        if st in ('canceled', 'unpaid'):
            return False, 'Tu suscripción no está activa. Renueva desde Mi cuenta o contacta con soporte.'
        if st == 'none':
            return False, 'No tienes una suscripción activa. Contacta con soporte.'
        return False, 'Acceso restringido: revisa el estado de tu suscripción en Mi cuenta.'

    if check_period_end and user.subscription_period_end:
        if user.subscription_period_end < datetime.utcnow():
            return False, (
                'Tu mensualidad no se ha renovado. '
                'Si ya has pagado, espera unos minutos; si no, actualiza tu método de pago en Mi cuenta.'
            )

    return True, ''


def apply_subscription_block(user, subscription_status):
    """Suspende cuenta de pago si Stripe indica impago o baja (sin notificar; usar suspend_user_for_nonpayment)."""
    if user.billing_type == 'free' or user.is_admin:
        return
    if subscription_status in SUBSCRIPTION_BLOCK_STATUSES:
        user.status = 'suspended'
    elif subscription_status in SUBSCRIPTION_ACTIVE_STATUSES:
        if user.status == 'suspended':
            user.status = 'active'


def _stripe_subscription_period_end(subscription_obj):
    if isinstance(subscription_obj, dict):
        return subscription_obj.get('current_period_end')
    return getattr(subscription_obj, 'current_period_end', None)


def sync_stripe_subscription(app, user, subscription_obj, *, mark_paid=False, apply_block=True):
    """Actualiza usuario desde objeto subscription de Stripe (objeto, dict o id str)."""
    if isinstance(subscription_obj, str):
        import stripe
        stripe.api_key = get_stripe_secret(app)
        subscription_obj = stripe.Subscription.retrieve(subscription_obj)
    if isinstance(subscription_obj, dict):
        status = subscription_obj.get('status', 'none')
        sub_id = subscription_obj.get('id', '')
    else:
        status = subscription_obj.status
        sub_id = subscription_obj.id
    user.stripe_subscription_id = sub_id or user.stripe_subscription_id
    user.subscription_status = status
    period_end = _stripe_subscription_period_end(subscription_obj)
    if period_end:
        user.subscription_period_end = datetime.utcfromtimestamp(int(period_end))
    if mark_paid and status in SUBSCRIPTION_ACTIVE_STATUSES:
        user.subscription_last_paid_at = datetime.utcnow()
    if apply_block:
        apply_subscription_block(user, status)
    return status


def find_user_for_stripe_subscription(db_session, User, sub_id=None, customer_id=None):
    user = None
    if sub_id:
        user = User.query.filter_by(stripe_subscription_id=sub_id).first()
    if not user and customer_id:
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
    return user


def process_stripe_webhook_event(app, db, mail, event, notify_fn, *, finalize_registration_fn,
                                 create_user_from_checkout_fn, get_settings_fn):
    """Procesa eventos Stripe: renovaciones mensuales, impagos y bajas."""
    from models import User, SubscriptionPlan

    etype = event['type']
    data = event['data']['object']

    if etype == 'checkout.session.completed' and data.get('mode') == 'subscription':
        meta = data.get('metadata') or {}
        if meta.get('checkout_intent_id') or not meta.get('user_id'):
            create_user_from_checkout_fn(app, mail, data, get_settings_fn)
        else:
            user_id = int(meta.get('user_id', 0) or data.get('client_reference_id', 0) or 0)
            plan_id = int(meta.get('plan_id', 0) or 0)
            user = User.query.get(user_id)
            plan = SubscriptionPlan.query.get(plan_id) if plan_id else None
            if user:
                if user.status == 'pending' and finalize_registration_fn:
                    finalize_registration_fn(user, plan, data)
                else:
                    sub_ref = data.get('subscription')
                    if sub_ref:
                        sync_stripe_subscription(app, user, sub_ref, mark_paid=True)
                        db.session.commit()
        return

    if etype in ('invoice.payment_succeeded', 'invoice.paid'):
        sub_id = data.get('subscription')
        if not sub_id:
            return
        user = find_user_for_stripe_subscription(db.session, User, sub_id=sub_id, customer_id=data.get('customer'))
        if not user or user.billing_type == 'free':
            return
        user.subscription_last_paid_at = datetime.utcnow()
        try:
            sync_stripe_subscription(app, user, sub_id, mark_paid=True)
        except Exception as e:
            print(f'[billing] sync on invoice paid: {e}')
            user.subscription_status = 'active'
            user.status = 'active'
            try:
                period_end = data.get('lines', {}).get('data', [{}])[0].get('period', {}).get('end')
                if period_end:
                    user.subscription_period_end = datetime.utcfromtimestamp(int(period_end))
            except Exception:
                pass
        db.session.commit()
        return

    if etype == 'invoice.payment_failed':
        sub_id = data.get('subscription')
        user = find_user_for_stripe_subscription(db.session, User, sub_id=sub_id) if sub_id else None
        if user and user.billing_type != 'free':
            suspend_user_for_nonpayment(
                user, 'pago mensual fallido en Stripe',
                subscription_status='past_due',
                db=db, notify_fn=notify_fn, app=app, mail=mail,
            )
            db.session.commit()
        return

    if etype in ('customer.subscription.deleted', 'customer.subscription.updated', 'customer.subscription.created'):
        sub_id = data.get('id')
        user = find_user_for_stripe_subscription(db.session, User, sub_id=sub_id, customer_id=data.get('customer'))
        if not user or user.billing_type == 'free':
            return
        sync_stripe_subscription(app, user, data, apply_block=False)
        status = data.get('status', user.subscription_status)
        if status in SUBSCRIPTION_BLOCK_STATUSES:
            reason = {
                'canceled': 'cancelación de suscripción en Stripe',
                'unpaid': 'impago tras reintentos de Stripe',
                'past_due': 'pago mensual pendiente en Stripe',
            }.get(status, f'estado Stripe: {status}')
            suspend_user_for_nonpayment(
                user, reason, subscription_status=status,
                db=db, notify_fn=notify_fn, app=app, mail=mail,
            )
        elif status in SUBSCRIPTION_ACTIVE_STATUSES and user.status == 'suspended':
            user.status = 'active'
        db.session.commit()


def monthly_subscription_line_item(plan, *, region='es', interval='month'):
    """Línea de checkout: suscripción recurrente mensual sin fecha de fin."""
    interval = 'year' if interval == 'year' else 'month'
    price_id = plan.stripe_price_for_region(region) if region in ('es', 'intl') else ''
    if interval == 'year':
        price_id = plan.stripe_price_id_yearly or price_id
        unit_price = plan.price_yearly or 0
    else:
        unit_price = plan.price_for_region(region) if region in ('es', 'intl') else plan.price_monthly

    line_item = {'quantity': 1}
    if price_id:
        line_item['price'] = price_id
    else:
        line_item['price_data'] = {
            'currency': 'eur',
            'recurring': {'interval': interval},
            'product_data': {'name': plan.name, 'description': (plan.description or '')[:200]},
            'unit_amount': int(round((unit_price or 0) * 100)),
        }
    return line_item


def monthly_subscription_data(metadata, *, trial_days=0):
    """Datos de suscripción Stripe: mensual, renovación automática hasta cancelación."""
    sub_data = {'metadata': metadata}
    if trial_days > 0:
        sub_data['trial_period_days'] = trial_days
    return sub_data


def create_subscription_checkout(
    app, user, plan, success_url, cancel_url,
    billing_interval='month', promotion_code=None,
):
    import stripe
    stripe.api_key = get_stripe_secret(app)
    interval = 'year' if billing_interval == 'year' else 'month'
    region = 'es'
    line_item = monthly_subscription_line_item(plan, region=region, interval=interval)
    sub_data = monthly_subscription_data(
        {'user_id': str(user.id), 'plan_id': str(plan.id)},
        trial_days=getattr(plan, 'trial_days', 0) or 0,
    )

    kwargs = dict(
        mode='subscription',
        payment_method_types=['card'],
        line_items=[line_item],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(user.id),
        customer_email=user.email,
        metadata={'user_id': str(user.id), 'plan_id': str(plan.id)},
        subscription_data=sub_data,
    )
    coupon = (promotion_code or '').strip() or (getattr(plan, 'stripe_coupon_id', '') or '').strip()
    if coupon:
        if coupon.startswith('promo_'):
            kwargs['discounts'] = [{'promotion_code': coupon}]
        else:
            kwargs['discounts'] = [{'coupon': coupon}]

    session = stripe.checkout.Session.create(**kwargs)
    return session


def create_public_subscription_checkout(app, plan, billing_region, success_url, cancel_url, checkout_intent_id):
    """Checkout sin usuario en BD (landing pública)."""
    import stripe
    from models import db, CheckoutIntent
    stripe.api_key = get_stripe_secret(app)
    region = billing_region if billing_region in ('es', 'intl') else 'es'
    line_item = monthly_subscription_line_item(plan, region=region, interval='month')
    sub_data = monthly_subscription_data(
        {
            'checkout_intent_id': str(checkout_intent_id),
            'plan_id': str(plan.id),
            'billing_region': region,
        },
        trial_days=getattr(plan, 'trial_days', 0) or 0,
    )

    session = stripe.checkout.Session.create(
        mode='subscription',
        payment_method_types=['card'],
        line_items=[line_item],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(checkout_intent_id),
        metadata={
            'checkout_intent_id': str(checkout_intent_id),
            'plan_id': str(plan.id),
            'billing_region': region,
        },
        subscription_data=sub_data,
    )
    intent = CheckoutIntent.query.get(checkout_intent_id)
    if intent:
        intent.stripe_session_id = session.id
        db.session.commit()
    return session


def create_billing_portal_session(app, user, return_url):
    import stripe
    stripe.api_key = get_stripe_secret(app)
    if not user.stripe_customer_id:
        if not user.email:
            raise ValueError('Usuario sin email')
        cust = stripe.Customer.create(
            email=user.email,
            name=user.username,
            metadata={'user_id': str(user.id)},
        )
        user.stripe_customer_id = cust.id
        from models import db
        db.session.commit()
    return stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=return_url,
    )


def send_test_template_email(app, mail, to_email, subject_tpl, body_tpl, sample_ctx=None):
    s = _payment_settings(app)
    academy = (s.academy_name if s and s.academy_name else None) or app.config.get('ACADEMY_NAME', 'Academia')
    ctx = sample_ctx or {
        'username': 'UsuarioPrueba',
        'email': to_email,
        'plan_name': 'Plan Ejemplo',
        'plan_price': '29.00 €/mes',
        'academy_name': academy,
        'login_url': 'https://ejemplo.com/login',
        'approval_note': '<p>(Nota de prueba)</p>',
        'bio': 'Ejemplo de necesidad',
        'city': 'Madrid',
        'created_at': '01/01/2026 10:00',
        'status': 'Activo',
    }
    subject = render_template_vars(subject_tpl, **ctx)
    inner = render_template_vars(body_tpl, **ctx)
    return send_html_email(app, mail, [to_email], subject, email_wrapper(academy, inner))
