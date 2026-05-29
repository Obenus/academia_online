# Academia Online

Plataforma web de academia / comunidad de aprendizaje construida con **Flask** y **PostgreSQL**. Incluye cursos con vídeo, foro, ranking por puntos, calendario de clases en directo, panel de administración, pagos por suscripción con **Stripe**, backups automáticos y despliegue con **Docker**.

Repositorio: [github.com/Obenus/academia_online](https://github.com/Obenus/academia_online)

---

## Índice

- [Funcionalidades de la aplicación base](#funcionalidades-de-la-aplicación-base)
- [Mejoras y funcionalidades añadidas](#mejoras-y-funcionalidades-añadidas)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos](#requisitos)
- [Instalación rápida con Docker](#instalación-rápida-con-docker)
- [Configuración](#configuración)
- [Panel de administración](#panel-de-administración)
- [Servicios en segundo plano](#servicios-en-segundo-plano)
- [Despliegue alternativo (Railway)](#despliegue-alternativo-railway)
- [Documentación adicional](#documentación-adicional)
- [Licencia y notas](#licencia-y-notas)

---

## Funcionalidades de la aplicación base

Estas capacidades formaban parte del proyecto original (academia tipo Skool / Marca Atractora):

| Área | Descripción |
|------|-------------|
| **Cursos** | Catálogo, secciones, lecciones, vídeos (Vimeo/YouTube), archivos adjuntos, progreso por lección |
| **Comunidad** | Foro con categorías, publicaciones, comentarios, likes, posts fijados |
| **Usuarios** | Registro con foto de perfil y bio, login, roles (alumno / admin) |
| **Aprobación** | Registro en estado `pending` hasta que un admin aprueba o rechaza |
| **Ranking** | Sistema de puntos por lecciones, comentarios y posts; clasificación |
| **Calendario** | Clases en directo con enlace Meet, recurrencia semanal/mensual |
| **Miembros** | Listado público de miembros activos con nivel y puntos |
| **Admin** | Panel con estadísticas, cursos, usuarios, clases, categorías, email masivo |
| **Pagos (cursos)** | Checkout Stripe por curso de pago (one-shot) |
| **PWA** | Manifest y service worker para instalación en móvil |
| **Notificaciones** | Avisos in-app (nuevas clases, aprobaciones, etc.) |
| **Ajustes comunidad** | Nombre academia, banner, descripción, enlace destacado |
| **Temas** | Modo claro / oscuro en la interfaz |

---

## Mejoras y funcionalidades añadidas

Resumen de lo implementado sobre la base original:

### Docker y despliegue

- `Dockerfile` con Python 3.12, Gunicorn y cliente PostgreSQL.
- `docker-compose.yml` con servicios:
  - **app** — aplicación web (puerto host `8080`).
  - **db** — PostgreSQL 16 (puerto host `5433`).
  - **backup** — worker de copias de seguridad programadas.
  - **billing** — worker de control de suscripciones en mora.
- `.env.example` y carpeta `secrets/` para credenciales fuera del repositorio.
- Puertos configurados para no chocar con servicios habituales (`80`, `8081`, `3306`, `6379`).

### Seguridad de secretos

- Claves sensibles en archivos montados (`SECRET_KEY`, SMTP, Stripe) vía `*_FILE`.
- Credenciales S3 y Stripe de backup/pagos **cifradas en base de datos** (Fernet).
- `.gitignore` excluye `.env`, `secrets/` y `backups/`.

### Suscripciones y pagos (Stripe)

- **Registro con suscripción mensual**: el usuario elige un plan y paga en Stripe Checkout antes de activarse.
- **Planes configurables** (`/admin/planes`): nombre, precio €/mes, descripción, Stripe Price ID opcional.
- **Configuración Stripe** (`/admin/pagos`): claves, webhook, activación automática tras pago o aprobación manual.
- **Webhook** `/webhooks/stripe` para renovaciones, fallos de pago y cancelaciones.
- **Cuentas gratuitas**: el admin puede crear usuarios o marcar existentes como `gratuito` (sin cobros).
- **Control de suscripciones** (`/admin/suscripciones`): tabla con estado de pago, cambio de estado y plan.
- **Tabla de usuarios** ampliada: columnas **Plan** y **Pago**, cambio de plan desde el listado.
- Suspensión automática por impago (worker + webhook) y notificación a administradores.

### Emails automáticos

- **Bienvenida** al usuario tras registro y pago confirmado (plantilla editable en Admin → Ajustes).
- **Aviso a administradores** en cada nuevo registro con datos completos (usuario, email, bio, plan, precio, fecha, estado).
- Variables de plantilla: `{{username}}`, `{{email}}`, `{{plan_name}}`, `{{plan_price}}`, `{{created_at}}`, `{{status}}`, `{{login_url}}`, `{{approval_note}}`, etc.

### Backups

- Módulo `backup_manager.py`: `pg_dump`, retención local, subida opcional a **S3**.
- Página dedicada **Admin → Backups** (`/admin/backups`), separada de ajustes de comunidad.
- Listado de copias disponibles con tamaño y fecha.
- **Restauración** desde la interfaz con confirmación explícita (`pg_restore`).
- Worker `backup_worker.py` que ejecuta copias según intervalo configurado en BD.

### Ajustes y administración

- **Ajustes de comunidad** (`/admin/ajustes`) separados de backups: marca, banner, emails.
- Sincronización del nombre de academia desde `.env` / `ACADEMY_NAME` si sigue en valor por defecto.
- Admin semilla solo en **primera instalación** (cuando no hay usuarios en BD), no en cada reinicio.
- Enlaces rápidos en el dashboard: Ajustes, Backups, Planes, Pagos Stripe, Suscripciones.

### Archivos nuevos principales

| Archivo | Función |
|---------|---------|
| `billing.py` | Stripe Checkout suscripción, emails, etiquetas de estado de pago |
| `backup_manager.py` | Crear, listar y restaurar backups; cifrado auxiliar |
| `backup_worker.py` | Ejecución periódica de backups |
| `billing_worker.py` | Suspensión de usuarios con periodo vencido |
| `docker-compose.yml` | Orquestación de servicios |
| `templates/admin/backups.html` | UI de backups |
| `templates/admin/payments.html` | UI Stripe |
| `templates/admin/plans.html` | UI planes |
| `templates/admin/subscriptions.html` | UI control de pagos |

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
├── app.py                 # Rutas y lógica principal
├── models.py              # Modelos SQLAlchemy
├── config.py              # Configuración desde entorno
├── billing.py             # Pagos, emails de registro
├── backup_manager.py      # Backups y restauración
├── backup_worker.py       # Worker de backups
├── billing_worker.py      # Worker de morosos
├── gunicorn.conf.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── secrets/               # No subir a Git (ver .gitignore)
├── backups/               # Dumps locales (no subir a Git)
├── templates/             # Vistas HTML
├── static/                # PWA, favicon, Tailwind
├── MANUAL_DESPLIEGUE.md   # Guía detallada de despliegue
└── README.md              # Este archivo
```

---

## Requisitos

- Docker y Docker Compose (recomendado), **o**
- Python 3.12+, PostgreSQL y dependencias de `requirements.txt`
- Cuenta Stripe (si usas pagos en registro)
- Servidor SMTP (si usas emails automáticos)

---

## Instalación rápida con Docker

```bash
git clone https://github.com/Obenus/academia_online.git
cd academia_online

cp .env.example .env
# Edita .env (ACADEMY_NAME, DATABASE_URL, MAIL_*, etc.)

mkdir -p secrets
printf '%s' 'TU_SECRET_KEY_LARGA' > secrets/secret_key
printf '%s' 'tu_email_smtp' > secrets/mail_username
printf '%s' 'tu_password_smtp' > secrets/mail_password
printf '%s' '' > secrets/stripe_secret_key
chmod 600 secrets/*

docker compose up -d --build
```

Abre: **http://localhost:8080**

### Puertos por defecto

| Servicio | Puerto host |
|----------|-------------|
| App web | 8080 |
| PostgreSQL | 5433 |

---

## Configuración

### Variables en `.env` (no sensibles)

| Variable | Descripción |
|----------|-------------|
| `ACADEMY_NAME` | Nombre mostrado en la plataforma |
| `DATABASE_URL` | En Docker: `postgresql://postgres:postgres@db:5432/miacademia` |
| `MAIL_SERVER` | Servidor SMTP |
| `MAIL_PORT` | Puerto SMTP (587 o 465) |
| `MAIL_USE_TLS` | `true` / `false` |
| `MAIL_USE_SSL` | `true` para puerto 465 |
| `STRIPE_PUBLIC_KEY` | Clave pública Stripe (opcional si se configura en admin) |

### Archivos en `secrets/` (sensibles)

| Archivo | Contenido |
|---------|-----------|
| `secret_key` | `SECRET_KEY` de Flask |
| `mail_username` | Usuario SMTP |
| `mail_password` | Contraseña SMTP |
| `stripe_secret_key` | Clave secreta Stripe (opcional) |

Stripe y S3 también pueden guardarse cifrados desde el panel admin (Pagos / Backups).

### Primer acceso

Si la base de datos está vacía, se crea un admin inicial (solo la primera vez):

- Email: `samuelgavilant@gmail.com`
- Contraseña: `Admin1234!`

**Cámbialos inmediatamente** tras el primer login.

---

## Panel de administración

Ruta base: `/admin` (requiere usuario con rol `admin`).

| Sección | Ruta | Descripción |
|---------|------|-------------|
| Dashboard | `/admin` | Estadísticas y accesos rápidos |
| Ajustes comunidad | `/admin/ajustes` | Marca, banner, plantillas de email |
| Backups | `/admin/backups` | Configuración, ejecutar y restaurar copias |
| Pagos Stripe | `/admin/pagos` | Claves, webhook, activación automática |
| Planes | `/admin/planes` | CRUD de planes y precios mensuales |
| Suscripciones | `/admin/suscripciones` | Estado de pago de todos los alumnos |
| Usuarios | `/admin/usuarios` | Aprobar, planes, pago, roles, cuentas gratis |
| Cursos | `/admin/cursos` | Gestión completa de formación |
| Clases | `/admin/clases` | Calendario de directos |
| Email masivo | `/admin/email` | Envío manual a alumnos |

### Webhook Stripe

Configura en el dashboard de Stripe la URL:

```
https://TU_DOMINIO/webhooks/stripe
```

Eventos recomendados: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`.

---

## Servicios en segundo plano

| Contenedor | Función |
|------------|---------|
| `miacademia-backup` | Revisa cada minuto si toca ejecutar backup según configuración |
| `miacademia-billing` | Cada hora revisa suscripciones vencidas y suspende cuentas |

Logs:

```bash
docker compose logs -f backup
docker compose logs -f billing
docker compose logs -f app
```

---

## Despliegue alternativo (Railway)

El proyecto incluye `Procfile` y `gunicorn.conf.py` para desplegar en [Railway](https://railway.app) con PostgreSQL añadido como plugin.

Consulta **MANUAL_DESPLIEGUE.md** para instrucciones paso a paso (fork, variables, dominio, etc.).

---

## Documentación adicional

- [MANUAL_DESPLIEGUE.md](MANUAL_DESPLIEGUE.md) — Guía completa de instalación, Railway, Stripe, email y solución de problemas.

---

## Licencia y notas

- Revisa y rota credenciales antes de usar en producción.
- No subas `.env`, `secrets/` ni dumps de `backups/` al repositorio.
- Los pagos de **cursos individuales** (checkout one-shot) siguen disponibles además de las **suscripciones de plataforma** en el registro.

Desarrollado a partir de la base **Academia Online / Marca Atractora**, ampliada con Docker, suscripciones Stripe, backups, emails automáticos y panel de administración extendido.
