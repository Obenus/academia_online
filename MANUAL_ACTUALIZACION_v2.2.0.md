# Manual de actualización v2.2.0

Guía de las novedades de la versión **2.2.0** y cómo usarlas desde el panel de administración.

**Importante:** esta actualización **no borra** cursos, biblioteca, usuarios, posts, planes ni textos ya guardados. Solo añade columnas, tablas y pantallas nuevas. Los campos nuevos empiezan vacíos o desactivados.

---

## Índice

1. [Qué incluye esta versión](#1-qué-incluye-esta-versión)
2. [Vídeo en la landing principal](#2-vídeo-en-la-landing-principal)
3. [Textos legales](#3-textos-legales)
4. [Landing comercial](#4-landing-comercial)
5. [Recuperación de contraseña](#5-recuperación-de-contraseña)
6. [Checklist de puesta en marcha](#6-checklist-de-puesta-en-marcha)
7. [Actualizar un servidor en producción](#7-actualizar-un-servidor-en-producción)
8. [Solución de problemas](#8-solución-de-problemas)

---

## 1. Qué incluye esta versión

| Novedad | Dónde se configura | URL pública |
|---------|--------------------|-------------|
| Vídeo YouTube/Vimeo en `/login` | Admin → Landing principal | `/login` |
| Política de la Comunidad, Privacidad, Cookies, Aviso Legal | Admin → Textos legales | `/legal/…` |
| Footer legal en todas las páginas | Automático | Enlace en pestaña nueva |
| Landing comercial (captación de leads) | Admin → Landing comercial | `/oferta` (o slug editable) |
| Listado y export CSV de leads | Admin → Landing comercial → Leads | — |
| Olvidé mi contraseña | Automático en login | `/recuperar-password` |

Documentación general del panel: [MANUAL_ADMINISTRADOR.md](MANUAL_ADMINISTRADOR.md).

---

## 2. Vídeo en la landing principal

### Para qué sirve

Coloca un vídeo de **YouTube o Vimeo** en la página de acceso (`/login`), **después** del bloque «Por qué nació (mejores preguntas)» y **antes** de «¿Qué es?».

### Cómo configurarlo

1. Entra en **Admin → Landing principal**.
2. Busca el campo **Vídeo (YouTube o Vimeo)**.
3. Pega la URL completa, por ejemplo:
   - `https://www.youtube.com/watch?v=XXXXXXXXXXX`
   - `https://youtu.be/XXXXXXXXXXX`
   - `https://vimeo.com/123456789`
4. Guarda.

### Comportamiento

- Solo se muestra el **reproductor** (sin título extra).
- Si el campo está **vacío**, la sección **no aparece** (no rompe landings ya publicadas).
- No se suben vídeos al servidor: solo el enlace.

### Comprobar

Abre `/login` (o «Ver landing →») y verifica que el vídeo se ve entre esas dos secciones.

---

## 3. Textos legales

### Para qué sirve

Permite redactar y publicar:

- Política de la Comunidad  
- Política de Privacidad  
- Política de Cookies  
- Aviso Legal  

### Cómo configurarlo

1. Entra en **Admin → Textos legales**.
2. Escribe cada documento en **Markdown** (títulos con `#`, listas con `-`, negrita con `**texto**`, etc.).
3. Guarda.
4. Usa **Ver →** junto a cada bloque para previsualizar.

### URLs públicas

| Documento | URL |
|-----------|-----|
| Comunidad | `/legal/comunidad` |
| Privacidad | `/legal/privacidad` |
| Cookies | `/legal/cookies` |
| Aviso legal | `/legal/aviso-legal` |

### Footer

En **todas** las páginas (login, zona privada, landings) aparece un footer con estos 4 enlaces. Se abren en **pestaña nueva**.

Si un texto está vacío, la página se muestra igual con un aviso de que aún no hay contenido; el footer sigue visible.

---

## 4. Landing comercial

### Para qué sirve

Página de captación independiente de la landing de suscripción Stripe. Sirve para:

- Mostrar imagen + texto + botón de WhatsApp  
- Recoger **nombre** y **email**  
- Guardar el lead en la base de datos  
- Avisar a un email configurable  
- Enviar al visitante un email automático con un enlace de WhatsApp (distinto del botón de la página)  

### Cómo configurarlo

1. Entra en **Admin → Landing comercial**.
2. Marca **Landing activa**.
3. Define el **slug** (por defecto `oferta` → URL `/oferta`). Solo letras minúsculas, números y guiones.
4. Completa:
   - Título  
   - Texto (Markdown)  
   - Enlace WhatsApp **de la página** (botón visible)  
   - Imagen de cabecera (subida al servidor)  
   - Email donde recibir cada registro  
   - Asunto y cuerpo de la **autorespuesta**  
   - Enlace WhatsApp **del email** (independiente del de la página)  
5. Guarda.

### Variables del email de respuesta

Puedes usar en asunto y cuerpo:

- `{{nombre}}`  
- `{{email}}`  
- `{{whatsapp_url}}` — el del email de respuesta  
- `{{academy_name}}`  

### Formulario público

Campos: nombre, email, checkbox obligatorio de Política de Privacidad (enlace a `/legal/privacidad` en pestaña nueva).

Anti-spam: honeypot oculto + límite de peticiones.

### Leads

**Admin → Landing comercial → Ver leads** (o `/admin/landing-comercial/leads`):

- Fecha, nombre, email  
- Botón **Exportar CSV**  

### Si está desactivada

La URL responde **404**. Así no se publica por error tras actualizar.

---

## 5. Recuperación de contraseña

### Cómo lo usa la alumna

1. En `/login`, si falla el acceso, el mensaje recuerda la opción de recuperación.
2. Enlace **¿Has olvidado tu contraseña?** bajo el formulario.
3. Introduce su email en `/recuperar-password`.
4. Recibe un email con un **enlace único válido 1 hora**.
5. Elige nueva contraseña (mínimo 8 caracteres) y vuelve a entrar.

### Notas de seguridad

- El mensaje en pantalla es genérico («Si el email está registrado…») para no revelar si existe la cuenta.
- El token es de un solo uso y caduca a la hora.
- Requiere SMTP configurado (igual que el resto de emails).

---

## 6. Checklist de puesta en marcha

Tras actualizar a v2.2.0:

- [ ] Rellenar los 4 textos legales  
- [ ] Comprobar el footer en login y en la zona privada  
- [ ] (Opcional) Añadir URL de vídeo en Landing principal  
- [ ] Activar y completar Landing comercial (slug, emails, WhatsApp, imagen)  
- [ ] Enviar un lead de prueba y revisar: email al admin, autorespuesta y listado de leads  
- [ ] Probar «olvidé mi contraseña» con una cuenta de prueba  
- [ ] Confirmar que cursos, biblioteca, usuarias y planes siguen intactos  

---

## 7. Actualizar un servidor en producción

### Requisitos

- Acceso SSH al servidor  
- El proyecto desplegado con **Docker Compose** (como en este repositorio)  
- Backup reciente recomendado (Admin → Backups o `pg_dump`)  

### Pasos recomendados

```bash
# 1) Ir al directorio de la aplicación
cd /ruta/a/academia_online   # o miacademia-main

# 2) (Recomendado) Copia de seguridad de la base de datos
docker compose exec -T db pg_dump -U postgres miacademia > backup_antes_v2.2.0.sql

# 3) Obtener el código nuevo
git fetch origin
git checkout main
git pull origin main
# Opcional: anclar a la versión etiquetada
# git checkout v2.2.0

# 4) Reconstruir sin borrar el volumen de PostgreSQL
#    (NO uses docker compose down -v)
docker compose stop billing reminder backup
docker compose up -d --build app

# 5) Esperar a que /login responda 200, luego levantar workers
docker compose up -d --build billing reminder backup

# 6) Comprobar
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/login
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/legal/privacidad
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/recuperar-password
docker compose logs --tail=50 app
```

### Qué NO hacer

- No ejecutar `docker compose down -v` (borra el volumen de la base de datos).  
- No borrar la carpeta `backups/` ni `secrets/`.  
- No restaurar un dump antiguo encima justo después de migrar, salvo recuperación de emergencia.

### Qué hace la migración sola

Al arrancar `app`, se crean (si no existen) columnas y tablas nuevas:

- `landing_video_url`, campos legales, campos de landing comercial  
- Tablas `commercial_lead` y `password_reset_token`  

Los datos previos permanecen.

---

## 8. Solución de problemas

### Tras el rebuild, `/login` no responde

Varios contenedores migran a la vez y pueden bloquearse en PostgreSQL. Arranca primero solo `app` y luego el resto (pasos 4–5 de arriba).

### No llegan emails (leads o recuperación de contraseña)

1. SMTP en `.env` y archivos en `secrets/`.  
2. `docker compose logs -f app`.  
3. En landing comercial, revisa el email de notificación y la plantilla de autorespuesta.

### `/oferta` da 404

La landing comercial está **desactivada** por defecto. Actívala en Admin → Landing comercial.

### El vídeo no se ve

URL válida de YouTube/Vimeo y campo guardado. Si la URL está vacía, la sección se oculta a propósito.

### Textos legales en blanco

Normal tras la actualización: hay que pegar el Markdown en Admin → Textos legales.

---

*Versión documentada: 2.2.0*  
*Relacionado: [README.md](README.md) · [MANUAL_ADMINISTRADOR.md](MANUAL_ADMINISTRADOR.md)*
