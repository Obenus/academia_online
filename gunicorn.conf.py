import os

# gthread: cada worker atiende varias peticiones (I/O). Con sync, 3 visitas lentas
# dejan la web colgada. Ajustable sin rebuild: GUNICORN_WORKERS / GUNICORN_THREADS.
workers = int(os.environ.get('GUNICORN_WORKERS', '4'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '30'))
graceful_timeout = 20
keepalive = 5
max_requests = 400
max_requests_jitter = 50
worker_tmp_dir = '/dev/shm'
bind = os.environ.get('GUNICORN_BIND') or (
    '0.0.0.0:' + os.environ.get('PORT', '5000')
)
preload_app = True
worker_class = 'gthread'


def post_fork(server, worker):
    """Reinicia el pool de conexiones BD en cada worker después del fork."""
    try:
        from app import db
        db.engine.dispose()
    except Exception:
        pass
