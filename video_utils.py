"""Utilidades para URLs de vídeo (miniaturas, embed, etc.)."""
import re
from urllib.parse import quote

_VIMEO_PRIVACY = (
    'controls=0&keyboard=0&dnt=1&title=0&byline=0&portrait=0'
    '&badge=0&sidedock=0&transparent=0&pip=0'
)
_YT_PRIVACY = (
    'controls=0&enablejsapi=1&rel=0&modestbranding=1&playsinline=1'
    '&iv_load_policy=3&fs=0&disablekb=1&cc_load_policy=0'
)


def video_provider(url):
    """'youtube', 'vimeo' o cadena vacía."""
    if not url:
        return ''
    url = url.strip().lower()
    if 'vimeo.com' in url:
        return 'vimeo'
    if 'youtu.be' in url or 'youtube.com' in url:
        return 'youtube'
    return ''


def youtube_video_id(url):
    """ID de 11 caracteres o cadena vacía."""
    if not url:
        return ''
    url = url.strip()
    for pattern in (
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'[?&]v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return ''


def video_embed_url(url, origin='', locked=True):
    """URL de embed para iframe. Si locked=True, minimiza salidas a YouTube/Vimeo."""
    if not url:
        return ''
    url = url.strip()
    if 'vimeo.com' in url:
        if 'player.vimeo.com' in url:
            base = url.split('?')[0]
            return f'{base}?{_VIMEO_PRIVACY}'
        path = url.split('vimeo.com/')[1].split('?')[0]
        parts = path.split('/')
        vid = parts[0]
        if len(parts) > 1 and parts[1]:
            return f'https://player.vimeo.com/video/{vid}?h={parts[1]}&{_VIMEO_PRIVACY}'
        return f'https://player.vimeo.com/video/{vid}?{_VIMEO_PRIVACY}'
    vid = None
    m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        vid = m.group(1)
    if not vid:
        m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
        if m:
            vid = m.group(1)
    if not vid:
        m = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})', url)
        if m:
            vid = m.group(1)
    if not vid:
        return ''
    qs = _YT_PRIVACY
    if origin:
        qs += f'&origin={quote(origin, safe="")}'
    return f'https://www.youtube-nocookie.com/embed/{vid}?{qs}'


def video_thumbnail_url(url):
    """Miniatura para YouTube o Vimeo. Cadena vacía si no se reconoce."""
    if not url:
        return ''
    url = url.strip()
    m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://img.youtube.com/vi/{m.group(1)}/hqdefault.jpg'
    m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://img.youtube.com/vi/{m.group(1)}/hqdefault.jpg'
    m = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://img.youtube.com/vi/{m.group(1)}/hqdefault.jpg'
    m = re.search(r'vimeo\.com/(?:video/)?(\d+)', url)
    if m:
        return f'https://vumbnail.com/{m.group(1)}.jpg'
    m = re.search(r'player\.vimeo\.com/video/(\d+)', url)
    if m:
        return f'https://vumbnail.com/{m.group(1)}.jpg'
    return ''
