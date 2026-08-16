"""Textos por defecto de la landing de conversión (NuncaTanYo)."""

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
}

# Vídeos opcionales entre bloques de texto (el de tras intro es landing_video_url, ya existente).
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
    'landing_title', 'landing_hook', 'landing_intro', 'landing_video_url', 'landing_what_is',
    'landing_how_helps', 'landing_explore_questions', 'landing_includes',
    'landing_for_you', 'landing_closing', 'landing_cta_text', 'landing_price_note',
    'landing_login_title', 'landing_login_subtitle',
    'landing_video_after_title', 'landing_video_after_hook',
    'landing_video_after_what_is', 'landing_video_after_how_helps',
    'landing_video_after_explore', 'landing_video_after_includes',
    'landing_video_after_for_you', 'landing_video_after_closing',
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
