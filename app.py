import os
import io
from functools import wraps
from datetime import datetime, timedelta

try:
    from PIL import Image as PILImage
    _PILLOW_OK = True
except ImportError:
    _PILLOW_OK = False

from flask import (Flask, render_template, redirect, url_for,
                   request, flash, jsonify, abort, send_file, make_response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_mail import Mail, Message as MailMessage
from sqlalchemy import text

from models import (db, User, Category, Post, Comment,
                    Course, Section, Lesson, LessonFile, LessonImage, Enrollment, LessonProgress, LiveClass,
                    SiteSettings, PointEvent, Notification, SubscriptionPlan,
                    Quiz, Assignment, PostReport, CourseCertificate, LiveClassCategory,
                    CheckoutIntent, CalendarMonthTheme, LibraryItem, Resource, ResourceTag)
from calendar_categories import ensure_calendar_categories, category_event_colors
from community_categories import ensure_community_categories, category_by_slug
from geo_utils import detect_billing_region, billing_region_label
from n8n_notify import notify_n8n_pregunta
from registration import create_user_from_checkout
from landing_content import (
    LANDING_DEFAULTS, LANDING_FORM_FIELDS,
    landing_text, landing_paragraphs, landing_lines,
)
from spain_provinces import (
    SPANISH_PROVINCES, CITY_OTHER_VALUE, CITY_OTHER_LABEL,
    parse_city_from_form, city_form_state, city_form_from_request,
)
from extensions import csrf, limiter, init_security
from db_migrate import run_migrations
from learning_utils import (
    course_progress, ordered_lessons, completed_lesson_ids,
    is_lesson_unlocked, issue_certificate,
)
from blueprints.features import bp as features_bp, register_bulk_email_routes, user_can_access_category
from blueprints.library import bp as library_bp
from blueprints.resources import bp as resources_bp
from video_utils import video_thumbnail_url, video_embed_url
from backup_manager import run_backup, encrypt_value, decrypt_value, list_local_backups, restore_backup
from billing import (
    payments_enabled, get_stripe_secret, get_stripe_public, get_stripe_webhook_secret,
    create_subscription_checkout, create_public_subscription_checkout,
    sync_stripe_subscription, send_welcome_email,
    send_admin_registration_email, notify_admins_payment_failed, user_payment_label,
    mark_whatsapp_vip_pending, video_embed_block,
    user_platform_access, process_stripe_webhook_event,
)

app = Flask(__name__)
app.config.from_pyfile('config.py')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 604800  # 7 días de caché para estáticos
# Pool BD conservador: 3 workers × 3 pool = 9 conexiones máx (Railway free tier = 10 conn limit)
app.config.setdefault('SQLALCHEMY_POOL_SIZE', 3)
app.config.setdefault('SQLALCHEMY_MAX_OVERFLOW', 2)
app.config.setdefault('SQLALCHEMY_POOL_RECYCLE', 280)
app.config.setdefault('SQLALCHEMY_POOL_PRE_PING', True)
app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {
    'pool_size': 3, 'max_overflow': 2, 'pool_recycle': 280, 'pool_pre_ping': True
})

db.init_app(app)
mail = Mail(app)
init_security(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Inicia sesión para continuar.'

# ── Jinja helpers ─────────────────────────────────────────────────────────────

def youtube_embed(url: str) -> str:
    return video_embed_url(url)


@app.template_filter('video_embed')
def video_embed_secure(url: str) -> str:
    from flask import request
    origin = request.url_root.rstrip('/') if request else ''
    return video_embed_url(url, origin=origin, locked=True)


app.jinja_env.filters['youtube_embed'] = youtube_embed
app.jinja_env.filters['video_thumbnail'] = video_thumbnail_url

def timeago(dt: datetime) -> str:
    diff = datetime.utcnow() - dt
    s = diff.total_seconds()
    if s < 60:    return 'ahora mismo'
    if s < 3600:  return f'hace {int(s//60)} min'
    if s < 86400: return f'hace {int(s//3600)} h'
    return f'hace {int(s//86400)} d'

app.jinja_env.filters['timeago'] = timeago
app.jinja_env.globals['get_level']  = lambda pts: get_level(pts)  # set after get_level is defined

def notify(user_id, type_, message, link=''):
    db.session.add(Notification(user_id=user_id, type=type_, message=message, link=link))

_SKIP_PATHS = ('/avatar/', '/curso/', '/comunidad/banner', '/leccion-imagen/', '/static/')

# Rutas accesibles con suscripción suspendida (gestionar pago)
_SUBSCRIPTION_EXEMPT_ENDPOINTS = frozenset({
    'login', 'logout', 'stripe_webhook', 'webhook_recording',
    'checkout_start', 'checkout_success', 'checkout',
    'account_settings', 'features.billing_portal',
    'serve_avatar', 'serve_banner', 'serve_course_cover', 'serve_lesson_image',
    'serve_file',
})

_SUBSCRIPTION_EXEMPT_PREFIXES = ('/static/', '/webhooks/')


@app.before_request
def enforce_subscription_access():
    """Bloquea el acceso si la mensualidad no está al día (impago o periodo vencido)."""
    if not current_user.is_authenticated or current_user.is_admin:
        return
    ep = request.endpoint or ''
    if ep in _SUBSCRIPTION_EXEMPT_ENDPOINTS:
        return
    if request.path.startswith(_SUBSCRIPTION_EXEMPT_PREFIXES):
        return
    if not payments_enabled(app):
        return
    allowed, message = user_platform_access(current_user, payments_on=True)
    if allowed:
        return
    logout_user()
    flash(message, 'error')
    return redirect(url_for('login'))


@app.before_request
def update_last_seen():
    # Saltar rutas de imágenes y estáticos — no necesitan actualizar last_seen
    if request.path.startswith(_SKIP_PATHS):
        return
    if current_user.is_authenticated:
        now = datetime.utcnow()
        if not current_user.last_seen or (now - current_user.last_seen).total_seconds() > 60:
            current_user.last_seen = now
            try:
                window_start = now
                window_end   = now + timedelta(hours=24)
                upcoming = LiveClass.query.filter(
                    LiveClass.scheduled_at >= window_start,
                    LiveClass.scheduled_at <= window_end
                ).all()
                for lc in upcoming:
                    exists = Notification.query.filter_by(
                        user_id=current_user.id, type='class_reminder', link='/calendario'
                    ).filter(Notification.message.contains(lc.title)).first()
                    if not exists:
                        notify(current_user.id, 'class_reminder',
                               f'🔔 "{lc.title}" empieza en menos de 24 horas', '/calendario')
            except Exception:
                pass
            db.session.commit()

def award_points(user_id, reason, ref_id, pts):
    if not PointEvent.query.filter_by(user_id=user_id, reason=reason, ref_id=ref_id).first():
        db.session.add(PointEvent(user_id=user_id, points=pts, reason=reason, ref_id=ref_id))
        db.session.commit()

# ── LEVEL SYSTEM ──────────────────────────────────────────────────────────────
_LEVELS = [
    # (threshold, name, emoji, color_hex)
    (0,       'Principiante', '🌱', '#6b7280'),
    (250,     'Aprendiz',     '⭐', '#d97706'),
    (750,     'Explorador',   '🔥', '#ea580c'),
    (2000,    'Comprometido', '💪', '#2563eb'),
    (5000,    'Avanzado',     '🚀', '#7c3aed'),
    (12000,   'Experto',      '💎', '#0891b2'),
    (30000,   'Élite',        '👑', '#b45309'),
    (75000,   'Maestro',      '⚡', '#dc2626'),
    (200000,  'Leyenda',      '🌟', '#db2777'),
    (500000,  'Inmortal',     '🏆', '#111827'),
]

def get_level(pts):
    """Return dict with level info for a given points total."""
    current = 0
    for i, (threshold, name, emoji, color) in enumerate(_LEVELS):
        if pts >= threshold:
            current = i
        else:
            break
    level_num   = current + 1
    _, name, emoji, color = _LEVELS[current]
    next_thresh = _LEVELS[current + 1][0] if current + 1 < len(_LEVELS) else None
    prev_thresh = _LEVELS[current][0]
    if next_thresh is not None:
        span = next_thresh - prev_thresh
        progress = min(100, round((pts - prev_thresh) * 100 / span))
        pts_to_next = next_thresh - pts
    else:
        progress    = 100
        pts_to_next = 0
    return {
        'num':        level_num,
        'name':       name,
        'emoji':      emoji,
        'color':      color,
        'progress':   progress,
        'pts_to_next': pts_to_next,
        'next_thresh': next_thresh,
        'is_max':     next_thresh is None,
    }

def get_leaderboard(since=None):
    # Una sola query GROUP BY + JOIN con User (evita N+1 y carga lazy)
    q = (db.session.query(User, db.func.sum(PointEvent.points).label('total'))
         .join(PointEvent, PointEvent.user_id == User.id))
    if since:
        q = q.filter(PointEvent.created_at >= since)
    rows = (q.group_by(User.id)
             .order_by(db.text('total DESC'))
             .limit(10)
             .all())
    return [(user, total or 0) for user, total in rows]

def display_academy_name(site=None):
    """Nombre visible de la academia (BD, .env o valor por defecto)."""
    if site is None:
        try:
            site = get_settings()
        except Exception:
            site = None
    n = ((site.academy_name if site else None) or '').strip()
    if n:
        return n
    return (app.config.get('ACADEMY_NAME') or '').strip() or 'Academia'


def get_settings():
    s = SiteSettings.query.first()
    env_name = (app.config.get('ACADEMY_NAME') or '').strip()
    if not s:
        s = SiteSettings(academy_name=env_name or 'Marca Atractora')
        db.session.add(s)
        db.session.commit()
    elif env_name and (not s.academy_name or s.academy_name.strip() in ('', 'Marca Atractora')):
        s.academy_name = env_name
        db.session.commit()
    elif not (s.academy_name or '').strip() and env_name:
        s.academy_name = env_name
        db.session.commit()
    return s

@app.context_processor
def inject_settings():
    from flask_wtf.csrf import generate_csrf
    try:
        site = get_settings()
    except Exception:
        site = None
    brand_style = ''
    if site:
        cp = getattr(site, 'color_primary', '') or '#7c3aed'
        cs = getattr(site, 'color_secondary', '') or '#6d28d9'
        ff = getattr(site, 'font_family', '') or ''
        brand_style = f'--color-primary:{cp};--color-secondary:{cs};'
        if ff:
            brand_style += f'--font-family-body:{ff};--font-family-display:{ff};'
    player_bar_style = _player_bar_style(site)
    ctx = {
        'site': site or SiteSettings(),
        'academy_name': display_academy_name(site),
        'csrf_token': generate_csrf,
        'spanish_provinces': SPANISH_PROVINCES,
        'city_other_value': CITY_OTHER_VALUE,
        'city_other_label': CITY_OTHER_LABEL,
        'brand_style': brand_style,
        'player_bar_style': player_bar_style,
        'video_embed_block': video_embed_block,
        'admin_nav': _admin_nav_counts(),
    }
    return ctx


def _admin_nav_counts():
    """Contadores para badges del menú admin (solo si hay sesión admin)."""
    from flask_login import current_user
    if not current_user.is_authenticated or not current_user.is_admin:
        return {'pending_reports': 0, 'whatsapp_pending': 0}
    try:
        return {
            'pending_reports': PostReport.query.filter_by(status='pending').count(),
            'whatsapp_pending': User.query.filter_by(whatsapp_vip_pending=True).count(),
        }
    except Exception:
        return {'pending_reports': 0, 'whatsapp_pending': 0}

app.register_blueprint(features_bp)
app.register_blueprint(library_bp)
app.register_blueprint(resources_bp)
register_bulk_email_routes(app, mail, get_settings)

# ── Auth helpers ──────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ── AUTH ──────────────────────────────────────────────────────────────────────

def _conversion_landing_context():
    site = get_settings()
    region = detect_billing_region('es')
    plans = SubscriptionPlan.query.filter_by(is_active=True).order_by(
        SubscriptionPlan.sort_order, SubscriptionPlan.id
    ).all()
    return dict(
        site=site,
        plans=plans,
        region=region,
        region_label=billing_region_label(region),
        stripe_pk=get_stripe_public(app),
        landing_text=landing_text,
        landing_paragraphs=landing_paragraphs,
        landing_lines=landing_lines,
    )


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('15 per minute')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('start_here'))
    ctx = _conversion_landing_context()
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        user  = User.query.filter_by(email=email).first()
        if user and user.check_password(pw):
            if user.role == 'admin' and getattr(user, 'status', 'active') != 'active':
                user.status = 'active'
                db.session.commit()
            if getattr(user, 'status', 'active') == 'pending':
                flash('Tu cuenta está pendiente de aprobación por un administrador. Te avisaremos pronto.', 'error')
                return render_template('public/conversion_landing.html', **ctx)
            if getattr(user, 'status', 'active') == 'rejected':
                flash('Tu solicitud de acceso ha sido denegada. Contacta con el administrador.', 'error')
                return render_template('public/conversion_landing.html', **ctx)
            if getattr(user, 'status', 'active') == 'suspended':
                flash('Tu cuenta está suspendida por impago o revisión administrativa. Contacta con soporte.', 'error')
                return render_template('public/conversion_landing.html', **ctx)
            allowed, msg = user_platform_access(user, payments_on=payments_enabled(app))
            if not allowed:
                flash(msg, 'error')
                return render_template('public/conversion_landing.html', **ctx)
            login_user(user, remember=True)
            return redirect(request.args.get('next') or url_for('start_here'))
        flash('Email o contraseña incorrectos.', 'error')
    return render_template('public/conversion_landing.html', **ctx)

def _finalize_registration_payment(user, plan, session_obj=None):
    """Tras pago Stripe: activar o dejar pendiente y enviar emails."""
    if user.subscription_status == 'active' and user.status in ('active', 'pending'):
        if session_obj and not isinstance(session_obj, dict):
            pass  # puede re-sincronizar periodo
        elif user.subscription_last_paid_at:
            return
    s = get_settings()
    auto = bool(s.pay_auto_activate) if s else True
    user.subscription_plan_id = plan.id if plan else user.subscription_plan_id
    user.subscription_status = 'active'
    user.subscription_last_paid_at = datetime.utcnow()

    if session_obj:
        try:
            import stripe
            stripe.api_key = get_stripe_secret(app)
            cust = session_obj.get('customer') if isinstance(session_obj, dict) else getattr(session_obj, 'customer', None)
            if cust:
                user.stripe_customer_id = cust
            sub_ref = session_obj.get('subscription') if isinstance(session_obj, dict) else getattr(session_obj, 'subscription', None)
            if sub_ref:
                sub_id = sub_ref if isinstance(sub_ref, str) else sub_ref.id
                sub = stripe.Subscription.retrieve(sub_id)
                sync_stripe_subscription(app, user, sub)
        except Exception as e:
            print(f'[billing] Error sync subscription: {e}')

    status_label = 'Activo (pago confirmado)' if auto else 'Pendiente de aprobación admin (pago confirmado)'
    if auto:
        user.status = 'active'
    else:
        user.status = 'pending'

    db.session.commit()
    plan_name = plan.name if plan else '—'
    login_url = url_for('login', _external=True)
    try:
        send_welcome_email(app, mail, user, plan_name, login_url, pending_approval=not auto)
    except Exception as e:
        print(f'[billing] welcome email: {e}')
    try:
        send_admin_registration_email(app, mail, user, plan_name, status_label, plan=plan)
    except Exception as e:
        print(f'[billing] admin reg email: {e}')
    for admin in User.query.filter_by(role='admin').all():
        notify(admin.id, 'new_user',
               f'🙋 Nuevo registro: {user.username} ({user.email}) — {status_label}',
               url_for('admin_subscriptions'))
    db.session.commit()


@app.route('/registro', methods=['GET', 'POST'])
@limiter.limit('10 per hour')
def register():
    return redirect(url_for('login'))


@app.route('/registro/exito')
def register_checkout_success():
    return redirect(url_for('checkout_success', session_id=request.args.get('session_id', '')))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/notificaciones/datos')
@login_required
def notifications_data():
    notifs = Notification.query.filter_by(user_id=current_user.id)\
        .order_by(Notification.created_at.desc()).limit(20).all()
    unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({
        'unread': unread,
        'items': [{'id': n.id, 'message': n.message, 'link': n.link,
                   'is_read': n.is_read, 'created_at': n.created_at.strftime('%d %b %H:%M')}
                  for n in notifs]
    })

@app.route('/notificaciones/leer', methods=['POST'])
@login_required
def notifications_read():
    nid = request.json.get('id')
    if nid:
        n = Notification.query.filter_by(id=nid, user_id=current_user.id).first()
        if n: n.is_read = True
    else:
        Notification.query.filter_by(user_id=current_user.id, is_read=False)\
            .update({'is_read': True})
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/cuenta', methods=['GET', 'POST'])
@login_required
def account_settings():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'profile':
            new_username = request.form.get('username', '').strip()
            new_email    = request.form.get('email', '').strip()
            new_bio      = request.form.get('bio', '').strip()
            new_city = parse_city_from_form(request.form)
            if new_username and new_username != current_user.username:
                if User.query.filter_by(username=new_username).first():
                    flash('Ese nombre de usuario ya está en uso.', 'error')
                    return redirect(url_for('account_settings'))
                current_user.username = new_username
            if new_email and new_email != current_user.email:
                if User.query.filter_by(email=new_email).first():
                    flash('Ese email ya está en uso.', 'error')
                    return redirect(url_for('account_settings'))
                current_user.email = new_email
            current_user.bio = new_bio
            current_user.city = new_city
            db.session.commit()
            flash('Perfil actualizado.', 'success')

        elif action == 'avatar':
            file = request.files.get('avatar')
            if file and file.filename:
                raw = file.read()
                if len(raw) > 4 * 1024 * 1024:
                    flash('La imagen no puede superar 4 MB.', 'error')
                    return redirect(url_for('account_settings'))
                current_user.avatar_data, current_user.avatar_mime = _compress_image(
                    raw, max_w=300, max_h=300, quality=82, square=True)
                db.session.commit()
                flash('Foto de perfil actualizada.', 'success')

        elif action == 'password':
            current_pw = request.form.get('current_password', '')
            new_pw     = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')
            if not current_user.check_password(current_pw):
                flash('La contraseña actual no es correcta.', 'error')
                return redirect(url_for('account_settings'))
            if len(new_pw) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres.', 'error')
                return redirect(url_for('account_settings'))
            if new_pw != confirm_pw:
                flash('Las contraseñas no coinciden.', 'error')
                return redirect(url_for('account_settings'))
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Contraseña actualizada.', 'success')

        return redirect(url_for('account_settings'))
    return render_template(
        'account_settings.html',
        city_state=city_form_state(current_user.city),
    )

# ── LANDING PÚBLICA Y CHECKOUT ─────────────────────────────────────────────────

@app.route('/')
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('start_here'))
    return redirect(url_for('login'))


@app.route('/checkout/iniciar', methods=['POST'])
@csrf.exempt
@limiter.limit('20 per hour')
def checkout_start():
    plan_id = request.form.get('plan_id', type=int)
    region = detect_billing_region('es')
    plan = SubscriptionPlan.query.filter_by(id=plan_id, is_active=True).first()
    if not plan or not payments_enabled(app):
        flash('Plan no disponible o pagos no configurados.', 'error')
        return redirect(url_for('login'))
    intent = CheckoutIntent(plan_id=plan.id, billing_region=region, status='pending')
    db.session.add(intent)
    db.session.commit()
    try:
        session = create_public_subscription_checkout(
            app, plan, region,
            success_url=url_for('checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('login', _external=True),
            checkout_intent_id=intent.id,
        )
        return redirect(session.url)
    except Exception as e:
        db.session.delete(intent)
        db.session.commit()
        flash(f'No se pudo iniciar el pago: {e}', 'error')
        return redirect(url_for('login'))


@app.route('/checkout/exito')
def checkout_success():
    course_id  = request.args.get('course_id', type=int)
    session_id = request.args.get('session_id', '')
    if course_id:
        if not current_user.is_authenticated:
            flash('Inicia sesión para completar la inscripción al curso.', 'error')
            return redirect(url_for('login'))
        if not session_id:
            flash('Enlace de pago no válido.', 'error')
            return redirect(url_for('courses'))
        stripe_key = get_stripe_secret(app)
        payment_ok = False
        if stripe_key and session_id.startswith('cs_'):
            try:
                import stripe
                stripe.api_key = stripe_key
                s = stripe.checkout.Session.retrieve(session_id)
                payment_ok = (s.payment_status == 'paid')
            except Exception:
                payment_ok = False
        if payment_ok and not current_user.is_enrolled(course_id):
            db.session.add(Enrollment(user_id=current_user.id,
                                      course_id=course_id,
                                      stripe_session_id=session_id))
            db.session.commit()
            flash('¡Pago completado! Ya tienes acceso al curso.', 'success')
        elif current_user.is_enrolled(course_id):
            flash('Ya estás inscrito en este curso.', 'success')
        else:
            flash('No se pudo verificar el pago. Contacta con el administrador.', 'error')
        return redirect(url_for('learn', course_id=course_id))

    if not session_id or not session_id.startswith('cs_'):
        flash('Sesión de pago no válida.', 'error')
        return redirect(url_for('login'))
    try:
        import stripe
        stripe.api_key = get_stripe_secret(app)
        sess = stripe.checkout.Session.retrieve(session_id, expand=['subscription'])
        if sess.payment_status != 'paid':
            flash('El pago no se ha completado.', 'error')
            return redirect(url_for('login'))
        user, created, _pw = create_user_from_checkout(app, mail, sess, get_settings)
        if not user:
            flash('No se pudo crear la cuenta. Contacta con soporte.', 'error')
            return redirect(url_for('login'))
        login_user(user, remember=True)
        flash('¡Bienvenida! Revisa tu email con los datos de acceso.', 'success')
        return redirect(url_for('start_here'))
    except Exception as e:
        flash(f'Error al verificar el pago: {e}', 'error')
        return redirect(url_for('login'))


@app.route('/empieza')
@login_required
def start_here():
    site = get_settings()
    return render_template('start/index.html', site=site)


@app.route('/webhooks/grabacion', methods=['POST'])
@csrf.exempt
def webhook_recording():
    secret = (app.config.get('RECORDING_WEBHOOK_SECRET') or '').strip()
    if not secret or request.headers.get('X-Webhook-Secret') != secret:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    from blueprints.library import upsert_recording_from_webhook
    item = upsert_recording_from_webhook(
        live_class_id=data.get('live_class_id'),
        recording_url=data.get('recording_url') or data.get('url', ''),
        title=data.get('title'),
        year=data.get('year'),
        month=data.get('month'),
    )
    if not item:
        return jsonify({'error': 'Invalid payload'}), 400
    return jsonify({'ok': True, 'library_item_id': item.id}), 200


# ── COMMUNITY ─────────────────────────────────────────────────────────────────

@app.route('/comunidad')
@login_required
def community():
    cat_id = request.args.get('cat', type=int)
    q = Post.query.filter_by(is_hidden=False).order_by(Post.pinned.desc(), Post.created_at.desc())
    if cat_id:
        q = q.filter_by(category_id=cat_id)
    posts      = q.limit(50).all()
    categories = [c for c in Category.query.all() if user_can_access_category(current_user, c)]
    five_min_ago  = datetime.utcnow() - timedelta(minutes=5)
    month_start   = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    member_count  = User.query.count()
    admin_count   = User.query.filter_by(role='admin').count()
    admins        = User.query.filter_by(role='admin').limit(5).all()
    online_users  = User.query.filter(User.last_seen >= five_min_ago).order_by(User.last_seen.desc()).limit(20).all()
    site = get_settings()
    member_of_month = None
    if site and site.member_of_month_user_id:
        member_of_month = User.query.get(site.member_of_month_user_id)
    now = datetime.utcnow()
    # Clase en directo ahora mismo (empezó hace menos de duration_min)
    from sqlalchemy import and_
    live_now = (LiveClass.query
                .filter(LiveClass.scheduled_at <= now)
                .all())
    live_now = next((lc for lc in live_now
                     if (now - lc.scheduled_at).total_seconds() / 60 < lc.duration_min), None)
    # Próxima clase
    next_class = (LiveClass.query
                  .filter(LiveClass.scheduled_at > now)
                  .order_by(LiveClass.scheduled_at.asc())
                  .first())
    return render_template('community/feed.html',
                           posts=posts, categories=categories, active_cat=cat_id,
                           member_count=member_count, admin_count=admin_count,
                           admins=admins, online_users=online_users,
                           member_of_month=member_of_month, member_of_month_note=site.member_of_month_note if site else '',
                           live_now=live_now, next_class=next_class, site=site)

@app.route('/comunidad/nuevo', methods=['GET', 'POST'])
@login_required
def new_post():
    categories = Category.query.all()
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        content     = request.form.get('content', '').strip()
        category_id = request.form.get('category_id', type=int)
        if not title or not content:
            flash('Título y contenido son obligatorios.', 'error')
        else:
            cat = Category.query.get(category_id) if category_id else None
            if cat and not user_can_access_category(current_user, cat):
                flash('No tienes acceso a esta categoría con tu plan actual.', 'error')
                return render_template('community/new_post.html', categories=categories)
            post = Post(user_id=current_user.id, title=title,
                        content=content, category_id=category_id)
            if cat and getattr(cat, 'slug', None) == 'preguntas-rocio':
                post.workflow_status = 'pendiente'
            db.session.add(post)
            db.session.commit()
            award_points(current_user.id, 'post', post.id, 4)
            if cat and getattr(cat, 'slug', None) == 'preguntas-rocio':
                notify_n8n_pregunta(app, post, current_user)
            return redirect(url_for('community'))
    return render_template('community/new_post.html', categories=categories)

@app.route('/comunidad/post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    if post.is_hidden and not current_user.is_admin and post.user_id != current_user.id:
        abort(404)
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if content:
            c = Comment(post_id=post_id, user_id=current_user.id, content=content)
            db.session.add(c)
            db.session.commit()
        return redirect(url_for('post_detail', post_id=post_id) + '#comments')
    return render_template('community/post.html', post=post)

@app.route('/comunidad/post/<int:post_id>/comentar', methods=['POST'])
@login_required
def add_comment_ajax(post_id):
    post    = Post.query.get_or_404(post_id)
    content = request.json.get('content', '').strip() if request.is_json else request.form.get('content', '').strip()
    if not content:
        return jsonify({'ok': False}), 400
    c = Comment(post_id=post_id, user_id=current_user.id, content=content)
    db.session.add(c)
    db.session.commit()
    award_points(current_user.id, 'comment', c.id, 2)
    if post.user_id != current_user.id:
        notify(post.user_id, 'comment',
               f'💬 {current_user.username} comentó en tu post "{post.title[:50]}"',
               f'/comunidad')
        db.session.commit()
    return jsonify({'ok': True, 'comment_id': c.id,
                    'username': current_user.username,
                    'initials': current_user.initials, 'content': content,
                    'timeago': 'ahora mismo',
                    'has_avatar': bool(current_user.avatar_data),
                    'user_id': current_user.id})

@app.route('/comunidad/post/<int:post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    if current_user in post.likes:
        post.likes.remove(current_user)
        liked = False
    else:
        post.likes.append(current_user)
        liked = True
        award_points(current_user.id, 'like', post.id, 1)
    db.session.commit()
    return jsonify({'likes': len(post.likes), 'liked': liked})

@app.route('/comunidad/post/<int:post_id>/pin', methods=['POST'])
@login_required
@admin_required
def pin_post(post_id):
    post = Post.query.get_or_404(post_id)
    post.pinned = not post.pinned
    db.session.commit()
    return redirect(url_for('post_detail', post_id=post_id))

@app.route('/comunidad/post/<int:post_id>/borrar', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not current_user.is_admin and post.user_id != current_user.id:
        abort(403)
    db.session.delete(post)
    db.session.commit()
    if request.is_json:
        return jsonify({'ok': True})
    return redirect(url_for('community'))

@app.route('/comunidad/post/<int:post_id>/editar', methods=['POST'])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not current_user.is_admin and post.user_id != current_user.id:
        abort(403)
    data = request.json if request.is_json else request.form
    title       = (data.get('title', '') or '').strip()
    content     = (data.get('content', '') or '').strip()
    category_id = data.get('category_id', None)
    if category_id:
        try:
            category_id = int(category_id)
        except (ValueError, TypeError):
            category_id = None
    if title and content:
        post.title       = title
        post.content     = content
        post.category_id = category_id or None
        db.session.commit()
    if request.is_json:
        return jsonify({'ok': True, 'title': post.title, 'content': post.content})
    return redirect(url_for('community'))

@app.route('/comunidad/comentario/<int:comment_id>/borrar', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if not current_user.is_admin and comment.user_id != current_user.id:
        abort(403)
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/comunidad/comentario/<int:comment_id>/editar', methods=['POST'])
@login_required
def edit_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if not current_user.is_admin and comment.user_id != current_user.id:
        abort(403)
    content = (request.json.get('content', '') if request.is_json else request.form.get('content', '')).strip()
    if content:
        comment.content = content
        db.session.commit()
    return jsonify({'ok': True, 'content': comment.content})

@app.route('/comunidad/comentario/<int:comment_id>/like', methods=['POST'])
@login_required
def like_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if current_user in comment.likes:
        comment.likes.remove(current_user)
        liked = False
    else:
        comment.likes.append(current_user)
        liked = True
    db.session.commit()
    return jsonify({'likes': len(comment.likes), 'liked': liked})

# ── COURSES ───────────────────────────────────────────────────────────────────

@app.route('/formaciones')
@login_required
def formaciones_redirect():
    return redirect(url_for('courses'))


@app.route('/cursos')
@login_required
def courses():
    all_courses  = Course.query.filter_by(is_published=True).order_by(Course.order.asc(), Course.created_at.asc()).all()
    enrolled_ids = {e.course_id for e in current_user.enrollments}
    # Progreso por curso
    completed_ids = {lp.lesson_id for lp in LessonProgress.query.filter_by(user_id=current_user.id).all()}
    progress = {}
    for c in all_courses:
        total = c.lesson_count
        if total == 0:
            progress[c.id] = 0
        else:
            done = sum(1 for s in c.sections for l in s.lessons if l.id in completed_ids)
            progress[c.id] = round(done * 100 / total)
    return render_template('courses/catalog.html',
                           courses=all_courses, enrolled_ids=enrolled_ids, progress=progress)

@app.route('/cursos/<int:course_id>')
@login_required
def course_detail(course_id):
    course   = Course.query.get_or_404(course_id)
    if not course.is_published and not current_user.is_admin:
        abort(404)
    enrolled = current_user.is_enrolled(course_id)
    return render_template('courses/detail.html', course=course, enrolled=enrolled)

@app.route('/cursos/<int:course_id>/inscribir', methods=['POST'])
@login_required
def enroll_free(course_id):
    course = Course.query.get_or_404(course_id)
    if not course.is_free:
        return redirect(url_for('checkout', course_id=course_id))
    if not current_user.is_enrolled(course_id):
        db.session.add(Enrollment(user_id=current_user.id, course_id=course_id))
        db.session.commit()
        flash('¡Inscrito correctamente!', 'success')
    return redirect(url_for('learn', course_id=course_id))

@app.route('/cursos/<int:course_id>/aprender')
@login_required
def learn(course_id):
    course = Course.query.get_or_404(course_id)
    if not current_user.is_enrolled(course_id) and not current_user.is_admin:
        if course.is_free:
            db.session.add(Enrollment(user_id=current_user.id, course_id=course_id))
            db.session.commit()
        else:
            return redirect(url_for('course_detail', course_id=course_id))

    lesson_id      = request.args.get('leccion', type=int)
    lessons_ordered = ordered_lessons(course)
    completed_ids = completed_lesson_ids(current_user.id)
    unlock_map = {}
    for les in lessons_ordered:
        ok, _msg = is_lesson_unlocked(current_user, les, course, completed_ids)
        unlock_map[les.id] = ok

    current_lesson = Lesson.query.get(lesson_id) if lesson_id else None
    if current_lesson and not unlock_map.get(current_lesson.id, True):
        flash('Esta lección aún no está disponible.', 'error')
        current_lesson = None
    if not current_lesson:
        for les in lessons_ordered:
            if unlock_map.get(les.id, True):
                current_lesson = les
                break

    prog = course_progress(current_user.id, course)
    cert = CourseCertificate.query.filter_by(
        user_id=current_user.id, course_id=course.id).first()
    section_quizzes = {s.id: Quiz.query.filter_by(section_id=s.id).first() for s in course.sections}
    section_assignments = {s.id: Assignment.query.filter_by(section_id=s.id).first() for s in course.sections}
    return render_template('courses/learn.html',
                           course=course,
                           current_lesson=current_lesson,
                           completed_ids=completed_ids,
                           unlock_map=unlock_map,
                           progress=prog,
                           certificate=cert,
                           section_quizzes=section_quizzes,
                           section_assignments=section_assignments)

@app.route('/cursos/<int:course_id>/completar/<int:lesson_id>', methods=['POST'])
@login_required
def complete_lesson(course_id, lesson_id):
    course = Course.query.get_or_404(course_id)
    lesson = Lesson.query.get_or_404(lesson_id)
    ok, msg = is_lesson_unlocked(current_user, lesson, course)
    if not ok:
        return jsonify({'ok': False, 'error': msg}), 403
    if not LessonProgress.query.filter_by(user_id=current_user.id, lesson_id=lesson_id).first():
        db.session.add(LessonProgress(user_id=current_user.id, lesson_id=lesson_id))
        db.session.commit()
        award_points(current_user.id, 'lesson', lesson_id, 3)
        issue_certificate(current_user, course)
    return jsonify({'ok': True})

# ── LEADERBOARD ───────────────────────────────────────────────────────────────

@app.route('/clasificacion')
@login_required
def leaderboard():
    return redirect(url_for('community'))

# ── CALENDAR ──────────────────────────────────────────────────────────────────

@app.route('/calendario')
@login_required
def calendar():
    upcoming = (LiveClass.query
                .filter(LiveClass.scheduled_at >= datetime.utcnow())
                .order_by(LiveClass.scheduled_at)
                .limit(8).all())
    categories = LiveClassCategory.query.order_by(
        LiveClassCategory.sort_order, LiveClassCategory.name
    ).all()
    now = datetime.utcnow()
    month_theme = CalendarMonthTheme.query.filter_by(year=now.year, month=now.month).first()
    return render_template('calendar/index.html', upcoming=upcoming, categories=categories,
                           month_theme=month_theme)

@app.route('/calendario/data')
@login_required
def calendar_data():
    classes = LiveClass.query.all()
    events  = []
    for c in classes:
        end = c.scheduled_at + timedelta(minutes=c.duration_min) if c.duration_min else None
        cat = c.category
        bg, border = category_event_colors(cat)
        prefix = ''
        if c.recurrence != 'none':
            prefix = '🔁 '
        elif cat and cat.emoji:
            prefix = cat.emoji + ' '
        events.append({
            'id':    c.id,
            'title': prefix + c.title,
            'start': c.scheduled_at.isoformat(),
            'end':   end.isoformat() if end else None,
            'extendedProps': {
                'description': c.description,
                'meet_url':    c.meet_url,
                'instructor':  c.instructor,
                'duration':    c.duration_min,
                'category':    cat.name if cat else '',
                'category_color': cat.color if cat else '',
                'subtopic':    getattr(c, 'subtopic', '') or '',
            },
            'backgroundColor': bg,
            'borderColor':     border,
        })
    return jsonify(events)

# ── PAYMENTS ──────────────────────────────────────────────────────────────────

@app.route('/checkout/<int:course_id>', methods=['POST'])
@login_required
def checkout(course_id):
    course = Course.query.get_or_404(course_id)
    if current_user.is_enrolled(course_id):
        return redirect(url_for('learn', course_id=course_id))

    stripe_key = app.config.get('STRIPE_SECRET_KEY', '')
    if not stripe_key:
        flash('Los pagos no están configurados aún. Contacta al administrador.', 'error')
        return redirect(url_for('course_detail', course_id=course_id))

    try:
        import stripe
        stripe.api_key = stripe_key
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': course.title, 'description': course.subtitle},
                    'unit_amount': int(course.price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('checkout_success', _external=True)
                        + f'?course_id={course_id}&session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=url_for('course_detail', course_id=course_id, _external=True),
        )
        return redirect(session.url)
    except Exception as e:
        flash(f'Error al procesar el pago: {e}', 'error')
        return redirect(url_for('course_detail', course_id=course_id))

@app.route('/webhooks/stripe', methods=['POST'])
@csrf.exempt
@limiter.limit('120 per minute')
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature', '')
    wh_secret = get_stripe_webhook_secret(app)
    if not wh_secret:
        return jsonify({'error': 'Webhook secret not configured'}), 503
    try:
        import stripe
        stripe.api_key = get_stripe_secret(app)
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    process_stripe_webhook_event(
        app, db, mail, event, notify,
        finalize_registration_fn=_finalize_registration_payment,
        create_user_from_checkout_fn=create_user_from_checkout,
        get_settings_fn=get_settings,
    )

    return jsonify({'ok': True}), 200


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    stats = {
        'users':       User.query.count(),
        'courses':     Course.query.count(),
        'posts':       Post.query.count(),
        'enrollments': Enrollment.query.count(),
        'library':     LibraryItem.query.count(),
        'resources':   Resource.query.count(),
        'active_subs': User.query.filter_by(subscription_status='active', role='student').count(),
    }
    categories = Category.query.all()
    all_plans = SubscriptionPlan.query.filter_by(is_active=True).order_by(SubscriptionPlan.sort_order).all()
    return render_template('admin/dashboard.html', stats=stats, categories=categories, all_plans=all_plans)

def _compress_image(file_storage, max_w=1200, max_h=1200, quality=82, square=False):
    """
    Lee un FileStorage (o bytes), redimensiona y comprime con Pillow.
    Devuelve (bytes_comprimidos, 'image/jpeg').
    Si Pillow no está disponible devuelve los bytes originales sin tocar.
    """
    raw = file_storage.read() if hasattr(file_storage, 'read') else file_storage
    if not _PILLOW_OK:
        return raw, 'image/jpeg'
    try:
        img = PILImage.open(io.BytesIO(raw))
        # Convertir modos especiales a RGB
        if img.mode in ('RGBA', 'P', 'LA'):
            bg = PILImage.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        if square:
            # Recortar al cuadrado centrado
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top  = (h - side) // 2
            img  = img.crop((left, top, left + side, top + side))
            img  = img.resize((min(side, max_w), min(side, max_w)), PILImage.LANCZOS)
        else:
            img.thumbnail((max_w, max_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True, progressive=True)
        return buf.getvalue(), 'image/jpeg'
    except Exception:
        return raw, 'image/jpeg'

def _cached_image(data, mimetype, max_age=86400):
    """Devuelve una respuesta con cabeceras de caché para imágenes binarias."""
    resp = make_response(send_file(io.BytesIO(data), mimetype=mimetype))
    resp.headers['Cache-Control'] = f'public, max-age={max_age}, immutable'
    return resp


def _is_true(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _hex_color(value, default=''):
    """Normaliza color #RGB o #RRGGBB; devuelve default si no es válido."""
    import re
    if not value:
        return default
    v = value.strip()
    m = re.match(r'^#([0-9a-fA-F]{3})$', v)
    if m:
        h = m.group(1)
        return '#' + ''.join(c * 2 for c in h).lower()
    m = re.match(r'^#([0-9a-fA-F]{6})$', v)
    if m:
        return '#' + m.group(1).lower()
    return default


def _player_bar_style(site):
    if not site:
        return ''
    bg = _hex_color(getattr(site, 'player_bar_bg', None), '#141414')
    accent = _hex_color(getattr(site, 'player_bar_accent', None), '') or _hex_color(
        getattr(site, 'color_primary', None), '#7c3aed'
    )
    text = _hex_color(getattr(site, 'player_bar_text', None), '#bfbfbf')
    btn = _hex_color(getattr(site, 'player_bar_btn', None), '#2a2a2a')
    return f'--lib-player-bg:{bg};--lib-player-accent:{accent};--lib-player-text:{text};--lib-player-btn:{btn};'


def _run_backup_now(settings: SiteSettings):
    secret_key = app.config.get('SECRET_KEY', '')
    payload = {
        'backup_local_path': settings.backup_local_path,
        'backup_retention_days': settings.backup_retention_days,
        'backup_s3_enabled': settings.backup_s3_enabled,
        'backup_s3_bucket': settings.backup_s3_bucket,
        'backup_s3_region': settings.backup_s3_region,
        'backup_s3_prefix': settings.backup_s3_prefix,
        'backup_s3_endpoint_url': settings.backup_s3_endpoint_url,
        'backup_s3_access_key': decrypt_value(settings.backup_s3_access_key_enc, secret_key),
        'backup_s3_secret_key': decrypt_value(settings.backup_s3_secret_key_enc, secret_key),
    }
    result = run_backup(payload, settings.academy_name or 'miacademia', app.config['SQLALCHEMY_DATABASE_URI'])
    settings.backup_last_run_at = datetime.utcnow()
    settings.backup_last_status = 'ok'
    settings.backup_last_error = ''
    db.session.commit()
    return result

@app.route('/avatar/<int:user_id>')
def serve_avatar(user_id):
    user = User.query.get_or_404(user_id)
    if user.avatar_data:
        return _cached_image(user.avatar_data, user.avatar_mime)
    abort(404)

@app.route('/curso/<int:course_id>/portada')
def serve_course_cover(course_id):
    course = Course.query.get_or_404(course_id)
    if course.cover_data:
        return _cached_image(course.cover_data, course.cover_mime)
    abort(404)

@app.route('/comunidad/banner')
def serve_banner():
    s = get_settings()
    if s.community_image_data:
        return _cached_image(s.community_image_data, s.community_image_mime)
    abort(404)

@app.route('/admin/ajustes', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    s = get_settings()
    if request.method == 'POST':
        s.academy_name          = request.form.get('academy_name', s.academy_name).strip()
        s.community_description = request.form.get('community_description', '').strip()
        s.link_url              = request.form.get('link_url', '').strip()
        s.link_text             = request.form.get('link_text', '').strip()
        s.welcome_email_subject = request.form.get('welcome_email_subject', '').strip()
        s.welcome_email_body = request.form.get('welcome_email_body', '').strip()
        s.admin_reg_email_subject = request.form.get('admin_reg_email_subject', '').strip()
        s.admin_reg_email_body = request.form.get('admin_reg_email_body', '').strip()
        s.event_reminder_email_subject = request.form.get('event_reminder_email_subject', '').strip()
        s.event_reminder_email_body = request.form.get('event_reminder_email_body', '').strip()
        s.event_reminder_24h_enabled = request.form.get('event_reminder_24h_enabled') == 'on'
        s.event_reminder_1h_enabled = request.form.get('event_reminder_1h_enabled') == 'on'
        s.billing_alert_email_subject = request.form.get('billing_alert_email_subject', '').strip()
        s.billing_alert_email_body = request.form.get('billing_alert_email_body', '').strip()
        s.welcome_video_url = request.form.get('welcome_video_url', '').strip()
        s.how_it_works_video_url = request.form.get('how_it_works_video_url', '').strip()
        s.start_page_intro = request.form.get('start_page_intro', '').strip()
        s.whatsapp_url = request.form.get('whatsapp_url', '').strip()
        s.brand_logo_url = request.form.get('brand_logo_url', '').strip()
        s.color_primary = _hex_color(request.form.get('color_primary'), s.color_primary or '#7c3aed')
        s.color_secondary = _hex_color(request.form.get('color_secondary'), s.color_secondary or '#6d28d9')
        s.font_family = request.form.get('font_family', '').strip()
        s.player_bar_bg = _hex_color(request.form.get('player_bar_bg'), '#141414')
        s.player_bar_accent = _hex_color(request.form.get('player_bar_accent'), '')
        s.player_bar_text = _hex_color(request.form.get('player_bar_text'), '#bfbfbf')
        s.player_bar_btn = _hex_color(request.form.get('player_bar_btn'), '#2a2a2a')
        mom_uid = request.form.get('member_of_month_user_id', type=int)
        s.member_of_month_user_id = mom_uid or None
        s.member_of_month_note = request.form.get('member_of_month_note', '').strip()
        s.member_of_month_month = request.form.get('member_of_month_month', '').strip()
        img = request.files.get('community_image_file')
        if img and img.filename:
            s.community_image_data, s.community_image_mime = _compress_image(
                img, max_w=1200, max_h=600, quality=82)
            s.community_image      = ''
        db.session.commit()
        flash('Ajustes guardados.', 'success')
        return redirect(url_for('admin_settings'))
    from billing import (
        default_welcome_subject, default_welcome_body,
        default_admin_reg_subject, default_admin_reg_body,
        default_event_reminder_subject, default_event_reminder_body,
        default_billing_alert_subject, default_billing_alert_body,
    )
    active_users = User.query.filter_by(status='active', role='student').order_by(User.username).all()
    return render_template(
        'admin/settings.html', s=s,
        default_welcome_subject=default_welcome_subject(),
        default_welcome_body=default_welcome_body(),
        default_admin_subject=default_admin_reg_subject(),
        default_admin_body=default_admin_reg_body(),
        default_event_reminder_subject=default_event_reminder_subject(),
        default_event_reminder_body=default_event_reminder_body(),
        default_billing_alert_subject=default_billing_alert_subject(),
        default_billing_alert_body=default_billing_alert_body(),
        active_users=active_users,
    )


@app.route('/admin/landing', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_landing():
    s = get_settings()
    if request.method == 'POST':
        if request.form.get('action') == 'reset':
            for field in LANDING_FORM_FIELDS:
                if hasattr(s, field):
                    setattr(s, field, LANDING_DEFAULTS.get(field, ''))
            flash('Textos restaurados al contenido original del PDF.', 'success')
        else:
            for field in LANDING_FORM_FIELDS:
                if hasattr(s, field):
                    setattr(s, field, request.form.get(field, '').strip())
            flash('Landing principal guardada.', 'success')
        db.session.commit()
        return redirect(url_for('admin_landing'))
    return render_template(
        'admin/landing.html', s=s, defaults=LANDING_DEFAULTS, fields=LANDING_FORM_FIELDS,
    )


@app.route('/admin/backups', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_backups():
    s = get_settings()
    has_s3_access = bool(s.backup_s3_access_key_enc)
    has_s3_secret = bool(s.backup_s3_secret_key_enc)
    if request.method == 'POST':
        s.backup_enabled        = _is_true(request.form.get('backup_enabled'))
        s.backup_interval_hours = max(1, int(request.form.get('backup_interval_hours', s.backup_interval_hours or 24)))
        s.backup_retention_days = max(1, int(request.form.get('backup_retention_days', s.backup_retention_days or 14)))
        s.backup_local_path     = request.form.get('backup_local_path', s.backup_local_path or '/app/backups').strip() or '/app/backups'
        s.backup_s3_enabled     = _is_true(request.form.get('backup_s3_enabled'))
        s.backup_s3_bucket      = request.form.get('backup_s3_bucket', '').strip()
        s.backup_s3_region      = request.form.get('backup_s3_region', 'eu-west-1').strip() or 'eu-west-1'
        s.backup_s3_prefix      = request.form.get('backup_s3_prefix', 'miacademia').strip() or 'miacademia'
        s.backup_s3_endpoint_url = request.form.get('backup_s3_endpoint_url', '').strip()
        secret_key = app.config.get('SECRET_KEY', '')
        s3_access = request.form.get('backup_s3_access_key', '').strip()
        s3_secret = request.form.get('backup_s3_secret_key', '').strip()
        if s3_access:
            s.backup_s3_access_key_enc = encrypt_value(s3_access, secret_key)
        if s3_secret:
            s.backup_s3_secret_key_enc = encrypt_value(s3_secret, secret_key)
        db.session.commit()
        flash('Configuración de backups guardada.', 'success')
        return redirect(url_for('admin_backups'))
    backup_path = s.backup_local_path or '/app/backups'
    backups = list_local_backups(backup_path)
    return render_template(
        'admin/backups.html', s=s,
        has_s3_access=has_s3_access, has_s3_secret=has_s3_secret,
        backups=backups,
    )


@app.route('/admin/backup/restaurar', methods=['POST'])
@login_required
@admin_required
def admin_restore_backup():
    s = get_settings()
    filename = request.form.get('backup_file', '').strip()
    if not filename:
        flash('Selecciona un backup para restaurar.', 'error')
        return redirect(url_for('admin_backups'))
    if not _is_true(request.form.get('confirm_restore')):
        flash('Debes confirmar que entiendes que se sobrescribirá la base de datos.', 'error')
        return redirect(url_for('admin_backups'))
    try:
        restore_backup(
            s.backup_local_path or '/app/backups',
            filename,
            app.config['SQLALCHEMY_DATABASE_URI'],
        )
        flash(f'Base de datos restaurada desde {filename}. Si la app se comporta raro, reinicia el contenedor.', 'success')
    except Exception as e:
        flash(f'Error al restaurar: {e}', 'error')
    return redirect(url_for('admin_backups'))


@app.route('/admin/backup/ejecutar', methods=['POST'])
@login_required
@admin_required
def admin_run_backup():
    s = get_settings()
    try:
        result = _run_backup_now(s)
        where = f" y subido a {s.backup_s3_bucket}/{result['s3_key']}" if result.get('s3_key') else ''
        flash(f"Backup creado: {result['file']}{where}", 'success')
    except Exception as e:
        s.backup_last_status = 'error'
        s.backup_last_error = str(e)[:2000]
        db.session.commit()
        flash(f'Error ejecutando backup: {e}', 'error')
    return redirect(url_for('admin_backups'))


@app.route('/admin/pagos', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_payments():
    s = get_settings()
    secret_key = app.config.get('SECRET_KEY', '')
    has_sk = bool(s.stripe_secret_key_enc)
    has_wh = bool(s.stripe_webhook_secret_enc)

    if request.method == 'POST':
        action = request.form.get('action', 'settings')
        if action == 'settings':
            s.payments_enabled = _is_true(request.form.get('payments_enabled'))
            s.pay_auto_activate = _is_true(request.form.get('pay_auto_activate'))
            s.stripe_public_key = request.form.get('stripe_public_key', '').strip()
            sk = request.form.get('stripe_secret_key', '').strip()
            wh = request.form.get('stripe_webhook_secret', '').strip()
            if sk:
                s.stripe_secret_key_enc = encrypt_value(sk, secret_key)
            if wh:
                s.stripe_webhook_secret_enc = encrypt_value(wh, secret_key)
            db.session.commit()
            flash('Configuración de pagos guardada.', 'success')
        return redirect(url_for('admin_payments'))

    return render_template(
        'admin/payments.html', s=s,
        has_sk=has_sk, has_wh=has_wh,
        webhook_url=url_for('stripe_webhook', _external=True),
    )


def _redirect_plans():
    return redirect(request.referrer or url_for('admin_plans'))


@app.route('/admin/planes', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_plans():
    if request.method == 'POST' and request.form.get('action') == 'add_plan':
        name = request.form.get('plan_name', '').strip()
        desc = request.form.get('plan_description', '').strip()
        price = request.form.get('plan_price', '0').replace(',', '.')
        try:
            price_f = float(price)
        except ValueError:
            price_f = 0.0
        stripe_price = request.form.get('plan_stripe_price_id', '').strip()
        price_es = request.form.get('plan_price_es', '0').replace(',', '.')
        price_intl = request.form.get('plan_price_intl', '0').replace(',', '.')
        try:
            price_es_f = float(price_es)
        except ValueError:
            price_es_f = price_f
        try:
            price_intl_f = float(price_intl)
        except ValueError:
            price_intl_f = price_f
        price_y = request.form.get('plan_price_yearly', '0').replace(',', '.')
        try:
            price_y_f = float(price_y)
        except ValueError:
            price_y_f = 0.0
        trial = int(request.form.get('trial_days', 0) or 0)
        coupon = request.form.get('stripe_coupon_id', '').strip()
        if name:
            db.session.add(SubscriptionPlan(
                name=name, description=desc, price_monthly=price_f,
                price_monthly_es=price_es_f, price_monthly_intl=price_intl_f,
                price_yearly=price_y_f,
                stripe_price_id=stripe_price,
                stripe_price_id_es=request.form.get('plan_stripe_price_id_es', '').strip(),
                stripe_price_id_intl=request.form.get('plan_stripe_price_id_intl', '').strip(),
                stripe_price_id_yearly=request.form.get('plan_stripe_price_id_yearly', '').strip(),
                trial_days=trial, stripe_coupon_id=coupon,
                is_active=True,
                sort_order=SubscriptionPlan.query.count(),
            ))
            db.session.commit()
            flash(f'Plan "{name}" creado.', 'success')
        return redirect(url_for('admin_plans'))

    plans_raw = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order, SubscriptionPlan.id).all()
    plans = []
    for p in plans_raw:
        plans.append({
            'id': p.id, 'name': p.name, 'description': p.description,
            'price_monthly': p.price_monthly, 'price_monthly_es': p.price_for_region('es'),
            'price_monthly_intl': p.price_for_region('intl'),
            'price_yearly': getattr(p, 'price_yearly', 0) or 0,
            'stripe_price_id': p.stripe_price_id,
            'stripe_price_id_es': getattr(p, 'stripe_price_id_es', '') or '',
            'stripe_price_id_intl': getattr(p, 'stripe_price_id_intl', '') or '',
            'stripe_price_id_yearly': getattr(p, 'stripe_price_id_yearly', '') or '',
            'trial_days': getattr(p, 'trial_days', 0) or 0,
            'stripe_coupon_id': getattr(p, 'stripe_coupon_id', '') or '',
            'is_active': p.is_active,
            'users_count': User.query.filter_by(subscription_plan_id=p.id).count(),
        })
    return render_template('admin/plans.html', plans=plans)


@app.route('/admin/planes/<int:plan_id>/editar', methods=['POST'])
@login_required
@admin_required
def admin_edit_plan(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    plan.name = request.form.get('plan_name', plan.name).strip()
    plan.description = request.form.get('plan_description', '').strip()
    price = request.form.get('plan_price', '0').replace(',', '.')
    try:
        plan.price_monthly = float(price)
    except ValueError:
        flash('Precio no válido.', 'error')
        return _redirect_plans()
    plan.stripe_price_id = request.form.get('plan_stripe_price_id', '').strip()
    pes = request.form.get('plan_price_es', '0').replace(',', '.')
    pintl = request.form.get('plan_price_intl', '0').replace(',', '.')
    try:
        plan.price_monthly_es = float(pes)
    except ValueError:
        pass
    try:
        plan.price_monthly_intl = float(pintl)
    except ValueError:
        pass
    plan.stripe_price_id_es = request.form.get('plan_stripe_price_id_es', '').strip()
    plan.stripe_price_id_intl = request.form.get('plan_stripe_price_id_intl', '').strip()
    py = request.form.get('plan_price_yearly', '0').replace(',', '.')
    try:
        plan.price_yearly = float(py)
    except ValueError:
        pass
    plan.stripe_price_id_yearly = request.form.get('plan_stripe_price_id_yearly', '').strip()
    plan.trial_days = int(request.form.get('trial_days', 0) or 0)
    plan.stripe_coupon_id = request.form.get('stripe_coupon_id', '').strip()
    db.session.commit()
    flash(f'Plan "{plan.name}" actualizado.', 'success')
    return _redirect_plans()


@app.route('/admin/planes/<int:plan_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_plan(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    in_use = User.query.filter_by(subscription_plan_id=plan.id).count()
    if in_use:
        flash(f'No se puede eliminar: {in_use} usuario(s) usan este plan.', 'error')
    else:
        db.session.delete(plan)
        db.session.commit()
        flash('Plan eliminado.', 'success')
    return _redirect_plans()


@app.route('/admin/planes/<int:plan_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_plan(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    plan.is_active = not plan.is_active
    db.session.commit()
    flash(f'Plan "{plan.name}" {"activado" if plan.is_active else "desactivado"}.', 'success')
    return _redirect_plans()


@app.route('/admin/suscripciones')
@login_required
@admin_required
def admin_subscriptions():
    users = User.query.filter_by(role='student').order_by(User.created_at.desc()).all()
    all_plans = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order, SubscriptionPlan.name).all()
    plans = {p.id: p for p in all_plans}
    rows = []
    for u in users:
        plan = plans.get(u.subscription_plan_id)
        rows.append({
            'user': u,
            'plan_name': plan.name if plan else ('Gratuito' if u.is_free_billing else '—'),
            'payment_label': user_payment_label(u),
        })
    return render_template('admin/subscriptions.html', rows=rows, all_plans=all_plans)


@app.route('/admin/suscripciones/<int:user_id>/estado', methods=['POST'])
@login_required
@admin_required
def admin_set_user_status(user_id):
    user = User.query.get_or_404(user_id)
    new_status = request.form.get('status', '').strip()
    if new_status in ('active', 'pending', 'suspended', 'rejected'):
        user.status = new_status
        db.session.commit()
        flash(f'Estado de {user.username} actualizado a {new_status}.', 'success')
    return redirect(url_for('admin_subscriptions'))


@app.route('/admin/suscripciones/<int:user_id>/gratuito', methods=['POST'])
@login_required
@admin_required
def admin_set_user_free(user_id):
    user = User.query.get_or_404(user_id)
    user.billing_type = 'free'
    user.subscription_status = 'none'
    user.status = 'active'
    db.session.commit()
    flash(f'{user.username} marcado como cuenta gratuita (sin cobros).', 'success')
    return redirect(url_for('admin_subscriptions'))


@app.route('/admin/suscripciones/<int:user_id>/whatsapp-ok', methods=['POST'])
@login_required
@admin_required
def admin_clear_whatsapp_vip(user_id):
    user = User.query.get_or_404(user_id)
    user.whatsapp_vip_pending = False
    db.session.commit()
    flash(f'WhatsApp VIP marcado como gestionado para {user.username}.', 'success')
    return redirect(url_for('admin_subscriptions'))


@app.route('/admin/posts/<int:post_id>/estado', methods=['POST'])
@login_required
@admin_required
def admin_post_workflow_status(post_id):
    post = Post.query.get_or_404(post_id)
    status = request.form.get('workflow_status', '').strip()
    if status in ('pendiente', 'respondida', 'importante', 'idea_contenido'):
        post.workflow_status = status
        db.session.commit()
        flash('Estado del post actualizado.', 'success')
    return redirect(request.referrer or url_for('community'))


@app.route('/admin/categorias/nueva', methods=['POST'])
@login_required
@admin_required
def admin_new_category():
    name  = request.form.get('name', '').strip()
    color = request.form.get('color', '#6366f1')
    emoji = request.form.get('emoji', '💬')
    plan_id = request.form.get('required_plan_id', type=int) or None
    if name and not Category.query.filter_by(name=name).first():
        db.session.add(Category(name=name, color=color, emoji=emoji, required_plan_id=plan_id))
        db.session.commit()
        flash('Categoría creada.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categorias/<int:cat_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    if getattr(cat, 'is_system', False):
        flash('Las categorías del sistema no se pueden eliminar.', 'error')
        return redirect(url_for('admin_dashboard'))
    db.session.delete(cat)
    db.session.commit()
    flash('Categoría eliminada.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cursos')
@login_required
@admin_required
def admin_courses():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template('admin/courses.html', courses=courses)

@app.route('/admin/cursos/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new_course():
    if request.method == 'POST':
        course = Course(
            title       = request.form.get('title', '').strip(),
            subtitle    = request.form.get('subtitle', '').strip(),
            description = request.form.get('description', '').strip(),
            price       = float(request.form.get('price', 0) or 0),
            is_published= 'published' in request.form,
            image       = request.form.get('image_url', '').strip(),
        )
        db.session.add(course)
        db.session.commit()
        flash('Curso creado. Ahora añade secciones y lecciones.', 'success')
        return redirect(url_for('admin_edit_course', course_id=course.id))
    return render_template('admin/new_course.html')

@app.route('/admin/cursos/<int:course_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_course(course_id):
    course = Course.query.get_or_404(course_id)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update':
            course.title        = request.form.get('title', course.title).strip()
            course.subtitle     = request.form.get('subtitle', course.subtitle).strip()
            course.description  = request.form.get('description', course.description).strip()
            course.price        = float(request.form.get('price', course.price) or 0)
            course.is_published = 'published' in request.form
            course.image        = request.form.get('image_url', course.image).strip()
            cover_file = request.files.get('cover_image')
            if cover_file and cover_file.filename:
                course.cover_data, course.cover_mime = _compress_image(
                    cover_file, max_w=800, max_h=500, quality=83)
            db.session.commit()
            flash('Curso actualizado.', 'success')
        elif action == 'add_section':
            t = request.form.get('section_title', '').strip()
            if t:
                db.session.add(Section(course_id=course_id,
                                       title=t, order=len(course.sections)))
                db.session.commit()
                flash('Sección añadida.', 'success')
    return render_template('admin/edit_course.html', course=course)

@app.route('/admin/cursos/reordenar', methods=['POST'])
@login_required
@admin_required
def admin_reorder_courses():
    order = request.json.get('order', [])
    for i, course_id in enumerate(order):
        Course.query.filter_by(id=course_id).update({'order': i})
    db.session.commit()
    return ('', 204)

@app.route('/admin/secciones/reordenar', methods=['POST'])
@login_required
@admin_required
def admin_reorder_sections():
    order = request.json.get('order', [])
    for i, section_id in enumerate(order):
        Section.query.filter_by(id=section_id).update({'order': i})
    db.session.commit()
    return ('', 204)

@app.route('/admin/cursos/<int:course_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    try:
        _delete_course_safely(course_id)
        flash('Formación eliminada.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar: {e}', 'error')
    return redirect(url_for('courses'))


def _delete_course_safely(course_id):
    """Borra un curso y todos sus hijos usando SQL directo (subqueries) para evitar
    conflictos FK en PostgreSQL. Usa db.engine.begin() para una transacción limpia
    totalmente independiente del ORM session."""
    with db.engine.begin() as conn:
        cid = int(course_id)
        # Borrar lesson_progress de todas las lecciones del curso
        conn.execute(text("""
            DELETE FROM lesson_progress
            WHERE lesson_id IN (
                SELECT l.id FROM lesson l
                JOIN section s ON s.id = l.section_id
                WHERE s.course_id = :cid
            )
        """), {'cid': cid})

        # Borrar lesson_image (también tiene CASCADE pero mejor explícito)
        conn.execute(text("""
            DELETE FROM lesson_image
            WHERE lesson_id IN (
                SELECT l.id FROM lesson l
                JOIN section s ON s.id = l.section_id
                WHERE s.course_id = :cid
            )
        """), {'cid': cid})

        # Borrar lesson_file
        conn.execute(text("""
            DELETE FROM lesson_file
            WHERE lesson_id IN (
                SELECT l.id FROM lesson l
                JOIN section s ON s.id = l.section_id
                WHERE s.course_id = :cid
            )
        """), {'cid': cid})

        # Borrar lecciones
        conn.execute(text("""
            DELETE FROM lesson
            WHERE section_id IN (
                SELECT id FROM section WHERE course_id = :cid
            )
        """), {'cid': cid})

        # Borrar secciones
        conn.execute(text('DELETE FROM section WHERE course_id = :cid'), {'cid': cid})

        # Borrar matrículas
        conn.execute(text('DELETE FROM enrollment WHERE course_id = :cid'), {'cid': cid})

        # Borrar el curso
        conn.execute(text('DELETE FROM course WHERE id = :cid'), {'cid': cid})

@app.route('/admin/seccion/<int:section_id>/leccion', methods=['POST'])
@login_required
@admin_required
def admin_add_lesson(section_id):
    section = Section.query.get_or_404(section_id)
    title   = request.form.get('title', '').strip()
    if title:
        db.session.add(Lesson(
            section_id   = section_id,
            title        = title,
            video_url    = request.form.get('video_url', '').strip(),
            description  = request.form.get('description', '').strip(),
            duration_min = int(request.form.get('duration', 0) or 0),
            order        = len(section.lessons),
            group_label  = request.form.get('group_label', '').strip() or None,
            drip_days    = int(request.form.get('drip_days', 0) or 0),
        ))
        db.session.commit()
        flash('Lección añadida.', 'success')
    return redirect(url_for('admin_edit_course', course_id=section.course_id))

@app.route('/admin/seccion/<int:section_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_section(section_id):
    section = Section.query.get_or_404(section_id)
    course_id = section.course_id
    try:
        sid = int(section_id)
        with db.engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM lesson_progress WHERE lesson_id IN
                (SELECT id FROM lesson WHERE section_id = :sid)
            """), {'sid': sid})
            conn.execute(text("""
                DELETE FROM lesson_image WHERE lesson_id IN
                (SELECT id FROM lesson WHERE section_id = :sid)
            """), {'sid': sid})
            conn.execute(text("""
                DELETE FROM lesson_file WHERE lesson_id IN
                (SELECT id FROM lesson WHERE section_id = :sid)
            """), {'sid': sid})
            conn.execute(text('DELETE FROM lesson  WHERE section_id = :sid'), {'sid': sid})
            conn.execute(text('DELETE FROM section WHERE id = :sid'),         {'sid': sid})
        flash('Sección eliminada.', 'success')
    except Exception as e:
        flash(f'Error al eliminar sección: {e}', 'error')
    return redirect(url_for('admin_edit_course', course_id=course_id))

@app.route('/admin/leccion/<int:lesson_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_lesson(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    course_id = lesson.section.course_id
    try:
        lid = int(lesson_id)
        with db.engine.begin() as conn:
            conn.execute(text('DELETE FROM lesson_progress WHERE lesson_id = :lid'), {'lid': lid})
            conn.execute(text('DELETE FROM lesson_image    WHERE lesson_id = :lid'), {'lid': lid})
            conn.execute(text('DELETE FROM lesson_file     WHERE lesson_id = :lid'), {'lid': lid})
            conn.execute(text('DELETE FROM lesson          WHERE id        = :lid'), {'lid': lid})
        flash('Lección eliminada.', 'success')
    except Exception as e:
        flash(f'Error al eliminar lección: {e}', 'error')
    return redirect(url_for('admin_edit_course', course_id=course_id))

@app.route('/admin/leccion/<int:lesson_id>/archivo', methods=['POST'])
@login_required
@admin_required
def admin_add_lesson_file(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    name = request.form.get('name', '').strip()
    f    = request.files.get('file')
    if name and f and f.filename:
        data = f.read()
        db.session.add(LessonFile(
            lesson_id = lesson_id,
            name      = name,
            mimetype  = f.mimetype or 'application/octet-stream',
            size      = len(data),
            data      = data,
        ))
        db.session.commit()
        flash('Archivo subido correctamente.', 'success')
    return redirect(url_for('admin_edit_course', course_id=lesson.section.course_id))

@app.route('/archivo/<int:file_id>')
@login_required
def serve_lesson_file(file_id):
    f = LessonFile.query.get_or_404(file_id)
    return send_file(
        io.BytesIO(f.data),
        mimetype=f.mimetype,
        as_attachment=True,
        download_name=f.name,
    )

@app.route('/admin/archivo/<int:file_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_lesson_file(file_id):
    f = LessonFile.query.get_or_404(file_id)
    course_id = f.lesson.section.course_id
    db.session.delete(f)
    db.session.commit()
    flash('Archivo eliminado.', 'success')
    return redirect(url_for('admin_edit_course', course_id=course_id))


# ── Lesson rich-text description ──────────────────────────────────────────────

@app.route('/admin/leccion/<int:lesson_id>/descripcion', methods=['POST'])
@login_required
@admin_required
def admin_save_lesson_description(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    desc = request.form.get('description', '').strip()
    # Limpiar contenido vacío que deja el editor Quill
    if desc in ('<p><br></p>', '<p></p>', '<br>'):
        desc = ''
    lesson.description = desc
    db.session.commit()
    # Llamado siempre por AJAX — devolver 204 (no content)
    return ('', 204)


@app.route('/admin/leccion/<int:lesson_id>/video', methods=['POST'])
@login_required
@admin_required
def admin_save_lesson_video(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson.video_url = request.form.get('video_url', '').strip()
    db.session.commit()
    return ('', 204)   # AJAX — no redirect needed


@app.route('/admin/leccion/<int:lesson_id>/grupo', methods=['POST'])
@login_required
@admin_required
def admin_save_lesson_group(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson.group_label = request.form.get('group_label', '').strip() or None
    db.session.commit()
    return ('', 204)

@app.route('/admin/leccion/<int:lesson_id>/imagen', methods=['POST'])
@login_required
@admin_required
def admin_upload_lesson_image(lesson_id):
    """TinyMCE images_upload_url handler — returns JSON with image location."""
    Lesson.query.get_or_404(lesson_id)   # ensure lesson exists
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'no file'}), 400
    data, mime = _compress_image(f, max_w=1400, max_h=1400, quality=82)
    img = LessonImage(lesson_id=lesson_id, mimetype=mime, data=data)
    db.session.add(img)
    db.session.commit()
    return jsonify({'location': url_for('serve_lesson_image', image_id=img.id)})


@app.route('/leccion-imagen/<int:image_id>')
@login_required
def serve_lesson_image(image_id):
    img = LessonImage.query.get_or_404(image_id)
    return _cached_image(img.data, img.mimetype, max_age=604800)  # 7 días


@app.route('/admin/clases')
@login_required
@admin_required
def admin_live_classes():
    classes = LiveClass.query.order_by(LiveClass.scheduled_at.desc()).all()
    categories = LiveClassCategory.query.order_by(
        LiveClassCategory.sort_order, LiveClassCategory.name
    ).all()
    return render_template('admin/live_classes.html', classes=classes, categories=categories)


@app.route('/admin/calendario/categorias/nueva', methods=['POST'])
@login_required
@admin_required
def admin_new_calendar_category():
    name = request.form.get('name', '').strip().lower()
    color = request.form.get('color', '#7c3aed').strip()
    emoji = request.form.get('emoji', '📅').strip() or '📅'
    if name and not LiveClassCategory.query.filter_by(name=name).first():
        db.session.add(LiveClassCategory(
            name=name, color=color, emoji=emoji,
            sort_order=LiveClassCategory.query.count(),
        ))
        db.session.commit()
        flash(f'Categoría «{name}» creada.', 'success')
    else:
        flash('Nombre de categoría no válido o ya existente.', 'error')
    return redirect(url_for('admin_live_classes'))


@app.route('/admin/calendario/categorias/<int:cat_id>/editar', methods=['POST'])
@login_required
@admin_required
def admin_edit_calendar_category(cat_id):
    cat = LiveClassCategory.query.get_or_404(cat_id)
    cat.name = request.form.get('name', cat.name).strip().lower()
    cat.color = request.form.get('color', cat.color).strip()
    cat.emoji = request.form.get('emoji', cat.emoji).strip() or '📅'
    try:
        cat.sort_order = int(request.form.get('sort_order', cat.sort_order) or 0)
    except ValueError:
        pass
    db.session.commit()
    flash('Categoría actualizada.', 'success')
    return redirect(url_for('admin_live_classes'))


@app.route('/admin/calendario/categorias/<int:cat_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_calendar_category(cat_id):
    cat = LiveClassCategory.query.get_or_404(cat_id)
    in_use = LiveClass.query.filter_by(category_id=cat.id).count()
    if in_use:
        flash(f'No se puede eliminar: {in_use} evento(s) usan esta categoría.', 'error')
    else:
        db.session.delete(cat)
        db.session.commit()
        flash('Categoría eliminada.', 'success')
    return redirect(url_for('admin_live_classes'))


@app.route('/admin/calendario/tematica', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_calendar_theme():
    now = datetime.utcnow()
    year = request.args.get('year', type=int) or now.year
    month = request.args.get('month', type=int) or now.month
    theme = CalendarMonthTheme.query.filter_by(year=year, month=month).first()
    if request.method == 'POST':
        title = request.form.get('theme_title', '').strip()
        desc = request.form.get('description', '').strip()
        year = int(request.form.get('year', year))
        month = int(request.form.get('month', month))
        if not title:
            flash('El título de la temática es obligatorio.', 'error')
        else:
            theme = CalendarMonthTheme.query.filter_by(year=year, month=month).first()
            if not theme:
                theme = CalendarMonthTheme(year=year, month=month, theme_title=title, description=desc)
                db.session.add(theme)
            else:
                theme.theme_title = title
                theme.description = desc
            db.session.commit()
            flash('Temática mensual guardada.', 'success')
            return redirect(url_for('admin_calendar_theme', year=year, month=month))
    month_names = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                   'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
    return render_template('admin/calendar_theme.html', theme=theme, year=year, month=month,
                           month_names=month_names)


@app.route('/admin/clases/nueva', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new_live_class():
    if request.method == 'POST':
        date_str   = request.form.get('scheduled_at', '')
        recurrence = request.form.get('recurrence', 'none')
        try:
            scheduled_at = datetime.fromisoformat(date_str)
        except Exception:
            scheduled_at = datetime.utcnow()
        category_id = request.form.get('category_id', type=int)
        lc = LiveClass(
            title        = request.form.get('title', '').strip(),
            description  = request.form.get('description', '').strip(),
            scheduled_at = scheduled_at,
            duration_min = int(request.form.get('duration', 60) or 60),
            meet_url     = request.form.get('meet_url', '').strip(),
            instructor   = request.form.get('instructor', '').strip(),
            recurrence   = recurrence,
            category_id  = category_id,
            subtopic     = request.form.get('subtopic', '').strip(),
        )
        db.session.add(lc)
        db.session.flush()  # get lc.id before commit

        if recurrence in ('weekly', 'monthly'):
            iterations = 104 if recurrence == 'weekly' else 24
            for i in range(1, iterations + 1):
                if recurrence == 'weekly':
                    next_dt = scheduled_at + timedelta(weeks=i)
                else:
                    month = scheduled_at.month - 1 + i
                    year  = scheduled_at.year + month // 12
                    month = month % 12 + 1
                    import calendar
                    day = min(scheduled_at.day, calendar.monthrange(year, month)[1])
                    next_dt = scheduled_at.replace(year=year, month=month, day=day)
                db.session.add(LiveClass(
                    title        = lc.title,
                    description  = lc.description,
                    scheduled_at = next_dt,
                    duration_min = lc.duration_min,
                    meet_url     = lc.meet_url,
                    instructor   = lc.instructor,
                    recurrence   = recurrence,
                    parent_id    = lc.id,
                    category_id  = lc.category_id,
                    subtopic     = lc.subtopic,
                ))

        db.session.commit()
        # Notify all users about the new class
        all_users = User.query.filter_by(role='student').all()
        for u in all_users:
            notify(u.id, 'new_class',
                   f'📅 Nueva clase programada: "{lc.title}" el {lc.scheduled_at.strftime("%d %b a las %H:%M")}',
                   '/calendario')
        db.session.commit()
        label = {'weekly': 'semanal', 'monthly': 'mensual'}.get(recurrence, '')
        flash(f'Clase programada{"  (recurrencia " + label + ")" if label else ""}.', 'success')
        return redirect(url_for('admin_live_classes'))
    categories = LiveClassCategory.query.order_by(
        LiveClassCategory.sort_order, LiveClassCategory.name
    ).all()
    return render_template('admin/new_live_class.html', categories=categories)

@app.route('/admin/clases/<int:class_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_live_class(class_id):
    lc = LiveClass.query.get_or_404(class_id)
    if request.method == 'POST':
        lc.title        = request.form.get('title', '').strip()
        lc.description  = request.form.get('description', '').strip()
        lc.meet_url     = request.form.get('meet_url', '').strip()
        lc.instructor   = request.form.get('instructor', '').strip()
        lc.duration_min = int(request.form.get('duration', 60) or 60)
        lc.category_id = request.form.get('category_id', type=int)
        lc.subtopic = request.form.get('subtopic', '').strip()
        try:
            lc.scheduled_at = datetime.fromisoformat(request.form.get('scheduled_at', ''))
        except Exception:
            pass
        update_all = request.form.get('update_all') == '1'
        if update_all and lc.parent_id is None:
            children = LiveClass.query.filter_by(parent_id=lc.id).all()
            for child in children:
                child.title        = lc.title
                child.description  = lc.description
                child.meet_url     = lc.meet_url
                child.instructor   = lc.instructor
                child.duration_min = lc.duration_min
                child.category_id  = lc.category_id
                child.subtopic     = lc.subtopic
        db.session.commit()
        flash('Evento actualizado.', 'success')
        return redirect(url_for('calendar'))
    scheduled_str = lc.scheduled_at.strftime('%Y-%m-%dT%H:%M')
    categories = LiveClassCategory.query.order_by(
        LiveClassCategory.sort_order, LiveClassCategory.name
    ).all()
    return render_template('admin/edit_live_class.html', lc=lc, scheduled_str=scheduled_str,
                           categories=categories)

@app.route('/admin/clases/<int:class_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_live_class(class_id):
    lc = LiveClass.query.get_or_404(class_id)
    delete_all = request.form.get('delete_all') == '1'
    if delete_all or (lc.parent_id is None and lc.recurrence != 'none'):
        # Delete parent + all children
        LiveClass.query.filter(
            (LiveClass.id == class_id) | (LiveClass.parent_id == class_id)
        ).delete(synchronize_session=False)
    else:
        db.session.delete(lc)
    db.session.commit()
    flash('Clase eliminada.', 'success')
    return redirect(url_for('admin_live_classes'))

@app.route('/admin/usuarios')
@login_required
@admin_required
def admin_users():
    pending = User.query.filter_by(status='pending').order_by(User.created_at.desc()).all()
    active  = User.query.filter(User.status != 'pending').order_by(User.created_at.desc()).all()
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order, SubscriptionPlan.name).all()
    plans_map = {p.id: p for p in plans}

    def _plan_name(u):
        if u.is_free_billing:
            return 'Gratuito'
        p = plans_map.get(u.subscription_plan_id)
        return p.name if p else '—'

    def _pay_label(u):
        return user_payment_label(u)

    return render_template(
        'admin/users.html',
        pending=pending, active=active, plans=plans,
        plan_name=_plan_name, pay_label=_pay_label,
    )


@app.route('/admin/usuarios/<int:user_id>/plan', methods=['POST'])
@login_required
@admin_required
def admin_set_user_plan(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('No se puede cambiar el plan de un administrador.', 'error')
        return redirect(url_for('admin_users'))
    plan_id = request.form.get('plan_id', '')
    if plan_id == 'free':
        user.billing_type = 'free'
        user.subscription_plan_id = None
        user.subscription_status = 'none'
    elif plan_id == 'none':
        user.billing_type = 'standard'
        user.subscription_plan_id = None
    else:
        try:
            pid = int(plan_id)
        except (TypeError, ValueError):
            flash('Plan no válido.', 'error')
            return redirect(url_for('admin_users'))
        plan = SubscriptionPlan.query.get(pid)
        if not plan:
            flash('Plan no encontrado.', 'error')
            return redirect(url_for('admin_users'))
        user.billing_type = 'standard'
        user.subscription_plan_id = plan.id
    db.session.commit()
    flash(f'Plan de {user.username} actualizado.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/aprobar', methods=['POST'])
@login_required
@admin_required
def admin_approve_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = 'active'
    notify(user.id, 'approved',
           '✅ Tu acceso a la plataforma ha sido aprobado. ¡Ya puedes entrar!', '/')
    db.session.commit()
    plan = SubscriptionPlan.query.get(user.subscription_plan_id) if user.subscription_plan_id else None
    plan_name = plan.name if plan else '—'
    try:
        send_welcome_email(
            app, mail, user, plan_name,
            url_for('login', _external=True),
            pending_approval=False,
        )
    except Exception as e:
        print(f'[billing] welcome on approve: {e}')
    flash(f'{user.username} ha sido aprobado.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/rechazar', methods=['POST'])
@login_required
@admin_required
def admin_reject_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = 'rejected'
    db.session.commit()
    flash(f'{user.username} ha sido rechazado.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/rol', methods=['POST'])
@login_required
@admin_required
def admin_toggle_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.role = 'admin' if user.role == 'student' else 'student'
        db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/suspender', methods=['POST'])
@login_required
@admin_required
def admin_toggle_status(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.status = 'active' if user.status != 'active' else 'suspended'
        db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('No puedes eliminar tu propia cuenta.', 'error')
        return redirect(url_for('admin_users'))
    # Delete related data
    from models import Post, Comment, Enrollment, LessonProgress, Notification, PointEvent
    LessonProgress.query.filter_by(user_id=user.id).delete()
    Enrollment.query.filter_by(user_id=user.id).delete()
    Notification.query.filter_by(user_id=user.id).delete()
    PointEvent.query.filter_by(user_id=user.id).delete()
    for post in Post.query.filter_by(user_id=user.id).all():
        Comment.query.filter_by(post_id=post.id).delete()
        db.session.delete(post)
    Comment.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario eliminado correctamente.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/nuevo', methods=['POST'])
@login_required
@admin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    role     = request.form.get('role', 'student')

    if not username or not email or not password:
        flash('Todos los campos son obligatorios.', 'error')
        return redirect(url_for('admin_users'))
    if User.query.filter_by(username=username).first():
        flash(f'El nombre de usuario "{username}" ya está en uso.', 'error')
        return redirect(url_for('admin_users'))
    if User.query.filter_by(email=email).first():
        flash(f'El email "{email}" ya está registrado.', 'error')
        return redirect(url_for('admin_users'))
    if role not in ('student', 'admin'):
        role = 'student'

    is_free = _is_true(request.form.get('billing_free'))
    new_user = User(
        username=username, email=email, role=role,
        status='active',
        billing_type='free' if is_free else 'standard',
        subscription_status='none' if is_free else 'none',
    )
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    free_msg = ' (cuenta gratuita, sin cobros)' if is_free else ''
    flash(f'✅ Usuario "{username}" creado como {"admin" if role == "admin" else "alumno"}{free_msg}.', 'success')
    return redirect(url_for('admin_users'))

# ── PÁGINA PÚBLICA DE MIEMBROS ────────────────────────────────────────────────

@app.route('/miembros')
@login_required
def members():
    users = (User.query
             .filter(User.status == 'active')
             .order_by(User.created_at.asc())
             .all())
    members_data = [{'user': u} for u in users]
    pending = []
    if current_user.is_admin:
        pending = User.query.filter_by(status='pending').order_by(User.created_at.desc()).all()
    return render_template('members.html', members=members_data, pending=pending)

@app.route('/miembros/<int:user_id>/aprobar', methods=['POST'])
@login_required
@admin_required
def members_approve(user_id):
    user = User.query.get_or_404(user_id)
    user.status = 'active'
    notify(user.id, 'approved',
           '✅ Tu solicitud de acceso ha sido aprobada. ¡Ya puedes entrar!', '/')
    db.session.commit()
    flash(f'✅ {user.username} aprobado correctamente.', 'success')
    return redirect(url_for('members'))

@app.route('/miembros/<int:user_id>/rechazar', methods=['POST'])
@login_required
@admin_required
def members_reject(user_id):
    user = User.query.get_or_404(user_id)
    user.status = 'rejected'
    db.session.commit()
    flash(f'🚫 {user.username} ha sido rechazado.', 'success')
    return redirect(url_for('members'))

@app.route('/miembros/<int:user_id>/rol', methods=['POST'])
@login_required
@admin_required
def members_toggle_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.role = 'admin' if user.role == 'student' else 'student'
        db.session.commit()
        flash(f'{"⚙️ " + user.username + " ahora es admin." if user.role == "admin" else "🎓 " + user.username + " ya no es admin."}', 'success')
    return redirect(url_for('members'))

@app.route('/miembros/<int:user_id>/expulsar', methods=['POST'])
@login_required
@admin_required
def members_suspend(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.status = 'suspended' if user.status == 'active' else 'active'
        db.session.commit()
        action = 'suspendido' if user.status == 'suspended' else 'reactivado'
        flash(f'Usuario {user.username} {action}.', 'success')
    return redirect(url_for('members'))

@app.route('/miembros/<int:user_id>/actividad')
@login_required
def member_activity(user_id):
    member = User.query.get_or_404(user_id)
    # Solo el propio usuario o un admin puede ver la actividad
    if not current_user.is_admin and current_user.id != user_id:
        abort(403)

    # Lecciones completadas
    completed = (db.session.query(LessonProgress, Lesson, Course)
                 .join(Lesson, LessonProgress.lesson_id == Lesson.id)
                 .join(Section, Lesson.section_id == Section.id)
                 .join(Course, Section.course_id == Course.id)
                 .filter(LessonProgress.user_id == user_id)
                 .order_by(LessonProgress.completed_at.desc())
                 .all())

    # Posts creados
    posts = Post.query.filter_by(user_id=user_id).order_by(Post.created_at.desc()).all()

    # Comentarios
    comments = (Comment.query.filter_by(user_id=user_id)
                .order_by(Comment.created_at.desc()).all())

    # Total de puntos
    total_pts = db.session.query(db.func.sum(PointEvent.points))\
                          .filter_by(user_id=user_id).scalar() or 0

    # Construir timeline unificado
    timeline = []
    for lp, lesson, course in completed:
        timeline.append({
            'date': lp.completed_at,
            'type': 'lesson',
            'icon': '📚',
            'text': f'Completó <strong>{lesson.title}</strong>',
            'sub':  course.title,
            'pts':  3,
        })
    for p in posts:
        timeline.append({
            'date': p.created_at,
            'type': 'post',
            'icon': '📝',
            'text': f'Publicó <strong>{p.title}</strong>',
            'sub':  None,
            'pts':  4,
        })
    for c in comments:
        timeline.append({
            'date': c.created_at,
            'type': 'comment',
            'icon': '💬',
            'text': 'Comentó en un post',
            'sub':  (c.content[:60] + '…') if len(c.content) > 60 else c.content,
            'pts':  2,
        })
    timeline.sort(key=lambda x: x['date'], reverse=True)

    # Estadísticas rápidas
    stats = {
        'lessons':  len(completed),
        'posts':    len(posts),
        'comments': len(comments),
        'points':   total_pts,
    }

    user_level = get_level(total_pts)
    return render_template('member_activity.html',
                           member=member, timeline=timeline, stats=stats,
                           user_level=user_level, total_pts=total_pts)

# ── ERROR PAGES ───────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def server_error(e):
    db.session.rollback()   # evitar que una sesión rota bloquee futuros requests
    try:
        return render_template('errors/500.html'), 500
    except Exception:
        return '<h1>Error interno del servidor</h1><p>Inténtalo de nuevo en unos segundos.</p>', 500


# ── INIT ──────────────────────────────────────────────────────────────────────

def seed_db():
    # SiteSettings — solo crea si no existe, nunca sobreescribe
    s = SiteSettings.query.first()
    env_name = (app.config.get('ACADEMY_NAME') or '').strip()
    if not s:
        s = SiteSettings(academy_name=env_name or 'Marca Atractora')
        db.session.add(s)
        db.session.commit()
    elif env_name and (not s.academy_name or s.academy_name.strip() == 'Marca Atractora'):
        # Si está el placeholder por defecto, tomar el nombre configurado en .env.
        s.academy_name = env_name
        db.session.commit()

    # Usuario admin semilla: solo en instalación inicial real (sin usuarios).
    if User.query.count() == 0:
        admin_seed = User(
            username='samuel',
            email='samuelgavilant@gmail.com',
            role='admin',
            status='active'
        )
        admin_seed.set_password('Admin1234!')
        db.session.add(admin_seed)

        # Alumno demo opcional, también solo en el primer bootstrap.
        demo_student = User(
            username='alumno_prueba',
            email='alumno@prueba.com',
            role='student',
            status='active'
        )
        demo_student.set_password('Prueba1234!')
        db.session.add(demo_student)
        db.session.commit()

    # Categorías por defecto — solo si no hay ninguna
    if not Category.query.first():
        for name, color, emoji in [
            ('General',   '#6366f1', '💬'),
            ('Anuncios',  '#f59e0b', '📢'),
            ('Preguntas', '#10b981', '❓'),
            ('Recursos',  '#3b82f6', '📚'),
        ]:
            db.session.add(Category(name=name, color=color, emoji=emoji))
    db.session.commit()

    # ── FASE 1 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 1 Crea tu Marca Personal').first():
        fase1 = Course(
            title='FASE 1 Crea tu Marca Personal',
            subtitle='Branding, mensaje, storytelling y confianza personal',
            description='Creamos la marca personal desde los cimientos, el branding, el mensaje, el storytelling... y ganamos confianza en nosotros mismos.',
            is_published=True,
            price=0.0,
        )
        db.session.add(fase1)
        db.session.flush()  # get fase1.id

        _sections = [
            ('1. ¡Empieza aquí!', [
                ('1.1 Bienvenida.', 'https://vimeo.com/946336180', 8, ''),
            ]),
            ('2. Empezando a crear tu Marca Personal', [
                ('2. ¿Por qué algunas marcas personales no funcionan?', 'https://vimeo.com/923682632', 8, ''),
                ('2.1 Definiendo bien a tu cliente ideal.', 'https://vimeo.com/923683543', 8, ''),
                ('2.3 ¿Qué problemas tiene mi cliente ideal?', 'https://vimeo.com/923689838', 10, ''),
                ('2.4 Creando tu producto.', 'https://vimeo.com/1100556159', 19, ''),
            ]),
            ('3. Mentalidad', [
                ('3.1 Perder el miedo a la cámara y vencer el SDI', 'https://vimeo.com/952659718', 16, ''),
                ('3.2 Vencer la procrastinación y tener energía.', 'https://vimeo.com/952662836', 12, ''),
                ('3.1 Conócete a ti mismo, define tu identidad.', 'https://vimeo.com/1111044925', 49,
                 'Descubre quién eres realmente, cuáles son tus valores y cómo construir una identidad sólida que te diferencie.'),
                ('3.2 Aumenta tu autoestima y sé magnético.', 'https://vimeo.com/1111323128', 46, ''),
            ]),
            ('4. Empezando a comunicar', [
                ('4.1 Branding', 'https://vimeo.com/1111346889', 16, ''),
                ('4.2 Características de tu discurso', 'https://vimeo.com/1111358958', 17, ''),
                ('4.3 Mejorando tu oratoria.', 'https://vimeo.com/941893844', 25, ''),
                ('4.4 Perfeccionando tu oratoria.', 'https://vimeo.com/945886893', 15, ''),
                ('4.5 Aumenta tu carisma.', 'https://vimeo.com/1006883420', 15, ''),
                ('4.6 Storytelling', 'https://vimeo.com/1118503138', 16, ''),
            ]),
            ('PREGUNTAS FRECUENTES', [
                ('¿Tengo que salir siempre guapo en los vídeos?', 'https://vimeo.com/923693122', 3, ''),
                ('¿Cómo puedo ayudar a mi familia con mis vídeos?', 'https://vimeo.com/924536418', 2, ''),
                ('¿Cómo identifico qué quiere mi público objetivo?', 'https://vimeo.com/924539791', 1, ''),
                ('¿Tengo que tener prisa por monetizar?', 'https://vimeo.com/924547779', 2, ''),
                ('¿Cómo encontramos a nuestro enemigo?', 'https://vimeo.com/932504096', 2, ''),
                ('¿Varios buyer persona para un mismo producto?', 'https://vimeo.com/932507919', 1, ''),
                ('¿Hacer el vídeo de pie o sentado?', 'https://vimeo.com/941899689', 2, ''),
                ('Ritual antes de grabar un vídeo.', 'https://vimeo.com/941902712', 4, ''),
                ('¿Cómo descargar vídeo de Artgrid? Videos de stock.', 'https://vimeo.com/948304344', 1, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections, 1):
            sec = Section(course_id=fase1.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(
                    section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur,
                    description=l_desc, order=l_order,
                ))
        db.session.commit()
        print('[seed] FASE 1 course created with all sections and lessons.')

    # ── FASE 2 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 2. Creación del contenido.').first():
        fase2 = Course(
            title='FASE 2. Creación del contenido.',
            subtitle='Equipo, cámara, iluminación, guion y edición',
            description='Crea tu contenido de forma profesional aunque no sepas por donde empezar.',
            is_published=True,
            price=0.0,
        )
        db.session.add(fase2)
        db.session.flush()

        _sections2 = [
            ('¡Empezamos!', [
                ('Introducción.', 'https://vimeo.com/930832256', 1, ''),
            ]),
            ('1. ¿Qué equipo necesito?', [
                ('1.1 Equipo Básico.', 'https://vimeo.com/930832331', 3, ''),
                ('1.2 Equipo intermedio.', 'https://vimeo.com/930832438', 4, ''),
                ('1.3 Equipo avanzado.', 'https://vimeo.com/930832564', 3, ''),
            ]),
            ('2. Cómo funciona una cámara.', [
                ('2.1 Fundamentos básicos de la fotografía.', 'https://vimeo.com/933627600', 6, ''),
                ('2.2 Cómo funciona la cámara del móvil.', 'https://vimeo.com/935942415', 3, ''),
                ('2.3 Partes de una cámara.', 'https://vimeo.com/939183805', 6, ''),
                ('2.4 Todo lo que tienes que saber sobre el audio.', 'https://vimeo.com/944079519', 8, ''),
            ]),
            ('3. La iluminación.', [
                ('3.2 Esquema básico de iluminación.', 'https://vimeo.com/1005879073', 10, ''),
            ]),
            ('4. Creación del guion.', [
                ('4.1 Empezando a crear nuestro guion.', 'https://vimeo.com/1043141856', 18, ''),
            ]),
            ('5. Vamos a grabarnos.', [
                ('5.1 Fundamentos básicos del vídeo.', '', 0, ''),
                ('5.2 Todo listo para grabarnos.', '', 0, ''),
                ('5.3 Contenidos y organización.', '', 0, ''),
                ('5.4 ¿Cómo hablar nuestro guion?', '', 0, ''),
            ]),
            ('Edición en Capcut', [
                ('Edita con Capcut tus vídeos.', 'https://vimeo.com/925901001', 13, ''),
                ('Añadiendo subtítulos a tus vídeos con Capcut.', 'https://vimeo.com/925904432', 12, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections2, 1):
            sec = Section(course_id=fase2.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(
                    section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur,
                    description=l_desc, order=l_order,
                ))
        db.session.commit()
        print('[seed] FASE 2 course created with all sections and lessons.')

    # ── FASE 3 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 3. Atraer en redes sociales.').first():
        fase3 = Course(
            title='FASE 3. Atraer en redes sociales.',
            subtitle='YouTube, Instagram, TikTok y crecimiento exponencial',
            description='Empápate de como funciona Las redes sociales de principio a fin y crea una comunidad que genere tus primeros miles de suscriptores.',
            is_published=True, price=0.0,
        )
        db.session.add(fase3)
        db.session.flush()

        _sections3 = [
            ('1. YouTube', [
                ('1.0 Crear tu Canal de YouTube',                                  'https://vimeo.com/905919839',           16, ''),
                ('1.1 Como subir un vídeo a Youtube',                              'https://vimeo.com/924959549',            6, ''),
                ('1.2 Analíticas para despegar',                                   'https://vimeo.com/851008454/d29b98cc32', 47, ''),
                ('1.3 La mentalidad que necesitas para YT',                        'https://vimeo.com/855072488/4182033253', 60, ''),
                ('1.4 Títulos y Miniaturas',                                       'https://vimeo.com/857239956/678447f67a', 47, ''),
                ('1.4.1 Crear una miniatura con canva',                            'https://vimeo.com/948289425',            6, ''),
                ('1.4.2 Hacer miniaturas con Photoshop',                           'https://vimeo.com/956991589',            6, ''),
                ('1.5. Todo lo que debes saber sobre SEO',                         'https://vimeo.com/858909961/13bf9725bb', 41, ''),
                ('1.6. Copy y guiones para tus vídeos',                            'https://vimeo.com/863595428/b7de18d61b', 55, ''),
                ('1.6.1 Tres guiones para crear vídeo de Youtube',                 'https://vimeo.com/1164633315',           23, ''),
                ('1.7 Edita tus vídeos para retener la atención',                  'https://vimeo.com/891387027',            42, ''),
                ('1.8 Audio y música',                                             'https://vimeo.com/891394359',            40, ''),
                ('1.9 Trucos para YT',                                             'https://vimeo.com/891403759',            45, ''),
                ('1.10 Crossplatform',                                             'https://vimeo.com/891405274',            44, ''),
                ('1.11 CrossPlatform con ADS para conseguir trafico',              'https://vimeo.com/893240433',            34, ''),
                ('1.12 Todo sobre el copyright',                                   'https://vimeo.com/953984470',             9, ''),
                ('1.13 Configurar google adsense para monetizar',                  'https://vimeo.com/988825345',             7, ''),
                ('1.14 Configuración fiscal Google adsense',                       'https://youtu.be/wtX_YIN3KLU',           15, ''),
                ('1.15 Hacer crecer tu canal de YT con publicidad',                'https://vimeo.com/1054224518',           16, ''),
            ]),
            ('2. INSTAGRAM', [
                ('2.1 Primeros pases en la plataforma',                            'https://vimeo.com/901650539',            56, ''),
                ('2.2 Crear carruseles virales',                                   'https://vimeo.com/906272733',            66, ''),
                ('2.3 Creando comunidad en Historias de Instagram',                'https://vimeo.com/908386985',            61, ''),
                ('2.3.1 Historias destacas de Instagram',                          'https://vimeo.com/1013965873',            9, ''),
                ('2.4 Como crecer (rápido) Instagram (Fran Berges)',               'https://vimeo.com/911247204',           116, ''),
                ('2.5 Automatiza Instagram con Manychat',                          'https://vimeo.com/1135690799',           63, ''),
                ('2.6 Como y cuando hacer lives',                                  'https://vimeo.com/915327765',            58, ''),
                ('2.7 ¿Cómo hacer un reel viral?',                                'https://vimeo.com/933436126',             9, ''),
                ('2.8 Estructura vídeo viral',                                     'https://vimeo.com/933441911',             4, ''),
                ('2.9 Ganchos y copy writing para tu reel viral',                  'https://vimeo.com/933444451',            14, ''),
                ('2.10 Vuélvete viral con reels',                                  'https://vimeo.com/903836622',            75, ''),
                ('2.11 Ganchos visuales',                                          'https://vimeo.com/1125481695',           15, ''),
                ('2.12 Retención de la audiencia',                                 'https://vimeo.com/983628820',            23, ''),
            ]),
            ('3. TIKTOK', [
                ('3.0 Crea y configura tu cuenta de tiktok',                       'https://vimeo.com/957594124',             8, ''),
                ('3.1 Empezando en Tiktok',                                        'https://vimeo.com/889656484',            63, ''),
                ('3.2 Creando contenido para posicionarte en Tiktok',              'https://vimeo.com/892008535',            68, ''),
                ('3.3 Como vender en tiktok',                                      'https://vimeo.com/894269285',            62, ''),
            ]),
            ('4. CRECIMIENTO EXPONENCIAL EN RRSS', [
                ('4.1 Empezamos',                                                   'https://vimeo.com/1057991097',           18, ''),
                ('4.2 Avatar Especifico',                                           'https://vimeo.com/1059652394',           19, ''),
                ('4.3 Avatar 3.0',                                                  'https://vimeo.com/1060927420',           14, ''),
                ('4.4 Análisis de tu competencia',                                  'https://vimeo.com/1069644542',           14, ''),
                ('4.5 Validar un producto',                                         'https://vimeo.com/1142114582',           16, ''),
                ('4.6 Estrategia de venta En Redes Sociales',                       'https://vimeo.com/1139986255',           22, ''),
                ('4.7 Optimización de contenidos. Chat GPT',                        'https://vimeo.com/1013969721',           11, ''),
                ('Como crear comunidad y fidelidad',                                'https://vimeo.com/951552493',            15, ''),
            ]),
            ('5. PREGUNTAS Y DUDAS', [
                ('¿Cuál es la mejor hora para publicar vídeo? (YT)',               'https://vimeo.com/943679344',             2, ''),
                ('¿Es bueno hacer publicidad en Instagram pagada?',                'https://vimeo.com/951268479',             3, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections3, 1):
            sec = Section(course_id=fase3.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur, description=l_desc, order=l_order))
        db.session.commit()
        print('[seed] FASE 3 course created with all sections and lessons.')

    # ── FASE 4 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 4. Ventas de 10k/mes').first():
        fase4 = Course(
            title='FASE 4. Ventas de 10k/mes',
            subtitle='Producto, VSL, cierre de ventas, setters y copywriting',
            description='Todo lo que debes saber para vender por internet.',
            is_published=True, price=0.0,
        )
        db.session.add(fase4)
        db.session.flush()

        _sections4 = [
            ('1. Como crear tu producto o servicio.', [
                ('2.1 Utilizando Chat GPT para tu cliente ideal.',      'https://vimeo.com/1013973439',  13, ''),
                ('2.1 Definiendo tu producto para le venta masiva.',    'https://vimeo.com/1133845788',  30, ''),
                ('2.3 Precio y entrega del producto.',                  'https://vimeo.com/880312444',   86, ''),
                ('2.4 Recupera la inversión del Master de MP.',         'https://vimeo.com/882643397',   67, ''),
                ('2.5 Estrategia webinar.',                             'https://vimeo.com/885310951',   70, ''),
                ('2.6 Llamada a venta.',                                'https://vimeo.com/887431717',   63, ''),
            ]),
            ('2. Método VSL.', [
                ('1.1 Estrategia.',                                     'https://vimeo.com/920521443',   22, ''),
                ('1.2 Creando el VSL',                                  'https://vimeo.com/923461657',   23, ''),
                ('1.3 Estructura inicio VSL',                           'https://vimeo.com/1077030592',  25, ''),
                ('1.4 Parte media VSL',                                 'https://vimeo.com/1084013382',  17, ''),
                ('1.5 Parte final VSL',                                 'https://vimeo.com/1089509756',   8, ''),
                ('1.6 Como grabarse el VSL',                            'https://vimeo.com/1111930051',   7, ''),
                ('1.7 Optimización y ejemplos de VSL.',                 'https://vimeo.com/926614704',   17, ''),
                ('1.8 Como abrir y configurar Calendly',                'https://vimeo.com/1152825477',  13, ''),
            ]),
            ('3. Cierre de ventas.', [
                ('3.1 Creencias sobre la venta.',                       'https://vimeo.com/1133793652',  17, ''),
                ('3.2 Gana mucho dinero cerrando ventas.',              'https://vimeo.com/1111879667',  30, ''),
            ]),
            ('4. Escalar con setters', [
                ('3.1 ¿Qué es un setter?',                              'https://vimeo.com/1015648578',  16, ''),
                ('3.2 Funciones de un setter.',                         'https://vimeo.com/1019435606',  15, ''),
                ('3.3 Procedimiento para tus setters.',                 'https://vimeo.com/1020480794',  12, ''),
                ('3.4 Role Play conversaciones de setters.',            'https://vimeo.com/1025041421',  15, ''),
                ('3.5 ¿Qué requerimos de un setter?',                   'https://vimeo.com/1031864419',  12, ''),
            ]),
            ('5. Copywriter persuasivo', [
                ('4.0 Proposito de tu Marca.',                          'https://vimeo.com/1051602353',  15, ''),
                ('4.1 ¿Que és el copywriting?',                         'https://vimeo.com/1033089083',  17, ''),
                ('4.2 Como usar el copy en tu negocio.',                'https://vimeo.com/1036130237',  16, ''),
                ('4.3 Copy writing aplicado a la pagina web',           '',                               0, ''),
                ('4.4 Haciendo de tu web una maquina de ventas.',       'https://vimeo.com/1039186835',  15, ''),
                ('4.5 Copywriting para email marketing',                'https://vimeo.com/1042754681',  18, ''),
                ('4.6 Gestor de mailing',                               'https://vimeo.com/1042755748',   5, ''),
                ('4.7 Redactar con IA y automatizaciones de email.',    'https://vimeo.com/1056078465',  14, ''),
                ('4.8 Estrategia de Marca para comunicar.',             'https://vimeo.com/1046164226',  20, ''),
            ]),
            ('6. Facebook ADS.', [
                ('3.1 MasterClass conceptos Facebook e Instagram ADS',  'https://vimeo.com/962585274',   87, ''),
                ('3.2 Masterclass FACEBOOK ADS 2 28-ago-2024',          'https://vimeo.com/1005964444',  57, ''),
            ]),
            ('7. Afiliación.', [
                ('Amazon afiliados + audible',                          'https://vimeo.com/1028207666',   4, ''),
            ]),
            ('PREGUNTAS FRECUENTES', [
                ('¿En que plataforma subimos nuestros cursos?',         'https://vimeo.com/932500762',    2, ''),
                ('¿tengo 2 buyerpersona creo dos productos?',           'https://vimeo.com/951268043',    8, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections4, 1):
            sec = Section(course_id=fase4.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur, description=l_desc, order=l_order))
        db.session.commit()
        print('[seed] FASE 4 course created with all sections and lessons.')

    # FASE 5 is handled exclusively by seed_fase5() — do NOT create it here


def fix_fase5_carpeta6():
    """Ensure '6 PROGRAMA TU MENTE PARA LA ABUNDANCIA' exists in FASE 5
    with all 7 lessons (3 originals + 4 finanzas). Creates the section if missing."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return

        all_lessons = [
            ('6.1 Atraer Abundancia y Dinero Cambiando tu Mente', 'https://youtu.be/l27PoZo_rpQ', 54),
            ('6.2 Tu vieja identidad sobre el dinero.',           'https://youtu.be/nG9F_gKpTTM', 31),
            ('6.3 El Dinero Está En La Relación Con Tu Padre',    'https://youtu.be/7samMzQPuzo', 18),
            ('¿Qué es el dinero?',                                'https://youtu.be/jBd3M20EQic', 11),
            ('¿Cómo ahorrar?',                                    'https://youtu.be/gN2Z6gVwsYA', 13),
            ('Gestiona tus finanzas personales.',                 'https://youtu.be/BbSj95aKAW4', 11),
            ('¿En que invertir?',                                 'https://youtu.be/L-yGqUTphN0', 16),
        ]

        sec = Section.query.filter_by(course_id=course.id,
                                      title='6 PROGRAMA TU MENTE PARA LA ABUNDANCIA').first()
        if not sec:
            # Section was deleted by an earlier seed — recreate it at a high order
            max_order = db.session.query(db.func.max(Section.order)).filter_by(
                course_id=course.id).scalar() or 0
            sec = Section(course_id=course.id,
                          title='6 PROGRAMA TU MENTE PARA LA ABUNDANCIA',
                          order=max_order + 1)
            db.session.add(sec)
            db.session.flush()
            print('[fix_fase5_carpeta6] Sección recreada.')

        existing_titles = {l.title for l in sec.lessons}
        max_l_order = max((l.order for l in sec.lessons), default=0)
        added = 0
        for title, url, dur in all_lessons:
            if title not in existing_titles:
                max_l_order += 1
                db.session.add(Lesson(section_id=sec.id, title=title,
                                      video_url=url, duration_min=dur, order=max_l_order))
                added += 1
        if added:
            db.session.commit()
            print(f'[fix_fase5_carpeta6] Añadidas {added} lecciones a carpeta 6.')
        else:
            print('[fix_fase5_carpeta6] Carpeta 6 ya estaba completa.')
    except Exception as e:
        print(f'[fix_fase5_carpeta6] ERROR: {e}')
        db.session.rollback()


def seed_descriptions():
    """Populate lesson descriptions using LESSON_DESCRIPTIONS dict via raw SQL."""
    updated = 0
    try:
        with db.engine.connect() as conn:
            for (course_title, lesson_title), html in LESSON_DESCRIPTIONS.items():
                row = conn.execute(text(
                    """SELECT l.id, l.description FROM lesson l
                       JOIN section s ON s.id = l.section_id
                       JOIN course c ON c.id = s.course_id
                       WHERE c.title = :ct AND l.title = :lt
                       LIMIT 1"""
                ), {'ct': course_title, 'lt': lesson_title}).fetchone()
                if row is None:
                    print(f'[seed_desc] WARNING - not found: {lesson_title!r}')
                    continue
                lesson_id, current_desc = row[0], row[1] or ''
                if len(current_desc) < 500:  # not yet rich
                    conn.execute(text(
                        'UPDATE lesson SET description = :html WHERE id = :lid'
                    ), {'html': html, 'lid': lesson_id})
                    updated += 1
                    print(f'[seed_desc] Updated id={lesson_id}: {lesson_title}')
            if updated:
                conn.commit()
                print(f'[seed_desc] Done — {updated} lesson(s) updated.')
            else:
                print('[seed_desc] All descriptions already rich.')
    except Exception as e:
        print(f'[seed_desc] ERROR: {e}')


# ── Forzar actualización de descripciones (solo admin) ───────────────────────

# Map of (course_title, lesson_title) → html description
LESSON_DESCRIPTIONS = {
    ('FASE 1 Crea tu Marca Personal', '1.1 Bienvenida.'): """<h2>¡Bienvenido!</h2>
<p>Estás en el lugar indicado para cambiar tu vida.</p>
<p>Lo más difícil ya lo has hecho, tener la humildad de aprender y formarte, así que mis más sincera enhorabuena.</p>
<p>Si tienes cualquier duda puedes anotarla en este formulario: <a href="https://forms.gle/FQ3L3W7E8Q8sNtaH8" target="_blank" rel="noopener noreferrer">https://forms.gle/FQ3L3W7E8Q8sNtaH8</a> — las dudas se resuelven los martes a las 20h (hora de España).</p>
<p>Tu camino empieza aquí y va a ser de dentro hacia afuera.</p>
<p><strong>VAMOS.</strong></p>""",

    ('FASE 1 Crea tu Marca Personal', '3.1 Conócete a ti mismo, define tu identidad.'): """<p>Los fundamentos para crear una Marca Personal se basan en:</p>
<ul>
  <li><strong>La identidad:</strong> Todo aquello que te define, desde tu manera de hablar, tu vestimenta, el color que utilizas para tus videos, tu peinado... También todo lo que está dentro de ti, como tu seguridad, la dureza del mensaje, la dulzura... Todo esto se puede entrenar y moldear para ir definiendo nuestra identidad.</li>
  <li><strong>Valor:</strong> El valor es lo que ayudas a los demás con tu mensaje, la identidad es lo que más le ayuda al otro y lo que más transmite, pero luego esta el mensaje. La información es la vía por la cual nosotros vamos a llegar al otro, un mensaje autentico, nuevo, fresco, creativo... va a atraer a nuestra audiencia.</li>
  <li><strong>Estrategia:</strong> La estrategia seria conocer el medio (las redes sociales), tener una fuente de ingresos, crear comunidad... Todo lo que tiene que ver con lo mecánico y los sistemas.</li>
</ul>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/5cd82c8ec20d4829a265e27212e9110e185a4ff7c916483398c51d6d679f9659-md.jpg" alt="Identidad, Valor, Estrategia" style="max-width:100%;border-radius:8px;margin:.75em 0"/>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/693fe4b714044043a07b4d3f11c3974a5d456466f2274b5797a886ff16ab5a9d-md.jpg" alt="" style="max-width:100%;border-radius:8px;margin:.75em 0"/>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/f21500dd3a41416a94d13f619749cae0e794403bde3b48b4bfbb5336b8520bed-md.jpg" alt="" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Singularidad y diferenciación</strong></p>
<p>Encuentra elementos que solo tú compartas y que formen parte de tu marca, podría ser un deporte, una actividad, una forma de vestir, una bebida... algo con lo que tu audiencia se identifique.</p>
<p>Tu historia es algo único, comparte tu evolución y tu historia de vida, de superación, te recomiendo que apliques el viaje del héroe a tu historia.</p>

<p><strong>Define qué es lo que haces</strong></p>
<p>Es importante definir que es lo que haces con una frase, para cuando alguien te pregunte o tengas que poner la descripción en tu Instagram o YouTube sepas directamente que poner. Ejemplo: <em>"Soy Samuel divulgador de la consciencia para generar un impacto en las personas y que estas puedan mejorar su vida y hacer de este mundo un lugar mejor."</em></p>
<p>Así mismo te recomiendo que apuntes en una <strong>lista los valores para tu marca.</strong> El valor, la integridad, la libertad, el amor... Para que sea lo que guíe tu camino y comuniques desde ahí.</p>

<p><strong>Haz una breve lista sobre qué problema resuelves</strong></p>
<p>Es fundamental determinar quién es la persona que vas a ayudar, aunque aún sea un poco pronto y lo iremos construyendo poco a poco a lo largo del master, coged ese arquetipo de persona que vais a ayudar con vuestro contenido y luego con vuestro proyecto.</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/45ad3f1af90a468e8c6a2880c20e6bf16adc5e1452b640e295df14b583e8ef28-md.jpg" alt="Ejercicio autoestima" style="max-width:100%;border-radius:8px;margin:.75em 0"/>
<p>Esto es un poco avanzado para el punto en el que estás, pero son términos que está bien que te vayan sonando. No te preocupes si esto no te sale, es lo que más vamos a trabajar a lo largo del máster.</p>

<p><strong>Branding</strong></p>
<p>Aquí nos metemos de lleno en la imagen de marca. Es sencillo, fíjate en quién te fijas y ve implementándolo en ti con tu estilo natural. Si te ves a ti mismo en tu mejor versión, ¿qué peinado lleva? ¿Cómo viste? ¿Qué complementos se pone?</p>
<ul>
  <li><strong>Tipo de letra.</strong></li>
  <li><strong>Ropa.</strong></li>
  <li><strong>Decoración.</strong></li>
  <li><strong>Peinado.</strong></li>
  <li><strong>Colores.</strong></li>
  <li><strong>Estilo.</strong></li>
  <li><strong>Energía.</strong></li>
</ul>
<p>Todas estas cualidades y más que se te vayan ocurriendo las puedes ir definiendo y poniendo en un documento con imágenes, recortes, anotaciones...</p>

<p><strong>¿Qué te diferencia de los demás?</strong></p>
<p>Haz una lista de tus cualidades, de las cosas que crees que eres mejor que el resto. Haz lo mismo con lo que creas que te cuesta más. Tener la virtud de poner luz en nuestras sombras nos hace tener más información para tomar mejores decisiones en un futuro.</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/d3ca0dbc6b804e0fa5c5e3cd8b1452c7612202d3bdd34eb38441697eb0d12457-md.jpg" alt="Ejercicio autoestima 2" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Autoconocimiento</strong></p>
<p>Te insto a que investigues sobre el eneagrama, los arquetipos de Carl Jung o cualquier herramienta de autoconocimiento, esto te dará una ventaja competitiva brutal.</p>

<p><strong>Potencia tu marca</strong></p>
<p>Mira en qué tribu social perteneces, quién es tu bando contrario, con quién te identificas. Esto puede definir mucho tu nicho y puedes hacer que tus seguidores te tengan como ídolo y referente en su causa.</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/0746927daba241e5a66447ca4d0ae9716f4cfe9901eb4e1ea1a02c847fac3ee3-md.jpg" alt="Tribu" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Haz de tu vida una película.</strong></p>
<p>Internet nos da una ventana al mundo, pero solo una ventana, cuida muy bien qué aparece, no porque tengas que impostar nada, ni ser una persona que no eres, sino que le pongas el alma a aquello que dejas ver por la ventana.</p>

<p><strong>Haz tu carta de diseño humano</strong></p>
<p><a href="https://freehumandesignchart.com/" target="_blank" rel="noopener noreferrer nofollow">https://freehumandesignchart.com/</a></p>
<p>Y lo comentamos en la llamada personal.</p>

<p><strong>Proyección</strong></p>
<p>¿Quién te inspira? ¿Cuál es la cualidad? (Para este ejercicio ver el vídeo)</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/99c0474c9e4f437e9f8f3931c8f1a66d856194f4da5147abb5972b2cbdc10b11-md.jpg" alt="Proyección" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Visualiza</strong></p>
<p>Este ejercicio es fundamental, visualiza dónde quieres estar, cuáles son tus objetivos, y siéntete como si ya los hubieras conseguido.</p>
<p>Puedes hacerte una visual board o visualizarte cuando no estés haciendo ninguna tarea intelectual. Da igual como lo hagas, pero define con todo lujo de detalles dónde quieres llegar y qué quieres hacer.</p>""",
}


def fix_duplicate_fase5():
    """Elimina la FASE 5 duplicada/mala de forma permanente.

    Reglas:
    - Si hay 2+ FASE 5: borra todas excepto la que tiene MÁS secciones.
      En caso de empate, conserva la de ID más alto (más nueva = seed_fase5).
    - Si solo hay 1 FASE 5 y tiene menos de 4 secciones: está rota/vacía → borra
      para que seed_fase5() la recree correctamente.
    - Si solo hay 1 con 4+ secciones: ya es la buena, no hace nada.
    """
    try:
        courses = Course.query.filter(Course.title.ilike('%FASE 5%')).all()

        if not courses:
            return  # seed_fase5() la creará

        if len(courses) == 1:
            c = courses[0]
            if len(c.sections) < 4:
                print(f'[fix_duplicate_fase5] Solo hay 1 FASE 5 (id={c.id}) pero tiene {len(c.sections)} secciones (<4) → rota, eliminando.')
                _delete_course_safely(c.id)
                print('[fix_duplicate_fase5] Eliminada. seed_fase5() la recreará correctamente.')
            return

        # Hay 2+ → queda la que tiene MÁS secciones; en empate, la de ID más alto
        sorted_courses = sorted(courses, key=lambda c: (len(c.sections), c.id), reverse=True)
        keep = sorted_courses[0]
        to_delete = sorted_courses[1:]

        print(f'[fix_duplicate_fase5] {len(courses)} FASE 5 encontradas. Conservando id={keep.id} ({len(keep.sections)} secciones).')
        for bad in to_delete:
            print(f'[fix_duplicate_fase5] Eliminando duplicado id={bad.id} ({len(bad.sections)} secciones)...')
            _delete_course_safely(bad.id)
            print(f'[fix_duplicate_fase5] Eliminado.')
    except Exception as e:
        print(f'[fix_duplicate_fase5] ERROR: {e}')


def seed_fase5():
    """Create the FASE 5 MENTALIDAD course with all sections and lessons if it doesn't exist."""
    try:
        if Course.query.filter_by(title='FASE 5 MENTALIDAD').first():
            return

        course = Course(
            title='FASE 5 MENTALIDAD',
            subtitle='Todo el desarrollo personal que necesitas para ser autentico y volverte magnético y viral.',
            is_published=True,
            price=0.0,
            image='https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/c8e27db218c843f5af0e2b02f6daba519f0cdd8d0a1e4ceb990888649241cae6.jpg',
        )
        db.session.add(course)
        db.session.flush()

        _sections = [
            ('1 Hábitos para la paz mental', 0, [
                ('1.1 Introduccion',                        'https://vimeo.com/749878520'),
                ('1.2 Como realizar este curso',            'https://vimeo.com/749881629/e2cbd4caf7'),
                ('1.3 ¿Porque cuesta tanto cambiar?',      None),
                ('2.1 El presente',                        None),
                ('2.1.1 Profundizando en la meditacion',   None),
                ('2.2 Pensar menos, sentir mas',           None),
                ('2.3 Decido vivir este momento.',         None),
                ('2.3.1 Sanar el pasado',                  None),
                ('3.1 La Aceptacion',                      None),
                ('4.1 Como se forma el ego',               None),
                ('4.1.2 ¿Para que?',                       None),
                ('4.1.1 Creencias',                        None),
                ('4.2 Niño Interior',                      None),
                ('5.1 La ilusion de uno mismo',            None),
                ('5.2 Recogida de proyecciones',           None),
                ('5.1.1 Reprogramar la mente',             None),
                ('6.1 Habitos',                            None),
                ('7.1 Mindfull eating.',                   None),
                ('7.2.1 Alimentacion consciente',          None),
                ('7.2.2 Alimentacion consciente',          None),
                ('8.1 Iniciacion a la respiracion',        None),
                ('8.2 Respiracion consciente',             None),
                ('9.1 Energia sexual',                     None),
                ('9.2 Sexualidad consciente',              None),
                ('10. Super habitos',                      None),
                ('11. Cierre de curso + regalo',           'https://vimeo.com/749914145/8f0ad0592b'),
            ]),
            ('2. Encuentra tu proposito', 1, [
                ('1. ¿A que me dedico?',                   'https://vimeo.com/733891828/9bc0bc2936'),
                ('2. Hoy vas a encontrar tu propósito.',   'https://vimeo.com/738144908/10eafd0ae1'),
                ('3. Tu don y tu talento.',                'https://vimeo.com/738152347/ea9a721a10'),
                ('4. El camino al propósito.',             'https://vimeo.com/733930732/8b5e4907c4'),
                ('5. El ego.',                             'https://vimeo.com/734454135/6eceed3077'),
                ('6. Monetiza tu pasión.',                 'https://vimeo.com/738158599/0f607a9a0d'),
            ]),
            ('5 REPROGRAMACIÓN MENTAL NIÑO INTERIOR', 2, [
                ('1. El Ambiente donde te programaste.',   'https://vimeo.com/1133998226'),
                ('2. La emoción que viviste de niño.',     'https://vimeo.com/1136253801'),
                ('3. Como se forja el personaje',          'https://vimeo.com/1138661240'),
                ('4. Desprogramando la mente',             'https://vimeo.com/1140914534'),
                ('5. Encuentro con el niño.',              'https://vimeo.com/1143207136'),
                ('6. Recogida de proyecciones.',           'https://vimeo.com/1145401240'),
                ('7. El personaje',                        'https://vimeo.com/1147459657'),
                ('8. El sistema del personaje.',           'https://vimeo.com/1152337303'),
                ('9. Final niño interior.',                'https://vimeo.com/1154444356'),
            ]),
            ('6 PROGRAMA TU MENTE PARA LA ABUNDANCIA', 3, [
                ('6.1 Atraer Abundancia y Dinero Cambiando tu Mente', 'https://youtu.be/l27PoZo_rpQ'),
                ('6.2 Tu vieja identidad sobre el dinero.',           'https://youtu.be/nG9F_gKpTTM'),
                ('6.3 El Dinero Está En La Relación Con Tu Padre',    'https://youtu.be/7samMzQPuzo'),
            ]),
        ]

        for sec_title, sec_order, lessons in _sections:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url or '',
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_fase5] FASE 5 MENTALIDAD course created with all sections and lessons.')
    except Exception as e:
        print(f'[seed_fase5] ERROR: {e}')
        db.session.rollback()


def seed_bono_habitos():
    """Ensure FASE 5 has a single '1. Habitos para la paz mental' section
    with all 26 lessons. Cleans up any old sub-section structure."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return

        # Guard: flat section already exists → nothing to do
        if Section.query.filter_by(course_id=course.id,
                                   title='1. Habitos para la paz mental').first():
            return

        # --- Cleanup: remove any sub-sections OR old flat section ---
        # Matches old names with or without accents / numbering variants
        old_names = [
            '1 Habitos para la paz mental',
            '1. Introduccion', '1. Introducción',
            '2. Aqui y ahora.', '2. Aquí y ahora.',
            '3. Aceptacion.', '3. Aceptación.',
            '4. La Mascara.',
            '5. La imagen de uno mismo.',
            '6. Habitos.', '6. Hábitos.',
            '7. Alimentacion.', '7. Alimentación.',
            '8. Respiracion.', '8. Respiración.',
            '9. Energia sexual.',
            '10. Super habitos y cierre.', '10. Super hábitos y cierre.',
        ]
        # Collect all sections to remove (by name or by order 0-9)
        secs_to_remove = []
        for name in old_names:
            sec = Section.query.filter_by(course_id=course.id, title=name).first()
            if sec and sec not in secs_to_remove:
                secs_to_remove.append(sec)
        for sec in Section.query.filter_by(course_id=course.id).filter(
                Section.order >= 0, Section.order <= 9).all():
            if sec not in secs_to_remove:
                secs_to_remove.append(sec)

        # Delete LessonProgress first to avoid FK constraint errors
        for sec in secs_to_remove:
            for lesson in sec.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
        db.session.flush()

        for sec in secs_to_remove:
            db.session.delete(sec)
        db.session.flush()

        # Reorder remaining sections compactly starting at 2
        remaining = Section.query.filter_by(course_id=course.id).order_by(Section.order).all()
        for i, sec in enumerate(remaining):
            sec.order = i + 2
        db.session.flush()

        # Create the single flat section at order 1
        new_sec = Section(course_id=course.id,
                          title='1. Habitos para la paz mental', order=1)
        db.session.add(new_sec)
        db.session.flush()

        _lessons = [
            ('1.1 Introduccion',                      'https://vimeo.com/749878520'),
            ('1.2 Como realizar este curso',           'https://vimeo.com/749881629/e2cbd4caf7'),
            ('1.3 Porque cuesta tanto cambiar',        'https://vimeo.com/749884233/1e320d927f'),
            ('2.1 El presente',                        'https://vimeo.com/749887187/ffba41cccb'),
            ('2.1.1 Profundizando en la meditacion',   'https://vimeo.com/749890461/a00d1504e0'),
            ('2.2 Pensar menos, sentir mas',           'https://vimeo.com/749888068/213b9224b8'),
            ('2.3 Decido vivir este momento',          'https://vimeo.com/749888144/f7e415bb2e'),
            ('2.3.1 Sanar el pasado',                  'https://vimeo.com/749892494/b00e80badc'),
            ('3.1 La Aceptacion',                      'https://vimeo.com/749893948/5b13abd2ba'),
            ('4.1 Como se forma el ego',               'https://vimeo.com/749894742/1fdf42c662'),
            ('4.1.2 Para que',                         'https://vimeo.com/749894828/5cdc074054'),
            ('4.1.1 Creencias',                        'https://vimeo.com/749894807/57e7fcf8e1'),
            ('4.2 Nino Interior',                      'https://vimeo.com/749897628/38e3e3a08d'),
            ('5.1 La ilusion de uno mismo',            'https://vimeo.com/749899407/9cef2eec80'),
            ('5.2 Recogida de proyecciones',           'https://vimeo.com/749901468/84733c5bfc'),
            ('5.1.1 Reprogramar la mente',             'https://vimeo.com/749899500/3357242a3d'),
            ('6.1 Reprogramar la mente',               'https://vimeo.com/749899500/3357242a3d'),
            ('7.1 Mindfull eating',                    'https://vimeo.com/749904175/162461a778'),
            ('7.2.1 Alimentacion consciente',          'https://vimeo.com/749906274/43a19e519b'),
            ('7.2.2 Alimentacion consciente 2',        'https://vimeo.com/749906363/e00d5f300d'),
            ('8.1 Iniciacion a la respiracion',        'https://vimeo.com/749908687/b0c7e3572b'),
            ('8.2 Respiracion consciente',             'https://vimeo.com/749909287/19c2af632c'),
            ('9.1 Energia sexual',                     'https://vimeo.com/749910594/f5716a6412'),
            ('9.2 Sexualidad consciente',              'https://vimeo.com/749910707/f8b9f064cf'),
            ('10. Super habitos',                      'https://vimeo.com/749912323/da572845b1'),
            ('11. Cierre de curso + regalo',           'https://vimeo.com/749914145/8f0ad0592b'),
        ]
        for l_order, (l_title, l_url) in enumerate(_lessons):
            db.session.add(Lesson(
                section_id=new_sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        print('[seed_bono_habitos] Seccion plana "1. Habitos para la paz mental" creada con 26 lecciones.')
    except Exception as e:
        print(f'[seed_bono_habitos] ERROR: {e}')
        db.session.rollback()


def seed_bono_organizacion():
    """Insert '3. Organización para creadores' into FASE 5 at order 12,
    shifting any existing sections with order >= 12 up by 1."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return
        if Section.query.filter_by(course_id=course.id, title='3. Organización para creadores').first():
            return
        # Shift sections with order >= 12 up by 1 to make room
        for sec in Section.query.filter_by(course_id=course.id).filter(Section.order >= 12).all():
            sec.order += 1
        db.session.flush()

        sec = Section(course_id=course.id, title='3. Organización para creadores', order=12)
        db.session.add(sec)
        db.session.flush()

        _org_lessons = [
            ('1. Introduccion (Valores)',                    'https://vimeo.com/792949917/3501d6b099'),
            ('2. La importancia de la organizacion',         'https://vimeo.com/792950165/8bb066c84e'),
            ('3. La concentracion',                          'https://vimeo.com/792950514/2d6ae9c0ae'),
            ('4. Distracciones',                             'https://vimeo.com/792950884/59d066a593'),
            ('5. Ladrones de tiempo',                        'https://vimeo.com/792951168/1b496771ac'),
            ('6. Decir que no',                              'https://vimeo.com/792951669/aa2eeef11f'),
            ('7. Tu energia',                                'https://vimeo.com/792952021/c4c9d1a216'),
            ('8. Multitarea y mision de vida',               'https://vimeo.com/792952713/45ca8844e5'),
            ('9. Empezar a organizar nuestra vida',          'https://vimeo.com/792953317/c1f6008c6f'),
            ('10. Tus 4 Roles',                              'https://vimeo.com/792954676/9bec279e9f'),
            ('11. Objetivos',                                'https://vimeo.com/792955039/28608189dd'),
            ('12. Los 3 objetivos del dia',                  'https://vimeo.com/792955692/96cda7441c'),
            ('13. Cortafuegos',                              'https://vimeo.com/792956258/e7e477d773'),
            ('14. Capsulas',                                 'https://vimeo.com/792956721/8f32e161d6'),
            ('15. Comprometerse',                            'https://vimeo.com/792957399/ce80a7c8de'),
            ('16. Minimalismo',                              'https://vimeo.com/792957978/e9eabf25b1'),
            ('17. Delegar y optimizar',                      'https://vimeo.com/792959094/c5962fdb7e'),
            ('18. Automatizacion',                           'https://vimeo.com/792960902/00db3c90ee'),
            ('19. El correo electronico',                    'https://vimeo.com/792962419/7bc02d0c61'),
            ('20. Final + Preguntas y Respuestas',           'https://vimeo.com/792962621/f3aff50888'),
            ('21. Preguntas y Respuestas 1',                 'https://vimeo.com/792964436/ca0d78f106'),
            ('22. Preguntas y Respuestas 2',                 'https://vimeo.com/792949615/f89a85ec32'),
            ('23. BONO Como funciona Notion',                'https://youtu.be/_W_hyG5qNq0?si=bkSO6HcfUjGK-WM7'),
            ('24. Organizacion para creadores de contenido', 'https://vimeo.com/952664864'),
        ]
        for l_order, (l_title, l_url) in enumerate(_org_lessons):
            db.session.add(Lesson(
                section_id=sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        print('[seed_bono_organizacion] Seccion "3. Organizacion para creadores" anadida a FASE 5.')
    except Exception as e:
        print(f'[seed_bono_organizacion] ERROR: {e}')
        db.session.rollback()


def seed_liberacion_emocional():
    """Insert '4. Liberacion emocional' into FASE 5 with 18 lessons."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return
        if Section.query.filter_by(course_id=course.id,
                                   title='4. Liberacion emocional').first():
            return
        # Shift sections with order >= 13 up by 1 to make room at order 13
        for sec in Section.query.filter_by(course_id=course.id).filter(
                Section.order >= 13).all():
            sec.order += 1
        db.session.flush()

        sec = Section(course_id=course.id, title='4. Liberacion emocional', order=13)
        db.session.add(sec)
        db.session.flush()

        _lessons = [
            ('Bienvenidos',   'https://vimeo.com/719536396'),
            ('Capitulo 1',    'https://vimeo.com/719536479'),
            ('Capitulo 2',    'https://vimeo.com/719536500'),
            ('Capitulo 3',    'https://vimeo.com/719536514'),
            ('Capitulo 4',    'https://vimeo.com/719536558'),
            ('Capitulo 5',    'https://vimeo.com/721359695'),
            ('Capitulo 6',    'https://vimeo.com/719536688'),
            ('Capitulo 7',    'https://vimeo.com/719536704'),
            ('Capitulo 8',    'https://vimeo.com/719536728'),
            ('Capitulo 9',    'https://vimeo.com/720549604'),
            ('Capitulo 10',   'https://vimeo.com/720555536'),
            ('Capitulo 11',   'https://vimeo.com/720564299'),
            ('Capitulo 12',   'https://vimeo.com/720564393'),
            ('Capitulo 13',   'https://vimeo.com/720564485'),
            ('Capitulo 14',   'https://vimeo.com/721350229'),
            ('Capitulo 15',   'https://vimeo.com/721350331'),
            ('Capitulo 16',   'https://vimeo.com/721350382'),
            ('Capitulo 17',   'https://vimeo.com/803924439'),
        ]
        for l_order, (l_title, l_url) in enumerate(_lessons):
            db.session.add(Lesson(
                section_id=sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        print('[seed_liberacion_emocional] Seccion "4. Liberacion emocional" creada con 18 lecciones.')
    except Exception as e:
        print(f'[seed_liberacion_emocional] ERROR: {e}')
        db.session.rollback()


_PREMIERE_LESSONS = [
    ('Introduccion',  'https://vimeo.com/828873589/ae73df2542'),
    ('Capitulo 1',    'https://vimeo.com/828874651/6bd0a0d35d'),
    ('Capitulo 2',    'https://vimeo.com/828874928/5bdc98bce0'),
    ('Capitulo 3',    'https://vimeo.com/828875116/62f0232ea8'),
    ('Capitulo 4',    'https://vimeo.com/828876182/ab62fe56c0'),
    ('Capitulo 5',    'https://vimeo.com/828877925/aa07c9debb'),
    ('Capitulo 6',    'https://vimeo.com/828879749/b7372fbba9'),
    ('Capitulo 7',    'https://vimeo.com/828881488/3a76019d7e'),
    ('Capitulo 8',    'https://vimeo.com/828883812/0b8033b8e7'),
    ('Capitulo 9',    'https://vimeo.com/828885212/75625f9a8d'),
    ('Capitulo 10',   'https://vimeo.com/828886466/7a65b850c5'),
    ('Capitulo 11',   'https://vimeo.com/828887594/775adbf33d'),
    ('Capitulo 12',   'https://vimeo.com/828888224/ecb31a3af2'),
    ('Capitulo 13',   'https://vimeo.com/828889019/a66ded4671'),
    ('Capitulo 14',   'https://vimeo.com/828889334/c25b1ceb62'),
    ('Capitulo 15',   'https://vimeo.com/828890481/9e86341402'),
    ('Capitulo 16',   'https://vimeo.com/828891176/f30d0c698a'),
    ('Capitulo 17',   'https://vimeo.com/828892828/679f839587'),
    ('Capitulo 18',   'https://vimeo.com/828893963/c78e0e28c9'),
    ('Capitulo 19',   'https://vimeo.com/828894481/3665f76746'),
    ('Capitulo 20',   'https://vimeo.com/828895240/fddafac1e2'),
    ('Capitulo 21',   'https://vimeo.com/828895753/5d4c219ad6'),
    ('Capitulo 22',   'https://vimeo.com/828896136/5469431a93'),
    ('Capitulo 23',   'https://vimeo.com/828896587/6d2b184113'),
]

_CAPCUT_LESSONS = [
    ('1.1 Introduccion',                              'https://vimeo.com/1031721899', '1. Introduccion al curso y primeros pasos'),
    ('1.2 Como instalar Capcut para PC',              'https://vimeo.com/1031721943', '1. Introduccion al curso y primeros pasos'),
    ('1.3 Cambio de idioma',                          'https://vimeo.com/1031721869', '1. Introduccion al curso y primeros pasos'),
    ('2.1 Conociendo la interfaz de Capcut',          'https://vimeo.com/1031721976', '2. Conociendo la interfaz de Capcut'),
    ('3. Atajos y configuracion del teclado',         'https://vimeo.com/1031722032', '3. Atajos y configuracion del teclado'),
    ('4.1 Primeros pasos en la Creacion de un Proyecto', 'https://vimeo.com/1031722268', '4. Creacion de un Proyecto y Gestion de Archivos'),
    ('4.2 Ajuste del Formato y Dimensiones del Video','https://vimeo.com/1031722198', '4. Creacion de un Proyecto y Gestion de Archivos'),
    ('5.1 Como cortar videos',                        'https://vimeo.com/1031723250', '5. Recursos para crear videos virales'),
    ('5.2 Transiciones y efectos de sonido',          'https://vimeo.com/1031723322', '5. Recursos para crear videos virales'),
    ('5.3 Capas y Superposicion de Elementos',        'https://vimeo.com/1031723378', '5. Recursos para crear videos virales'),
    ('5.4 Textos y Subtitulos en Video',              'https://vimeo.com/1031723452', '5. Recursos para crear videos virales'),
    ('5.5 Audio y efectos de voz',                    'https://vimeo.com/1031723532', '5. Recursos para crear videos virales'),
    ('5.6 Efectos y animacion',                       'https://vimeo.com/1031723592', '5. Recursos para crear videos virales'),
    ('5.7 Musica',                                    'https://vimeo.com/1031723678', '5. Recursos para crear videos virales'),
    ('5.8 Elementos graficos para destacar puntos',   'https://vimeo.com/1031722936', '5. Recursos para crear videos virales'),
    ('5.9 Zoom y Keyframes',                          'https://vimeo.com/1031722983', '5. Recursos para crear videos virales'),
    ('5.10 Filtros',                                  'https://vimeo.com/1031723083', '5. Recursos para crear videos virales'),
    ('5.11 Exportacion del Video',                    'https://vimeo.com/1031723161', '5. Recursos para crear videos virales'),
    ('6.1 Conclusion',                                'https://vimeo.com/1031723091', '6. Conclusion'),
]

_WEB_LESSONS = [
    ('0. Bienvenidos',                              'https://vimeo.com/953217254'),
    ('1. Eligiendo y contratando nuestro hosting',  'https://vimeo.com/953194088'),
    ('2. Configuración e instalación de Wordpress', 'https://vimeo.com/953194175'),
    ('3. Panel de control de Wordpress',            'https://vimeo.com/953194195'),
    ('4. Iniciando sesión y editor nativo de Wordpress', 'https://vimeo.com/953194296'),
    ('5. Instalando elementor',                     'https://vimeo.com/953194327'),
    ('6. Jugando con Wordpress y Elementor',        'https://vimeo.com/953194382'),
    ('7. Editor de Elementor al completo',          'https://vimeo.com/953194406'),
    ('8. Vinculando nuestra cuenta de elementor',   'https://vimeo.com/953194499'),
    ('9. Descubriendo las plantillas de elementor', 'https://vimeo.com/953193184'),
    ('10. Mejores temas para Elementor',            'https://vimeo.com/953193668'),
    ('11. Instalando e importando nuestro primer tema', 'https://vimeo.com/953193585'),
    ('12. Opciones globales de configuración',      'https://vimeo.com/953193771'),
    ('13. Personalizando nuestro Header + LOGO',    'https://vimeo.com/953193299'),
    ('14. Elementos de nuestro sitio web: Títulos', 'https://vimeo.com/953193834'),
    ('15. Botones, enlaces y rutas',                'https://vimeo.com/953193412'),
    ('16. Columnas y secciones',                    'https://vimeo.com/953193456'),
    ('17. Sección de vídeo y enlaces',              'https://vimeo.com/953193909'),
    ('18. Carrousel de imágenes',                   'https://vimeo.com/953194006'),
    ('19. Cómo hacer y restaurar copias de seguridad', 'https://vimeo.com/953194053'),
    ('20. Plugins avanzados de seguridad',          'https://vimeo.com/953193531'),
]


def _build_programas_sections(course):
    """Helper: create/ensure '1. Capcut', '2. Premiere' and '3. Crea tu web' sections."""
    existing_titles = {s.title for s in course.sections}

    if '1. Capcut' not in existing_titles:
        sec = Section(course_id=course.id, title='1. Capcut', order=0)
        db.session.add(sec)
        db.session.flush()
        for l_order, (l_title, l_url, l_group) in enumerate(_CAPCUT_LESSONS):
            db.session.add(Lesson(section_id=sec.id, title=l_title,
                                  video_url=l_url, group_label=l_group, order=l_order))

    if '2. Premiere' not in existing_titles:
        sec2 = Section(course_id=course.id, title='2. Premiere', order=1)
        db.session.add(sec2)
        db.session.flush()
        for l_order, (l_title, l_url) in enumerate(_PREMIERE_LESSONS):
            db.session.add(Lesson(section_id=sec2.id, title=l_title,
                                  video_url=l_url, order=l_order))

    if '3. Crea tu web' not in existing_titles:
        sec3 = Section(course_id=course.id, title='3. Crea tu web', order=2)
        db.session.add(sec3)
        db.session.flush()
        for l_order, (l_title, l_url) in enumerate(_WEB_LESSONS):
            db.session.add(Lesson(section_id=sec3.id, title=l_title,
                                  video_url=l_url, order=l_order))


def seed_programas_marca():
    try:
        course = Course.query.filter_by(title='Programas para tu marca').first()

        if not course:
            course = Course(
                title='Programas para tu marca',
                subtitle='Herramientas y programas para potenciar tu marca personal',
                description='Formaciones sobre herramientas clave para crear y hacer crecer tu marca personal.',
                is_published=True,
                price=0.0,
            )
            db.session.add(course)
            db.session.flush()
            _build_programas_sections(course)
        else:
            sections = Section.query.filter_by(course_id=course.id).all()
            titles = {s.title for s in sections}

            # If old multi-section capcut structure exists, wipe and rebuild
            valid = (
                {'1. Capcut'},
                {'1. Capcut', '2. Premiere'},
                {'1. Capcut', '2. Premiere', '3. Crea tu web'},
            )
            if titles not in valid:
                for sec in sections:
                    for lesson in sec.lessons:
                        LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
                    db.session.delete(sec)
                db.session.flush()

            _build_programas_sections(course)

        db.session.commit()
        print('[seed_programas_marca] "Programas para tu marca" actualizado: Capcut + Premiere + Crea tu web.')
    except Exception as e:
        print(f'[seed_programas_marca] ERROR: {e}')
        db.session.rollback()


def seed_clases_2026():
    try:
        if Course.query.filter_by(title='Clases pasadas grabadas 2026').first():
            return
        course = Course(
            title='Clases pasadas grabadas 2026',
            subtitle='Todas las clases en directo de 2026',
            description='Accede a todas las grabaciones de las clases en directo realizadas durante 2026.',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.flush()

        sections_data = [
            ('Mayo 2026', 0, [
                ('6-5-2026 Constancia',  'https://vimeo.com/1189876173'),
                ('6-5-2026 Ventas',      'https://vimeo.com/1189510087'),
                ('3-5-2026',             'https://vimeo.com/1188857174'),
            ]),
            ('Abril 2026', 1, [
                ('29-4-2026 Creatividad',                          ''),
                ('28-4-2026 Mentalidad',                           'https://vimeo.com/1187597054'),
                ('26-4-2026 Retencion de Atencion por psicologia', 'https://vimeo.com/1186745469'),
                ('20-4-2026 Youtube y herramientas IA',            'https://vimeo.com/1184715176'),
                ('19-4-2026 - Juego de paja o mina de oro',        'https://vimeo.com/1184537495'),
                ('4-4-2026 - Meta Ads - Mentalidad - Coherencia',  'https://vimeo.com/1180151061'),
                ('2-4-2026 Dejar ir',                              'https://vimeo.com/1179488825'),
            ]),
            ('Marzo 2026', 2, [
                ('26-3-2026 Cuenta tu historia',          'https://vimeo.com/1177467500'),
                ('24-3-2026 Autoridad',                   'https://vimeo.com/1176131131'),
                ('18-3-2026',                             'https://vimeo.com/1174683508'),
                ('15-3-2026',                             'https://vimeo.com/1173810561'),
                ('14-03-2026 El poder de la comunicacion','https://vimeo.com/1173634843'),
                ('11-03-2026',                            'https://vimeo.com/1172320523'),
                ('4-3-2026 Mentalidad y habitos',         'https://vimeo.com/1170587953'),
                ('1-3-2026 Como atraer a tu publico objetivo', 'https://vimeo.com/1169364567'),
            ]),
            ('Febrero 2026', 3, [
                ('25-2-2026 Mentalidad y dinero',              'https://vimeo.com/1168247763'),
                ('25-2-2026 Analisis de Marcas Personales',    'https://vimeo.com/1168019884'),
                ('22-02-2026',                                 'https://vimeo.com/1167179538'),
                ('19-2-2026 Mentalidad',                       'https://vimeo.com/1166276374'),
                ('4-2-2026 Romper creencias Marca Personal',   'https://vimeo.com/1161575886'),
            ]),
            ('Enero 2026', 4, [
                ('28-1-2026', 'https://vimeo.com/1159154315'),
            ]),
        ]

        for sec_title, sec_order, lessons in sections_data:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url,
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_clases_2026] Curso "Clases pasadas grabadas 2026" creado con 5 secciones.')
    except Exception as e:
        print(f'[seed_clases_2026] ERROR: {e}')
        db.session.rollback()


def seed_ia():
    try:
        if Course.query.filter_by(title='IA').first():
            return
        course = Course(
            title='IA',
            subtitle='Crea contenido con Inteligencia Artificial y crece en redes sociales',
            description='Aprende a crear contenido faceless con IA: guiones, voz, edición y miniaturas para monetizar tu canal.',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.flush()

        sections_data = [
            ('FASE 1 CREAMOS TU CANAL', 0, [
                ('Bienvenida.',                                  'https://vimeo.com/905900462'),
                ('La mentalidad necesaria para este negocio.',   'https://vimeo.com/905900938'),
                ('¿Qué son los canales automatizados?',          'https://vimeo.com/905903531'),
                ('Como consumir este curso.',                     'https://vimeo.com/905905036'),
                ('Ayúdame.',                                     'https://vimeo.com/905906678'),
                ('2.1 Encuentra tu nicho.',                      'https://vimeo.com/905912313'),
                ('2.1.1 Escoge un nicho en tendencias.',         'https://vimeo.com/1022413674'),
                ('2.2 Abrir tu canal de YouTube.',               'https://vimeo.com/905919839'),
                ('2.3 Personalización del Canal + VidIQ.',       'https://vimeo.com/1022429684'),
                ('2.1.2 Algunos nichos interesantes.',           'https://vimeo.com/1025159053'),
                ('3.0 Analizando a tu audiencia.',               'https://vimeo.com/997656483'),
                ('3.1 Crear un guion con Chat GPT.',             'https://vimeo.com/913400871'),
                ('3.2 Ideas para crear tu guion.',               'https://vimeo.com/914177000'),
                ('GPTs Para crear tus guiones mas realistas.',   'https://vimeo.com/1011825608'),
                ('4.3 Pasar de texto a voz (Eleven Labs)',       'https://vimeo.com/918913820'),
                ('5.1 Edición con CapCut.',                      'https://vimeo.com/921756345'),
                ('5.2 Edición con CapCut Y exportado.',          'https://vimeo.com/925753040'),
                ('6.0 Entrar en Discord para acceder a Midjourney', 'https://vimeo.com/1000681997'),
                ('6.1 Creación de miniaturas con Midjourney.',   'https://vimeo.com/1024298757'),
                ('6.2 Crear miniatura con Canva',                'https://vimeo.com/948289425'),
                ('6.3 Crear miniatura con Leonardo AI',          'https://vimeo.com/1023726321'),
                ('6.4 Crear miniatura con Photoshop.',           'https://vimeo.com/956991589'),
                ('6.5 Mejorando miniaturas',                     'https://vimeo.com/967114138'),
                ('7.1 Resumen de todo lo que hemos visto.',      'https://vimeo.com/1018457003'),
                ('¿Cómo descargar videos de artgrid?',           'https://vimeo.com/948304344'),
            ]),
            ('FASE 2 PROGRAMAS PARA TU MARCA', 1, [
                ('1.1 Programa Animaciones dibujo Mano (VideoScribe)', 'https://vimeo.com/935925338'),
                ('1.2 Crear un avatar con IA (D-ID)',                  'https://vimeo.com/941271568'),
                ('1.3 Crear un avatar de ti (HeyGen)',                 'https://vimeo.com/1013616444'),
                ('2.1 Animación de fotos (Pikalabs)',                  'https://vimeo.com/937626127'),
                ('2.2 De texto a vídeo FLIKI',                         'https://vimeo.com/1022661291'),
                ('3.1 Leonardo AI (programa para hacer miniaturas)',    'https://vimeo.com/1023726321'),
            ]),
        ]

        for sec_title, sec_order, lessons in sections_data:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url,
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_ia] Curso "IA" creado con 2 fases y 31 lecciones.')
    except Exception as e:
        print(f'[seed_ia] ERROR: {e}')
        db.session.rollback()


def seed_coach_profesional():
    try:
        if Course.query.filter_by(title='Hazte Coach profesional').first():
            return
        course = Course(
            title='Hazte Coach profesional',
            subtitle='',
            description='',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.commit()
        print('[seed_coach_profesional] Curso "Hazte Coach profesional" creado.')
    except Exception as e:
        print(f'[seed_coach_profesional] ERROR: {e}')
        db.session.rollback()


def seed_clases_2025():
    try:
        if Course.query.filter_by(title='Clases 2025').first():
            return
        course = Course(
            title='Clases 2025',
            subtitle='Todas las clases en directo grabadas 2024-2025',
            description='Accede a todas las grabaciones de las clases en directo realizadas desde abril 2024 hasta enero 2026.',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.flush()

        sections_data = [
            ('Enero 2026', 0, [
                ('15-1-2026 Como hacer ofertas',                      'https://vimeo.com/1154799599'),
                ('🔴 Final formación niño interior',                   'https://vimeo.com/1154444356'),
                ('11-1-2026 ¿Qué publicar en RRSS?',                  'https://vimeo.com/1153399221'),
                ('17-01-2026 - Como hacer sus primeros 1.000 mil EUR', 'https://vimeo.com/1155578698'),
                ('🔴 8-1-2025 El sistema del personaje.',              'https://vimeo.com/1152337303'),
                ('ENERO: METODO 30X FOCUS - BRYAN TRACY',              'https://vimeo.com/1151250794'),
                ('4-1-2026 Estrategia de contenido',                   'https://vimeo.com/1151511851'),
            ]),
            ('Diciembre 2025', 1, [
                ('28-12-2025 Final de año',                   'https://vimeo.com/1149927847'),
                ('🔴18-12-2025',                              'https://vimeo.com/1147459657'),
                ('16-12-2025',                                'https://vimeo.com/1147107355'),
                ('🔴10-12-2025 Recogida de proyecciones',     'https://vimeo.com/1145401240'),
                ('9-12-2025',                                 'https://vimeo.com/1145021487'),
                ('🔴 3-12-2025 Encuentro con el niño',        'https://vimeo.com/1143207136'),
            ]),
            ('Noviembre 2025', 2, [
                ('🔴 26-11-2025 Niño interior 4',                            'https://vimeo.com/1140914534'),
                ('29-11-2025 Espionaje de competencia + retención',          'https://vimeo.com/1141695080'),
                ('25-11-2015 Validar tu producto',                           'https://vimeo.com/1140554628'),
                ('🔴 19-11-2025 niño interior 3',                            'https://vimeo.com/1138661240'),
                ('19-11-2025 Estrategia general en redes para vender',       'https://vimeo.com/1138410011'),
                ('23-11-2025 Revisión de perfiles + ideas',                  'https://vimeo.com/1139893456'),
                ('16-11-2025 Estrategia entre redes',                        'https://vimeo.com/1137475177'),
                ('15-11-2025 Practica de rolplay + ventas',                  'https://vimeo.com/1137412964'),
                ('🔴 14-11-2025 Niño interior 2',                            'https://vimeo.com/1136253801'),
                ('11-11-2025 Retención en YouTube',                          'https://vimeo.com/1135873618'),
                ('9-11-2025 Historias de Instagram.',                        'https://vimeo.com/1135117297'),
                ('2-11-2025 Conversaciones en instagram',                    'https://vimeo.com/1132961723'),
                ('1-11-2025',                                                'https://vimeo.com/1132816708'),
            ]),
            ('Octubre 2025', 3, [
                ('21-10-2025',                                         'https://vimeo.com/1129305180'),
                ('5-10-2024 hook visuales.',                           'https://vimeo.com/1124648577'),
                ('29-10-2025 Oratoria consciente para redes sociales', 'https://vimeo.com/1131618392'),
            ]),
            ('Septiembre 2025', 4, [
                ('30-9-2025',                                      'https://vimeo.com/1123326774'),
                ('21-9-2025',                                      'https://vimeo.com/1120633252'),
                ('16-9-2025',                                      'https://vimeo.com/1119227745'),
                ('15-9-2025 Pasar de seguidores a clientes',       'https://vimeo.com/1118647697'),
                ('9-9-2025 Estrategia historias de Instagram.',    'https://vimeo.com/1117220874'),
                ('7-9-2025 Storytelling',                          'https://vimeo.com/1116592377'),
                ('2-9-2025 Trucos instagram',                      'https://vimeo.com/1115420774'),
            ]),
            ('Agosto 2025', 5, [
                ('26-8-2025 Análisis Marca Personales Alumnos', 'https://vimeo.com/1113470753'),
                ('24-8-2025',                                   'https://vimeo.com/1112720380'),
                ('17-8-2025',                                   'https://vimeo.com/1110751972'),
                ('5-8-2025',                                    'https://vimeo.com/1107522387'),
                ('3-8-2025 Generar comunidad en historias.',    'https://vimeo.com/1106916027'),
            ]),
            ('Julio 2025', 6, [
                ('30-7-2025',                                                   'https://vimeo.com/1105703917'),
                ('20-7-2025',                                                   'https://vimeo.com/1102956963'),
                ('8-7-2025 Estrategias contenido Youtube e instagram',          'https://vimeo.com/1099773083'),
                ('3-7-2025 Análisis de alumnos.',                               'https://vimeo.com/1098412325'),
                ('1-7-2025 Organización y calendarios de contenidos',           'https://vimeo.com/1097964796'),
            ]),
            ('Junio 2025', 7, [
                ('26-6-2025 Mentalidad',        'https://vimeo.com/1097344696'),
                ('24-6-2025',                   'https://vimeo.com/1096036077'),
                ('18-6-2025 Colaboraciones',    'https://vimeo.com/1094176751'),
                ('11-06-2025 Estrategia ganadora', 'https://vimeo.com/1092234172'),
                ('4-6-2025',                    'https://vimeo.com/1090614061'),
            ]),
            ('Mayo 2025', 8, [
                ('28-5-2025 Estrategia de contenido y producto', 'https://vimeo.com/1088540117'),
                ('20-5-2025 Análisis avatar con chat GPT',       'https://vimeo.com/1086185888'),
                ('18-5-2025 Trucos revisando contenido.',        'https://vimeo.com/1085594526'),
                ('14-5-2025 Trucos en el contenido',             'https://vimeo.com/1084883105'),
                ('13-5-2025 Trucos para instagram',              'https://vimeo.com/1084013765'),
                ('11-5-2025 Proposito en tu contenido.',         'https://vimeo.com/1083423939'),
                ('7-5-2025 Motivación Integrar la Sombra',       'https://vimeo.com/1082314951'),
                ('6-5-2025 Crear contenido viral',               'https://vimeo.com/1081955919'),
                ('4-5-2025 Elevar el nivel de consciencia',      'https://vimeo.com/1081313993'),
            ]),
            ('Abril 2025', 9, [
                ('30-4-2025 Contenido',                                    'https://vimeo.com/1080328343'),
                ('29-4-2025 Titulos',                                      'https://vimeo.com/1080099680'),
                ('23-4-2025 ¿Cómo hacer para que paren en el feed?',      'https://vimeo.com/1077729888'),
                ('16-4-2025 VSL en profundidad',                           'https://vimeo.com/1076164098'),
                ('15-4-2025 Motivación',                                   'https://vimeo.com/1075947951'),
                ('14-4-2025 VSL estrategia completa',                      'https://vimeo.com/1075124049'),
                ('9-4-2025 Historias de instagram',                        'https://vimeo.com/1074067588'),
                ('8-4-2025 Vender por Whastapp',                           'https://vimeo.com/1073686689'),
                ('6-4-2025',                                               'https://vimeo.com/1073010901'),
                ('1-4-2025 Automatización con IA TONET',                   'https://vimeo.com/1071552454'),
            ]),
            ('Marzo 2025', 10, [
                ('30-3-2025 IA como asistente',                         'https://vimeo.com/1070842309'),
                ('26-3-2025 Crecimiento Masivo en redes clase 5',       'https://vimeo.com/1069735468'),
                ('25-3-2025',                                           'https://vimeo.com/1069528783'),
                ('23-3-2025',                                           'https://vimeo.com/1068660985'),
                ('19-3-2025',                                           'https://vimeo.com/1067492688'),
                ('16-3-2025',                                           'https://vimeo.com/1066381229'),
                ('15-3-2025',                                           'https://vimeo.com/1066211842'),
                ('12-3-2025 Constancia',                                'https://vimeo.com/1065246322'),
                ('11-3-2025 Finanzas personales e inversión',           'https://vimeo.com/1064850100'),
                ('8-3-2025 Google ADS poner anuncio en google.',        'https://vimeo.com/1063919086'),
                ('5-3-2025 Encontrar a tu cliente',                     'https://vimeo.com/1062939381'),
                ('4-3-2025',                                            'https://vimeo.com/1062539139'),
            ]),
            ('Febrero 2025', 11, [
                ('26-2-2025 Avatar 3.0',                            'https://vimeo.com/1060624030'),
                ('25-2-2025',                                       'https://vimeo.com/1060245758'),
                ('19-2-2025 Buyer persona',                         'https://vimeo.com/1058485470'),
                ('17-2-2025 GPT\'s interesantes',                   'https://vimeo.com/1057629154'),
                ('15-2-2025 Edicion con capcut (Jenny)',             'https://vimeo.com/1057281769'),
                ('12-2-2025 Crecimiento masivo en RRSS 1',          'https://vimeo.com/1056140137'),
                ('11-2-2025 Eliminar resistencias',                  'https://vimeo.com/1055722980'),
                ('10-2-2025 Analisis estrategia Marca Personal',     'https://vimeo.com/1055024291'),
                ('09-02-2025 Preguntas y Respuestas',                'https://vimeo.com/1054860750'),
                ('5-2-2025 Copy 8',                                  'https://vimeo.com/1053911839'),
                ('3-2-2025 Estrategia en Youtube',                   'https://vimeo.com/1052928025'),
                ('02-02-2025 Crea tu Oceano Azul',                   'https://vimeo.com/1053342320'),
            ]),
            ('Enero 2025', 12, [
                ('29-1-2025 Copywriting 7',                         'https://vimeo.com/1051679678'),
                ('28-1-2025 Proposito para tu proyecto',            'https://vimeo.com/1051296007'),
                ('27-1-2025',                                       'https://vimeo.com/1050905159'),
                ('25-01-2025 Crecimiento Acelerado con Publicidad', 'https://vimeo.com/1050756441'),
                ('22-1-2025',                                       'https://vimeo.com/1049436953'),
                ('21-1-2025',                                       'https://vimeo.com/1049068089'),
                ('18-01-2025 Servicios y Productos',                'https://vimeo.com/1048587484'),
                ('15-1-2025 Avatar 3.0',                            'https://vimeo.com/1047490228'),
                ('14-1-2025 Avatar 2.0',                            'https://vimeo.com/1047037016'),
                ('12-1-2025 Avatar 0.1',                            'https://vimeo.com/1046217798'),
                ('9-1-2025 Copywriting 6',                          'https://vimeo.com/1045132445'),
                ('07-01-2025 Elevar el nivel de conciencia',        'https://vimeo.com/1044795402'),
            ]),
            ('Diciembre 2024', 13, [
                ('18-12-2024 Copy 5',                          'https://vimeo.com/1040530020'),
                ('15-12-2024 Cosas que te hacen viral',        'https://vimeo.com/1039460534'),
                ('08-12-2024 Crear GPTS',                      'https://vimeo.com/1037256479'),
                ('07-12-2024 Productividad + PyR',             'https://vimeo.com/1037200624'),
                ('03-12-2024 Preguntas y respuestas',          'https://vimeo.com/1035748510'),
                ('30-12-2024 Atraer a tu publico objetivo',    'https://vimeo.com/1043000993'),
            ]),
            ('Noviembre 2024', 14, [
                ('30-11-2024 Videos RolPlay',                   'https://vimeo.com/1034830397'),
                ('27-11-2024 Copywriter 2',                     'https://vimeo.com/1033995541'),
                ('26-11-2024',                                  'https://vimeo.com/1033608600'),
                ('20-11-2024 copy 1',                           'https://vimeo.com/1031685641'),
                ('17-11-2024 Ia de videos',                     'https://vimeo.com/1030542186'),
                ('16-11-2024 ChatGPT y PyR',                    'https://vimeo.com/1030377991'),
                ('15-11-2024 setter 4',                         'https://vimeo.com/1029400098'),
                ('12-11-2024 Análisis mercado para producto',   'https://vimeo.com/1028991502'),
                ('10-11-2024 Analizamos Canales de YT',         'https://vimeo.com/1028205920'),
                ('09-11-2024 Estrategia Mensajes y PyR',        'https://vimeo.com/1028041386'),
                ('07-11-2024 Análisis Marcas Personales',       'https://vimeo.com/1027438043'),
                ('06-11-2024 Monetización YouTube',             'https://vimeo.com/1027049700'),
                ('03-11-2024 Amazon afliliados',                'https://vimeo.com/1025931419'),
                ('02-11-2024 PyR',                              'https://vimeo.com/1028011101'),
            ]),
            ('Octubre 2024', 15, [
                ('30-10-2024 Setter 4',                                     'https://vimeo.com/1024893241'),
                ('29-10-2024 Recursos gratis',                              'https://vimeo.com/1024510579'),
                ('26-10-2024 Estudio de Mercado',                           'https://vimeo.com/1023599192'),
                ('21-10-2024 Analizando nichos para YT',                    'https://vimeo.com/1021497994'),
                ('19-10-2024 VSL y PyR',                                    'https://vimeo.com/1021338754'),
                ('16-10-2024 Setter 3 Análisis de canales y voz',           'https://vimeo.com/1020332240'),
                ('15-10-2024 Como crear una landing page',                  'https://vimeo.com/1019932102'),
                ('9-10-2024 Sistema setter || revision canal',              'https://vimeo.com/1018054139'),
                ('8-10-2024 Aumentar retención y canales de YT',            'https://vimeo.com/1017670354'),
                ('05-10-2024 Ofertas irresistibles y monetización',         'https://vimeo.com/1016561843'),
                ('02-10-2024 Setter figura.',                               'https://vimeo.com/1015401719'),
                ('01-10-2024 P&R',                                          'https://vimeo.com/1015141880'),
            ]),
            ('Septiembre 2024', 16, [
                ('29-9-2024 Inteligencia artificial para contenidos.', 'https://vimeo.com/1014101056'),
                ('28-09-2024 Base de Marketing pre-escalar',           'https://vimeo.com/1014115958'),
                ('25-9-2024 Historias destacadas instagram',           'https://vimeo.com/1012910840'),
                ('25-9-2024 Repasamos canales.',                       'https://vimeo.com/1012674859'),
                ('20-9-2024 Tendencias RRSS 2025',                    'https://vimeo.com/1011097003'),
                ('12-9-2024 Empezar a vender en RRSS',                'https://vimeo.com/1008565130'),
                ('11-9-2024 Reels virales con transición',             'https://vimeo.com/1008183717'),
                ('5-9-2024 Retener la atención.',                      'https://vimeo.com/1006352665'),
                ('3-9-2024 Tendencias en redes sociales.',             'https://vimeo.com/1005962046'),
            ]),
            ('Agosto 2024', 17, [
                ('21-8-2024 Entrenar el Carisma',                  'https://vimeo.com/1001342356'),
                ('20-8-2024 Análisis de Marca Personales.',         'https://vimeo.com/1000895736'),
                ('14-08-2024 Creando atmosfera para vender.',       'https://vimeo.com/998841537'),
                ('13-8-2024 Motivacion y estadisticas par aYT',    'https://vimeo.com/998372315'),
                ('7-8-2024 Edición en capcut',                     'https://vimeo.com/995939545'),
                ('6-8-2024 Revisión de canales',                   'https://vimeo.com/995524046'),
            ]),
            ('Julio 2024', 18, [
                ('30-7-2024 Crecimiento en YT + Ventas.',                    'https://vimeo.com/992315467'),
                ('24-7-2024 Vencer las excusas para crear contenido.',       'https://vimeo.com/989754430'),
                ('23-7-2024 Superar el miedo a crear contenido.',            'https://vimeo.com/989094804'),
                ('17-7-2024 Resolvemos dudas para e crecimiento RRSS',       'https://vimeo.com/986998405'),
                ('16-7-2024 MOTIVACION',                                     'https://vimeo.com/985582401'),
                ('10-7-2024 Retención de la audiencia.',                     'https://vimeo.com/982167628'),
                ('9-7-2024 P&R',                                             'https://vimeo.com/981553993'),
                ('3-7-2024 Posicionamiento SEO YT, Instagram, tiktok',      'https://vimeo.com/975647679'),
                ('2-7-2024 Cambio de estrategia P&R',                        'https://vimeo.com/974426591'),
            ]),
            ('Junio 2024', 19, [
                ('27-6-2024 Vender sin vender.',                            'https://vimeo.com/970090412'),
                ('25-6-2024 Preguntas y Respuestas.',                       'https://vimeo.com/968222164'),
                ('19-6-2024 Facebook ADS (Basico)',                         'https://vimeo.com/962585274'),
                ('18-6-2024 Crecer rápido en Instagram.',                   'https://vimeo.com/961542519'),
                ('12-6-2024 Estrategia de contenido.',                      'https://vimeo.com/970023795'),
                ('11-6-2024 Enlazar FB e Insta, como crear comunidad',      'https://vimeo.com/956706697'),
                ('05-06-2024 Estrategia contenidos + inversiones.',         'https://vimeo.com/958460844'),
                ('04-06-2024 Ads, motivación y ganar dinero.',              'https://vimeo.com/953680797'),
            ]),
            ('Abril 2024', 20, [
                ('29-05-2024 Perder el miedo a la cámara.',      'https://vimeo.com/951669832'),
                ('28-05-2024 Análisis canales de YouTube.',      'https://vimeo.com/951248358'),
                ('22-05-2024 Estrategia de contenidos.',         'https://vimeo.com/949292736'),
                ('21-05-2024 Preguntas y respuestas.',           'https://vimeo.com/948865310'),
                ('15-05-2024 Escribir un guion.',                'https://vimeo.com/948059231'),
                ('14-05-2024 Preguntas y respuestas',            'https://vimeo.com/946584164'),
            ]),
        ]

        for sec_title, sec_order, lessons in sections_data:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url,
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_clases_2025] Curso "Clases 2025" creado con 21 secciones.')
    except Exception as e:
        print(f'[seed_clases_2025] ERROR: {e}')
        db.session.rollback()


@app.route('/admin/fix-programas-marca')
@login_required
@admin_required
def admin_fix_programas_marca():
    """Reorganiza Programas para tu marca: todas las lecciones en 1 carpeta Capcut con subcarpetas."""
    try:
        course = Course.query.filter_by(title='Programas para tu marca').first()
        if not course:
            flash('No se encontro el curso "Programas para tu marca".', 'error')
            return redirect(url_for('courses'))

        # Delete existing sections (and their lessons/progress)
        for sec in list(course.sections):
            for lesson in sec.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
            db.session.delete(sec)
        db.session.flush()

        # Create single section with sub-folders via group_label
        sec = Section(course_id=course.id, title='1. Capcut', order=0)
        db.session.add(sec)
        db.session.flush()

        for l_order, (l_title, l_url, l_group) in enumerate(_CAPCUT_LESSONS):
            db.session.add(Lesson(
                section_id=sec.id,
                title=l_title,
                video_url=l_url,
                group_label=l_group,
                order=l_order,
            ))

        db.session.commit()
        flash('Programas para tu marca reorganizado: 1 carpeta Capcut con subcarpetas.', 'success')
        return redirect(url_for('admin_edit_course', course_id=course.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
        return redirect(url_for('courses'))


@app.route('/admin/fix-liberacion-emocional')
@login_required
@admin_required
def admin_fix_liberacion_emocional():
    """Force-insert '4. Liberacion emocional' into FASE 5, idempotent."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            flash('No se encontro el curso FASE 5 MENTALIDAD', 'error')
            return redirect(url_for('admin_dashboard'))

        # Remove any previous attempt
        existing = Section.query.filter_by(course_id=course.id,
                                           title='4. Liberacion emocional').first()
        if existing:
            for lesson in existing.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
            db.session.delete(existing)
            db.session.flush()

        # Shift sections with order >= 13 up by 1
        for sec in Section.query.filter_by(course_id=course.id).filter(
                Section.order >= 13).all():
            sec.order += 1
        db.session.flush()

        new_sec = Section(course_id=course.id,
                          title='4. Liberacion emocional', order=13)
        db.session.add(new_sec)
        db.session.flush()

        _lessons = [
            ('Bienvenidos',   'https://vimeo.com/719536396'),
            ('Capitulo 1',    'https://vimeo.com/719536479'),
            ('Capitulo 2',    'https://vimeo.com/719536500'),
            ('Capitulo 3',    'https://vimeo.com/719536514'),
            ('Capitulo 4',    'https://vimeo.com/719536558'),
            ('Capitulo 5',    'https://vimeo.com/721359695'),
            ('Capitulo 6',    'https://vimeo.com/719536688'),
            ('Capitulo 7',    'https://vimeo.com/719536704'),
            ('Capitulo 8',    'https://vimeo.com/719536728'),
            ('Capitulo 9',    'https://vimeo.com/720549604'),
            ('Capitulo 10',   'https://vimeo.com/720555536'),
            ('Capitulo 11',   'https://vimeo.com/720564299'),
            ('Capitulo 12',   'https://vimeo.com/720564393'),
            ('Capitulo 13',   'https://vimeo.com/720564485'),
            ('Capitulo 14',   'https://vimeo.com/721350229'),
            ('Capitulo 15',   'https://vimeo.com/721350331'),
            ('Capitulo 16',   'https://vimeo.com/721350382'),
            ('Capitulo 17',   'https://vimeo.com/803924439'),
        ]
        for l_order, (l_title, l_url) in enumerate(_lessons):
            db.session.add(Lesson(
                section_id=new_sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        flash('Seccion "4. Liberacion emocional" creada con 18 lecciones.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/purgar-duplicados-fase5', methods=['POST'])
@login_required
@admin_required
def admin_purge_duplicate_fase5():
    """Ruta de emergencia: elimina TODAS las FASE 5 y deja que seed_fase5 las recree
    correctamente en el próximo arranque. Usar solo si quedan duplicadas."""
    try:
        courses = Course.query.filter(Course.title.ilike('%FASE 5%')).all()
        if not courses:
            flash('No se encontraron cursos FASE 5.', 'error')
            return redirect(url_for('courses'))

        # Ordenar: conservar la de más secciones / mayor ID
        sorted_courses = sorted(courses, key=lambda c: (len(c.sections), c.id), reverse=True)
        keep = sorted_courses[0]
        to_delete = sorted_courses[1:]

        if not to_delete:
            flash(f'Solo hay 1 FASE 5 (id={keep.id}, {len(keep.sections)} secciones). Nada que purgar.', 'success')
            return redirect(url_for('courses'))

        deleted_ids = []
        for bad in to_delete:
            _delete_course_safely(bad.id)
            deleted_ids.append(bad.id)

        flash(f'Duplicados eliminados: ids {deleted_ids}. Conservada la id={keep.id} con {len(keep.sections)} secciones.', 'success')
    except Exception as e:
        flash(f'Error al purgar: {e}', 'error')
    return redirect(url_for('courses'))


@app.route('/admin/fix-fase5-habitos')
@login_required
@admin_required
def admin_fix_fase5_habitos():
    """One-shot route: collapse all bono sub-sections in FASE 5 into a
    single '1. Habitos para la paz mental' section with 26 lessons."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            flash('No se encontro el curso FASE 5 MENTALIDAD', 'error')
            return redirect(url_for('admin_dashboard'))

        # Identify sections to remove (orders 0-9 or any Habitos variant)
        secs_to_remove = [
            sec for sec in Section.query.filter_by(course_id=course.id).all()
            if sec.order <= 9 or 'Habitos' in sec.title or 'abitos' in sec.title
        ]

        # Delete LessonProgress for every lesson in those sections first
        for sec in secs_to_remove:
            for lesson in sec.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
        db.session.flush()

        # Now safe to delete the sections (cascade removes lessons/files)
        for sec in secs_to_remove:
            db.session.delete(sec)
        db.session.flush()

        # Reorder remaining sections compactly starting at 2
        remaining = Section.query.filter_by(course_id=course.id).order_by(Section.order).all()
        for i, sec in enumerate(remaining):
            sec.order = i + 2
        db.session.flush()

        # Create the single flat section at order 1
        new_sec = Section(course_id=course.id, title='1. Habitos para la paz mental', order=1)
        db.session.add(new_sec)
        db.session.flush()

        lessons = [
            ('1.1 Introduccion',                      'https://vimeo.com/749878520'),
            ('1.2 Como realizar este curso',           'https://vimeo.com/749881629/e2cbd4caf7'),
            ('1.3 Porque cuesta tanto cambiar',        'https://vimeo.com/749884233/1e320d927f'),
            ('2.1 El presente',                        'https://vimeo.com/749887187/ffba41cccb'),
            ('2.1.1 Profundizando en la meditacion',   'https://vimeo.com/749890461/a00d1504e0'),
            ('2.2 Pensar menos, sentir mas',           'https://vimeo.com/749888068/213b9224b8'),
            ('2.3 Decido vivir este momento',          'https://vimeo.com/749888144/f7e415bb2e'),
            ('2.3.1 Sanar el pasado',                  'https://vimeo.com/749892494/b00e80badc'),
            ('3.1 La Aceptacion',                      'https://vimeo.com/749893948/5b13abd2ba'),
            ('4.1 Como se forma el ego',               'https://vimeo.com/749894742/1fdf42c662'),
            ('4.1.2 Para que',                         'https://vimeo.com/749894828/5cdc074054'),
            ('4.1.1 Creencias',                        'https://vimeo.com/749894807/57e7fcf8e1'),
            ('4.2 Nino Interior',                      'https://vimeo.com/749897628/38e3e3a08d'),
            ('5.1 La ilusion de uno mismo',            'https://vimeo.com/749899407/9cef2eec80'),
            ('5.2 Recogida de proyecciones',           'https://vimeo.com/749901468/84733c5bfc'),
            ('5.1.1 Reprogramar la mente',             'https://vimeo.com/749899500/3357242a3d'),
            ('6.1 Reprogramar la mente',               'https://vimeo.com/749899500/3357242a3d'),
            ('7.1 Mindfull eating',                    'https://vimeo.com/749904175/162461a778'),
            ('7.2.1 Alimentacion consciente',          'https://vimeo.com/749906274/43a19e519b'),
            ('7.2.2 Alimentacion consciente 2',        'https://vimeo.com/749906363/e00d5f300d'),
            ('8.1 Iniciacion a la respiracion',        'https://vimeo.com/749908687/b0c7e3572b'),
            ('8.2 Respiracion consciente',             'https://vimeo.com/749909287/19c2af632c'),
            ('9.1 Energia sexual',                     'https://vimeo.com/749910594/f5716a6412'),
            ('9.2 Sexualidad consciente',              'https://vimeo.com/749910707/f8b9f064cf'),
            ('10. Super habitos',                      'https://vimeo.com/749912323/da572845b1'),
            ('11. Cierre de curso + regalo',           'https://vimeo.com/749914145/8f0ad0592b'),
        ]
        for l_order, (l_title, l_url) in enumerate(lessons):
            db.session.add(Lesson(
                section_id=new_sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        flash('FASE 5 corregida: 26 lecciones en una sola carpeta.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/update-descriptions', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_force_descriptions():
    """Force-update lesson descriptions via direct SQL. GET shows status, POST applies updates."""
    lines = []
    try:
        with db.engine.connect() as conn:
            for (course_title, lesson_title), html in LESSON_DESCRIPTIONS.items():
                # Find lesson id via raw SQL join
                row = conn.execute(text(
                    """SELECT l.id, l.description FROM lesson l
                       JOIN section s ON s.id = l.section_id
                       JOIN course c ON c.id = s.course_id
                       WHERE c.title = :ct AND l.title = :lt
                       LIMIT 1"""
                ), {'ct': course_title, 'lt': lesson_title}).fetchone()

                if row is None:
                    lines.append(f'❌ NO encontrada: "{lesson_title}" en "{course_title}"')
                    continue

                lesson_id = row[0]
                current_desc = row[1] or ''
                already_rich = len(current_desc) > 500
                lines.append(f'✅ id={lesson_id} — "{lesson_title[:50]}" — desc_len={len(current_desc)} — rica={already_rich}')

                if request.method == 'POST':
                    conn.execute(text(
                        'UPDATE lesson SET description = :html WHERE id = :lid'
                    ), {'html': html, 'lid': lesson_id})
                    lines.append(f'   → 💾 Descripción actualizada ({len(html)} chars)')

            if request.method == 'POST':
                conn.commit()
                lines.append('\n✔ COMMIT realizado correctamente.')
    except Exception as e:
        lines.append(f'\n💥 ERROR: {e}')

    output = '\n'.join(lines)
    action_btn = ''
    if request.method == 'GET':
        action_btn = f'<form method="POST"><button type="submit" style="margin-top:16px;padding:10px 20px;background:#7c3aed;color:white;border:none;border-radius:8px;cursor:pointer;font-size:14px">🚀 Aplicar actualización ahora</button></form>'

    return f'''<!DOCTYPE html><html><body style="font-family:monospace;padding:30px;background:#0f0f0f;color:#d4d4d4">
<h2 style="color:#a78bfa">🔧 Admin — Actualizar descripciones</h2>
<pre style="background:#1a1a1a;padding:20px;border-radius:8px;white-space:pre-wrap">{output}</pre>
{action_btn}
<br><a href="{url_for('admin_dashboard')}" style="color:#7c3aed">← Volver al admin</a>
</body></html>'''


# ── Importar descripciones desde Skool ───────────────────────────────────────
@app.route('/admin/importar-descripciones-skool')
@login_required
@admin_required
def admin_importar_descripciones():
    """Lee skool_export.json y actualiza descriptions en las lecciones con formato HTML."""
    import unicodedata, re as _re, json as _json, html as _html

    skool_path = os.path.join(os.path.dirname(__file__), 'skool_export.json')
    if not os.path.exists(skool_path):
        flash('No se encuentra skool_export.json', 'error')
        return redirect(url_for('admin_dashboard'))

    # ── Conversor texto plano → HTML ──────────────────────────────────────────
    # Tabla de caracteres unicode matemáticos en negrita → ASCII normal
    _BOLD_OFFSET = [(0x1D400, 0x1D419, 'A'), (0x1D41A, 0x1D433, 'a'),
                    (0x1D4D0, 0x1D4E9, 'A'), (0x1D4EA, 0x1D503, 'a'),
                    (0x1D5D4, 0x1D5ED, 'A'), (0x1D5EE, 0x1D607, 'a'),
                    (0x1D608, 0x1D621, 'A'), (0x1D622, 0x1D63B, 'a'),
                    (0x1D56C, 0x1D585, 'A'), (0x1D586, 0x1D59F, 'a')]

    def _demath(text):
        """Convierte letras unicode matemáticas en negrita a ASCII."""
        result = []
        for ch in text:
            cp = ord(ch)
            replaced = False
            for start, end, base in _BOLD_OFFSET:
                if start <= cp <= end:
                    result.append(chr(ord(base) + cp - start))
                    replaced = True
                    break
            if not replaced:
                result.append(ch)
        return ''.join(result)

    def texto_a_html(texto):
        if not texto:
            return ''
        # Limpiar caracteres invisibles
        texto = texto.replace('​', '').replace('‌', '').replace('‍', '')
        texto = texto.replace('\xa0', ' ').replace('﻿', '')
        # Convertir matemáticas unicode en negrita a HTML <strong>
        texto = _demath(texto)
        # Escapar HTML
        texto = _html.escape(texto)
        # Restaurar URLs como links clickables (después del escape)
        texto = _re.sub(
            r'(https?://[^\s&<>"\']+)',
            r'<a href="\1" target="_blank" rel="noopener">\1</a>',
            texto
        )
        # Dividir en bloques por líneas en blanco
        bloques = _re.split(r'\n{2,}', texto.strip())
        html_parts = []
        for bloque in bloques:
            lineas = [l.strip() for l in bloque.split('\n') if l.strip()]
            if not lineas:
                continue
            # Detectar lista numerada (1. / 1) / 1- al inicio)
            is_numbered = all(_re.match(r'^\d+[\.\)\-]', l) for l in lineas) and len(lineas) > 1
            # Detectar lista de viñetas (• - * al inicio)
            is_bullet = all(_re.match(r'^[•\-\*]\s', l) for l in lineas) and len(lineas) > 1
            if is_numbered:
                items = [_re.sub(r'^\d+[\.\)\-]\s*', '', l) for l in lineas]
                html_parts.append('<ol>' + ''.join(f'<li>{i}</li>' for i in items) + '</ol>')
            elif is_bullet:
                items = [_re.sub(r'^[•\-\*]\s*', '', l) for l in lineas]
                html_parts.append('<ul>' + ''.join(f'<li>{i}</li>' for i in items) + '</ul>')
            elif len(lineas) == 1:
                # Una sola línea — párrafo o encabezado si es corta y en mayúsculas
                l = lineas[0]
                if len(l) < 80 and (l.isupper() or l.endswith(':')) and not l.startswith('<a'):
                    html_parts.append(f'<h3>{l}</h3>')
                else:
                    html_parts.append(f'<p>{l}</p>')
            else:
                # Varias líneas — primera puede ser título si es corta
                first = lineas[0]
                rest = lineas[1:]
                if len(first) < 80 and first.endswith(':') and not first.startswith('<a'):
                    html_parts.append(f'<h3>{first}</h3>')
                    html_parts.append('<p>' + '<br>'.join(rest) + '</p>')
                else:
                    html_parts.append('<p>' + '<br>'.join(lineas) + '</p>')
        return '\n'.join(html_parts)

    # ── Normalizar títulos para matching ─────────────────────────────────────
    def normalizar(t):
        t = t.lower().strip()
        t = _re.sub(r'^[\d\.\[\]]+\s*', '', t)
        t = unicodedata.normalize('NFD', t)
        t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
        t = _re.sub(r'[^a-z0-9 ]', '', t)
        return t.strip()

    with open(skool_path, encoding='utf-8') as f:
        skool_data = _json.load(f)

    # Construir mapa normalizado → datos
    skool_map = {}
    for curso in skool_data:
        for sec in curso['sections']:
            for les in sec['lessons']:
                key = normalizar(les['title'])
                if key and les.get('description'):
                    skool_map[key] = les

    # Cruzar con lecciones de la academia
    lessons = Lesson.query.all()
    updated = 0
    no_match = []

    for lesson in lessons:
        key = normalizar(lesson.title)
        skool = skool_map.get(key)
        if skool:
            changed = False
            if skool.get('description') and not lesson.description:
                lesson.description = texto_a_html(skool['description'])
                changed = True
            if changed:
                updated += 1
        else:
            no_match.append(lesson.title)

    db.session.commit()
    msg = f'✅ {updated} lecciones actualizadas.'
    if no_match:
        msg += f' Sin coincidencia ({len(no_match)}): ' + ', '.join(no_match[:5])
        if len(no_match) > 5:
            msg += f'... y {len(no_match)-5} más'
    flash(msg, 'success')
    return redirect(url_for('admin_dashboard'))


# ── Ruta diagnóstico de base de datos (solo admin) ────────────────────────────
@app.route('/admin/fix-fase5-ahora')
@login_required
@admin_required
def admin_fix_fase5_now():
    """Ruta de emergencia: borra FASE 5 duplicadas usando SQL directo. Visita esta URL para forzarlo."""
    try:
        fase5_list = Course.query.filter(Course.title.ilike('%FASE 5%')).all()
        if len(fase5_list) <= 1:
            flash(f'Solo hay {len(fase5_list)} FASE 5 en la base de datos. Nada que borrar.', 'success')
            return redirect(url_for('courses'))
        sorted_f5 = sorted(fase5_list, key=lambda c: (len(c.sections), c.id), reverse=True)
        keep = sorted_f5[0]
        deleted = []
        for bad in sorted_f5[1:]:
            _delete_course_safely(bad.id)
            deleted.append(f'id={bad.id}')
        flash(f'✅ Eliminadas FASE 5 duplicadas ({", ".join(deleted)}). Conservada id={keep.id} con {len(keep.sections)} secciones.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('courses'))


@app.route('/admin/recomprimir-imagenes')
@login_required
@admin_required
def admin_recompress_images():
    """Recomprime todas las imágenes existentes en BD con Pillow (una sola vez)."""
    if not _PILLOW_OK:
        flash('Pillow no está instalado. Despliega primero el nuevo requirements.txt.', 'error')
        return redirect(url_for('admin_dashboard'))
    done = 0
    saved_kb = 0
    # Avatares
    for user in User.query.filter(User.avatar_data != None).all():
        old_size = len(user.avatar_data)
        new_data, new_mime = _compress_image(user.avatar_data, max_w=300, max_h=300, quality=82, square=True)
        if len(new_data) < old_size:
            saved_kb += (old_size - len(new_data)) // 1024
            user.avatar_data = new_data
            user.avatar_mime = new_mime
            done += 1
    # Portadas de curso
    for course in Course.query.filter(Course.cover_data != None).all():
        old_size = len(course.cover_data)
        new_data, new_mime = _compress_image(course.cover_data, max_w=800, max_h=500, quality=83)
        if len(new_data) < old_size:
            saved_kb += (old_size - len(new_data)) // 1024
            course.cover_data = new_data
            course.cover_mime = new_mime
            done += 1
    # Banner comunidad
    s = get_settings()
    if s.community_image_data:
        old_size = len(s.community_image_data)
        new_data, new_mime = _compress_image(s.community_image_data, max_w=1200, max_h=600, quality=82)
        if len(new_data) < old_size:
            saved_kb += (old_size - len(new_data)) // 1024
            s.community_image_data = new_data
            s.community_image_mime = new_mime
            done += 1
    # Imágenes de lección
    for li in LessonImage.query.all():
        old_size = len(li.data)
        new_data, new_mime = _compress_image(li.data, max_w=1400, max_h=1400, quality=82)
        if len(new_data) < old_size:
            saved_kb += (old_size - len(new_data)) // 1024
            li.data = new_data
            li.mimetype = new_mime
            done += 1
    db.session.commit()
    flash(f'✅ {done} imágenes recomprimidas. Espacio liberado: ~{saved_kb} KB.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/db-status')
@login_required
def admin_db_status():
    if current_user.role != 'admin':
        abort(403)
    db_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if 'postgresql' in db_url or 'postgres' in db_url:
        db_type = '✅ PostgreSQL (datos permanentes)'
    else:
        db_type = '⚠️ SQLite (datos SE PIERDEN en cada despliegue)'
    try:
        user_count    = User.query.count()
        comment_count = Comment.query.count()
        course_count  = Course.query.count()
        users_with_avatar = User.query.filter(User.avatar_data != None).count()
    except Exception as e:
        return f'<pre>Error BD: {e}</pre>'
    return f'''<pre style="font-family:monospace;padding:20px">
Base de datos: {db_type}
URL tipo: {"postgresql" if "postgresql" in db_url else "sqlite"}

Usuarios:        {user_count}
Con foto perfil: {users_with_avatar}
Comentarios:     {comment_count}
Cursos:          {course_count}
</pre><a href="{url_for("admin_dashboard")}">← Volver al panel</a>'''

# Inicializar BD siempre (tanto con gunicorn como directo)
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f'[DB] ERROR en create_all: {e}')
    try:
        with db.engine.connect() as conn:
            # lesson_file binary migration
            conn.execute(text("ALTER TABLE lesson_file ADD COLUMN IF NOT EXISTS data BYTEA"))
            conn.execute(text("ALTER TABLE lesson_file ADD COLUMN IF NOT EXISTS mimetype VARCHAR(100) DEFAULT 'application/octet-stream'"))
            conn.execute(text("ALTER TABLE lesson_file ADD COLUMN IF NOT EXISTS size INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE lesson_file DROP COLUMN IF EXISTS url"))
            # user: last_seen + avatar
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS avatar_data BYTEA"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS avatar_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'"))
            # site_settings: binary banner
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS community_image_data BYTEA"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS community_image_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_enabled BOOLEAN DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_interval_hours INTEGER DEFAULT 24"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_retention_days INTEGER DEFAULT 14"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_local_path VARCHAR(300) DEFAULT '/app/backups'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_enabled BOOLEAN DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_bucket VARCHAR(200) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_region VARCHAR(100) DEFAULT 'eu-west-1'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_prefix VARCHAR(200) DEFAULT 'miacademia'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_endpoint_url VARCHAR(300) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_access_key_enc TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_s3_secret_key_enc TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_last_run_at TIMESTAMP"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_last_status VARCHAR(40) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS backup_last_error TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS payments_enabled BOOLEAN DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS stripe_public_key VARCHAR(200) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS stripe_secret_key_enc TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS stripe_webhook_secret_enc TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS pay_auto_activate BOOLEAN DEFAULT TRUE"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS welcome_email_subject VARCHAR(300) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS welcome_email_body TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS admin_reg_email_subject VARCHAR(300) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS admin_reg_email_body TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_email_subject VARCHAR(300) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_email_body TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_24h_enabled BOOLEAN DEFAULT TRUE"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_1h_enabled BOOLEAN DEFAULT TRUE"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS player_bar_bg VARCHAR(20) DEFAULT '#141414'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS player_bar_accent VARCHAR(20) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS player_bar_text VARCHAR(20) DEFAULT '#bfbfbf'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS player_bar_btn VARCHAR(20) DEFAULT '#2a2a2a'"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_hook TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_intro TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_what_is TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_how_helps TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_explore_questions TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_includes TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_for_you TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_closing TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_cta_text VARCHAR(120) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_price_note VARCHAR(200) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_login_title VARCHAR(200) DEFAULT ''"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS landing_login_subtitle VARCHAR(300) DEFAULT ''"))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS subscription_plan (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    description TEXT DEFAULT '',
                    price_monthly DOUBLE PRECISION DEFAULT 0,
                    stripe_price_id VARCHAR(120) DEFAULT '',
                    is_active BOOLEAN DEFAULT TRUE,
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS billing_type VARCHAR(20) DEFAULT 'standard'"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_plan_id INTEGER REFERENCES subscription_plan(id)"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(120) DEFAULT ''"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(120) DEFAULT ''"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(30) DEFAULT 'none'"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_period_end TIMESTAMP"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_last_paid_at TIMESTAMP"))
            # point_event table (created by db.create_all, but add index hint)
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_point_event_user ON point_event(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_point_event_date ON point_event(created_at)"))
            conn.execute(text("ALTER TABLE live_class ADD COLUMN IF NOT EXISTS recurrence VARCHAR(10) DEFAULT 'none'"))
            conn.execute(text("ALTER TABLE live_class ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES live_class(id)"))
            # course cover image (binary)
            conn.execute(text("ALTER TABLE course ADD COLUMN IF NOT EXISTS cover_data BYTEA"))
            conn.execute(text("ALTER TABLE course ADD COLUMN IF NOT EXISTS cover_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            conn.execute(text("ALTER TABLE course ADD COLUMN IF NOT EXISTS \"order\" INTEGER DEFAULT 0"))
            # lesson inline images for rich-text descriptions
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS lesson_image (
                    id SERIAL PRIMARY KEY,
                    lesson_id INTEGER NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
                    mimetype VARCHAR(100) DEFAULT 'image/jpeg',
                    data BYTEA NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS notification (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES \"user\"(id),
                    type VARCHAR(30),
                    message VARCHAR(300),
                    link VARCHAR(200) DEFAULT '',
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            run_migrations(conn)
            conn.commit()
    except Exception as e:
        print(f'[DB] ERROR en migraciones: {e}')
    try:
        ensure_calendar_categories()
        ensure_community_categories()
    except Exception as e:
        print(f'[seed] categories: {e}')
    sk = app.config.get('SECRET_KEY', '')
    if not sk or sk == 'cambiar-en-produccion-secret-key-aqui':
        print('[SECURITY] ⚠️ SECRET_KEY por defecto — configura secrets/secret_key antes de producción.')
    # ── Diagnóstico de base de datos ──────────────────────────────────────────
    _db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if 'postgresql' in _db_uri:
        print('=' * 60)
        print('[DB] ✅ POSTGRESQL — datos PERSISTENTES')
        print('=' * 60)
    else:
        print('=' * 60)
        print('[DB] ⚠️  SQLITE — datos se pierden en cada despliegue.')
        print('[DB]    Asegúrate de tener DATABASE_URL en Railway → Variables.')
        print('=' * 60)

    try:
        seed_db()
    except Exception as e:
        print(f'[seed] ERROR en seed_db: {e}')
        db.session.rollback()

    try:
        seed_descriptions()
    except Exception as e:
        print(f'[seed_desc] ERROR en seed_descriptions: {e}')
        db.session.rollback()

    # DB column migration: add group_label to lesson if missing
    try:
        with db.engine.connect() as _conn:
            _conn.execute(text(
                "ALTER TABLE lesson ADD COLUMN IF NOT EXISTS group_label VARCHAR(200)"
            ))
            _conn.commit()
    except Exception as _e:
        print(f'[migration] group_label: {_e}')

    # DB migration: comment_likes table
    try:
        with db.engine.connect() as _conn:
            _conn.execute(text("""
                CREATE TABLE IF NOT EXISTS comment_likes (
                    user_id    INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    comment_id INTEGER NOT NULL REFERENCES comment(id) ON DELETE CASCADE,
                    PRIMARY KEY (user_id, comment_id)
                )
            """))
            _conn.commit()
    except Exception as _e:
        print(f'[migration] comment_likes: {_e}')

    fix_duplicate_fase5()
    seed_fase5()
    fix_fase5_carpeta6()
    seed_bono_habitos()
    seed_bono_organizacion()
    seed_liberacion_emocional()
    seed_programas_marca()
    seed_clases_2026()
    seed_ia()
    seed_clases_2025()
    seed_coach_profesional()

    # Backfill points — solo si faltan registros (compara conteos, evita N+1 en cada arranque)
    try:
        lp_count = LessonProgress.query.count()
        pt_lesson = PointEvent.query.filter_by(reason='lesson').count()
        if lp_count > 0 and pt_lesson < lp_count:
            # Usar INSERT ... WHERE NOT EXISTS para ser eficiente
            with db.engine.begin() as _c:
                _c.execute(text("""
                    INSERT INTO point_event (user_id, points, reason, ref_id, created_at)
                    SELECT lp.user_id, 3, 'lesson', lp.lesson_id, lp.completed_at
                    FROM lesson_progress lp
                    WHERE NOT EXISTS (
                        SELECT 1 FROM point_event pe
                        WHERE pe.user_id = lp.user_id AND pe.reason = 'lesson' AND pe.ref_id = lp.lesson_id
                    )
                """))
                _c.execute(text("""
                    INSERT INTO point_event (user_id, points, reason, ref_id, created_at)
                    SELECT c.user_id, 2, 'comment', c.id, c.created_at
                    FROM comment c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM point_event pe
                        WHERE pe.user_id = c.user_id AND pe.reason = 'comment' AND pe.ref_id = c.id
                    )
                """))
                _c.execute(text("""
                    INSERT INTO point_event (user_id, points, reason, ref_id, created_at)
                    SELECT p.user_id, 4, 'post', p.id, p.created_at
                    FROM post p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM point_event pe
                        WHERE pe.user_id = p.user_id AND pe.reason = 'post' AND pe.ref_id = p.id
                    )
                """))
            print('[backfill] Puntos completados correctamente.')
    except Exception as e:
        print(f'[seed] ERROR en backfill points: {e}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)
