"""Webhook saliente para preguntas a Rocío."""
import json
import urllib.request


def notify_n8n_pregunta(app, post, author):
    url = (app.config.get('N8N_WEBHOOK_PREGUNTAS') or '').strip()
    if not url:
        return
    payload = {
        'post_id': post.id,
        'title': post.title,
        'content': post.content,
        'author': author.username,
        'email': author.email,
        'created_at': post.created_at.isoformat() if post.created_at else '',
        'status': post.workflow_status or 'pendiente',
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url, data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f'[n8n] webhook error post {post.id}: {e}')
