"""Envío masivo de emails por tandas con registro."""
import time

from models import db, EmailCampaign, User


BATCH_SIZE = 50
BATCH_DELAY_SEC = 2


def send_bulk_campaign(app, mail, admin_id, subject, body_html, target='students'):
    from billing import send_html_email, _mail_configured
    if not _mail_configured(app, mail):
        raise RuntimeError('SMTP no configurado')

    if target == 'all':
        users = User.query.filter_by(status='active').all()
    else:
        users = User.query.filter_by(status='active', role='student').all()

    campaign = EmailCampaign(
        admin_id=admin_id,
        subject=subject,
        target=target,
        batch_size=BATCH_SIZE,
    )
    db.session.add(campaign)
    db.session.commit()

    sent = failed = 0
    for i in range(0, len(users), BATCH_SIZE):
        batch = users[i:i + BATCH_SIZE]
        for user in batch:
            if not user.email:
                failed += 1
                continue
            try:
                if send_html_email(app, mail, [user.email], subject, body_html):
                    sent += 1
                else:
                    failed += 1
            except Exception as e:
                print(f'[email_bulk] fail {user.email}: {e}')
                failed += 1
        db.session.commit()
        if i + BATCH_SIZE < len(users):
            time.sleep(BATCH_DELAY_SEC)

    campaign.total_sent = sent
    campaign.total_failed = failed
    db.session.commit()
    return campaign, sent, failed
