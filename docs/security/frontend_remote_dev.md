# React local y publicado → Render académico actual

**API: https://mitbol-backend.onrender.com. React: http://localhost:5173 y
https://mitbol-frontend.onrender.com.**
Neon `production / mitbol_cloud_dev` es la base académica de pruebas, no la
producción definitiva del restaurante. Se reutilizan servicio, base y roles actuales;
no crear ramas/servicios, ejecutar migraciones ni cambiar permisos. Los compañeros
sólo necesitan React y credenciales de aplicación. El perfil `frontend_dev`
admite el origen local original o ambos orígenes simultáneamente, en cualquier
orden y sin duplicados. El frontend publicado solo no es una configuración válida
de este perfil; `restaurant` conserva sus restricciones actuales.

## 1. Integrar y configurar Render (operador, después de autorización)

Integrar el código en develop mediante PR y CI, después de autorizar la integración.
Si el servicio tiene Auto Deploy, el merge puede desplegarlo. Para esta actualización
se conservan los flags y secretos existentes; no regenerar `AUTH_LIMIT_SECRET`.
El código público sigue arrancando con los flags apagados en una instalación nueva.

Variables propuestas **después de integrar y desplegar el cambio**, sin `/` final
en los orígenes. Esta intervención no modifica las variables reales de Render:

| Variable | Valor |
| --- | --- |
| APP_ENV | `cloud` (conservar) |
| AUTH_PROFILE | `frontend_dev` |
| AUTH_PUBLIC_ORIGIN | `https://mitbol-backend.onrender.com` |
| AUTH_ORIGINS | `["http://localhost:5173","https://mitbol-frontend.onrender.com"]` |
| CORS_ORIGINS | `["http://localhost:5173","https://mitbol-frontend.onrender.com"]` (público, sin credenciales) |
| EXPECTED_DATABASE | `mitbol_cloud_dev` (conservar) |
| EXPECTED_DATABASE_HOST | `ep-green-hill-bddfbmgh-pooler.c-8.us-east-2.aws.neon.tech` (host aprobado actual) |
| DATABASE_URL | Conservar el secreto actual de `mitbol_render_reader` |
| AUTH_DATABASE_URL | Secreto de `mitbol_admin_writer`, mismo host/base, `sslmode=verify-full&channel_binding=require` |
| AUTH_PROXY_MODE | `render` |
| AUTH_COOKIE_SECURE / AUTH_COOKIE_PARTITIONED | `true` / `true` |
| AUTH_LIMIT_BACKEND | `postgresql` |
| AUTH_LIMIT_SECRET | Conservar el secreto estable existente; 32 bytes aleatorios, Base64 URL-safe sin padding (43 caracteres canónicos) |
| AUTH_LOGIN_ATTEMPTS / AUTH_IP_ATTEMPTS | `5` / `30` |
| AUTH_LIMIT_WINDOW_SECONDS | `900` |
| ADMIN_ENABLED | `false`, siempre (clave legada local) |
| AUTH_ENABLED | Conservar la activación existente; el valor predeterminado sigue siendo `false` |
| AUTH_SYNC_ENABLED | `false`; panel HU-010, no las sincronizaciones programadas de GitHub Actions |
| AUTH_CATALOG_ENABLED | `true` para operaciones Admin/Staff |
| AUTH_IMAGES_ENABLED | `true` para CRUD de fotos de prueba, o `false` si no se necesitan |

La configuración original `AUTH_ORIGINS=["http://localhost:5173"]` sigue siendo
válida. Cada solicitud autenticada debe incluir exactamente un `Origin` autorizado
y un `Host` igual al backend aprobado. Se rechazan orígenes desconocidos, `null`,
ausentes, duplicados o combinados; no se utilizan cabeceras Forwarded para decidirlos.
`ScopedCORSMiddleware` conserva el CORS público y autenticado separados, con las
reglas específicas de pedidos, cancelación y seguimiento de invitados.

Conservar `AUTH_RECOVERY_URL` como único destino HTTPS configurado para recuperar
la contraseña, junto con el proveedor de correo existente. No se construye el
destino a partir de `Origin`, `Host`, parámetros ni cabeceras reenviadas.

Los DSN sólo van en secretos de Render. La contraseña SQL debe estar codificada
como componente URL; nunca imprimirla ni ponerla en Git o variables VITE_*.
Ambas conexiones se contrastan con el host/base explícitos y TLS; roles mínimos
existentes, sin owner en Render. Se conserva el verificador SQL del escritor por
conexión, en sólo lectura. Si el lector usa otro host aprobado, conservarlo y
usar exactamente ese mismo host para AUTH_DATABASE_URL y EXPECTED_DATABASE_HOST.

Render aporta RENDER=true y RENDER_EXTERNAL_URL: no sobrescribirlas. El perfil
exige que esa URL coincida con AUTH_PUBLIC_ORIGIN y con el backend académico.
No confía en cabeceras Forwarded para cambiar Host o eludir el limitador.

Sólo para una instalación nueva, para generar AUTH_LIMIT_SECRET sin mostrarlo,
desde PowerShell sin transcripción
ni grabación (no conecta ni escribe en Neon):

```powershell
$limitSecret = & .\.venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(32))'
Set-Clipboard -Value $limitSecret.Trim()
Remove-Variable limitSecret
# Pegar como secreto en Render/gestor, sin incluirlo en comandos. Después:
Set-Clipboard -Value ''
```

Usar portapapeles sin historial/sincronización para secretos. La clave existe
transitoriamente en memoria; limpiar referencias no garantiza borrarla de memoria.
No regenerarla en cada arranque. Mantener los demás parámetros de sesión existentes.

## 2. Cuentas ficticias (ESCRIBEN en Neon; ejecutar sólo tras autorización)

Reutilizar la función PowerShell de [6.1](admin_auth_phase5.md#61-primeras-cuentas-en-neon--preparación-sin-escrituras).
**Definirla no conecta ni escribe.** Usa el host directo aprobado del entorno actual.
Usa neondb_owner únicamente en el Windows del operador, con entrada SQL oculta;
ese secreto nunca se configura en FastAPI/Render ni se comparte con frontend.

Primer comando seguro, desde la raíz del backend; no conecta:

```powershell
& .\.venv\Scripts\python.exe -B -m app.cli.admin_accounts --help
```

**Detenerse antes de las invocaciones siguientes hasta autorizar las escrituras.**
Si se conoce la contraseña de aplicación de la cuenta 1 y es única/segura:

```powershell
Invoke-MitbolAccountOperation -Operation access -AccountId 1
```

Responder `admin`, activar cuenta `si` y perfil laboral `si`. No cambia contraseña:
conserva la existente, añade auditoría y revoca las sesiones previas. Si no se
conoce esa contraseña, dejar 1 desactivada y crear otro Admin con `create`.
Nunca asignar Staff a la cuenta 1 para sustituir la prueba de ambos roles.

Crear Staff (y Admin alternativo sólo si hace falta):

```powershell
Invoke-MitbolAccountOperation -Operation create
```

Responder `staff` (o `admin` para el alternativo); datos **sintéticos**, correo y
código únicos, por ejemplo `staff@mitbol.test` / `DEV-STAFF`; fecha AAAA-MM-DD;
activación `si`; contraseña propia de 15–1024 caracteres y confirmación, ocultas.
Aunque los prompts históricos digan «real», en esta base académica se introducen
datos ficticios. No usar contraseñas publicadas del laboratorio en esta API remota.

Esperado: `Operación confirmada. Cuenta <id>; referencia de auditoría <uuid>.`
El hash es Argon2id; no se muestra. UNIQUE evita email/código duplicados. Si no
hay confirmación del COMMIT, parar y consultar sólo esa cuenta antes de repetir.
No borrar cuentas/auditoría. Compartir sólo credenciales de aplicación por canal
seguro; ninguna credencial SQL. Los cambios de prueba se ven en el catálogo
académico compartido, incluido el menú público de demostración.

## 3. Prueba mínima desde React en el navegador

En ambos frontends configurar `VITE_API_URL=https://mitbol-backend.onrender.com`.
Para desarrollo local usar Vite con
host localhost, puerto 5173 y strictPort. No usar 127.0.0.1 ni otro puerto.
Adaptar [admin_api_client.ts](../examples/admin_api_client.ts); ya implementa
credentials:include, login JSON con X-CSRF-Protection:1, CSRF de sesión en memoria
y X-CSRF-Token en cada escritura (también logout). Origin lo añade el navegador;
no poner tokens/cookies/contraseñas en consola o localStorage.

```typescript
const api = adminApi(import.meta.env.VITE_API_URL);
const menu = await publicMenu(import.meta.env.VITE_API_URL); // credentials:omit
await api.login(emailDelFormulario, passwordDelFormulario); // login + /me 200
await api.request("/api/auth/me");                          // 200, usuario/roles
await api.availability(productId, false);                  // Admin/Staff: 200
await api.description(productId, "Prueba académica");      // Admin: 200; Staff: 403
const tag = await api.createTag(`prueba-${Date.now()}`);     // Admin: POST 201
await api.attachTag(variantId, tag.tag_id);                 // PUT 204
await api.request(`/api/admin/variants/${variantId}/tags`); // GET 200
await api.detachTag(variantId, tag.tag_id);                 // DELETE 204
await api.logout();                                       // POST {}, CSRF, 204
// api.request('/api/auth/me') debe dar 401 después del logout.
// Al recargar antes de logout: api.restore() recupera /me y /csrf.
```

Usar IDs del menú actual. No existe CRUD de productos, precios ni IDs Loyverse.
DELETE de etiquetas retira la asociación, no borra globalmente la etiqueta.
Fotos Admin: POST `/api/admin/variants/{id}/images` con FormData(file), GET de esa
ruta, PATCH `/{image_id}` con `{display_order:1}`, DELETE `/{image_id}`. No fijar
Content-Type manualmente al enviar FormData. Staff sólo puede consultar imágenes.
Contrato completo: [rutas y errores](admin_frontend_integration.md#contratos-disponibles).

Como Staff, disponibilidad debe dar 200 y descripción/POST tags/POST imágenes
**403 con CSRF válido**. Probar cada operación por separado: el primer 403 lanza
ApiError, por lo que no ejecutar las líneas Admin siguientes sin manejarlo.
401 vuelve al login; 429 respeta Retry-After; no reintentar escrituras/login en
bucle ante 429/503. La cuota cloud de 30 admisiones/900 s se comparte por equipo.

Validar en los navegadores/versiones usados: login 200 **y /me 200**, recargar y
restaurar sesión/CSRF, escritura autorizada, 403 Staff y logout seguido de /me 401.
En DevTools revisar sólo atributos: __Host-mitbol_session, HttpOnly, Secure,
SameSite=None, Partitioned, Path=/ y sin Domain. HTTP localhost solicita HTTPS
remoto: Secure corresponde a la API. No desactivar CSRF, origen ni TLS.
CHIPS [depende del navegador](https://developer.mozilla.org/en-US/docs/Web/Privacy/Guides/Third-party_cookies/Partitioned_cookies):
login 200 solo no acredita cookie persistente. Si /me da 401, comprobar bloqueo
de cookies y usar un navegador compatible sin desactivar protecciones. Las
pruebas Python no certifican este paso real; no se ejecutó contra Render.
Las cookies particionadas pueden requerir inicios de sesión independientes en
localhost y en el frontend publicado; no se promete una sesión compartida entre
ellos. `logout-all` y el restablecimiento de contraseña revocan las sesiones en
el backend independientemente de su contexto del navegador.

Usar solicitudes directas al backend HTTPS: el navegador añade `Origin` y no se
debe fabricarlo. Postman no comprueba las políticas CORS/CHIPS del navegador.
Un proxy de Vite puede cambiar Host, Origin y el contexto de las cookies, por lo
que no sustituye la validación directa desde los dos frontends.

Las fotos usan disco efímero: pueden perderse en reinicios, reposo o despliegues
de [Render Free](https://render.com/docs/free), aunque queden referencias DB.
Es válido para las pruebas académicas; almacenamiento durable queda para entrega.

## 4. Verificación focalizada de los dos orígenes

```powershell
$focusedTests = @(
    'tests/test_frontend_remote.py'
    'tests/test_auth_cloud.py'
    'tests/test_cloud_configuration.py'
    'tests/test_password_recovery_delivery.py'
    'tests/test_password_recovery.py::test_recovery_hashes_token_changes_password_and_revokes_every_session_without_changing_permissions'
    'tests/test_order_submission.py::test_orders_cors_allows_submission_headers_without_exposing_other_routes'
    'tests/test_order_tracking.py::test_tracking_cors_allows_only_get_with_private_header'
    'tests/test_order_cancellation.py::test_cancellation_cors_is_limited_to_its_route'
    'tests/test_cart.py::test_cart_json_is_available_to_react_without_broadening_other_cors_routes'
)
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py @focusedTests -q --tb=short
git diff --check
git diff --stat
```

El ejecutor utiliza exclusivamente PostgreSQL 18 desechable en TEMP; no lee `.env`
ni conecta con Neon. Los ensayos cubren la configuración original y dual, ambos
órdenes, encabezados inválidos, CSRF, permisos, cookies HTTP, renovación, expiración,
logout/revocación y recuperación con captura en memoria, sin correos reales.
Resultado de esta selección en `feature/auth-dual-origin`: **159 passed**, dos
advertencias de deprecación de dependencias, PostgreSQL 18 temporal detenido.
CI conserva sus comprobaciones habituales. La verificación en navegadores reales
queda pendiente para después del despliegue autorizado. No ejecutar merge, push,
despliegue ni cambios de variables como parte de esta intervención.
