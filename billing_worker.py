"""Revisa suscripciones vencidas y suspende usuarios morosos."""
import os
import time
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor


def get_secret(name: str) -> str:
    file_var = os.environ.get(f'{name}_FILE')
    if file_var:
        try:
            with open(file_var, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except OSError:
            pass
    return os.environ.get(name, '')


def run_cycle(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, username, email, subscription_status, subscription_period_end,
                   billing_type, status
            FROM "user"
            WHERE role = 'student'
              AND billing_type != 'free'
              AND subscription_status IN ('active', 'past_due')
              AND subscription_period_end IS NOT NULL
              AND subscription_period_end < NOW()
        """)
        overdue = cur.fetchall()
        for u in overdue:
            cur.execute(
                """
                UPDATE "user"
                SET status = 'suspended',
                    subscription_status = 'past_due'
                WHERE id = %s AND status = 'active'
                """,
                (u['id'],),
            )
            cur.execute("""
                INSERT INTO notification (user_id, type, message, link, is_read, created_at)
                SELECT id, 'payment_failed',
                       %s, '/admin/suscripciones', FALSE, NOW()
                FROM "user" WHERE role = 'admin'
            """, (f"⚠️ {u['username']} no ha abonado la mensualidad. Cuenta suspendida.",))
            print(f"[billing-worker] Suspendido user_id={u['id']} ({u['username']})")
        conn.commit()
        if overdue:
            print(f"[billing-worker] Procesados {len(overdue)} usuario(s) en mora.")


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
