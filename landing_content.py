"""Textos por defecto y HTML único de la landing de conversión (NuncaTanYo)."""
import html as html_lib

from video_utils import video_embed_url_public


LANDING_DEFAULTS = {
    'landing_title': 'NUNCA TAN YO',
    'landing_hook': '''¿Te pasa que últimamente te notas diferente?
Más cansada.
Más irritable.
Con menos paciencia.
Como si algo hubiera cambiado y no supieras muy bien ponerle nombre.

¿Te pasa que quieres a tu familia con locura pero cada vez necesitas más espacio?
¿Que llevas años ocupándote de todo el mundo y ya no sabes muy bien qué necesitas tú?
¿Que tienes una vida que, sobre el papel, está bien… y aun así hay una parte de ti que siente que algo falta?

Si has respondido sí a alguna de estas preguntas, quiero que sepas algo:

No estás perdida.
Estás cambiando.

Y quizá el problema no es que te pase algo.
Quizá el problema es que nadie nos enseñó qué hacer cuando llegaba esta etapa.

Porque los hijos crecen.
Las relaciones cambian.
Las prioridades cambian.
Y nosotras también.

Y empiezan a aparecer preguntas que llevaban años esperando su turno.
¿Qué quiero ahora?
¿Qué necesito?
¿Qué cosas ya no quiero seguir sosteniendo?
¿Quién soy en esta nueva etapa?
¿Y qué quiero hacer con todo lo que todavía me queda por vivir?''',
    'landing_intro': '''No necesitas más consejos.
Internet ya está lleno de consejos.

Lo que muchas veces necesitamos son mejores preguntas.
Preguntas que nos ayuden a entendernos.
A escucharnos.
A tomar decisiones con más claridad.
Y a soltar poco a poco la culpa, la exigencia y la carga que llevamos años sosteniendo.

Por eso nació NuncaTanYo.''',
    'landing_what_is': '''NuncaTanYo no es terapia.
NuncaTanYo no es un curso.
NuncaTanYo no es otra obligación más.

NuncaTanYo es un círculo de mujeres.
Un espacio donde poder pensar en voz alta.
Compartir.
Reflexionar.
Reírnos de nosotras mismas.
Y descubrir que muchas de las cosas que estamos viviendo también les están pasando a otras mujeres.

Porque a veces una buena conversación cambia más que cien consejos.''',
    'landing_how_helps': '''Dentro de NuncaTanYo voy a acompañarte a hacerte mejores preguntas.

No voy a decirte lo que tienes que hacer.
No voy a decidir por ti.
No voy a darte fórmulas mágicas.

Voy a ayudarte a encontrar tus propias respuestas.
Con más claridad.
Con menos culpa.
Y con el apoyo de otras mujeres que están recorriendo una etapa parecida.''',
    'landing_explore_questions': '''¿Qué necesito yo ahora?
¿Qué estoy sosteniendo que ya no me corresponde?
¿Qué quiero conservar de la mujer que he sido?
¿Qué necesito dejar atrás?
¿Cómo poner límites sin sentir culpa?
¿Cómo volver a priorizarme sin sentirme egoísta?
¿Qué quiero hacer con todo lo que todavía me queda por vivir?''',
    'landing_includes': '''Un encuentro semanal en directo conmigo.
Una comunidad de mujeres que están viviendo preguntas parecidas a las tuyas.
Conversaciones honestas, sin juicios y sin etiquetas.
Audios, reflexiones y recursos para acompañarte cada semana.
Biblioteca con todos los contenidos.
Un espacio donde participar cuando te apetezca y simplemente escuchar cuando lo necesites.''',
    'landing_for_you': '''Sientes que estás entrando en una nueva etapa de tu vida.
Te haces preguntas que antes no te hacías.
Estás cansada de intentar llegar a todo.
Quieres recuperar claridad sobre lo que quieres.
Necesitas bajar la carga y la exigencia.
Buscas conversaciones reales más que consejos rápidos.
Intuyes que todavía te queda muchísimo por vivir.''',
    'landing_closing': '''Porque aquí no venimos a hacerlo perfecto.
Venimos a escucharnos.

Tu ratito.
Tu espacio.
Tu círculo.
Un lugar donde volver a ti.''',
    'landing_cta_text': '🤍 QUIERO MI PLAZA',
    'landing_price_note': 'Sin permanencia · Cancela cuando quieras',
    'landing_login_title': '¿Ya eres miembro?',
    'landing_login_subtitle': 'Accede con tu email y contraseña',
    'landing_success_text': '''¡Bienvenida a Nunca Tan Yo! 💛

Tu registro se ha completado correctamente y ya formas parte de la comunidad.

📩 Revisa tu correo, porque te hemos enviado toda la información de acceso. Si no lo encuentras, mira también en Spam, Promociones o Correo no deseado y márcanos como remitente seguro para no perderte nada.

Ahora empieza lo importante: entrar, mirar, curiosear, resolver tus primeras dudas… y empezar a hacerte un poco más de hueco a ti dentro de tu propia vida.

✨ Accede a la comunidad y descubre todo lo que tienes disponible: contenidos, ayuda, encuentros, herramientas y un espacio donde compartir lo que te pasa con mujeres que probablemente están viviendo muchas de las mismas cosas que tú.

No tienes que hacerlo todo hoy. Empieza por entrar, echar un vistazo y ver qué es lo que más necesitas ahora.

Porque sí: ya estás un poquito más cerca de hacer muchos de esos cambios que llevas tiempo pensando.

💬 Y una cosa más: pídele a Rocío acceso al grupo VIP de WhatsApp de Nunca Tan Yo, donde estaremos todavía más cerca, compartiremos avisos, encuentros, novedades y contenido especial.''',
    'landing_success_button_text': 'ACCEDER A LA COMUNIDAD',
    'landing_success_footer': '''Nos vemos dentro.
Esto acaba de empezar. 💛''',
}

# Campos legacy (solo para migración / defaults del HTML).
LANDING_LEGACY_CONTENT_FIELDS = [
    'landing_hook', 'landing_intro', 'landing_what_is', 'landing_how_helps',
    'landing_explore_questions', 'landing_includes', 'landing_for_you', 'landing_closing',
]

LANDING_VIDEO_FIELDS = [
    ('landing_video_after_title', 'Vídeo tras el título / cabecera'),
    ('landing_video_after_hook', 'Vídeo tras la apertura'),
    ('landing_video_url', 'Vídeo tras «Por qué nació» (entre intro y ¿Qué es?)'),
    ('landing_video_after_what_is', 'Vídeo tras «¿Qué es?»'),
    ('landing_video_after_how_helps', 'Vídeo tras «¿Cómo puede ayudarte?»'),
    ('landing_video_after_explore', 'Vídeo tras «Preguntas que exploraremos»'),
    ('landing_video_after_includes', 'Vídeo tras «¿Qué encontrarás dentro?»'),
    ('landing_video_after_for_you', 'Vídeo tras «Este círculo es para ti si…»'),
    ('landing_video_after_closing', 'Vídeo tras el cierre emocional'),
]

LANDING_FORM_FIELDS = [
    'landing_title',
    'landing_body_html',
    'landing_cta_text', 'landing_price_note',
    'landing_login_title', 'landing_login_subtitle',
    'landing_success_text', 'landing_success_button_text', 'landing_success_footer',
]


def landing_text(site, field):
    """Texto guardado en BD o valor por defecto del PDF."""
    val = (getattr(site, field, None) or '').strip()
    return val or LANDING_DEFAULTS.get(field, '')


def landing_paragraphs(site, field):
    """Lista de párrafos (bloques separados por línea en blanco)."""
    raw = landing_text(site, field)
    parts = [p.strip() for p in raw.replace('\r\n', '\n').split('\n\n') if p.strip()]
    if parts:
        return parts
    return [ln.strip() for ln in raw.split('\n') if ln.strip()]


def landing_lines(site, field):
    """Lista de líneas (una por fila)."""
    return [ln.strip() for ln in landing_text(site, field).split('\n') if ln.strip()]


def _esc(text):
    return html_lib.escape(text or '', quote=False)


def _paragraphs_from_raw(raw):
    raw = (raw or '').replace('\r\n', '\n').strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split('\n\n') if p.strip()]
    if parts:
        return parts
    return [ln.strip() for ln in raw.split('\n') if ln.strip()]


def _lines_from_raw(raw):
    return [ln.strip() for ln in (raw or '').replace('\r\n', '\n').split('\n') if ln.strip()]


def _field_value(source, field):
    if isinstance(source, dict):
        val = (source.get(field) or '').strip()
    else:
        val = (getattr(source, field, None) or '').strip()
    return val or LANDING_DEFAULTS.get(field, '')


def _video_url(source, field):
    if isinstance(source, dict):
        return (source.get(field) or '').strip()
    return (getattr(source, field, None) or '').strip()


def _iframe_block(url):
    embed = video_embed_url_public(url)
    if not embed:
        return ''
    src = html_lib.escape(embed, quote=True)
    return (
        '<section class="conv-section">\n'
        '  <div class="relative w-full overflow-hidden rounded-2xl bg-black" style="padding-bottom:56.25%">\n'
        f'    <iframe src="{src}" title="Vídeo" class="absolute inset-0 w-full h-full border-0" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" '
        'allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe>\n'
        '  </div>\n'
        '</section>\n'
    )


def _prose_paragraphs(paragraphs, lead_count=0):
    chunks = []
    for i, p in enumerate(paragraphs):
        # Conservar saltos de línea internos como <br>
        inner = '<br>\n'.join(_esc(line) for line in p.split('\n'))
        cls = ' class="lead"' if i < lead_count else ''
        chunks.append(f'<p{cls}>{inner}</p>')
    return '\n'.join(chunks)


def build_landing_body_html(source=None):
    """Genera el HTML único a partir de campos legacy (o defaults)."""
    source = source or LANDING_DEFAULTS
    title = _field_value(source, 'landing_title')
    parts = []

    # Hero + hook
    parts.append('<section class="conv-section">')
    parts.append(f'  <h1 class="conv-hero mb-6">{_esc(title)}</h1>')
    v = _iframe_block(_video_url(source, 'landing_video_after_title'))
    if v:
        parts.append(v)
    hook_ps = _paragraphs_from_raw(_field_value(source, 'landing_hook'))
    if hook_ps:
        parts.append('  <div class="conv-prose">')
        parts.append(_prose_paragraphs(hook_ps, lead_count=4))
        parts.append('  </div>')
    parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_hook'))
    if v:
        parts.append(v)

    intro_ps = _paragraphs_from_raw(_field_value(source, 'landing_intro'))
    if intro_ps:
        parts.append('<section class="conv-section conv-prose">')
        parts.append(_prose_paragraphs(intro_ps))
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_url'))
    if v:
        parts.append(v)

    what_ps = _paragraphs_from_raw(_field_value(source, 'landing_what_is'))
    if what_ps:
        parts.append('<section class="conv-section">')
        parts.append(f'  <h2>¿Qué es {_esc(title)}?</h2>')
        parts.append('  <div class="conv-prose">')
        parts.append(_prose_paragraphs(what_ps))
        parts.append('  </div>')
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_what_is'))
    if v:
        parts.append(v)

    how_ps = _paragraphs_from_raw(_field_value(source, 'landing_how_helps'))
    if how_ps:
        parts.append('<section class="conv-section">')
        parts.append('  <h2>¿Cómo puede ayudarte?</h2>')
        parts.append('  <div class="conv-prose">')
        parts.append(_prose_paragraphs(how_ps))
        parts.append('  </div>')
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_how_helps'))
    if v:
        parts.append(v)

    explore = _lines_from_raw(_field_value(source, 'landing_explore_questions'))
    if explore:
        parts.append('<section class="conv-section">')
        parts.append('  <h2>Algunas de las preguntas que exploraremos juntas</h2>')
        parts.append('  <ul class="conv-list">')
        for line in explore:
            parts.append(f'    <li><span class="ico">🤍</span><span>{_esc(line)}</span></li>')
        parts.append('  </ul>')
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_explore'))
    if v:
        parts.append(v)

    includes = _lines_from_raw(_field_value(source, 'landing_includes'))
    if includes:
        parts.append('<section class="conv-section">')
        parts.append('  <h2>¿Qué encontrarás dentro?</h2>')
        parts.append('  <ul class="conv-list">')
        for line in includes:
            parts.append(f'    <li><span class="ico">🤍</span><span>{_esc(line)}</span></li>')
        parts.append('  </ul>')
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_includes'))
    if v:
        parts.append(v)

    closing_parts = _paragraphs_from_raw(_field_value(source, 'landing_closing'))
    if closing_parts:
        first = closing_parts[0]
        parts.append('<section class="conv-section conv-prose">')
        for line in first.split('\n'):
            line = line.strip()
            if line:
                parts.append(f'  <p>{_esc(line)}</p>')
        parts.append('</section>')

    for_you = _lines_from_raw(_field_value(source, 'landing_for_you'))
    if for_you:
        parts.append('<section class="conv-section">')
        parts.append('  <h2>Este círculo es para ti si…</h2>')
        parts.append('  <ul class="conv-check">')
        for line in for_you:
            parts.append(f'    <li>{_esc(line)}</li>')
        parts.append('  </ul>')
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_for_you'))
    if v:
        parts.append(v)

    if len(closing_parts) > 1:
        parts.append('<section class="conv-section conv-closing">')
        for line in closing_parts[1].split('\n'):
            line = line.strip()
            if line:
                parts.append(f'  <p>{_esc(line)}</p>')
        parts.append('</section>')

    v = _iframe_block(_video_url(source, 'landing_video_after_closing'))
    if v:
        parts.append(v)

    return '\n'.join(parts).strip() + '\n'


# HTML por defecto (sin vídeos) se sustituye por el contenido editorial actual.
def _load_default_landing_body():
    import os
    path = os.path.join(os.path.dirname(__file__), 'data', 'landing_body_default.html')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip() + '\n'
    except OSError:
        return build_landing_body_html(LANDING_DEFAULTS)


LANDING_DEFAULTS['landing_body_html'] = _load_default_landing_body()


def ensure_landing_body_html(site, *, commit=False, db_session=None):
    """Si landing_body_html está vacío, lo rellena migrando campos legacy."""
    current = (getattr(site, 'landing_body_html', None) or '').strip()
    if current:
        return False
    site.landing_body_html = build_landing_body_html(site)
    if commit and db_session is not None:
        db_session.commit()
    return True
