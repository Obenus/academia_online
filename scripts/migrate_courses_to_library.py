#!/usr/bin/env python3
"""Migra lecciones con vídeo de cursos publicados a Biblioteca del Círculo."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import db, Course, LibraryItem


def run(dry_run=False):
    with app.app_context():
        count = 0
        for course in Course.query.filter_by(is_published=True).all():
            y = course.created_at.year if course.created_at else 2026
            m = course.created_at.month if course.created_at else 1
            for section in course.sections:
                for lesson in section.lessons:
                    if not lesson.video_url:
                        continue
                    exists = LibraryItem.query.filter_by(
                        title=lesson.title, video_url=lesson.video_url
                    ).first()
                    if exists:
                        continue
                    item = LibraryItem(
                        title=lesson.title,
                        description=f'{course.title} — {section.title}',
                        video_url=lesson.video_url,
                        year=y,
                        month=m,
                        item_type='extra',
                        is_published=True,
                    )
                    if not dry_run:
                        db.session.add(item)
                    count += 1
        if not dry_run:
            for course in Course.query.filter_by(is_published=True).all():
                course.is_published = False
            db.session.commit()
        print(f'{"[dry-run] " if dry_run else ""}Migrados {count} vídeos a biblioteca.')


if __name__ == '__main__':
    run(dry_run='--dry-run' in sys.argv)
