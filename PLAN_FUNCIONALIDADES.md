# Plan de funcionalidades implementadas

## Seguridad
- CSRF global (Flask-WTF) + inyección automática en formularios y `fetch`
- Webhook Stripe: rechazo si falta `whsec_` (sin fallback inseguro)
- Cookies: `HttpOnly`, `SameSite=Lax`, `Secure` con `SESSION_COOKIE_SECURE=true`
- Rate limit: login 15/min, registro 10/h, webhook 120/min, email prueba 5/h
- Cabeceras HTTP vía Flask-Talisman (CSP, HSTS si HTTPS)
- Aviso en arranque si `SECRET_KEY` por defecto

## Alumnos
- **Mi progreso** (`/mi-progreso`): % por curso, continuar, certificado
- **Portal Stripe** (`/mi-cuenta/suscripcion`): tarjeta, facturas, cancelar
- **Drip**: `drip_days` por lección + secuencia (completar anterior)
- **Cuestionarios** por sección (admin crea en editar curso)
- **Tareas** con feedback mentor (admin → Tareas / entregas)
- **Certificado PDF** al completar lecciones + cuestionarios obligatorios
- **Reportar posts** en comunidad

## Negocio / planes
- Precio **anual**, **días de prueba**, **cupón Stripe** por plan
- Registro: mensual/anual + código promocional
- Categorías de foro restringidas por plan

## Admin
- Estadísticas, export CSV usuarios, moderación
- Email masivo por tandas (50) + historial campañas
- Email de prueba de plantillas en Pagos Stripe

## Infra
- Worker `reminder`: emails 24h y 1h antes de clases (`docker compose` servicio `reminder`)

## Producción
En `.env`: `SESSION_COOKIE_SECURE=true` y proxy HTTPS delante del puerto 8080.
