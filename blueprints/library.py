"""Blueprint: Biblioteca del Círculo."""
from functools import wraps
from collections import defaultdict
from urllib.parse import quote

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify, Response
from flask_login import login_required, current_user

from models import db, LibraryItem, LiveClass, Course, SiteSettings
from video_utils import video_embed_url, video_thumbnail_url, video_provider, youtube_video_id, vimeo_oembed_thumbnail

bp = Blueprint('library', __name__)

MONTH_NAMES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
               'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


@bp.app_template_global('video_thumb')
def video_thumb(url):
    """URL directa de miniatura (CDN YouTube/Vumbnail). Evita saturar Gunicorn."""
    return video_thumbnail_url(url) or ''


def _sort_library_items(items):
    """Orden manual primero (menor = antes); si empatan, el más reciente."""
    return sorted(
        items,
        key=lambda i: (
            int(i.sort_order or 0),
            -(i.created_at.timestamp() if i.created_at else 0),
            -i.id,
        ),
    )


def _next_first_sort_order():
    """Asigna un orden menor que el mínimo actual → el nuevo vídeo queda el primero."""
    m = db.session.query(db.func.min(LibraryItem.sort_order)).scalar()
    if m is None:
        return 0
    return int(m) - 1


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


def _catalog_card_key(card):
    if card.get('kind') == 'encuentros':
        return 'encuentros'
    if card.get('kind') == 'course' and card.get('course'):
        return f"c:{card['course'].id}"
    return f"g:{card.get('title') or ''}"


def _apply_catalog_order(cards, order_raw):
    """Ordena tarjetas según library_catalog_order; las nuevas van al final."""
    if not order_raw or not str(order_raw).strip():
        return cards
    keys = [k.strip() for k in str(order_raw).splitlines() if k.strip()]
    if not keys:
        return cards
    by_key = {}
    for card in cards:
        key = _catalog_card_key(card)
        card['key'] = key
        by_key[key] = card
    ordered = []
    for key in keys:
        card = by_key.pop(key, None)
        if card:
            ordered.append(card)
    ordered.extend(by_key.values())
    return ordered


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

    for card in cards:
        card['key'] = _catalog_card_key(card)

    site = SiteSettings.query.first()
    order_raw = getattr(site, 'library_catalog_order', None) if site else None
    cards = _apply_catalog_order(cards, order_raw)

    return cards, by_title, encuentros


def _items_for_slug(slug, by_title, encuentros):
    """Devuelve ítems respetando Orden (menor número = primero)."""
    if slug == 'encuentros':
        return _sort_library_items(encuentros)
    if slug.isdigit():
        course = Course.query.get(int(slug))
        if not course:
            abort(404)
        items = LibraryItem.query.filter(
            LibraryItem.is_published == True,  # noqa: E712
            LibraryItem.description.like(f'{course.title} —%'),
        ).all()
        return _sort_library_items(items)
    from urllib.parse import unquote
    title = unquote(slug)
    return _sort_library_items(by_title.get(title, []))


@bp.route('/biblioteca')
@login_required
def index():
    cards, by_title, encuentros = _build_catalog()
    # Miniatura del primer vídeo según orden manual (cuando no hay portada de curso)
    for card in cards:
        card['thumb_url'] = ''
        first = None
        if card['kind'] == 'encuentros' and encuentros:
            ordered = _sort_library_items(encuentros)
            first = ordered[0] if ordered else None
        elif card['kind'] in ('course', 'group'):
            ordered = _sort_library_items(by_title.get(card['title'], []))
            first = ordered[0] if ordered else None
        if first and first.video_url:
            card['thumb_url'] = video_thumbnail_url(first.video_url) or ''
    return render_template('library/index.html', cards=cards)


@bp.route('/biblioteca/reordenar', methods=['POST'])
@login_required
@admin_required
def catalog_reorder():
    """Reordena las tarjetas del catálogo /biblioteca (admin)."""
    raw = (request.json or {}).get('order') or []
    if not isinstance(raw, list):
        abort(400)
    keys = []
    for key in raw:
        if not isinstance(key, str):
            continue
        key = key.strip()
        if key:
            keys.append(key)

    site = SiteSettings.query.first()
    if not site:
        site = SiteSettings()
        db.session.add(site)
    site.library_catalog_order = '\n'.join(keys)

    # Mantener /cursos alineado con el orden de formaciones arrastradas aquí
    course_ids = []
    for key in keys:
        if key.startswith('c:'):
            try:
                course_ids.append(int(key[2:]))
            except ValueError:
                continue
    for i, cid in enumerate(course_ids):
        Course.query.filter_by(id=cid).update({'order': i})

    db.session.commit()
    return ('', 204)


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


# Caché simple de miniaturas (evita saturar workers en listados)
_THUMB_CACHE = {}
_THUMB_CACHE_MAX = 200


@bp.route('/biblioteca/api/miniatura/<int:item_id>')
@login_required
def thumbnail(item_id):
    """Miniatura rápida: YouTube por redirect CDN; Vimeo con caché y timeout corto."""
    import urllib.request
    from flask import redirect as flask_redirect

    q = LibraryItem.query.filter_by(id=item_id)
    if not current_user.is_admin:
        q = q.filter_by(is_published=True)
    item = q.first_or_404()
    if not item.video_url:
        abort(404)

    provider = video_provider(item.video_url)
    remote = video_thumbnail_url(item.video_url)

    # YouTube: redirect directo al CDN (no bloquea Gunicorn)
    if provider == 'youtube' and remote:
        return flask_redirect(remote, code=302)

    cache_key = f'{item.id}:{item.video_url}'
    cached = _THUMB_CACHE.get(cache_key)
    if cached:
        data, ctype = cached
        return Response(data, mimetype=ctype, headers={'Cache-Control': 'private, max-age=86400'})

    if provider == 'vimeo' and not remote:
        remote = vimeo_oembed_thumbnail(item.video_url)

    if not remote:
        abort(404)

    def _fetch(url, timeout=3):
        req = urllib.request.Request(url, headers={'User-Agent': 'MiAcademia/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0]
            return body, ctype

    try:
        data, ctype = _fetch(remote)
    except Exception:
        if provider == 'vimeo':
            alt = vimeo_oembed_thumbnail(item.video_url)
            if alt and alt != remote:
                try:
                    data, ctype = _fetch(alt)
                except Exception:
                    abort(404)
            else:
                abort(404)
        else:
            abort(404)

    if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
        _THUMB_CACHE.clear()
    _THUMB_CACHE[cache_key] = (data, ctype)
    return Response(data, mimetype=ctype, headers={'Cache-Control': 'private, max-age=86400'})


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
        LibraryItem.sort_order.asc(),
        LibraryItem.created_at.desc(),
        LibraryItem.id.desc(),
    ).all()
    return render_template(
        'admin/library.html',
        items=items,
        month_names=MONTH_NAMES,
    )


@bp.route('/admin/biblioteca/reordenar', methods=['POST'])
@login_required
@admin_required
def admin_reorder():
    """Guarda el orden tras arrastrar (mismo patrón que /admin/cursos/reordenar).

    Acepta la lista completa (admin) o un subconjunto (vista de una formación):
    en ese caso se reordenan solo esos ítems manteniendo el resto en su sitio.
    """
    raw = (request.json or {}).get('order') or []
    if not isinstance(raw, list) or not raw:
        abort(400)
    order_ids = []
    for item_id in raw:
        try:
            order_ids.append(int(item_id))
        except (TypeError, ValueError):
            continue
    if not order_ids:
        abort(400)

    order_set = set(order_ids)
    if len(order_set) != len(order_ids):
        abort(400)

    all_items = LibraryItem.query.order_by(
        LibraryItem.sort_order.asc(),
        LibraryItem.created_at.desc(),
        LibraryItem.id.desc(),
    ).all()
    known = {i.id for i in all_items}
    if not order_set.issubset(known):
        abort(400)

    it = iter(order_ids)
    new_ids = []
    for item in all_items:
        if item.id in order_set:
            new_ids.append(next(it))
        else:
            new_ids.append(item.id)

    for i, iid in enumerate(new_ids):
        LibraryItem.query.filter_by(id=iid).update({'sort_order': i})
    db.session.commit()
    return ('', 204)


@bp.route('/admin/biblioteca/<int:item_id>/orden', methods=['POST'])
@login_required
@admin_required
def admin_set_order(item_id):
    """Ajusta el orden (+1 / -1). Menor número = más arriba."""
    item = LibraryItem.query.get_or_404(item_id)
    delta = request.form.get('delta', type=int) or 0
    item.sort_order = int(item.sort_order or 0) + delta
    db.session.commit()
    flash(f'Orden de «{item.title}» → {item.sort_order}', 'success')
    return redirect(url_for('library.admin_list'))


@bp.route('/admin/biblioteca/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new():
    if request.method == 'POST':
        # Nuevo vídeo siempre al principio; luego se puede reordenar a mano
        item = LibraryItem(
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            video_url=request.form.get('video_url', '').strip(),
            year=int(request.form.get('year', 2026)),
            month=int(request.form.get('month', 1)),
            item_type=request.form.get('item_type', 'extra'),
            sort_order=_next_first_sort_order(),
            is_published=request.form.get('is_published') == 'on',
            live_class_id=request.form.get('live_class_id', type=int) or None,
        )
        if not item.title:
            flash('El título es obligatorio.', 'error')
        else:
            db.session.add(item)
            db.session.commit()
            flash('Elemento añadido al principio de la biblioteca. Puedes cambiar el orden arrastrando.', 'success')
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
        sort_order=_next_first_sort_order(),
    )
    db.session.add(item)
    db.session.commit()
    return item
