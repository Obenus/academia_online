"""Categorías del calendario: valores por defecto y seed."""
from models import db, LiveClassCategory

DEFAULT_CALENDAR_CATEGORIES = [
    ('temática mensual', '#7c3aed', '📆', 1),
    ('encuentros', '#2563eb', '🤝', 2),
    ('rituales', '#db2777', '🕯️', 3),
    ('fechas', '#ea580c', '📌', 4),
    ('descargables', '#059669', '📥', 5),
    ('invitados', '#0891b2', '⭐', 6),
]


def ensure_calendar_categories():
    """Crea categorías por defecto si la tabla está vacía."""
    if LiveClassCategory.query.count() > 0:
        return
    for name, color, emoji, order in DEFAULT_CALENDAR_CATEGORIES:
        db.session.add(LiveClassCategory(
            name=name, color=color, emoji=emoji, sort_order=order,
        ))
    db.session.commit()


def category_event_colors(category):
    if not category:
        return '#6366f1', '#4f46e5'
    return category.color, category.color
