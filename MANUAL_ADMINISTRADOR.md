# Manual del administrador

Guía operativa del panel de administración de la plataforma (comunidad de suscripción con cursos, foro, biblioteca, calendario y pagos Stripe).

**Acceso:** `https://TU_DOMINIO/admin` — requiere usuario con rol `admin`.

---

## Índice

1. [Primeros pasos](#1-primeros-pasos)
2. [Navegación del panel](#2-navegación-del-panel)
3. [Comunidad](#3-comunidad)
4. [Contenido](#4-contenido)
5. [Calendario](#5-calendario)
6. [Miembros y suscripciones](#6-miembros-y-suscripciones)
7. [Facturación y Stripe](#7-facturación-y-stripe)
8. [Sistema](#8-sistema)
9. [Flujo de alta de nuevas alumnas](#9-flujo-de-alta-de-nuevas-alumnas)
10. [Webhooks e integraciones](#10-webhooks-e-integraciones)
11. [Consejos y solución de problemas](#11-consejos-y-solución-de-problemas)

---

## 1. Primeros pasos

### Acceso inicial

En la **primera instalación** (base de datos vacía) se crea un administrador automáticamente. Las credenciales por defecto están en el README; **cámbialas en cuanto entres**.

### Orden recomendado de configuración

1. **Ajustes y marca** — nombre de la academia, colores, banner, emails.
2. **Stripe** — claves API, webhook y activación de pagos.
3. **Planes** — precios España e internacional, IDs de precio en Stripe.
4. **Landing principal** — textos de la página de acceso (`/login`).
5. **Biblioteca / Recursos** — contenido para alumnas activas.
6. **Calendario** — eventos en directo y temática mensual.
7. **Backups** — copias automáticas antes de cambios importantes.

### Tras actualizar el código (Docker)

```bash
docker compose up -d --build
```

Si la app tarda en responder tras un rebuild, arranca primero solo el servicio `app` y después `billing`, `reminder` y `backup` (varios contenedores ejecutan migraciones al inicio y pueden bloquearse entre sí en PostgreSQL).

---

## 2. Navegación del panel

El menú lateral agrupa las secciones así:

| Grupo | Secciones |
|-------|-----------|
| **Comunidad** | Ajustes y marca, Landing principal, Publicar post, Moderación |
| **Contenido** | Biblioteca, Recursos, Cursos (legacy) |
| **Calendario** | Eventos, Temática mensual |
| **Miembros** | Usuarios, Suscripciones, Email masivo |
| **Facturación** | Planes, Stripe |
| **Sistema** | Estadísticas, Backups, Entregas |

Los badges rojos en **Moderación** y **Suscripciones** indican reportes pendientes o altas VIP de WhatsApp por revisar.

---

## 3. Comunidad

### Ajustes y marca (`/admin/ajustes`)

Configuración central de la plataforma:

- **Identidad:** nombre de la academia, imagen de portada del foro, descripción, enlace destacado.
- **Emails automáticos:** bienvenida al usuario, aviso a administradores en cada registro, recordatorios de eventos (24 h y 1 h antes).
- **Landing y Empieza por aquí:** URL de vídeos de bienvenida, texto de la página `/empieza`, enlace al grupo de WhatsApp.
- **Miembro del mes:** usuario destacado, nota y mes (formato `YYYY-MM`).
- **Marca:** logo, colores primario/secundario, fuente.
- **Barra del reproductor:** colores de la barra de controles de vídeo en la Biblioteca (fondo, acento, texto, botones).
- **Alertas de facturación:** plantilla de email cuando una alumna cancela o entra en impago.

Las plantillas de email admiten variables como `{{username}}`, `{{email}}`, `{{plan_name}}`, `{{login_url}}`, etc. (se muestran en cada bloque del formulario).

### Landing principal (`/admin/landing`)

Edita todos los textos de la **página de conversión y acceso** (`/login`):

- Titular, apertura emocional, qué es el círculo, beneficios, preguntas que se exploran, qué incluye, para quién es, cierre.
- Texto del botón de suscripción y nota de precio.
- Título y subtítulo del formulario de login.

Usa **Restaurar textos del PDF** para volver a los textos originales de NuncaTanYo. Los párrafos se separan con una línea en blanco; las listas, con una línea por ítem.

El precio mostrado en la landing **no se edita aquí**: viene de **Planes** y se aplica automáticamente según la ubicación de la visitante (España o internacional; si no se detecta, España).

### Publicar post

Acceso directo al formulario de nueva publicación en el foro (misma función que `/comunidad/nuevo`).

### Moderación

Revisa reportes de publicaciones y comentarios. Puedes cambiar el estado de posts del foro (por ejemplo, preguntas para Rocío con flujo de revisión).

---

## 4. Contenido

### Biblioteca del Círculo (`/admin/biblioteca`)

Repositorio principal de formación en vídeo para alumnas suscritas.

**Tipos de ítem:**

- **Lección / módulo** — vídeo de una formación (YouTube o Vimeo).
- **Encuentro** — grabación de una clase en directo.

**Campos importantes:**

- Título, descripción (para agrupar por formación usa `Nombre formación — subtítulo`).
- URL del vídeo (YouTube/Vimeo).
- Año y mes (organización del catálogo).
- Orden y publicación (solo los publicados son visibles).

**Reproductor:** la URL de embed no se expone en el HTML; se sirve por API autenticada. Los colores de la barra de controles se configuran en Ajustes.

**Webhook de grabaciones:** tras un encuentro, un flujo externo (n8n) puede llamar a `POST /webhooks/grabacion` con el ID de la clase y la URL de la grabación para crear o actualizar el ítem automáticamente (requiere cabecera secreta `RECORDING_WEBHOOK_SECRET`).

**Migración desde cursos antiguos:** existe el script `scripts/migrate_courses_to_library.py` para volcar lecciones del módulo legacy a la biblioteca.

### Recursos (`/admin/recursos`)

Archivos descargables (PDFs, plantillas, etc.) con **etiquetas** para filtrar en la vista pública `/recursos`.

- Crear recurso: título, descripción, archivo, etiquetas (separadas por espacios o comas).
- Editar o eliminar desde el listado admin.

### Cursos (legacy) (`/admin/cursos`)

Módulo de formación clásico (secciones, lecciones, vídeos, archivos). Sigue operativo pero el contenido nuevo debería ir preferentemente a **Biblioteca**. Útil para mantener formaciones antiguas o el progreso por lección.

---

## 5. Calendario

### Eventos (`/admin/clases`)

Gestión de clases y encuentros en directo:

- Título, fecha/hora, duración, instructora.
- Enlace de Google Meet (u otro).
- Categoría del calendario (colores y agrupación).
- Recurrencia semanal o mensual (opcional).

Los alumnos ven el calendario en `/calendario`. Los recordatorios por email los envía el servicio **reminder** (configurable en Ajustes).

### Temática mensual (`/admin/calendario/tematica`)

Define el **tema del mes** y subtema que se muestran en el calendario público (motivación visual y contexto del contenido del mes).

### Categorías de calendario

Desde la página de eventos puedes crear, editar y borrar categorías (nombre, color).

---

## 6. Miembros y suscripciones

### Usuarios (`/admin/usuarios`)

- Aprobar o rechazar registros pendientes.
- Cambiar rol (alumna / admin).
- Asignar plan y tipo de facturación.
- Crear usuarios manualmente (incluidas cuentas gratuitas).
- Suspender o eliminar cuentas.

### Suscripciones (`/admin/suscripciones`)

Vista global del estado de pago:

| Estado | Significado habitual |
|--------|----------------------|
| Activa | Suscripción al día |
| Impago / past_due | Fallo de cobro; el worker puede suspender la cuenta |
| Cancelada | La alumna ha cancelado en Stripe |
| Gratuita | Cuenta sin cobros (marcada por admin) |

**Acciones:**

- Cambiar estado manualmente si hace falta.
- Marcar como **gratuita** (sin Stripe).
- Confirmar **WhatsApp VIP** cuando hayas añadido a la alumna al grupo (quita el badge de pendiente).

### Email masivo (`/admin/email`)

Envío manual de correo a alumnas (filtros por estado, plan, etc.). Requiere SMTP configurado.

---

## 7. Facturación y Stripe

### Planes (`/admin/planes`)

Cada plan de suscripción mensual incluye:

| Campo | Uso |
|-------|-----|
| Precio ES (€) | Visitantes desde España |
| Precio INTL (€) | Visitantes fuera de España |
| Stripe Price ID ES / INTL | IDs de precio en Stripe (recomendado) |
| Precio anual | Opcional, con su Price ID |
| Días de prueba | Periodo trial en Stripe |
| Cupón por defecto | Código promocional Stripe |

Activa o desactiva planes. No se puede eliminar un plan con usuarios asignados.

**Detección de región:** automática por cabecera de país del proxy (Cloudflare `CF-IPCountry`). Sin detección → precio España.

### Stripe (`/admin/pagos`)

- Clave pública y secreta (la secreta se guarda cifrada).
- Secreto del webhook.
- Activar pagos y **activación automática** tras pago (sin aprobación manual).

**Webhook en Stripe:**

```
https://TU_DOMINIO/webhooks/stripe
```

Eventos recomendados: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`.

---

## 8. Sistema

### Estadísticas

Métricas de uso: usuarios, actividad, contenido.

### Backups (`/admin/backups`)

- Intervalo, retención y ruta local.
- Subida opcional a Amazon S3 (claves cifradas en BD).
- Ejecutar copia manual o **restaurar** (operación destructiva: pide confirmación).

Las copias las genera el contenedor `miacademia-backup` según la configuración.

### Entregas

Gestión de tareas/entregas de alumnas (si el módulo está activo en tu despliegue).

---

## 9. Flujo de alta de nuevas alumnas

1. La visitante entra en **`/login`** (landing de conversión).
2. Ve un único precio según su ubicación (ES / internacional).
3. Elige plan y pulsa suscribirse → **Stripe Checkout**.
4. Tras el pago, Stripe notifica el webhook.
5. La plataforma crea la cuenta (o la activa), envía email de bienvenida con credenciales y avisa a los administradores.
6. La alumna accede a: Empieza por aquí, Biblioteca, Foro, Calendario, Recursos.

Si **activación automática** está desactivada, el admin debe aprobar en Usuarios tras el pago.

Usuarios ya registrados que visitan `/` son redirigidos al panel de la comunidad; los no autenticados, a `/login`.

---

## 10. Webhooks e integraciones

| Endpoint | Función |
|----------|---------|
| `POST /webhooks/stripe` | Pagos, renovaciones, cancelaciones, impagos |
| `POST /webhooks/grabacion` | Añadir grabación a Biblioteca tras un encuentro |

**Variables de entorno útiles:**

| Variable | Descripción |
|----------|-------------|
| `PUBLIC_BASE_URL` | URL pública (emails y redirects) |
| `ADMIN_EMAIL` | Destino de avisos de registro e impagos |
| `N8N_WEBHOOK_PREGUNTAS` | Notificación externa en posts «Preguntas Rocío» |
| `RECORDING_WEBHOOK_SECRET` | Secreto para webhook de grabaciones |

---

## 11. Consejos y solución de problemas

### La landing muestra precio de España siempre

- En producción, asegura que Cloudflare (o tu proxy) envía `CF-IPCountry`.
- En local no hay geolocalización: es normal ver precio ES.
- Prueba internacional: `https://TU_DOMINIO/login?region=intl`.

### Los pagos no funcionan

1. Pagos activados en `/admin/pagos`.
2. Claves Stripe correctas (modo test vs live).
3. Webhook configurado y secreto guardado en admin.
4. Price IDs en cada plan coinciden con Stripe.

### No llegan emails

1. SMTP en `.env` y archivos en `secrets/`.
2. Revisa logs: `docker compose logs -f app`.
3. Recordatorios de calendario: servicio `reminder` en marcha.

### Vídeo de biblioteca no carga

1. URL válida de YouTube o Vimeo.
2. Usuario autenticada y suscripción activa.
3. En YouTube, dominio permitido en configuración del vídeo si aplica.

### Tras desplegar cambios, errores 500

Suele deberse a migraciones pendientes. Reinicia `app` y revisa logs. Si persiste: `docker compose logs app` buscando `[DB] ERROR`.

### Cuentas suspendidas por impago

El worker `billing` revisa periodos vencidos. Tras regularizar en Stripe, el webhook reactiva; o el admin cambia el estado manualmente.

---

## Documentación relacionada

- [README.md](README.md) — visión general e instalación
- [MANUAL_DESPLIEGUE.md](MANUAL_DESPLIEGUE.md) — despliegue en servidor, Railway, Docker

---

*Versión de plataforma documentada: 2.0.0*
