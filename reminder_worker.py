#!/usr/bin/env python3
"""Worker: recordatorios por email 24h y 1h antes de clases en directo."""
import os
import time
from datetime import datetime, timedelta

from app import app, mail
from models import db, User, LiveClass, LiveClassReminderLog, SiteSettings
from billing import send_html_email, email_wrapper, _mail_configured


def _window(now, hours_ahead, tolerance_min=15):
    target = now + timedelta(hours=hours_ahead)
    return target - timedelta(minutes=tolerance_min), target + timedelta(minutes=tolerance_min)


def _send_reminder(lc, user, rtype, site):
    subject = f'Recordatorio: {lc.title} ({rtype})'
    when = lc.scheduled_at.strftime('%d/%m/%Y %H:%M UTC')
    inner = f"""<p>Hola <strong>{user.username}</strong>,</p>
<p>Te recordamos la clase en directo <strong>{lc.title}</strong>.</p>
<p>📅 <strong>{when}</strong></p>
<p>⏱ Duración aproximada: {lc.duration_min} min</p>
{f'<p><a href="{lc.meet_url}">Unirse a la clase</a></p>' if lc.meet_url else ''}
<p style="color:#71717a;font-size:12px">También puedes verla en el calendario de la academia.</p>"""
    academy = site.academy_name if site else 'Academia'
    return send_html_email(
        app, mail, [user.email], subject,
        email_wrapper(academy, inner),
    )


def run_once():
    with app.app_context():
        if not _mail_configured(app):
            print('[reminder] MAIL no configurado, omitiendo.')
            return
        site = SiteSettings.query.first()
        now = datetime.utcnow()
        users = User.query.filter_by(status='active').filter(User.role != 'rejected').all()
        classes = LiveClass.query.filter(LiveClass.scheduled_at > now).all()

        for rtype, hours in (('24h', 24), ('1h', 1)):
            start, end = _window(now, hours)
            for lc in classes:
                if not (start <= lc.scheduled_at <= end):
                    continue
                for user in users:
                    if user.role == 'admin':
                        continue
                    exists = LiveClassReminderLog.query.filter_by(
                        live_class_id=lc.id, user_id=user.id, reminder_type=rtype,
                    ).first()
                    if exists:
                        continue
                    try:
                        if _send_reminder(lc, user, rtype, site):
                            db.session.add(LiveClassReminderLog(
                                live_class_id=lc.id, user_id=user.id, reminder_type=rtype,
                            ))
                            db.session.commit()
                    except Exception as e:
                        print(f'[reminder] {user.email} {lc.id} {rtype}: {e}')
                        db.session.rollback()


def main():
    interval = int(os.environ.get('REMINDER_INTERVAL_SEC', '300'))
    print(f'[reminder] worker cada {interval}s')
    while True:
        try:
            run_once()
        except Exception as e:
            print(f'[reminder] error: {e}')
        time.sleep(interval)


if __name__ == '__main__':
    main()
