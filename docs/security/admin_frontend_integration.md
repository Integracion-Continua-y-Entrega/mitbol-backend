# Integración administrativa React → FastAPI

6 de noviembre de 2026. Código en `feature/admin-frontend-integration`, desde
`origin/develop`. No se cambiaron Neon, Render, permisos SQL ni cuentas reales.
Cuenta ficticia 1 desactivada por el usuario; no reutilizarla ni crear otra.

## Comenzar ahora: laboratorio local

Con Python/dependencias del backend y binarios PostgreSQL 18 ya instalados:

```powershell
Set-Location 'C:\Proyectos\mitbol\mitbol-backend'
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --serve-admin
```

El ejecutor existente crea su propio clúster PG18 en TEMP, valida directorio,
usuario, versión y TCP loopback, aplica **sólo allí** las migraciones existentes
y prepara lector/escritor con los scripts ya probados. Añade un catálogo y dos
cuentas sintéticas: `admin@mitbol.example.test`, `staff@mitbol.example.test`.
Contraseña pública **sólo de este laboratorio**: `Solo laboratorio MITBOL 2026!`.
No introducir credenciales reales, no usar esta contraseña en otro entorno.

API: **http://localhost:8000**, OpenAPI: `/docs`. React debe abrirse en
**http://localhost:5173**, usando `http://localhost:8000` como base administrativa.
Usar localhost en ambos: mezclar localhost con 127.0.0.1 cambia el sitio de cookies.
Uvicorn sólo escucha en 127.0.0.1, no confía en proxy y admite sólo origen local.
No usar este laboratorio con el frontend desplegado en Render ni con túneles.
Ctrl+C detiene servidor y PostgreSQL. Cada inicio crea datos nuevos; imágenes
y archivos del clúster quedan en TEMP para diagnóstico. No toca .env/mitbol_dev.

Comprobación automática, sin dejar servidor permanente:

```powershell
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --serve-admin --check
```

Esperado: `ADMIN_DEMO_OK`. Comprueba login, permisos Admin/Staff, cambios de
disponibilidad/descripción, auditoría y logout con el escritor limitado.

## Consumir desde React

Reutilizar [admin_api_client.ts](../examples/admin_api_client.ts). Es un ejemplo
en este repositorio, no se modificó frontend. La cookie queda HttpOnly; sólo el
CSRF se conserva en memoria. Nada de session token en localStorage/Authorization.

```typescript
const api = adminApi("http://localhost:8000");
const user = await api.login(email, password);
await api.availability(productId, false);          // Admin o Staff
await api.description(productId, "Sin picante");  // Admin
await api.createTag("vegano");                    // Admin
await api.logout();
```

`login` hace POST y verifica `/me`, detectando cookie rechazada sin reintentar
en bucle. Tras recargar usar `api.restore()` (`/me` + `/csrf`). UI usa `roles` y
`permissions` para controles; FastAPI siempre decide. Cada fetch administrativo
incluye `credentials:"include"`; el navegador añade Origin. Login exige JSON y
`X-CSRF-Protection:1`. Toda escritura posterior exige `X-CSRF-Token` de esa sesión,
también logout y cambio de contraseña. JSON vacío `{}` para logout/logout-all.
El menú público sigue usando `credentials:"omit"` y su CORS independiente.

## Contratos disponibles

Todos los IDs son enteros positivos internos, no IDs Loyverse. Los cuerpos y
respuestas completos están en `/docs` del laboratorio. A = Admin, S = Staff.

| Método / ruta | Cuerpo | Permiso / resultado |
| --- | --- | --- |
| POST `/api/auth/login` | `{email,password}` | Sin sesión, Origin + protección de login; 200 usuario, plazos y csrf_token; Set-Cookie |
| GET `/api/auth/me` | — | A/S, sesión; usuario/permisos y plazos |
| GET `/api/auth/csrf` | — | A/S, sesión; csrf_token |
| POST `/api/auth/logout` | `{}` | A/S + CSRF; 204, revoca sesión y borra cookie |
| POST `/api/auth/logout-all` | `{}` | A/S + CSRF; 204, revoca todas las sesiones |
| POST `/api/auth/change-password` | `{current_password,new_password}` | A/S + CSRF; 204, Argon2id y revocación total |
| GET `/api/admin/products/{id}/availability` | — | A/S: catalog:read |
| PATCH `/api/admin/products/{id}/availability` | `{is_available:boolean}` | A/S: availability:write; 200 estado |
| GET `/api/admin/variants/{id}/availability` | — | A/S |
| PATCH `/api/admin/variants/{id}/availability` | `{is_available:boolean}` | A/S; 200 estado |
| GET `/api/admin/products/{id}/description` | — | A/S |
| PATCH `/api/admin/products/{id}/description` | `{description:string\|null}` | A: description:write, máximo 2000 caracteres; 200 |
| GET `/api/admin/tags` | — | A: tags:write |
| POST `/api/admin/tags` | `{name:string}` | A: tags:write, normalización Unicode, 1–150 caracteres; 201 |
| GET `/api/admin/variants/{id}/tags` | — | A: tags:write |
| PUT `/api/admin/variants/{id}/tags/{tag_id}` | — | A + CSRF; asociación idempotente, 204 |
| DELETE `/api/admin/variants/{id}/tags/{tag_id}` | — | A + CSRF; 204 |
| GET `/api/admin/variants/{id}/images` | — | A/S, referencias existentes |
| POST `/api/admin/variants/{id}/images` | FormData `file`, `display_order` opcional | A: images:write; sólo laboratorio, 201 |
| PATCH `/api/admin/variants/{id}/images/{image_id}` | `{display_order:integer}` | A; sólo laboratorio |
| DELETE `/api/admin/variants/{id}/images/{image_id}` | — | A; sólo laboratorio, 204 |

Todas las escrituras administrativas exigen sesión/Origin/CSRF. Imágenes:
JPEG/PNG/WebP estático, 5 MB y 20 MP, sin fijar Content-Type manual en FormData.
Cloud mantiene las tres escrituras de imágenes cerradas por falta de
almacenamiento duradero. No hay altas de usuarios/roles, escritura de precios,
IDs Loyverse ni sincronización accesibles por estos contratos.

Errores: 401 credencial/sesión inválida o expirada; 403 permiso/CSRF/Host/Origin;
404 recurso/ruta cerrada; 409 duplicado/conflicto; 413 tamaño; 415 formato;
422 esquema/ID; 429 límite (respetar Retry-After); 503 DB/TLS/perfil/limitador
no disponibles. No reintentar automáticamente escrituras tras errores inciertos.
500 es error interno saneado. `/` o un menú 200 no certifican autenticación.

## Estrategia para los dominios actuales

Frontend y API son sitios distintos. SameSite=Lax no sirve para ese fetch.
Se selecciona **CHIPS**, cookie `__Host-mitbol_session` con HttpOnly, Secure,
SameSite=None, Path=/, **Partitioned**, sin Domain. El navegador la particiona
según el sitio superior; no requiere compartir onrender.com ni comprar dominio.
Soporte depende del navegador: véanse [MDN CHIPS](https://developer.mozilla.org/en-US/docs/Web/Privacy/Guides/Third-party_cookies/Partitioned_cookies)
y [WebKit Safari 26.2](https://webkit.org/blog/17640/webkit-features-for-safari-26-2/).
No se usa SameSite=None sin partición como solución automática. Si el navegador
rechaza la cookie, `/me` falla y se informa al usuario. No se sustituye por token
persistente en JavaScript. Un futuro mismo origen/dominio propio evita esta
dependencia de compatibilidad, pero no es necesario para desarrollar localmente.

Python 3.13 no ofrece el atributo Partitioned en Morsel; se añade el atributo
estándar a la cookie recién emitida por Starlette, también al borrarla. No se
modifica Morsel global ni se interpola texto de la petición en ese encabezado.

El perfil HTTP cloud exige Host `mitbol-backend.onrender.com` y Origin exactamente
`https://mitbol-frontend.onrender.com`, incluso en GET privados; rechaza valores
ausentes/duplicados. CORS es exacto con credenciales; CSRF sigue obligatorio.

[Render termina TLS y fuerza HTTPS público](https://render.com/docs/web-services),
aunque el salto interno llegue por HTTP. El perfil opt-in requiere RENDER=true
y `AUTH_PROXY_MODE=render`; no aplica esa confianza a otros entornos. No deriva
Host, esquema ni identidad del usuario de cabeceras Forwarded. **No confía en
X-Forwarded-For**: usa una cuota de red compartida en PostgreSQL, además de la
cuota por cuenta. `AUTH_IP_ATTEMPTS=30` significa aquí **30 admisiones totales
por ventana y tipo de operación**, no 30 por cliente. Es deliberadamente más
restrictivo y puede bloquear temporalmente a todos; se conserva hasta disponer
de una cadena de proxies demostrable. No hay confianza universal en proxies.

## Qué falta para abrir cloud (sin ejecutar aquí)

El código ya permite el perfil seguro explícito; cambiar sólo AUTH_ENABLED no
basta. Mantener las variables actuales apagadas mientras se revisa/integra código.
Configuración futura, **sólo después de autorización**, sin repetir migraciones
ni verificadores manuales de permisos:

| Variable | Perfil previsto |
| --- | --- |
| APP_ENV / RENDER | cloud / true (Render aporta RENDER) |
| DATABASE_URL | Lector actual, sin sustituirlo |
| EXPECTED_DATABASE / EXPECTED_DATABASE_HOST | mitbol_cloud_dev / host pooled aprobado actual |
| AUTH_DATABASE_URL | Secreto writer, mismo host/base autorizados, verify-full y channel_binding=require |
| AUTH_PROXY_MODE | render |
| AUTH_PUBLIC_ORIGIN | https://mitbol-backend.onrender.com |
| AUTH_ORIGINS | `["https://mitbol-frontend.onrender.com"]` |
| AUTH_COOKIE_SECURE / AUTH_COOKIE_PARTITIONED | true / true |
| AUTH_LIMIT_BACKEND | postgresql |
| AUTH_LIMIT_SECRET | Clave HMAC canónica estable de 32 bytes, igual en todas las instancias |
| AUTH_ENABLED | false durante preparación; true sólo al autorizar apertura |
| AUTH_CATALOG_ENABLED | false al probar login; true después, previa autorización |
| AUTH_IMAGES_ENABLED / ADMIN_ENABLED | false / false |

El pool del escritor reutiliza el bloque de verificación SQL aprobado en
`docs/database/verify_admin_writer_neon.sql`, sólo lectura, al abrir conexiones
físicas y **después** de verificar TLS/destino. El despliegue debe conservar ese
archivo del repositorio. No crea roles, modifica grants ni lee alembic_version.
Falta crear las cuentas **definitivas** por el bootstrap ya existente, con
autorización separada y datos legítimos; no reactivar cuenta 1 ni crear ficticias.

Falta integrar este código, desplegarlo con flags false y autorizar/configurar
los secretos anteriores. Después, autorizar login con catálogo cerrado y probar
desde el frontend real: cookie particionada, `/me`, CSRF, expiración y logout en
los navegadores soportados del equipo. Sólo entonces abrir catálogo y verificar
Admin/Staff. Las pruebas locales no acreditan transporte/proxy ni cookies reales
de Render. **NO-GO para activar hoy sin esas acciones autorizadas**; no hace
falta comprar dominio ni cambiar PostgreSQL para comenzar con el laboratorio.

## Verificación y revisión

```powershell
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q --tb=short
git diff --check
git diff --stat
git diff -- .github/workflows/backend-ci.yml
```

CI conserva exclusivamente push develop y PR hacia develop/main; elimina push
feature/**. Jobs, PG18, aislamiento, permisos del token y artefactos no cambian.
No se hicieron commits, push ni cambios en servicios externos.

Después de revisar el diff, comandos **manuales**, no ejecutados aquí:

```powershell
git status --short
git diff --check
git add -- .github/workflows/backend-ci.yml .env.example README.MD app scripts/test_auth_phase1.py tests/test_auth_cloud.py tests/test_dev_admin.py docs/examples/admin_api_client.ts docs/security/admin_frontend_integration.md docs/security/admin_auth_phase5.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat: prepare secure admin frontend integration"
git push -u origin feature/admin-frontend-integration
```

Abrir el PR con base develop y comparación feature/admin-frontend-integration.
Si se usa GitHub CLI, preparar el cuerpo con saltos reales, sin secretos:

```powershell
$prBodyPath = Join-Path $env:TEMP 'mitbol-admin-integration-pr.md'
@'
Prepara la integración React con sesiones individuales y permisos Admin/Staff.
Añade perfil Render opt-in con cookies CHIPS, origen/Host exactos, CSRF,
TLS y perfil SQL aprobado del escritor; mantiene lector y administración legada separados.
Usa cuota global conservadora además de la cuota por cuenta en cloud.
Incluye laboratorio efímero reproducible y cliente TypeScript; no activa servicios reales.
CI elimina únicamente push feature/**, conservando push develop y PR develop/main.

Validación: suite completa PostgreSQL 18, laboratorio Admin/Staff por HTTP local,
parser YAML y sintaxis TypeScript. No se probaron cookies reales en Render;
activación requiere cuentas definitivas, secretos y prueba autorizada en navegador.
'@ | Set-Content -LiteralPath $prBodyPath -Encoding UTF8
gh pr create --base develop --head feature/admin-frontend-integration --title "Prepare secure Admin/Staff integration with React" --body-file $prBodyPath
```

El PR dispara CI; push a esta feature ya no lo hace. No fusionar hasta revisar
el check completo. Render continúa con flags false durante la integración.

## Cambios y evidencia local

- CI: `.github/workflows/backend-ci.yml`, únicamente elimina push feature/**.
- Perfil y cookies: `app/core/config.py`, `app/core/auth_http.py`,
  `app/api/auth.py`, `app/api/admin_catalog.py`, `app/main.py`.
- Conexiones: `app/db/auth_database.py`, `app/db/connection.py`,
  `app/db/writer_policy.py`, `app/services/auth_service.py`.
- Laboratorio: `app/cli/dev_admin.py`, `scripts/test_auth_phase1.py`.
- Pruebas: `tests/test_auth_cloud.py`, `tests/test_dev_admin.py`.
- Integración: `docs/examples/admin_api_client.ts`, esta guía, nota de estado
  en `docs/security/admin_auth_phase5.md`, `README.MD`, `.env.example`.

Suite completa: **1362 aprobadas**, cero fallos/errores/omisiones, 417.52 s;
dos deprecaciones preexistentes Starlette/AnyIO. Ambos PostgreSQL temporales
detenidos. Log:
`TEMP/mitbol-sync-preflight-76312d413c92433e9b4c90f167f395c4/test-run.log`.
Antes, 219 pruebas focalizadas aprobadas. Además, flujo completo Admin/Staff
probado por HTTP real en localhost: login, sesión, disponibilidad, descripción
permitida/denegada, logout y replay denegado. El servidor del laboratorio y
su PostgreSQL se cerraron con Ctrl+C tras corregir el lanzador Windows.

YAML parseado y comparado contra HEAD: jobs y demás claves idénticos. Ejemplo
TypeScript validado sintácticamente con Node 22.12, sin instalar dependencias
frontend. Comandos PowerShell analizados por el parser y git diff --check limpio.
La simulación cloud usa PostgreSQL local y transporte simulado; **no** es prueba
de cookies particionadas en navegador ni de conectividad/proxy real de Render.
