"""Revisa suscripciones vencidas o en impago y suspende usuarios morosos."""
import os
import time

import psycopg2
from psycopg2.extras import RealDictCursor

BLOCK_STATUSES = ('past_due', 'unpaid', 'canceled', 'incomplete', 'incomplete_expired', 'paused')


def _suspend_user(cur, user_row):
    """Marca suspendido en BD; devuelve True si acaba de suspenderse."""
    cur.execute(
        """
        UPDATE "user"
        SET status = 'suspended',
            subscription_status = CASE
                WHEN subscription_status IN ('active', 'trialing') THEN 'past_due'
                ELSE subscription_status
            END,
            whatsapp_vip_pending = TRUE
        WHERE id = %s AND status = 'active' AND billing_type != 'free'
        """,
        (user_row['id'],),
    )
    if cur.rowcount:
        print(f"[billing-worker] Suspendido user_id={user_row['id']} ({user_row['username']})")
        return True
    return False


def run_cycle(conn):
    suspended = []  # (user_id, reason)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, username, email, subscription_status, subscription_period_end,
                   billing_type, status
            FROM "user"
            WHERE role = 'student'
              AND billing_type != 'free'
              AND subscription_status IN ('active', 'trialing', 'past_due')
              AND subscription_period_end IS NOT NULL
              AND subscription_period_end < NOW()
        """)
        for u in cur.fetchall():
            if _suspend_user(cur, u):
                suspended.append((u['id'], 'mensualidad no renovada (periodo vencido)'))

        cur.execute(f"""
            SELECT id, username, email, subscription_status, subscription_period_end,
                   billing_type, status
            FROM "user"
            WHERE role = 'student'
              AND billing_type != 'free'
              AND status = 'active'
              AND subscription_status IN ({','.join('%s' for _ in BLOCK_STATUSES)})
        """, list(BLOCK_STATUSES))
        for u in cur.fetchall():
            if _suspend_user(cur, u):
                suspended.append((u['id'], f"estado de suscripción: {u['subscription_status']}"))

        conn.commit()

    if not suspended:
        return

    try:
        from app import app, mail
        from models import db, User, Notification
        from billing import notify_admins_payment_failed

        def _notify(admin_id, ntype, message, link):
            db.session.add(Notification(
                user_id=admin_id, type=ntype, message=message, link=link,
            ))

        with app.app_context():
            for uid, reason in suspended:
                user = User.query.get(uid)
                if user:
                    notify_admins_payment_failed(
                        db, _notify, user, reason,
                        app=app, mail=mail,
                    )
            db.session.commit()
    except Exception as e:
        print(f'[billing-worker] Aviso email admin: {e}')

    print(f"[billing-worker] {len(suspended)} suspensión(es) por impago — admins notificados por email.")


def main():
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        print('[billing-worker] DATABASE_URL no configurada.')
        return
    while True:
        try:
            with psycopg2.connect(db_url) as conn:
                run_cycle(conn)
        except Exception as e:
            print(f'[billing-worker] Error: {e}')
        time.sleep(3600)


if __name__ == '__main__':
    main()
