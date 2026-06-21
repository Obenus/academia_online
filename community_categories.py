"""Categorías fijas del foro de comunidad."""
from models import db, Category

SYSTEM_CATEGORIES = [
    ('general', 'General', '#6366f1', '💬'),
    ('autocuidado', 'Autocuidado', '#ec4899', '🌸'),
    ('a-mi-tambien', 'A mí también me pasa', '#f59e0b', '🤝'),
    ('preguntas-rocio', 'Preguntas para Rocío', '#7c3aed', '💜'),
]


def ensure_community_categories():
    for slug, name, color, emoji in SYSTEM_CATEGORIES:
        cat = Category.query.filter_by(slug=slug).first()
        if not cat:
            existing = Category.query.filter_by(name=name).first()
            if existing:
                existing.slug = slug
                existing.is_system = True
                existing.color = color
                existing.emoji = emoji
            else:
                db.session.add(Category(
                    name=name, slug=slug, color=color, emoji=emoji, is_system=True))
        else:
            cat.name = name
            cat.color = color
            cat.emoji = emoji
            cat.is_system = True
    db.session.commit()


def category_by_slug(slug):
    return Category.query.filter_by(slug=slug).first()
