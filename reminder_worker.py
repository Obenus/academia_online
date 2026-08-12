#!/usr/bin/env python3
"""Worker: recordatorios por email 24h y 1h antes de eventos del calendario."""
import os
import time
from datetime import datetime, timedelta

from app import app, mail
from models import db, User, LiveClass, LiveClassReminderLog, SiteSettings
from billing import _mail_configured, send_event_reminder_email


def _window(now, hours_ahead, tolerance_min=15):
    target = now + timedelta(hours=hours_ahead)
    return target - timedelta(minutes=tolerance_min), target + timedelta(minutes=tolerance_min)


def run_once():
    with app.app_context():
        try:
            if not _mail_configured(app):
                print('[reminder] MAIL no configurado, omitiendo.')
                return
            site = SiteSettings.query.first()
            base = app.config.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
            if base:
                calendar_url = f'{base}/calendario'
            else:
                from flask import url_for
                with app.test_request_context('/'):
                    calendar_url = url_for('calendar', _external=True)

            now = datetime.utcnow()
            users = User.query.filter_by(status='active').filter(User.role != 'rejected').all()
            classes = LiveClass.query.filter(LiveClass.scheduled_at > now).all()

            reminder_enabled = {
                '24h': not site or site.event_reminder_24h_enabled is not False,
                '1h': not site or site.event_reminder_1h_enabled is not False,
            }

            for rtype, hours in (('24h', 24), ('1h', 1)):
                if not reminder_enabled.get(rtype, True):
                    continue
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
                            if send_event_reminder_email(
                                app, mail, user, lc, rtype, site=site, calendar_url=calendar_url,
                            ):
                                db.session.add(LiveClassReminderLog(
                                    live_class_id=lc.id, user_id=user.id, reminder_type=rtype,
                                ))
                                db.session.commit()
                        except Exception as e:
                            print(f'[reminder] {user.email} {lc.id} {rtype}: {e}')
                            db.session.rollback()
        finally:
            db.session.remove()


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
