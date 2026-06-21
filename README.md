# Academia Online / NuncaTanYo

Plataforma web de academia y comunidad de suscripción construida con **Flask** y **PostgreSQL**. Incluye landing de conversión, biblioteca de vídeo, foro, calendario de encuentros, recursos descargables, panel de administración, pagos por suscripción con **Stripe** (precios España/internacional automáticos), backups automáticos y despliegue con **Docker**.

**Versión actual:** `v2.1.0`

Repositorio: [github.com/Obenus/academia_online](https://github.com/Obenus/academia_online)

---

## Índice

- [Novedades v2.0.0](#novedades-v200)
- [Funcionalidades](#funcionalidades)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos](#requisitos)
- [Instalación rápida con Docker](#instalación-rápida-con-docker)
- [Configuración](#configuración)
- [Panel de administración](#panel-de-administración)
- [Servicios en segundo plano](#servicios-en-segundo-plano)
- [Documentación](#documentación)
- [Licencia y notas](#licencia-y-notas)

---

## Novedades v2.0.0

### Experiencia pública

- **Landing de conversión** en `/login` con textos editables (marca NuncaTanYo).
- **Checkout sin registro previo**: la alumna paga en Stripe y la cuenta se crea automáticamente.
- **Precio automático España / internacional** según ubicación (Cloudflare `CF-IPCountry`; por defecto España).
- Página **Empieza por aquí** (`/empieza`) con vídeos de bienvenida.
- **Biblioteca del Círculo** — catálogo de formaciones y encuentros con reproductor propio (YouTube/Vimeo).
- **Recursos** con etiquetas y descarga de archivos.
- Foro con categorías fijas (incl. Preguntas Rocío) y flujo de moderación.
- Calendario con **temática mensual** y categorías de eventos.
- **Miembro del mes** en lugar de ranking público.
- Tema visual personalizable (`theme.css`, colores de marca).

### Administración

- Menú lateral reorganizado por áreas.
- **Landing principal** — editor de todos los textos de `/login`.
- CRUD de **Biblioteca** y **Recursos**.
- Planes con **precio ES e INTL** y Stripe Price IDs por región.
- Colores de la **barra del reproductor** de vídeo.
- Gestión de **WhatsApp VIP** pendiente tras alta.
- Webhook de **grabaciones** para volcar encuentros a la biblioteca.

### Infraestructura

- Worker **reminder** — emails 24 h y 1 h antes de eventos.
- Migraciones centralizadas en `db_migrate.py`.
- Integración **n8n** para preguntas a Rocío (opcional).

---

## Funcionalidades

| Área | Descripción |
|------|-------------|
| **Landing** | Página de conversión + login + suscripción Stripe |
| **Biblioteca** | Vídeos por formación y encuentros grabados |
| **Recursos** | Archivos descargables con tags |
| **Comunidad** | Foro, comentarios, likes, moderación |
| **Calendario** | Eventos en directo, recurrencia, temática mensual |
| **Cursos (legacy)** | Módulo clásico con progreso por lección |
| **Usuarios** | Roles, aprobación, cuentas gratuitas |
| **Suscripciones** | Stripe mensual recurrente, control de impagos y **aviso por email a admins al suspender** |
| **Admin** | Panel completo (ver manual) |
| **Backups** | pg_dump local + S3 opcional |
| **PWA** | Instalable en móvil |

---

## Stack tecnológico

- **Backend:** Python 3.12, Flask 3, Flask-Login, Flask-SQLAlchemy, Flask-Mail
- **Base de datos:** PostgreSQL (producción/Docker), SQLite (fallback local)
- **Servidor:** Gunicorn
- **Pagos:** Stripe (Checkout + webhooks + suscripciones)
- **Email:** SMTP (configurable)
- **Backups:** pg_dump / pg_restore, boto3 (S3 opcional)
- **Frontend:** Jinja2, Tailwind (local), JavaScript vanilla
- **Infra:** Docker Compose

---

## Estructura del proyecto

```
academia_online/
├── app.py                    # Rutas principales
├── models.py                 # Modelos SQLAlchemy
├── billing.py                # Stripe, emails de facturación
├── registration.py           # Alta tras checkout
├── landing_content.py        # Textos por defecto de la landing
├── geo_utils.py              # Detección ES / internacional
├── video_utils.py            # YouTube / Vimeo embed
├── blueprints/
│   ├── library.py            # Biblioteca del Círculo
│   └── resources.py          # Recursos descargables
├── backup_manager.py         # Backups y restauración
├── backup_worker.py
├── billing_worker.py
├── reminder_worker.py        # Recordatorios de calendario
├── docker-compose.yml
├── templates/
│   ├── public/               # Landing de conversión
│   ├── library/
│   ├── resources/
│   └── admin/
├── MANUAL_ADMINISTRADOR.md   # Guía del panel admin
├── MANUAL_DESPLIEGUE.md      # Despliegue en servidor
└── README.md
```

---

## Requisitos

- Docker y Docker Compose (recomendado), **o**
- Python 3.12+, PostgreSQL y dependencias de `requirements.txt`
- Cuenta Stripe (suscripciones)
- Servidor SMTP (emails automáticos)
- Cloudflare u otro proxy con cabecera de país (precios por región)

---

## Instalación rápida con Docker

```bash
git clone https://github.com/Obenus/academia_online.git
cd academia_online

cp .env.example .env
# Edita .env (ACADEMY_NAME, PUBLIC_BASE_URL, MAIL_*, etc.)

mkdir -p secrets
printf '%s' 'TU_SECRET_KEY_LARGA' > secrets/secret_key
printf '%s' 'tu_email_smtp' > secrets/mail_username
printf '%s' 'tu_password_smtp' > secrets/mail_password
printf '%s' '' > secrets/stripe_secret_key
chmod 600 secrets/*

docker compose up -d --build
```

Abre: **http://localhost:8080/login**

### Puertos por defecto

| Servicio | Puerto host |
|----------|-------------|
| App web | 8080 |
| PostgreSQL | 5433 |

### Tras un rebuild

Si varios contenedores arrancan a la vez y la app no responde, levanta primero solo `app` y después el resto:

```bash
docker compose stop billing reminder backup
docker compose up -d app
# Espera a que responda, luego:
docker compose start billing reminder backup
```

---

## Configuración

### Variables en `.env`

| Variable | Descripción |
|----------|-------------|
| `ACADEMY_NAME` | Nombre mostrado en la plataforma |
| `PUBLIC_BASE_URL` | URL pública (emails, redirects Stripe) |
| `DATABASE_URL` | En Docker: `postgresql://postgres:postgres@db:5432/miacademia` |
| `DEFAULT_BILLING_REGION` | Región por defecto si no hay geolocalización (`es`) |
| `ADMIN_EMAIL` | Email para avisos de registro e impagos |
| `N8N_WEBHOOK_PREGUNTAS` | Webhook n8n para posts «Preguntas Rocío» |
| `RECORDING_WEBHOOK_SECRET` | Secreto para webhook de grabaciones |
| `MAIL_*` | Servidor SMTP |
| `STRIPE_PUBLIC_KEY` | Clave pública Stripe (opcional si se configura en admin) |

### Archivos en `secrets/`

| Archivo | Contenido |
|---------|-----------|
| `secret_key` | `SECRET_KEY` de Flask |
| `mail_username` | Usuario SMTP |
| `mail_password` | Contraseña SMTP |
| `stripe_secret_key` | Clave secreta Stripe (opcional) |

### Primer acceso

Si la base de datos está vacía, se crea un admin inicial (solo la primera vez). Consulta las credenciales en la sección de despliegue del manual o en el historial de tu instalación. **Cámbialas inmediatamente.**

---

## Panel de administración

Ruta base: `/admin` (rol `admin`).

| Sección | Ruta |
|---------|------|
| Dashboard | `/admin` |
| Ajustes y marca | `/admin/ajustes` |
| Landing principal | `/admin/landing` |
| Biblioteca | `/admin/biblioteca` |
| Recursos | `/admin/recursos` |
| Cursos (legacy) | `/admin/cursos` |
| Eventos calendario | `/admin/clases` |
| Temática mensual | `/admin/calendario/tematica` |
| Usuarios | `/admin/usuarios` |
| Suscripciones | `/admin/suscripciones` |
| Email masivo | `/admin/email` |
| Planes | `/admin/planes` |
| Stripe | `/admin/pagos` |
| Backups | `/admin/backups` |

Guía detallada: **[MANUAL_ADMINISTRADOR.md](MANUAL_ADMINISTRADOR.md)**

### Webhook Stripe

```
https://TU_DOMINIO/webhooks/stripe
```

Eventos: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`.

Cuando falla el cobro mensual, la cuenta se suspende y los administradores reciben un email (plantilla en **Ajustes → Alertas de facturación**; destino: `ADMIN_EMAIL`).

---

## Servicios en segundo plano

| Contenedor | Función |
|------------|---------|
| `miacademia-app` | Aplicación web (Gunicorn) |
| `miacademia-db` | PostgreSQL 16 |
| `miacademia-backup` | Copias de seguridad programadas |
| `miacademia-billing` | Suspensión por impago y email automático a administradores |
| `miacademia-reminder` | Recordatorios de eventos (24 h / 1 h) |

```bash
docker compose logs -f app
docker compose logs -f billing
docker compose logs -f reminder
docker compose logs -f backup
```

---

## Documentación

| Documento | Contenido |
|-----------|-----------|
| [MANUAL_ADMINISTRADOR.md](MANUAL_ADMINISTRADOR.md) | Uso del panel admin, flujos, webhooks |
| [MANUAL_DESPLIEGUE.md](MANUAL_DESPLIEGUE.md) | Instalación en servidor, Railway, Stripe, email |

---

## Licencia y notas

- No subas `.env`, `secrets/` ni dumps de `backups/` al repositorio.
- Rota credenciales antes de producción.
- El módulo de **cursos legacy** convive con la **Biblioteca**; el contenido nuevo debería ir a Biblioteca.
- Precios internacionales: requiere cabecera de país del proxy; sin ella se aplica precio España.

Desarrollado a partir de la base **Academia Online**, ampliada para **NuncaTanYo** con landing de conversión, biblioteca, recursos, precios por región y panel de administración extendido.
