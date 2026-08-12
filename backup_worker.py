import os
import time
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from backup_manager import run_backup, decrypt_value


def utcnow():
    return datetime.now(timezone.utc)


def get_secret(name: str) -> str:
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        try:
            with open(file_var, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return os.environ.get(name, "")


def main():
    db_url = os.environ.get("DATABASE_URL", "")
    secret_key = get_secret("SECRET_KEY")
    if not db_url:
        print("[backup-worker] DATABASE_URL no configurada.")
        return

    while True:
        try:
            with psycopg2.connect(db_url) as conn:
                conn.autocommit = True
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM site_settings ORDER BY id ASC LIMIT 1")
                    s = cur.fetchone()

            if not s or not s.get("backup_enabled"):
                time.sleep(60)
                continue

            last_run = s.get("backup_last_run_at")
            interval_h = max(int(s.get("backup_interval_hours") or 24), 1)
            due = not last_run or (utcnow() - last_run.replace(tzinfo=timezone.utc)).total_seconds() >= interval_h * 3600
            if not due:
                time.sleep(60)
                continue

            payload = {
                "backup_local_path": s.get("backup_local_path") or "/app/backups",
                "backup_retention_days": s.get("backup_retention_days") or 14,
                "backup_s3_enabled": s.get("backup_s3_enabled"),
                "backup_s3_bucket": s.get("backup_s3_bucket") or "",
                "backup_s3_region": s.get("backup_s3_region") or "eu-west-1",
                "backup_s3_prefix": s.get("backup_s3_prefix") or "miacademia",
                "backup_s3_endpoint_url": s.get("backup_s3_endpoint_url") or "",
                "backup_s3_access_key": decrypt_value(s.get("backup_s3_access_key_enc") or "", secret_key),
                "backup_s3_secret_key": decrypt_value(s.get("backup_s3_secret_key_enc") or "", secret_key),
            }
            app_name = s.get("academy_name") or "miacademia"
            try:
                result = run_backup(payload, app_name, db_url)
                status, err = "ok", ""
                print(f"[backup-worker] OK {result['file']}")
            except Exception as e:
                status, err = "error", str(e)[:2000]
                print(f"[backup-worker] ERROR {e}")

            with psycopg2.connect(db_url) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE site_settings
                        SET backup_last_run_at = NOW(),
                            backup_last_status = %s,
                            backup_last_error = %s
                        WHERE id = %s
                        """,
                        (status, err, s["id"]),
                    )
        except Exception as e:
            print(f"[backup-worker] Loop error: {e}")

        time.sleep(60)


if __name__ == "__main__":
    main()
