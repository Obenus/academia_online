"""Facturación: Stripe, emails y estado de suscripción."""
from datetime import datetime, timezone
from html import escape

from backup_manager import decrypt_value, encrypt_value


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


def _mail_configured(app):
    return bool(app.config.get('MAIL_USERNAME'))


def render_template_vars(text, **kwargs):
    for key, val in kwargs.items():
        text = text.replace('{{' + key + '}}', str(val or ''))
        text = text.replace('{{ ' + key + ' }}', str(val or ''))
    return text


def send_html_email(app, mail, recipients, subject, body_html):
    from flask_mail import Message as MailMessage
    if not recipients or not _mail_configured(app):
        return False
    msg = MailMessage(subject=subject, recipients=recipients, html=body_html)
    mail.send(msg)
    return True


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
    return 'Alerta suscripción: {{username}} — {{reason}}'


def default_billing_alert_body():
    return """<p>Se ha producido un evento de suscripción:</p>
<ul>
<li><strong>Usuario:</strong> {{username}}</li>
<li><strong>Email:</strong> {{email}}</li>
<li><strong>Plan:</strong> {{plan_name}}</li>
<li><strong>Motivo:</strong> {{reason}}</li>
<li><strong>Fecha:</strong> {{fecha}}</li>
</ul>
<p style="color:#71717a;font-size:12px">Revisa /admin/suscripciones y el grupo WhatsApp VIP si aplica.</p>"""


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


def notify_admins_payment_failed(db, notify_fn, user, reason, app=None, mail=None):
    from models import User
    mark_whatsapp_vip_pending(user)
    admins = User.query.filter_by(role='admin').all()
    for admin in admins:
        notify_fn(
            admin.id,
            'payment_failed',
            f'⚠️ {user.username} no ha abonado la mensualidad ({reason}). Revisa su cuenta.',
            '/admin/suscripciones',
        )
    if app and mail:
        try:
            send_admin_billing_alert_email(app, mail, user, reason)
        except Exception as e:
            print(f'[billing] admin alert email: {e}')


def user_payment_label(user):
    if user.billing_type == 'free':
        return 'Gratuito'
    st = user.subscription_status or 'none'
    if st == 'active':
        return 'Al día'
    if st == 'past_due':
        return 'Pago pendiente'
    if st == 'canceled':
        return 'Cancelado'
    if user.status == 'pending':
        return 'Pendiente registro/pago'
    if user.status == 'suspended':
        return 'Suspendido'
    return 'Sin suscripción'


def sync_stripe_subscription(app, user, subscription_obj):
    """Actualiza usuario desde objeto subscription de Stripe (objeto o dict o id str)."""
    if isinstance(subscription_obj, str):
        import stripe
        stripe.api_key = get_stripe_secret(app)
        subscription_obj = stripe.Subscription.retrieve(subscription_obj)
    if isinstance(subscription_obj, dict):
        status = subscription_obj.get('status', 'none')
        sub_id = subscription_obj.get('id', '')
        period_end = subscription_obj.get('current_period_end')
    else:
        status = subscription_obj.status
        sub_id = subscription_obj.id
        period_end = subscription_obj.current_period_end
    user.stripe_subscription_id = sub_id or user.stripe_subscription_id
    user.subscription_status = status
    if period_end:
        user.subscription_period_end = datetime.utcfromtimestamp(int(period_end))
    if status == 'active':
        user.subscription_last_paid_at = datetime.utcnow()
    return status


def create_subscription_checkout(
    app, user, plan, success_url, cancel_url,
    billing_interval='month', promotion_code=None,
):
    import stripe
    stripe.api_key = get_stripe_secret(app)
    interval = 'year' if billing_interval == 'year' else 'month'
    line_item = {'quantity': 1}
    price_id = plan.stripe_price_id_yearly if interval == 'year' else plan.stripe_price_id
    unit_price = plan.price_yearly if interval == 'year' else plan.price_monthly
    if price_id:
        line_item['price'] = price_id
    else:
        line_item['price_data'] = {
            'currency': 'eur',
            'recurring': {'interval': interval},
            'product_data': {'name': plan.name, 'description': (plan.description or '')[:200]},
            'unit_amount': int(round((unit_price or 0) * 100)),
        }
    sub_data = {'metadata': {'user_id': str(user.id), 'plan_id': str(plan.id)}}
    trial_days = getattr(plan, 'trial_days', 0) or 0
    if trial_days > 0:
        sub_data['trial_period_days'] = trial_days

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
    price_id = plan.stripe_price_for_region(region)
    unit_price = plan.price_for_region(region)
    line_item = {'quantity': 1}
    if price_id:
        line_item['price'] = price_id
    else:
        line_item['price_data'] = {
            'currency': 'eur',
            'recurring': {'interval': 'month'},
            'product_data': {'name': plan.name, 'description': (plan.description or '')[:200]},
            'unit_amount': int(round((unit_price or 0) * 100)),
        }
    sub_data = {
        'metadata': {
            'checkout_intent_id': str(checkout_intent_id),
            'plan_id': str(plan.id),
            'billing_region': region,
        }
    }
    trial_days = getattr(plan, 'trial_days', 0) or 0
    if trial_days > 0:
        sub_data['trial_period_days'] = trial_days

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
