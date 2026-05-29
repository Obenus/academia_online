"""Progreso, drip de lecciones y finalización de cursos."""
from datetime import datetime, timedelta

from models import (
    Course, Lesson, LessonProgress, Enrollment, Section,
    CourseCertificate, Quiz, QuizAttempt,
)


def ordered_lessons(course):
    lessons = []
    for section in sorted(course.sections, key=lambda s: s.order):
        for lesson in sorted(section.lessons, key=lambda l: l.order):
            lessons.append(lesson)
    return lessons


def completed_lesson_ids(user_id):
    return {
        p.lesson_id
        for p in LessonProgress.query.filter_by(user_id=user_id).all()
    }


def course_progress(user_id, course):
    lessons = ordered_lessons(course)
    if not lessons:
        return {'total': 0, 'done': 0, 'percent': 0, 'last_lesson': None}
    done_ids = completed_lesson_ids(user_id)
    done = sum(1 for l in lessons if l.id in done_ids)
    last = None
    for l in lessons:
        if l.id not in done_ids:
            last = l
            break
    if last is None and lessons:
        last = lessons[-1]
    return {
        'total': len(lessons),
        'done': done,
        'percent': round(done * 100 / len(lessons)),
        'last_lesson': last,
    }


def enrollment_date(user_id, course_id):
    e = Enrollment.query.filter_by(user_id=user_id, course_id=course_id).first()
    return e.enrolled_at if e else datetime.utcnow()


def is_lesson_unlocked(user, lesson, course, done_ids=None):
    if user.is_admin:
        return True, None
    done_ids = done_ids if done_ids is not None else completed_lesson_ids(user.id)
    lessons = ordered_lessons(course)
    idx = next((i for i, l in enumerate(lessons) if l.id == lesson.id), -1)
    if idx < 0:
        return False, 'Lección no encontrada en el curso.'

    enroll_at = enrollment_date(user.id, course.id)
    drip_days = getattr(lesson, 'drip_days', 0) or 0
    if drip_days > 0:
        unlock_at = enroll_at + timedelta(days=drip_days)
        if datetime.utcnow() < unlock_at:
            return False, f'Disponible desde {unlock_at.strftime("%d/%m/%Y")}.'

    if idx == 0:
        return True, None

    prev = lessons[idx - 1]
    if prev.id not in done_ids:
        return False, f'Completa antes: «{prev.title}».'
    return True, None


def quiz_passed_for_section(user_id, section_id):
    quiz = Quiz.query.filter_by(section_id=section_id, is_required=True).first()
    if not quiz:
        return True
    return QuizAttempt.query.filter_by(
        user_id=user_id, quiz_id=quiz.id, passed=True
    ).first() is not None


def course_fully_complete(user_id, course):
    prog = course_progress(user_id, course)
    if prog['percent'] < 100:
        return False
    for section in course.sections:
        if not quiz_passed_for_section(user_id, section.id):
            return False
    return True


def issue_certificate(user, course):
    if not getattr(course, 'certificate_enabled', True):
        return None
    if not course_fully_complete(user.id, course):
        return None
    existing = CourseCertificate.query.filter_by(
        user_id=user.id, course_id=course.id
    ).first()
    if existing:
        return existing
    import secrets
    code = secrets.token_hex(8).upper()
    cert = CourseCertificate(
        user_id=user.id,
        course_id=course.id,
        certificate_code=code,
    )
    from models import db
    db.session.add(cert)
    db.session.commit()
    return cert
