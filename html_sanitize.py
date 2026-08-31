"""Sanitizado de HTML para la landing (permite iframes de vídeo)."""
import html as html_lib
import re
from html.parser import HTMLParser
from markupsafe import Markup


_ALLOWED_TAGS = frozenset({
    'a', 'b', 'br', 'blockquote', 'div', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'hr', 'i', 'iframe', 'img', 'li', 'ol', 'p', 'section', 'span', 'strong', 'u', 'ul',
})

_VOID = frozenset({'br', 'hr', 'img'})

_ALLOWED_ATTRS = {
    '*': {'class', 'id', 'style'},
    'a': {'href', 'title', 'target', 'rel'},
    'img': {'src', 'alt', 'title', 'width', 'height', 'loading', 'decoding'},
    'iframe': {
        'src', 'title', 'width', 'height', 'allow', 'allowfullscreen',
        'frameborder', 'loading', 'referrerpolicy', 'style', 'class',
    },
}

_SAFE_URL = re.compile(
    r'^(https?:)?//'
    r'|^/'
    r'|^#'
    r'|^mailto:',
    re.I,
)

_IFRAME_HOST = re.compile(
    r'^(https?:)?//'
    r'(www\.)?'
    r'(youtube\.com|youtube-nocookie\.com|youtu\.be|player\.vimeo\.com|vimeo\.com)'
    r'(/|$)',
    re.I,
)


def _attr_ok(tag, name, value):
    name = name.lower()
    if name.startswith('on') or name in ('srcdoc',):
        return False
    allowed = _ALLOWED_ATTRS.get(tag, set()) | _ALLOWED_ATTRS.get('*', set())
    if name not in allowed:
        return False
    if name in ('href', 'src'):
        val = (value or '').strip()
        if not val or val.lower().startswith('javascript:'):
            return False
        if tag == 'iframe':
            return bool(_IFRAME_HOST.match(val))
        return bool(_SAFE_URL.match(val))
    return True


class _LandingHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ('script', 'style', 'object', 'embed', 'link', 'meta', 'base'):
            self._skip += 1
            return
        if self._skip or tag not in _ALLOWED_TAGS:
            return
        if tag == 'iframe':
            src = ''
            for name, value in attrs:
                if (name or '').lower() == 'src':
                    src = (value or '').strip()
                    break
            if not _attr_ok('iframe', 'src', src):
                self._skip += 1  # omitir hasta </iframe>
                self._skip_iframe = getattr(self, '_skip_iframe', 0) + 1
                return
        parts = [tag]
        for name, value in attrs:
            if value is None:
                value = ''
            if _attr_ok(tag, name, value):
                parts.append(f'{name}="{html_lib.escape(value, quote=True)}"')
        self._out.append('<' + ' '.join(parts) + '>')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ('script', 'style', 'object', 'embed', 'link', 'meta', 'base'):
            if self._skip:
                self._skip -= 1
            return
        if tag == 'iframe' and getattr(self, '_skip_iframe', 0):
            self._skip_iframe -= 1
            if self._skip:
                self._skip -= 1
            return
        if self._skip or tag not in _ALLOWED_TAGS or tag in _VOID:
            return
        self._out.append(f'</{tag}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID and tag.lower() in _ALLOWED_TAGS and not self._skip:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self._skip:
            return
        self._out.append(html_lib.escape(data))

    def handle_entityref(self, name):
        if not self._skip:
            self._out.append(f'&{name};')

    def handle_charref(self, name):
        if not self._skip:
            self._out.append(f'&#{name};')

    def result(self):
        return ''.join(self._out)


def sanitize_landing_html(html):
    """HTML de landing: tipografía + iframes YouTube/Vimeo; sin scripts."""
    if not (html or '').strip():
        return Markup('')
    parser = _LandingHTMLSanitizer()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return Markup('')
    return Markup(parser.result())
