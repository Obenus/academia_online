"""Registro post-pago: creación de usuario desde Stripe Checkout."""
import re
import secrets
from datetime import datetime

from models import db, User, CheckoutIntent, SubscriptionPlan
from billing import (
    sync_stripe_subscription, send_welcome_email, send_admin_registration_email,
    as_plain_dict,
)


def _slug_username(email, name=''):
    base = ''
    if name:
        base = re.sub(r'[^a-zA-Z0-9]', '', name.split()[0].lower()[:12])
    if not base and email:
        base = re.sub(r'[^a-zA-Z0-9]', '', email.split('@')[0].lower()[:12])
    if not base:
        base = 'miembro'
    candidate = base
    n = 1
    while User.query.filter_by(username=candidate).first():
        candidate = f'{base}{n}'
        n += 1
    return candidate[:80]


def _extract_customer(session_obj):
    details = as_plain_dict(session_obj.get('customer_details'))
    email = (details.get('email') or session_obj.get('customer_email') or '').strip().lower()
    name = (details.get('name') or '').strip()
    return email, name


def create_user_from_checkout(app, mail, session_obj, get_settings):
    """Idempotente: devuelve (user, created, plain_password)."""
    session_obj = as_plain_dict(session_obj)
    sid = session_obj.get('id') or session_obj.get('session_id', '')
    if not sid:
        return None, False, ''

    intent = CheckoutIntent.query.filter_by(stripe_session_id=sid).first()
    meta = as_plain_dict(session_obj.get('metadata'))
    if not intent and meta.get('checkout_intent_id'):
        intent = CheckoutIntent.query.get(int(meta.get('checkout_intent_id', 0) or 0))

    if intent and intent.user_id:
        user = User.query.get(intent.user_id)
        if user:
            return user, False, intent.plain_password or ''

    email, name = _extract_customer(session_obj)
    if not email:
        return None, False, ''

    existing = User.query.filter_by(email=email).first()
    if existing:
        if intent:
            intent.user_id = existing.id
            intent.status = 'completed'
            intent.customer_email = email
            intent.customer_name = name
        _sync_user_subscription(existing, session_obj, intent, app)
        db.session.commit()
        return existing, False, ''

    plan_id = int(meta.get('plan_id', 0) or (intent.plan_id if intent else 0) or 0)
    plan = SubscriptionPlan.query.get(plan_id) if plan_id else None
    region = meta.get('billing_region', 'es') or (intent.billing_region if intent else 'es')

    plain_pw = secrets.token_urlsafe(10)
    user = User(
        username=_slug_username(email, name),
        email=email,
        bio='',
        city='',
        status='active',
        billing_type='standard',
        subscription_plan_id=plan.id if plan else None,
    )
    user.set_password(plain_pw)
    db.session.add(user)
    db.session.flush()

    if intent:
        intent.user_id = user.id
        intent.status = 'completed'
        intent.customer_email = email
        intent.customer_name = name
        intent.plain_password = plain_pw
        if not intent.stripe_session_id:
            intent.stripe_session_id = sid
    else:
        fallback_plan = plan or SubscriptionPlan.query.filter_by(is_active=True).first()
        if not fallback_plan:
            db.session.rollback()
            return None, False, ''
        intent = CheckoutIntent(
            stripe_session_id=sid,
            plan_id=fallback_plan.id,
            billing_region=region,
            customer_email=email,
            customer_name=name,
            status='completed',
            user_id=user.id,
            plain_password=plain_pw,
        )
        db.session.add(intent)

    _sync_user_subscription(user, session_obj, intent, app)
    db.session.commit()

    site = get_settings()
    login_url = app.config.get('PUBLIC_BASE_URL', '').rstrip('/') + '/login'
    if not app.config.get('PUBLIC_BASE_URL'):
        from flask import url_for
        with app.test_request_context('/'):
            login_url = url_for('login', _external=True)

    plan_name = plan.name if plan else 'Suscripción'
    region_label = 'España' if region == 'es' else 'Internacional'
    try:
        send_welcome_email(
            app, mail, user, plan_name, login_url,
            pending_approval=False, plain_password=plain_pw, site=site,
        )
        send_admin_registration_email(
            app, mail, user, plan_name,
            f'Activo (pago {region_label})', plan=plan, region_label=region_label,
        )
    except Exception as e:
        print(f'[registration] email error: {e}')

    return user, True, plain_pw


def _is_test_checkout(session_obj):
    meta = as_plain_dict(session_obj.get('metadata'))
    if str(meta.get('payment_test_mode', '')) in ('1', 'true', 'True'):
        return True
    sid = str(session_obj.get('id') or '')
    return sid.startswith('cs_test_mode_')


def _sync_user_subscription(user, session_obj, intent, app):
    session_obj = as_plain_dict(session_obj)
    sub_id = session_obj.get('subscription')
    if isinstance(sub_id, dict):
        sub_id = sub_id.get('id', '')
    cust_id = session_obj.get('customer', '')
    if isinstance(cust_id, dict):
        cust_id = cust_id.get('id', '')
    if cust_id:
        user.stripe_customer_id = cust_id
    if _is_test_checkout(session_obj):
        if sub_id:
            user.stripe_subscription_id = sub_id
        user.subscription_status = 'active'
        user.status = 'active'
        user.subscription_last_paid_at = datetime.utcnow()
        return
    if sub_id and app:
        sync_stripe_subscription(app, user, sub_id, mark_paid=True)
    elif user.subscription_status in ('none', ''):
        user.subscription_status = 'active'
    user.subscription_last_paid_at = datetime.utcnow()
