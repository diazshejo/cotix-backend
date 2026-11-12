# Cotix backend

API Django + Django REST Framework con PostgreSQL. El frontend React + Vite está en el repositorio hermano `cotix-frontend`.

## Arranque local reproducible

Clona ambos repositorios como carpetas hermanas, `cotix-backend` y `cotix-frontend`. Necesitas Docker y Docker Compose.

```bash
cd cotix-backend
cp .env.example .env
cd ../cotix-frontend
cp .env.example .env
cd ../cotix-backend
docker compose -f compose.local.yaml up --build -d
```

Antes de arrancar, cambia `DJANGO_SECRET_KEY` y `DB_PASSWORD` en `cotix-backend/.env` por valores locales propios. Deja `DB_HOST=localhost` en ese archivo: Compose lo sustituye por `db` dentro del contenedor. La app queda en `http://localhost:5173`; la API, en `http://localhost:8000/api/`. Usa el mismo nombre de host en ambos servicios para que las cookies funcionen en desarrollo.

El servicio `scheduler` ejecuta `run_catalog_integration_syncs` cada minuto. En producción, configura un único proceso programador o cron con ese comando; no levantes varias copias simultáneas. El servicio `db` guarda datos en el volumen nombrado `postgres_data`. `docker compose -f compose.local.yaml down` conserva ese volumen; `down -v` lo elimina.

La sincronización REST sigue enlaces `next` del mismo origen hasta terminar y limita páginas, filas y tamaño por respuesta. Solo desactiva productos ausentes cuando el origen acredita un catálogo completo mediante `count` coincidente o una secuencia paginada; una respuesta aislada sin esas señales actualiza productos, pero no elimina ausentes.

Para consultar logs: `docker compose -f compose.local.yaml logs -f backend frontend scheduler`. Para detener: `docker compose -f compose.local.yaml down`. No comitas `.env` ni secretos.

Antes de publicar la página comercial faltan textos legales aprobados y canales reales de soporte; los enlaces y testimonios de ejemplo se quitaron de la interfaz.

En producción, la API exige una clave secreta única de al menos 50 caracteres, HTTPS, cookies de sesión `Secure` y `HttpOnly`, y orígenes explícitos para CORS y CSRF. Usa `.env.staging.example` como guía para `ACCESS_TOKEN_COOKIE_SECURE`, `REFRESH_TOKEN_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_SECURE`, `SECURE_SSL_REDIRECT` y `FRONTEND_URL`. Publica frontend y API bajo el mismo sitio para que `SameSite=Lax` permita la sesión. Configura `TRUST_PROXY_SSL_HEADER=True` solo si el backend recibe tráfico exclusivamente de un proxy confiable que establece `X-Forwarded-Proto`.
