"""Rutas: progreso, portal Stripe, quizzes, tareas, moderación, admin stats, certificados."""
from datetime import datetime, timedelta
from functools import wraps
import csv
import io

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash,
    jsonify, abort, send_file, current_app,
)
from flask_login import login_required, current_user

from models import (
    db, User, Course, Section, Lesson, Enrollment, LessonProgress,
    SubscriptionPlan, Category, Post, PostReport,
    Quiz, QuizQuestion, QuizOption, QuizAttempt,
    Assignment, AssignmentSubmission, CourseCertificate,
    EmailCampaign, SiteSettings, LiveClass,
)
from learning_utils import (
    course_progress, ordered_lessons, completed_lesson_ids,
    is_lesson_unlocked, issue_certificate, course_fully_complete,
)
from certificate_pdf import build_certificate_pdf
from email_bulk import send_bulk_campaign, BATCH_SIZE
from billing import (
    payments_enabled, create_billing_portal_session, send_test_template_email,
    get_stripe_public,
)
from extensions import limiter

bp = Blueprint('features', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _site():
    return SiteSettings.query.first()


def user_can_access_category(user, category):
    if not category or not category.required_plan_id:
        return True
    if user.is_admin or user.is_free_billing:
        return True
    return user.subscription_plan_id == category.required_plan_id and user.subscription_ok


# ── Progreso ─────────────────────────────────────────────────────────────────

@bp.route('/mi-progreso')
@login_required
def my_progress():
    enrollments = Enrollment.query.filter_by(user_id=current_user.id).all()
    rows = []
    for en in enrollments:
        c = Course.query.get(en.course_id)
        if not c:
            continue
        prog = course_progress(current_user.id, c)
        cert = CourseCertificate.query.filter_by(
            user_id=current_user.id, course_id=c.id
        ).first()
        rows.append({
            'course': c,
            'progress': prog,
            'certificate': cert,
            'enrolled_at': en.enrolled_at,
        })
    return render_template('student/progress.html', rows=rows)


# ── Portal Stripe ───────────────────────────────────────────────────────────

@bp.route('/mi-cuenta/suscripcion')
@login_required
def billing_portal():
    if current_user.is_free_billing or current_user.is_admin:
        flash('Tu cuenta no requiere gestión de suscripción en Stripe.', 'error')
        return redirect(url_for('account_settings'))
    if not payments_enabled(current_app):
        flash('Los pagos no están activos.', 'error')
        return redirect(url_for('account_settings'))
    try:
        sess = create_billing_portal_session(
            current_app, current_user,
            return_url=url_for('account_settings', _external=True),
        )
        return redirect(sess.url)
    except Exception as e:
        flash(f'No se pudo abrir el portal de facturación: {e}', 'error')
        return redirect(url_for('account_settings'))


# ── Certificado PDF ─────────────────────────────────────────────────────────

@bp.route('/cursos/<int:course_id>/certificado')
@login_required
def download_certificate(course_id):
    course = Course.query.get_or_404(course_id)
    if not current_user.is_enrolled(course_id) and not current_user.is_admin:
        abort(403)
    cert = issue_certificate(current_user, course)
    if not cert:
        flash('Aún no has completado el curso (lecciones y cuestionarios obligatorios).', 'error')
        return redirect(url_for('learn', course_id=course_id))
    site = _site()
    pdf = build_certificate_pdf(
        site.academy_name if site else 'Academia',
        current_user.username,
        course.title,
        cert.certificate_code,
        cert.issued_at,
    )
    return send_file(
        pdf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'certificado_{course.id}_{cert.certificate_code}.pdf',
    )


# ── Quiz ────────────────────────────────────────────────────────────────────

@bp.route('/cursos/<int:course_id>/quiz/<int:quiz_id>', methods=['GET', 'POST'])
@login_required
def take_quiz(course_id, quiz_id):
    course = Course.query.get_or_404(course_id)
    quiz = Quiz.query.get_or_404(quiz_id)
    if quiz.section.course_id != course_id:
        abort(404)
    if request.method == 'POST':
        total = len(quiz.questions)
        if total == 0:
            flash('Este cuestionario no tiene preguntas.', 'error')
            return redirect(url_for('features.take_quiz', course_id=course_id, quiz_id=quiz_id))
        correct = 0
        for q in quiz.questions:
            chosen = request.form.get(f'q_{q.id}', type=int)
            opt = QuizOption.query.get(chosen) if chosen else None
            if opt and opt.is_correct and opt.question_id == q.id:
                correct += 1
        score = int(round(correct * 100 / total))
        passed = score >= (quiz.pass_percent or 70)
        db.session.add(QuizAttempt(
            user_id=current_user.id, quiz_id=quiz.id, score=score, passed=passed,
        ))
        db.session.commit()
        if passed:
            issue_certificate(current_user, course)
            flash(f'¡Aprobado! Puntuación: {score}%', 'success')
        else:
            flash(f'No aprobado ({score}%). Necesitas {quiz.pass_percent}%.', 'error')
        return redirect(url_for('learn', course_id=course_id))
    return render_template('student/quiz.html', course=course, quiz=quiz)


@bp.route('/admin/seccion/<int:section_id>/quiz', methods=['POST'])
@login_required
@admin_required
def admin_add_quiz(section_id):
    section = Section.query.get_or_404(section_id)
    title = request.form.get('quiz_title', '').strip() or f'Cuestionario — {section.title}'
    q = Quiz(section_id=section_id, title=title,
             pass_percent=int(request.form.get('pass_percent', 70) or 70))
    db.session.add(q)
    db.session.flush()
    for i in range(1, 4):
        qt = request.form.get(f'q{i}_text', '').strip()
        if not qt:
            continue
        question = QuizQuestion(quiz_id=q.id, text=qt, order=i)
        db.session.add(question)
        db.session.flush()
        correct = int(request.form.get(f'q{i}_correct', 1) or 1)
        for j in range(1, 5):
            ot = request.form.get(f'q{i}_o{j}', '').strip()
            if ot:
                db.session.add(QuizOption(
                    question_id=question.id, text=ot,
                    is_correct=(j == correct),
                ))
    db.session.commit()
    flash('Cuestionario creado.', 'success')
    return redirect(url_for('admin_edit_course', course_id=section.course_id))


# ── Tareas / entregas ───────────────────────────────────────────────────────

@bp.route('/cursos/<int:course_id>/tarea/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
def submit_assignment(course_id, assignment_id):
    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.section.course_id != course_id:
        abort(404)
    sub = AssignmentSubmission.query.filter_by(
        user_id=current_user.id, assignment_id=assignment_id,
    ).order_by(AssignmentSubmission.submitted_at.desc()).first()
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if not content:
            flash('Escribe tu entrega.', 'error')
        else:
            if sub and sub.status == 'returned':
                sub.content = content
                sub.status = 'pending'
                sub.submitted_at = datetime.utcnow()
                sub.mentor_feedback = ''
            else:
                sub = AssignmentSubmission(
                    assignment_id=assignment_id,
                    user_id=current_user.id,
                    content=content,
                )
                db.session.add(sub)
            db.session.commit()
            flash('Entrega enviada. El mentor la revisará pronto.', 'success')
            return redirect(url_for('learn', course_id=course_id))
    return render_template('student/assignment.html',
                           course_id=course_id, assignment=assignment, submission=sub)


@bp.route('/admin/tareas')
@login_required
@admin_required
def admin_assignments():
    subs = (AssignmentSubmission.query
            .order_by(AssignmentSubmission.submitted_at.desc()).limit(100).all())
    return render_template('admin/assignments.html', submissions=subs)


@bp.route('/admin/tareas/<int:sub_id>/feedback', methods=['POST'])
@login_required
@admin_required
def admin_assignment_feedback(sub_id):
    sub = AssignmentSubmission.query.get_or_404(sub_id)
    sub.mentor_feedback = request.form.get('feedback', '').strip()
    sub.status = request.form.get('status', 'reviewed')
    sub.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash('Feedback guardado.', 'success')
    return redirect(url_for('features.admin_assignments'))


@bp.route('/admin/seccion/<int:section_id>/tarea', methods=['POST'])
@login_required
@admin_required
def admin_add_assignment(section_id):
    section = Section.query.get_or_404(section_id)
    title = request.form.get('assignment_title', '').strip()
    desc = request.form.get('assignment_desc', '').strip()
    if title:
        db.session.add(Assignment(section_id=section_id, title=title, description=desc))
        db.session.commit()
        flash('Tarea creada.', 'success')
    return redirect(url_for('admin_edit_course', course_id=section.course_id))


# ── Moderación ────────────────────────────────────────────────────────────────

@bp.route('/comunidad/post/<int:post_id>/reportar', methods=['POST'])
@login_required
def report_post(post_id):
    post = Post.query.get_or_404(post_id)
    reason = request.form.get('reason', '').strip() or 'Reportado por usuario'
    exists = PostReport.query.filter_by(
        post_id=post_id, reporter_id=current_user.id, status='pending',
    ).first()
    if not exists:
        db.session.add(PostReport(post_id=post_id, reporter_id=current_user.id, reason=reason))
        db.session.commit()
        flash('Reporte enviado. Un administrador lo revisará.', 'success')
    else:
        flash('Ya has reportado esta publicación.', 'error')
    return redirect(request.referrer or url_for('community_post', post_id=post_id))


@bp.route('/admin/moderacion')
@login_required
@admin_required
def admin_moderation():
    reports = PostReport.query.filter_by(status='pending').order_by(PostReport.created_at.desc()).all()
    hidden = Post.query.filter_by(is_hidden=True).order_by(Post.created_at.desc()).limit(30).all()
    return render_template('admin/moderation.html', reports=reports, hidden_posts=hidden)


@bp.route('/admin/moderacion/<int:report_id>', methods=['POST'])
@login_required
@admin_required
def admin_resolve_report(report_id):
    report = PostReport.query.get_or_404(report_id)
    action = request.form.get('action')
    if action == 'hide':
        report.post.is_hidden = True
        report.post.hidden_reason = request.form.get('reason', 'Moderación')[:300]
        report.status = 'resolved'
    elif action == 'dismiss':
        report.status = 'dismissed'
    elif action == 'unhide':
        report.post.is_hidden = False
        report.status = 'resolved'
    report.resolved_at = datetime.utcnow()
    db.session.commit()
    flash('Acción aplicada.', 'success')
    return redirect(url_for('features.admin_moderation'))


# ── Admin estadísticas ───────────────────────────────────────────────────────

@bp.route('/admin/estadisticas')
@login_required
@admin_required
def admin_stats():
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stats = {
        'users_total': User.query.count(),
        'users_active': User.query.filter_by(status='active').count(),
        'users_pending': User.query.filter_by(status='pending').count(),
        'users_suspended': User.query.filter_by(status='suspended').count(),
        'subs_active': User.query.filter_by(subscription_status='active').count(),
        'subs_past_due': User.query.filter_by(subscription_status='past_due').count(),
        'courses': Course.query.count(),
        'enrollments': Enrollment.query.count(),
        'posts': Post.query.count(),
        'reports_pending': PostReport.query.filter_by(status='pending').count(),
        'new_users_month': User.query.filter(User.created_at >= month_start).count(),
        'certificates': CourseCertificate.query.count(),
        'live_upcoming': LiveClass.query.filter(LiveClass.scheduled_at >= now).count(),
    }
    top_courses = []
    for c in Course.query.filter_by(is_published=True).all():
        n = Enrollment.query.filter_by(course_id=c.id).count()
        top_courses.append((c, n))
    top_courses.sort(key=lambda x: -x[1])
    return render_template('admin/stats.html', stats=stats, top_courses=top_courses[:10])


@bp.route('/admin/usuarios/exportar.csv')
@login_required
@admin_required
def admin_export_users_csv():
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow([
        'id', 'username', 'email', 'role', 'status', 'billing_type',
        'plan_id', 'subscription_status', 'created_at', 'last_seen',
    ])
    for u in User.query.order_by(User.id).all():
        w.writerow([
            u.id, u.username, u.email, u.role, u.status, u.billing_type,
            u.subscription_plan_id or '', u.subscription_status,
            u.created_at.isoformat() if u.created_at else '',
            u.last_seen.isoformat() if u.last_seen else '',
        ])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'usuarios_{datetime.utcnow().strftime("%Y%m%d")}.csv',
    )


@bp.route('/admin/pagos/email-prueba', methods=['POST'])
@login_required
@admin_required
@limiter.limit('5 per hour')
def admin_test_email():
    s = _site()
    to = request.form.get('test_email', '').strip() or current_user.email
    which = request.form.get('which', 'welcome')
    from billing import default_welcome_subject, default_welcome_body
    from billing import default_admin_reg_subject, default_admin_reg_body
    if which == 'admin':
        sub = s.admin_reg_email_subject or default_admin_reg_subject()
        body = s.admin_reg_email_body or default_admin_reg_body()
    else:
        sub = s.welcome_email_subject or default_welcome_subject()
        body = s.welcome_email_body or default_welcome_body()
    try:
        from app import mail
        ok = send_test_template_email(current_app, mail, to, sub, body)
        if ok:
            flash(f'Email de prueba enviado a {to}.', 'success')
        else:
            flash('No se pudo enviar. Revisa SMTP en Ajustes.', 'error')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_payments'))


@bp.route('/admin/email/campanas')
@login_required
@admin_required
def admin_email_campaigns():
    campaigns = EmailCampaign.query.order_by(EmailCampaign.created_at.desc()).limit(50).all()
    return render_template('admin/email_campaigns.html', campaigns=campaigns)


def register_bulk_email_routes(app, mail, get_settings_fn):
    """Parchea admin_email para envío por tandas — llamado desde app.py."""

    @app.route('/admin/email', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def admin_email():
        if request.method == 'POST':
            subject = request.form.get('subject', '').strip()
            body = request.form.get('body', '').strip()
            target = request.form.get('target', 'students')
            if not subject or not body:
                flash('Asunto y mensaje obligatorios.', 'error')
                return redirect(url_for('admin_email'))
            from billing import apply_smtp_config, _mail_configured
            apply_smtp_config(app, mail)
            if not _mail_configured(app, mail):
                flash('Email no configurado. Ve a Ajustes → SMTP.', 'error')
                return redirect(url_for('admin_email'))
            site = get_settings_fn()
            html = f"""<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
<div style="background:#7c3aed;padding:24px;border-radius:12px 12px 0 0;text-align:center">
<h1 style="color:#fff;margin:0;font-size:20px">🎓 {site.academy_name}</h1></div>
<div style="background:#fff;padding:32px;border:1px solid #e4e4e7">
<h2 style="color:#18181b">{subject}</h2>
<div style="color:#52525b;line-height:1.7;white-space:pre-wrap">{body}</div>
<p style="color:#a1a1aa;font-size:12px;margin-top:24px">Email de la academia. Lote máx. {BATCH_SIZE} destinatarios.</p>
</div></div>"""
            campaign, sent, failed = send_bulk_campaign(
                app, mail, current_user.id, subject, html, target,
            )
            flash(f'Campaña #{campaign.id}: {sent} enviados, {failed} fallidos.', 'success')
            return redirect(url_for('features.admin_email_campaigns'))
        total_students = User.query.filter_by(status='active', role='student').count()
        total_all = User.query.filter_by(status='active').count()
        site = get_settings_fn()
        return render_template(
            'admin/email.html',
            total_students=total_students,
            total_all=total_all,
            batch_size=BATCH_SIZE,
            academy_name=(site.academy_name if site and site.academy_name else app.config.get('ACADEMY_NAME', 'Academia')),
        )

    return admin_email
