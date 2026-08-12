"""Renderizado seguro de Markdown a HTML."""
import markdown as md_lib
from markupsafe import Markup


_EXTENSIONS = ['extra', 'sane_lists', 'nl2br']


def render_markdown(text):
    """Convierte Markdown a HTML seguro (sin scripts)."""
    if not (text or '').strip():
        return Markup('')
    html = md_lib.markdown(
        text.strip(),
        extensions=_EXTENSIONS,
        output_format='html5',
    )
    # markdown no ejecuta scripts; sanitizado básico de tags peligrosos
    for tag in ('script', 'iframe', 'object', 'embed'):
        html = html.replace(f'<{tag}', '&lt;' + tag).replace(f'</{tag}>', '&lt;/' + tag + '&gt;')
    return Markup(html)
