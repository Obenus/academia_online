import base64
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import boto3
from cryptography.fernet import Fernet, InvalidToken


def utcnow():
    return datetime.now(timezone.utc)


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256((secret_key or "fallback-key").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(raw: str, secret_key: str) -> str:
    if not raw:
        return ""
    return _fernet(secret_key).encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_value(cipher: str, secret_key: str) -> str:
    if not cipher:
        return ""
    try:
        return _fernet(secret_key).decrypt(cipher.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def _safe_name(value: str, fallback: str) -> str:
    cleaned = "".join(ch for ch in (value or "") if ch.isalnum() or ch in ("-", "_"))
    return cleaned or fallback


def _cleanup_old_backups(local_path: Path, retention_days: int):
    cutoff_ts = utcnow().timestamp() - max(retention_days, 1) * 86400
    for f in local_path.glob("backup_*.dump"):
        if f.stat().st_mtime < cutoff_ts:
            f.unlink(missing_ok=True)


def run_backup(settings: dict, app_name: str, db_url: str, logger=print) -> dict:
    local_path = Path(settings.get("backup_local_path") or "/app/backups")
    local_path.mkdir(parents=True, exist_ok=True)

    timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
    prefix_name = _safe_name(app_name, "miacademia")
    dump_file = local_path / f"backup_{prefix_name}_{timestamp}.dump"

    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(dump_file),
        db_url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "pg_dump falló").strip())

    s3_key = ""
    if settings.get("backup_s3_enabled"):
        bucket = (settings.get("backup_s3_bucket") or "").strip()
        if not bucket:
            raise RuntimeError("S3 habilitado pero falta bucket.")

        region = (settings.get("backup_s3_region") or "eu-west-1").strip()
        prefix = (settings.get("backup_s3_prefix") or "miacademia").strip().strip("/")
        endpoint = (settings.get("backup_s3_endpoint_url") or "").strip() or None
        access_key = settings.get("backup_s3_access_key") or ""
        secret_key = settings.get("backup_s3_secret_key") or ""

        if not access_key or not secret_key:
            raise RuntimeError("S3 habilitado pero faltan credenciales.")

        s3_key = f"{prefix}/{dump_file.name}" if prefix else dump_file.name
        client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        client.upload_file(str(dump_file), bucket, s3_key)
        logger(f"[backup] Subido a s3://{bucket}/{s3_key}")

    _cleanup_old_backups(local_path, int(settings.get("backup_retention_days") or 14))
    return {
        "ok": True,
        "file": str(dump_file),
        "s3_key": s3_key,
        "ran_at": utcnow(),
    }


def list_local_backups(local_path: str) -> list:
    """Lista backups .dump ordenados del más reciente al más antiguo."""
    p = Path(local_path or "/app/backups")
    if not p.exists():
        return []
    items = []
    for f in p.glob("backup_*.dump"):
        try:
            st = f.stat()
            items.append({
                "name": f.name,
                "path": str(f.resolve()),
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            })
        except OSError:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _resolve_backup_file(local_path: str, filename: str) -> Path:
    """Valida que el archivo esté dentro del directorio de backups."""
    base = Path(local_path or "/app/backups").resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise ValueError("Archivo de backup no válido.")
    if not target.name.startswith("backup_") or not target.name.endswith(".dump"):
        raise ValueError("Solo se permiten archivos backup_*.dump")
    return target


def restore_backup(local_path: str, filename: str, db_url: str, logger=print) -> dict:
    """Restaura un dump PostgreSQL (formato custom) sobre la BD indicada."""
    dump_file = _resolve_backup_file(local_path, filename)
    logger(f"[restore] Iniciando restauración desde {dump_file.name}")

    cmd = [
        "pg_restore",
        "--dbname", db_url,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        str(dump_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # pg_restore puede devolver código 1 con avisos menores; si stderr tiene FATAL, fallar
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 and "FATAL" in err.upper():
        raise RuntimeError(err or f"pg_restore falló con código {proc.returncode}")
    if proc.returncode != 0 and err:
        logger(f"[restore] Avisos: {err[:500]}")
    logger(f"[restore] Completado: {dump_file.name}")
    return {"ok": True, "file": str(dump_file)}
