"""Blueprint: Biblioteca del Círculo."""
from functools import wraps
from collections import defaultdict
from urllib.parse import quote

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify, Response
from flask_login import login_required, current_user

from models import db, LibraryItem, LiveClass, Course
from video_utils import video_embed_url, video_thumbnail_url, video_provider, youtube_video_id

bp = Blueprint('library', __name__)

MONTH_NAMES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
               'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _course_title_from_item(item):
    if item.description and ' — ' in item.description:
        return item.description.split(' — ', 1)[0].strip()
    return ''


def _build_catalog():
    """Agrupa ítems publicados por formación (como el catálogo antiguo)."""
    items = LibraryItem.query.filter_by(is_published=True).all()
    by_title = defaultdict(list)
    encuentros = []

    for item in items:
        if item.item_type == 'encuentro':
            encuentros.append(item)
            continue
        title = _course_title_from_item(item)
        if title:
            by_title[title].append(item)
        else:
            by_title[item.title].append(item)

    cards = []
    courses = {c.title: c for c in Course.query.all()}
    seen = set()

    for course in Course.query.order_by(Course.order, Course.id).all():
        its = by_title.get(course.title, [])
        if not its:
            continue
        seen.add(course.title)
        cards.append({
            'kind': 'course',
            'course': course,
            'title': course.title,
            'subtitle': course.subtitle or f'{len(its)} vídeo(s)',
            'count': len(its),
            'slug': str(course.id),
        })

    for title, its in sorted(by_title.items()):
        if title in seen:
            continue
        cards.append({
            'kind': 'group',
            'course': None,
            'title': title,
            'subtitle': f'{len(its)} vídeo(s)',
            'count': len(its),
            'slug': quote(title, safe=''),
        })

    if encuentros:
        cards.insert(0, {
            'kind': 'encuentros',
            'course': None,
            'title': 'Encuentros en vivo',
            'subtitle': 'Grabaciones de las sesiones del círculo',
            'count': len(encuentros),
            'slug': 'encuentros',
        })

    return cards, by_title, encuentros


def _items_for_slug(slug, by_title, encuentros):
    if slug == 'encuentros':
        return sorted(encuentros, key=lambda i: (i.year, i.month, i.sort_order), reverse=True)
    if slug.isdigit():
        course = Course.query.get(int(slug))
        if not course:
            abort(404)
        return LibraryItem.query.filter(
            LibraryItem.is_published == True,
            LibraryItem.description.like(f'{course.title} —%'),
        ).order_by(LibraryItem.sort_order, LibraryItem.id).all()
    from urllib.parse import unquote
    title = unquote(slug)
    return sorted(by_title.get(title, []), key=lambda i: (i.sort_order, i.id))


@bp.route('/biblioteca')
@login_required
def index():
    cards, by_title, encuentros = _build_catalog()
    return render_template('library/index.html', cards=cards)


@bp.route('/biblioteca/ver/<int:item_id>')
@login_required
def watch(item_id):
    """Redirige al listado con el reproductor modal abierto."""
    item = LibraryItem.query.filter_by(id=item_id, is_published=True).first_or_404()
    course_title = _course_title_from_item(item)
    if item.item_type == 'encuentro':
        slug = 'encuentros'
    elif course_title:
        c = Course.query.filter_by(title=course_title).first()
        slug = str(c.id) if c else quote(course_title, safe='')
    else:
        slug = quote(item.title, safe='')
    return redirect(url_for('library.course_view', slug=slug, play=item.id))


def _embed_origin():
    """Origen de la página para embeds (YouTube exige Referer válido)."""
    origin = getattr(request, 'origin', None)
    if origin:
        return origin.rstrip('/')
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    host = request.headers.get('X-Forwarded-Host', request.host)
    return f'{scheme}://{host}'.rstrip('/')


@bp.route('/biblioteca/api/reproducir/<int:item_id>')
@login_required
def play_api(item_id):
    """Devuelve la URL de embed solo a usuarios autenticados (no va en el HTML)."""
    item = LibraryItem.query.filter_by(id=item_id, is_published=True).first_or_404()
    if not item.video_url:
        return jsonify({'error': 'Sin vídeo'}), 400
    origin = _embed_origin()
    embed = video_embed_url(item.video_url, origin=origin, locked=True)
    if not embed:
        return jsonify({'error': 'Formato no soportado'}), 400
    provider = video_provider(item.video_url)
    payload = {
        'title': item.title,
        'embed_src': embed,
        'provider': provider,
        'origin': origin,
    }
    if provider == 'youtube':
        yid = youtube_video_id(item.video_url)
        if not yid:
            return jsonify({'error': 'ID de YouTube no válido'}), 400
        payload['youtube_id'] = yid
    return jsonify(payload)


@bp.route('/biblioteca/api/miniatura/<int:item_id>')
@login_required
def thumbnail(item_id):
    """Miniatura vía servidor — no expone el ID de YouTube/Vimeo en la URL del navegador."""
    import urllib.request
    item = LibraryItem.query.filter_by(id=item_id, is_published=True).first_or_404()
    remote = video_thumbnail_url(item.video_url)
    if not remote:
        abort(404)
    try:
        req = urllib.request.Request(remote, headers={'User-Agent': 'MiAcademia/1.0'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
            ctype = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0]
        return Response(data, mimetype=ctype, headers={'Cache-Control': 'private, max-age=3600'})
    except Exception:
        abort(404)


@bp.route('/biblioteca/<slug>')
@login_required
def course_view(slug):
    cards, by_title, encuentros = _build_catalog()
    items = _items_for_slug(slug, by_title, encuentros)
    if not items:
        abort(404)

    if slug == 'encuentros':
        course = None
        title = 'Encuentros en vivo'
        subtitle = 'Grabaciones de las sesiones del círculo'
    elif slug.isdigit():
        course = Course.query.get_or_404(int(slug))
        title = course.title
        subtitle = course.subtitle or ''
    else:
        from urllib.parse import unquote
        course = Course.query.filter_by(title=unquote(slug)).first()
        title = unquote(slug)
        subtitle = f'{len(items)} vídeo(s)'

    play_id = request.args.get('play', type=int)

    return render_template(
        'library/course.html',
        course=course,
        title=title,
        subtitle=subtitle,
        items=items,
        slug=slug,
        play_id=play_id,
    )


@bp.route('/admin/biblioteca')
@login_required
@admin_required
def admin_list():
    items = LibraryItem.query.order_by(
        LibraryItem.year.desc(), LibraryItem.month.desc(), LibraryItem.sort_order
    ).all()
    return render_template('admin/library.html', items=items, month_names=MONTH_NAMES)


@bp.route('/admin/biblioteca/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new():
    if request.method == 'POST':
        item = LibraryItem(
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            video_url=request.form.get('video_url', '').strip(),
            year=int(request.form.get('year', 2026)),
            month=int(request.form.get('month', 1)),
            item_type=request.form.get('item_type', 'extra'),
            sort_order=int(request.form.get('sort_order', 0) or 0),
            is_published=request.form.get('is_published') == 'on',
            live_class_id=request.form.get('live_class_id', type=int) or None,
        )
        if not item.title:
            flash('El título es obligatorio.', 'error')
        else:
            db.session.add(item)
            db.session.commit()
            flash('Elemento añadido a la biblioteca.', 'success')
            return redirect(url_for('library.admin_list'))
    classes = LiveClass.query.order_by(LiveClass.scheduled_at.desc()).limit(50).all()
    return render_template('admin/library_form.html', item=None, classes=classes, month_names=MONTH_NAMES)


@bp.route('/admin/biblioteca/<int:item_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    if request.method == 'POST':
        item.title = request.form.get('title', '').strip()
        item.description = request.form.get('description', '').strip()
        item.video_url = request.form.get('video_url', '').strip()
        item.year = int(request.form.get('year', item.year))
        item.month = int(request.form.get('month', item.month))
        item.item_type = request.form.get('item_type', 'extra')
        item.sort_order = int(request.form.get('sort_order', 0) or 0)
        item.is_published = request.form.get('is_published') == 'on'
        item.live_class_id = request.form.get('live_class_id', type=int) or None
        db.session.commit()
        flash('Biblioteca actualizada.', 'success')
        return redirect(url_for('library.admin_list'))
    classes = LiveClass.query.order_by(LiveClass.scheduled_at.desc()).limit(50).all()
    return render_template('admin/library_form.html', item=item, classes=classes, month_names=MONTH_NAMES)


@bp.route('/admin/biblioteca/<int:item_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('Elemento eliminado.', 'success')
    return redirect(url_for('library.admin_list'))


def upsert_recording_from_webhook(live_class_id, recording_url, title=None, year=None, month=None):
    """Crea o actualiza grabación en biblioteca (webhook n8n/post-encuentro)."""
    lc = LiveClass.query.get(live_class_id) if live_class_id else None
    if lc:
        year = year or lc.scheduled_at.year
        month = month or lc.scheduled_at.month
        title = title or lc.title
    if not recording_url or not year or not month:
        return None
    existing = None
    if live_class_id:
        existing = LibraryItem.query.filter_by(live_class_id=live_class_id, item_type='encuentro').first()
    if existing:
        existing.video_url = recording_url
        if title:
            existing.title = title
        db.session.commit()
        return existing
    item = LibraryItem(
        title=title or 'Grabación del encuentro',
        video_url=recording_url,
        year=year,
        month=month,
        item_type='encuentro',
        live_class_id=live_class_id,
        is_published=True,
    )
    db.session.add(item)
    db.session.commit()
    return item
