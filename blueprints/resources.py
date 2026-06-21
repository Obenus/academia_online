"""Blueprint: Recursos con tags."""
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user
from io import BytesIO

from models import db, Resource, ResourceTag

bp = Blueprint('resources', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _parse_tags(raw):
    if not raw:
        return []
    names = [t.strip().lower() for t in raw.replace(',', ' ').split() if t.strip()]
    tags = []
    for name in names:
        tag = ResourceTag.query.filter_by(name=name).first()
        if not tag:
            tag = ResourceTag(name=name)
            db.session.add(tag)
            db.session.flush()
        tags.append(tag)
    return tags


@bp.route('/recursos')
@login_required
def index():
    tag_filter = request.args.get('tag', '').strip().lower()
    q = Resource.query.order_by(Resource.created_at.desc())
    if tag_filter:
        q = q.join(Resource.tags).filter(ResourceTag.name == tag_filter)
    items = q.all()
    all_tags = ResourceTag.query.order_by(ResourceTag.name).all()
    return render_template('resources/index.html', items=items, all_tags=all_tags, active_tag=tag_filter)


@bp.route('/recursos/<int:res_id>/descargar')
@login_required
def download(res_id):
    res = Resource.query.get_or_404(res_id)
    if res.file_url:
        return redirect(res.file_url)
    if res.file_data:
        return send_file(
            BytesIO(res.file_data),
            mimetype=res.file_mime or 'application/octet-stream',
            as_attachment=True,
            download_name=res.file_name or res.title,
        )
    abort(404)


@bp.route('/admin/recursos')
@login_required
@admin_required
def admin_list():
    items = Resource.query.order_by(Resource.created_at.desc()).all()
    return render_template('admin/resources.html', items=items)


@bp.route('/admin/recursos/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new():
    if request.method == 'POST':
        res = Resource(
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            media_type=request.form.get('media_type', 'pdf'),
            file_url=request.form.get('file_url', '').strip(),
        )
        f = request.files.get('file')
        if f and f.filename:
            res.file_data = f.read()
            res.file_mime = f.mimetype or 'application/octet-stream'
            res.file_name = f.filename
        res.tags = _parse_tags(request.form.get('tags', ''))
        if not res.title:
            flash('El título es obligatorio.', 'error')
        else:
            db.session.add(res)
            db.session.commit()
            flash('Recurso creado.', 'success')
            return redirect(url_for('resources.admin_list'))
    return render_template('admin/resource_form.html', item=None)


@bp.route('/admin/recursos/<int:res_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit(res_id):
    res = Resource.query.get_or_404(res_id)
    if request.method == 'POST':
        res.title = request.form.get('title', '').strip()
        res.description = request.form.get('description', '').strip()
        res.media_type = request.form.get('media_type', 'pdf')
        res.file_url = request.form.get('file_url', '').strip()
        f = request.files.get('file')
        if f and f.filename:
            res.file_data = f.read()
            res.file_mime = f.mimetype or 'application/octet-stream'
            res.file_name = f.filename
        res.tags = _parse_tags(request.form.get('tags', ''))
        db.session.commit()
        flash('Recurso actualizado.', 'success')
        return redirect(url_for('resources.admin_list'))
    tag_str = ' '.join(t.name for t in res.tags)
    return render_template('admin/resource_form.html', item=res, tag_str=tag_str)


@bp.route('/admin/recursos/<int:res_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete(res_id):
    res = Resource.query.get_or_404(res_id)
    db.session.delete(res)
    db.session.commit()
    flash('Recurso eliminado.', 'success')
    return redirect(url_for('resources.admin_list'))
