# MITBOL: autenticación administrativa, fase 2

Documento histórico de cierre de fase 2. La integración posterior con las rutas reales y el estado vigente están en [fase 3](admin_auth_phase3.md); las referencias de este documento a HU-005/HU-007 cerradas describen el estado anterior a esa integración.

La [fase 4](admin_auth_phase4.md) revisa los permisos SQL y sustituye los locks de empleado/roles descritos aquí por lecturas bajo el lock de cuenta y el protocolo de invalidación existentes. Se conservan los contratos de autenticación y los modelos.

La [fase 5](admin_auth_phase5.md) añade el backend persistente de límites y gates de catálogo/imágenes apagados por defecto. La limitación en memoria descrita aquí sigue disponible sólo para desarrollo local; cloud permanece bloqueado con el dominio pendiente.

Fecha: 5 de noviembre de 2026, America/Mexico_City. Rama: `design/admin-auth-security`. **Implementación funcional exclusivamente local; sin despliegue ni apertura administrativa de HU-005/HU-007.**

Esta fase continúa la [persistencia y primitivas de fase 1](admin_auth_phase1.md) y la [arquitectura de identidad única](admin_auth_proposal.md). Conserva exactamente las siete tablas y la migración `20261105_0004`; no genera otra migración ni modifica esa revisión. No crea cuentas de cliente ni cuentas administrativas predeterminadas.

## Qué se implementó y dónde

| Archivo | Responsabilidad real |
| --- | --- |
| [core/config.py](../../app/core/config.py) | Activación opt-in, bloqueo cloud, base aislada explícita, orígenes loopback, plazos de sesión y límites configurables. |
| [main.py](../../app/main.py) | Registro condicional de las seis rutas, pool de autenticación, CORS local y errores sin campos secretos. |
| [api/auth.py](../../app/api/auth.py) | Login, logout, logout-all, me, csrf y cambio de contraseña. |
| [api/auth_dependencies.py](../../app/api/auth_dependencies.py) | `get_current_user` y `require_permission`, con transacciones de duración de función. |
| [core/auth_http.py](../../app/core/auth_http.py) | Rechazo de clientes remotos/proxies/orígenes no admitidos, límite de cuerpo previo al parser, cookie y errores públicos. |
| [core/auth_limiter.py](../../app/core/auth_limiter.py) | Limitador local atómico y acotado por cuenta/IP; semáforo de trabajo Argon2. |
| [core/auth_errors.py](../../app/core/auth_errors.py) | Errores controlados, sin DSN, SQL o entradas del usuario. |
| [db/auth_database.py](../../app/db/auth_database.py) | Pool local separado, con `AUTH_DATABASE_URL` explícita y sin fallback a la conexión pública. |
| [repositories/auth.py](../../app/repositories/auth.py) | Consultas, locks, revocación y helper de invalidación bajo lock de cuenta. |
| [services/auth_service.py](../../app/services/auth_service.py) | Transacciones, verificación Argon2id, sesión/actividad, contexto CSRF, invalidaciones y auditoría. |
| [schemas/auth.py](../../app/schemas/auth.py) | Entradas estrictas con SecretStr, salidas explícitas y validación de correo. |
| [test_auth_api.py](../../tests/test_auth_api.py) | Flujos HTTP, permisos en rutas ficticias, concurrencia, rollback y errores PostgreSQL temporales. |
| [test_auth_phase2_core.py](../../tests/test_auth_phase2_core.py) | Configuración cerrada, atomicidad/capacidad de límites y control de hashing. |
| [scripts/test_auth_phase1.py](../../scripts/test_auth_phase1.py) | Ejecutor existente ampliado para incluir la fase 2; conserva aislamiento y sanea variables AUTH del proceso de pruebas. |

No se modifica el router real de administración ni se implementan escrituras de descripciones, imágenes, etiquetas o disponibilidad mediante estas cuentas. Las rutas usadas para verificar dependencias sólo existen dentro de las fixtures de prueba.

## Activación local y separación de Render

`AUTH_ENABLED` vale false por defecto. En cloud, true causa rechazo explícito de configuración, incluso con cookie Secure y aunque se copie/modifique una instancia de Settings. El bloqueo señala que faltan limitación compartida y revisión de publicación. `AUTH_COOKIE_SECURE=false` también se rechaza en cloud. Render sigue sin registrar las rutas.

El nuevo modo exige `ADMIN_ENABLED=false`; es incompatible con la clave administrativa temporal. No convierte X-Admin-Key en una credencial de cuenta ni habilita el router HU-005/HU-007.

| Variable | Valor predeterminado / regla |
| --- | --- |
| `AUTH_ENABLED` | false; opt-in sólo local/test. |
| `AUTH_DATABASE_URL` | Obligatoria al activar. PostgreSQL loopback, nombre `mitbol_test_auth_<32 hex>` o `mitbol_auth_local`; rechaza mitbol_dev, bases remotas, neondb_owner, mitbol_render_reader y parámetros que alteren identidad/destino. |
| `AUTH_ORIGINS` | JSON explícito; por defecto `["http://localhost:5173"]`. Sólo hosts loopback en esta fase. |
| `AUTH_COOKIE_SECURE` | true por defecto. Elegir false explícitamente para HTTP local directo. |
| `AUTH_ABSOLUTE_SECONDS` | 28800 (ocho horas), configurable hasta ese máximo. |
| `AUTH_IDLE_SECONDS` | 1800 (treinta minutos), positivo y no superior al absoluto. |
| `AUTH_TOUCH_SECONDS` | 60; intervalo mínimo para escribir actividad, inferior al plazo de inactividad. |
| `AUTH_LOGIN_ATTEMPTS` | 5 intentos por cuenta y ventana. |
| `AUTH_IP_ATTEMPTS` | 30 intentos por IP y ventana. |
| `AUTH_LIMIT_WINDOW_SECONDS` | 900 (quince minutos). |
| `AUTH_LIMIT_MAX_KEYS` | 2000 claves anónimas en memoria; capacidad agotada deniega en vez de expulsar límites activos. |
| `AUTH_HASH_CONCURRENCY` | 2 trabajos Argon2 simultáneos por proceso. |

Los intentos admitidos incluyen éxitos y fallos; no se resetean al acertar. Cambio de contraseña tiene un espacio de límites separado. La identidad se normaliza antes del límite y las claves se derivan con HMAC, sin guardar el correo/IP como claves en claro. No se persisten contraseñas o secretos de sesión en el limitador.

**El limitador sólo sirve para desarrollo con un proceso.** Pierde contadores al reiniciar o recargar y no coordina instancias. Settings rechaza WEB_CONCURRENCY/UVICORN_WORKERS distintos de uno; el operador debe usar también `--workers 1`, sin asumir que todas las opciones de un supervisor se reflejan en esas variables. Ninguna configuración de esta fase permite publicar login en cloud: falta un almacén compartido/persistente y su revisión.

El pool de autenticación es independiente de `DATABASE_URL`. Cada transacción verifica conexión loopback y contrasta base, current_user y session_user con su identidad técnica explícita. En las pruebas sólo se utiliza mitbol_ci, propietario del PostgreSQL temporal. No se reutiliza neondb_owner ni se modifica el lector de producción.

Ejemplo de configuración de proceso para un futuro ensayo manual **con una base temporal y cuentas ya aprovisionadas mediante un procedimiento autorizado**; no se ejecutó para crear datos ni modifica el .env real:

```powershell
$env:APP_ENV = 'test'  # No carga .env; todas las URL deben ser del entorno aislado.
$env:ADMIN_ENABLED = 'false'
$env:AUTH_ENABLED = 'true'
$env:DATABASE_URL = '<URL_DEL_POSTGRESQL_TEMPORAL_AUTORIZADO>'
$env:AUTH_DATABASE_URL = $env:DATABASE_URL
$env:AUTH_COOKIE_SECURE = 'false'
$env:AUTH_ORIGINS = '["http://localhost:5173"]'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

La URL es un marcador, no una conexión ejecutable. La base debe tener el esquema revisado y datos legítimos del operador; no hay alta automática ni migraciones al arrancar. El nombre `mitbol_auth_local` es una opción de configuración para desarrollo, no una base creada en esta tarea. No exponer este modo mediante túneles, proxies o bind público.

React seguirá consultando el menú en `https://mitbol-backend.onrender.com`. Su base URL de autenticación será `http://localhost:8000`; son servicios distintos durante esta fase. No se despliega login en Render y no se cambia su CORS.

## Login y sesión

1. El navegador envía JSON con correo/contraseña, Origin permitido y X-CSRF-Protection: 1. Esa cabecera obliga a preflight cuando cambia el origen; solicitudes simples de formulario se rechazan.
2. El guard comprueba conexión directa loopback, Host local, ausencia de cabeceras Forwarded/X-Forwarded y perfil HTTP/HTTPS. Rechaza Origin ajeno/null; cualquier POST sin Origin autorizado se rechaza. Limita el cuerpo de autenticación a 16384 bytes antes del parser.
3. El servicio aplica límites antes del trabajo Argon2 y abre una transacción. Busca y bloquea la cuenta; bloquea empleado/asignaciones/roles. Verifica el hash real o un hash ficticio de proceso cuando no existe cuenta. Cuenta desactivada, contraseña incorrecta, empleado ausente/inhabilitado y cuenta inexistente producen el mismo 401 genérico.
4. Login correcto crea un token aleatorio nuevo, sesión persistente con digest SHA-256 y fechas UTC, y hash CSRF. Una cookie previa de esa misma cuenta se revoca por rotación; no se reutiliza el identificador suministrado por el cliente. Otros dispositivos permanecen hasta logout-all/cambio de contraseña.
5. Actualiza last_login_at y registra éxito en AuditEvents dentro de la misma transacción. Sólo después de confirmar entrega la cookie. El identificador de sesión nunca aparece en JSON; los errores tampoco devuelven contraseñas, hashes o mensajes del controlador SQL.

Los formularios usan SecretStr y las rutas auth tienen un manejador de validación que devuelve un 422 genérico: FastAPI no refleja el input que falló. No se registra el cuerpo ni Cookie/Set-Cookie. Un cambio de contraseña incorrecto no devuelve el valor recibido.

Me y las dependencias vuelven a cargar cuenta, empleado, roles y sesión. Comprueban is_active, auth_version, revocación y límites absoluto/inactivo. Cuenta autenticada sin el permiso requerido recibe 403; una cuenta/empleado deshabilitado deja de tener sesión administrativa válida y recibe 401.

La actividad sólo se escribe al superar AUTH_TOUCH_SECONDS y nunca extiende expires_at. Las consultas públicas del menú no renuevan sesiones. La cookie conserva su vencimiento absoluto inicial; la inactividad se impone en servidor aunque el navegador conserve la cookie.

## Cookies, CORS y CSRF

| Perfil | Cookie |
| --- | --- |
| HTTP local | `mitbol_session_local`, HttpOnly, SameSite=Lax, Path=/, sin Domain, Secure=false explícito. |
| HTTPS local simulado en pruebas | `__Host-mitbol_session`, HttpOnly, SameSite=Lax, Path=/, sin Domain, Secure=true. Una petición HTTP con ese perfil se rechaza. |
| Cloud | Rutas bloqueadas. Secure sería obligatorio, pero no basta para habilitar publicación. |

localhost:5173 y localhost:8000 tienen distinto origen y requieren CORS con credenciales, pero comparten sitio cuando usan el mismo esquema/host. No mezclar localhost y 127.0.0.1 al consumir la API. Las cookies no se aíslan por puerto. El navegador administra la cookie HttpOnly; React no la lee ni debe almacenarla en localStorage/sessionStorage. [MDN Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie).

El middleware CORS exterior permite sólo AUTH_ORIGINS, credenciales y métodos/cabeceras explícitos. Resuelve OPTIONS sin sesión y añade cabeceras también a errores de orígenes autorizados. Un preflight aceptado no concede acceso; la petición real vuelve a verificar conexión/origen/sesión. CORS no reemplaza autorización.

CSRF tiene dos partes:

- Login exige Origin permitido, JSON y X-CSRF-Protection: 1; no requiere una sesión anterior ni crea una sesión anónima.
- Las demás mutaciones exigen Origin permitido y X-CSRF-Token de la sesión. La comparación es segura; CSRF inválido devuelve 403 y no renueva actividad ni revoca la sesión válida por sí solo.

El token CSRF se deriva mediante HMAC-SHA-256 usando el secreto aleatorio de sesión como clave y un contexto con separación de dominio. Ese contexto incluye cuenta, versión, hash de contraseña, empleado y asignaciones/códigos/permisos vigentes. AccountSessions guarda sólo SHA-256 del token CSRF. Conocer ese digest o el token CSRF no permite recuperar el secreto de sesión.

GET /csrf recalcula el mismo token para una sesión válida. Así una recarga, otra pestaña o un reinicio del proceso no exige guardar secretos en claro ni rotar tokens de otras pestañas. Cambiar el contexto de seguridad rompe la coincidencia con el digest emitido y deja de aceptar la sesión. [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

GET sin Origin se admite sólo bajo conexión local directa y requiere cookie válida en los endpoints protegidos. Un POST sin Origin siempre falla: curl/Swagger de otro origen deben enviar/configurar el origen permitido según corresponda. No se confía en Host como prueba única de procedencia.

## Endpoints y contrato

| Endpoint | Entrada/protección | Respuesta correcta |
| --- | --- | --- |
| POST /api/auth/login | JSON email/password; Origin y cabecera de protección de login; límite de intentos. | 200, Set-Cookie y usuario/plazos/token CSRF. |
| GET /api/auth/me | Cookie y sesión/empleado válidos. | 200, usuario/roles/permisos y plazos. |
| GET /api/auth/csrf | Cookie y sesión válidas. | 200, csrf_token. |
| POST /api/auth/logout | Cookie, Origin y X-CSRF-Token; Content-Type application/json. | 204, revoca sesión actual y borra cookie. |
| POST /api/auth/logout-all | Igual. | 204, incrementa auth_version, revoca todas las sesiones propias y borra cookie. |
| POST /api/auth/change-password | Igual, más current_password/new_password en JSON. | 204, cambia hash, incrementa versión, revoca todas las sesiones y exige nuevo login. |

Ejemplo de solicitud sin secretos reales:

```http
POST /api/auth/login
Origin: http://localhost:5173
Content-Type: application/json
X-CSRF-Protection: 1

{"email":"staff@example.test","password":"<contraseña de prueba capturada por el usuario>"}
```

Forma de respuesta, con valores de ejemplo y token omitido:

```json
{
  "user": {
    "account_id": 1,
    "person_id": 1,
    "email": "staff@example.test",
    "name": "Persona de prueba",
    "roles": ["staff"],
    "permissions": ["availability:write", "catalog:read"]
  },
  "expires_at": "2026-11-06T04:00:00Z",
  "idle_expires_at": "2026-11-05T20:30:00Z",
  "csrf_token": "<token CSRF, nunca identificador de sesión>"
}
```

El ejemplo no crea esa cuenta ni define una contraseña predeterminada. Me devuelve los mismos campos salvo csrf_token; GET /csrf sólo devuelve ese token. Logout puede enviarse con cuerpo `{}` y la cabecera CSRF. Todas las respuestas sensibles son no-store.

| Estado | Significado |
| --- | --- |
| 400 | Contraseña actual incorrecta al intentar cambiarla, o HTTP con perfil de cookie Secure. |
| 401 | Credenciales inválidas o sesión ausente/forjada/expirada/revocada/incompatible con contexto actual. Se borra la cookie correspondiente. |
| 403 | Origin, procedencia local, CSRF o permiso rechazados. |
| 404 | Módulo deshabilitado o rutas administrativas reales todavía no registradas. |
| 413 / 415 | Cuerpo demasiado grande / Content-Type distinto de JSON para POST auth. |
| 422 | Entrada inválida, contraseña nueva fuera de política o igual a la anterior. Sin reflejar secretos. |
| 429 | Límite de cuenta/IP, capacidad del limitador o hashing ocupado; incluye Retry-After. |
| 503 | PostgreSQL/destino/commit de autenticación no disponible. Sin DSN, SQL ni traceback. |

No se aceptan tokens en Authorization, X-Admin-Key, query string o cuerpo como alternativa a la cookie. Logout sin sesión válida responde 401; no anuncia una revocación que no pudo comprobar. Una red fallida/503 tampoco demuestra que el servidor haya cerrado la sesión: la UI debe distinguirlo de limpiar su estado local.

## Integración prevista con React

Ejemplo de coordinación, no archivos frontend añadidos al repositorio:

```javascript
const AUTH_API = "http://localhost:8000";
const MENU_API = "https://mitbol-backend.onrender.com";
let csrfToken = null; // Sólo memoria; la cookie HttpOnly la maneja el navegador.

async function login(email, password) {
  const response = await fetch(`${AUTH_API}/api/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
    body: JSON.stringify({ email, password }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail);
  csrfToken = data.csrf_token;
  return data.user;
}

async function restoreSession() {
  const me = await fetch(`${AUTH_API}/api/auth/me`, { credentials: "include" });
  if (me.status === 401) { csrfToken = null; return null; }
  if (!me.ok) throw new Error("No fue posible consultar la sesión");
  const csrf = await fetch(`${AUTH_API}/api/auth/csrf`, { credentials: "include" });
  if (!csrf.ok) throw new Error("No fue posible preparar la protección CSRF");
  csrfToken = (await csrf.json()).csrf_token;
  return (await me.json()).user;
}

async function authPost(path, payload = {}) {
  const response = await fetch(`${AUTH_API}/api/auth/${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(payload),
  });
  if (response.status === 401) csrfToken = null;
  if (!response.ok) throw new Error((await response.json()).detail);
  csrfToken = null; // Los tres POST protegidos actuales cierran la sesión al acertar.
}

async function loadPublicMenu() {
  return (await fetch(`${MENU_API}/api/menu`)).json();
}
```

El navegador envía Origin; React no debe intentar establecer esa cabecera prohibida manualmente. Los botones se muestran según permisos para facilitar uso, pero FastAPI sigue verificando cada operación. Ante 401, pedir nuevo login; ante 403, mostrar acceso denegado; ante 429, respetar Retry-After. No reintentar automáticamente escrituras de resultado incierto.

Estos ejemplos no habilitan edición de catálogo desde React. La integración con operaciones HU-005/HU-007 corresponde a una fase posterior.

## Dependencias y transacciones: fragmentos del código real

El patrón de los endpoints es:

```python
User = Annotated[Principal, Depends(get_current_user, scope="function")]
```

El permiso reutilizable contiene:

```python
def require_permission(permission: str):
    def dependency(user: Annotated[Principal, Depends(get_current_user, scope="function")]):
        if permission not in user.permissions:
            raise AuthError(403, "No tienes permiso para esta operación.", code="INSUFFICIENT_PERMISSION")
        return user
    return dependency
```

`get_current_user` abre `service.authenticated(...)` y mantiene su transacción hasta terminar la función. `scope="function"` garantiza que commit/errores ocurran antes de enviar una respuesta de éxito. No cambiarlo por una dependencia yield de duración de respuesta al conectar escrituras reales. [FastAPI: dependencias con yield](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/).

El orden de locks es cuenta → empleado/asignaciones/roles → sesión. Login, lectura de sesión, logout-all y cambio de contraseña siguen ese orden. La cuenta se bloquea antes de verificar la contraseña para que una modificación concurrente no emita una sesión basada en credenciales antiguas y sobreviva a su invalidación.

El helper de repositorio se usa bajo la cuenta ya bloqueada:

```python
def invalidate_locked_account(db, account, now, reason):
    account.auth_version += 1
    revoke_all(db, account.account_id, now, reason)
```

La lectura+incremento es segura aquí porque la fila se mantiene con FOR UPDATE hasta commit. Las futuras herramientas autorizadas de cambio de roles/estado deben usar la misma transacción y helper, no modificar filas por una vía que omita la versión. Los permisos se consultan otra vez y el contexto CSRF detecta cambios observados incluso si una actualización directa omitió incrementar la versión. No puede reconstruir un cambio transitorio que se haya revertido sin actualizar auth_version: ésa es responsabilidad de la futura gestión autorizada, todavía sin endpoints en esta fase.

Un error de permiso/CSRF revierte la unidad original y registra la denegación en otra transacción, sin confirmar cambios parciales. Las invalidaciones observadas revocan la sesión afectada. Errores PostgreSQL se convierten en 503; no se repiten las escrituras automáticamente. Un commit de resultado incierto requiere comprobar estado, no anunciar éxito.

AuditEvents registra login correcto/denegado, logout/logout-all, cambio de contraseña y denegaciones asociadas a cuenta conocida. Los rechazos de origen, cookies mal formadas y límites previos no fuerzan nuevas escrituras de auditoría. Los eventos no guardan correos/passwords/tokens; la referencia de sesión es el UUID interno, no su secreto. Los permisos SQL append-only/retención siguen pendientes de diseñar con el escritor.

## Pruebas y evidencia

Ejecución de la suite completa en PostgreSQL 18 desechable:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra
```

El nombre del ejecutor es histórico y ahora incluye ambas fases. Usa un clúster temporal para catálogo/autenticación y otro para ACL del lector; verifica ruta data_directory, identidad mitbol_ci y versión. Sanea variables PG/AUTH heredadas, no lee .env y mantiene las fixtures públicas en el esquema de catálogo anterior. Los perfiles/cuñas y contraseñas de tests son ficticios; no se crean usuarios de producción. Las únicas migraciones ejecutadas son en bases temporales propias.

Cobertura: login correcto/incorrecto y estados de cuenta/empleado; Admin/Staff; hashes y cookies; plazos absoluto/inactivo y actividad; login CSRF, origen ausente/ajeno, JSON y tamaño; CORS/preflight; logout/replay/logout-all; cambio de contraseña y segundo dispositivo; versión/roles/estado/hash cambiados; rate limiting y atomicidad/capacidad; rollback por auditoría; carreras login/invalidation y actividad/logout; regresión del menú y ACL del lector.

Resultado final: **1021 pruebas aprobadas, cero fallos, cero errores y cero omitidas**, con dos avisos de deprecación existentes de Starlette/AnyIO, en 215.37 segundos de pytest. Ambos clústeres temporales quedaron detenidos. `pip check` no detectó incompatibilidades. El resultado amplía las 955 pruebas de fase 1 con 66 casos de fase 2.

La evidencia está en `TEMP/mitbol-sync-preflight-8841c755505342adbe58a4f964dcb0f2/test-run.log`. Las pruebas de cookies/CORS son HTTP ASGI mediante TestClient, no una sesión manual en navegador ni un despliegue. La prueba HTTPS comprueba atributos bajo un esquema HTTPS simulado; no certifica un terminador TLS real.

## Pendientes para producción y para fase 3

- Acordar y construir limitación compartida/persistente con pruebas multiinstancia. Sólo entonces revisar el bloqueo cloud, junto con el resto de requisitos; no quitarlo para publicar esta implementación local.
- Diseñar el escritor PostgreSQL mínimo, pools y grants de seguridad/auditoría/retención, sin dar escritura ni acceso a credenciales a mitbol_render_reader. No usar neondb_owner en web.
- Aprobar dominios HTTPS de React/API, cookies, proxies de confianza y pruebas de navegador. No convertir Secure=false local en una opción de producción ni asumir que SameSite=None elimina bloqueos de terceros.
- Implementar aprovisionamiento/recuperación controlados y gestión de cuentas/roles usando el protocolo de invalidación. Aún no existe alta operativa ni cuentas predeterminadas; hired_at/teléfono legítimos del propietario siguen pendientes si se desea aprovisionarlo.
- Definir controles de contraseña comprometida, rehash operativo, observabilidad sin secretos y limpieza/retención. Los tiempos se verifican en servidor, independientemente de cuándo se limpien filas antiguas.
- Autorizar por separado la conexión de estas dependencias con HU-005/HU-007 y su auditoría transaccional/guard de archivos. Las rutas reales permanecen cerradas ahora.

## Preguntas posibles del profesor

1. **¿Qué diferencia hay entre cuenta, empleado y rol?** PersonalAccounts autentica; Employees habilita el perfil laboral; EmployeeRoles/Role.code permite asignar permisos sin duplicar credenciales.
2. **¿Por qué una sesión opaca y no JWT?** La sesión persistente permite revocar acceso sin añadir otra lista de revocación; no hace falta un token autónomo para este panel.
3. **¿Por qué SHA-256 para sesión y Argon2id para contraseña?** El primero indexa un secreto aleatorio de 256 bits; la contraseña elegida por una persona requiere un algoritmo deliberadamente costoso.
4. **¿Por qué CORS no basta?** Un cliente HTTP puede llamar directamente. CORS controla lectura desde navegadores; las dependencias comprueban cuenta/sesión/permiso.
5. **¿Por qué CSRF si la cookie es HttpOnly?** El navegador envía cookies automáticamente; HttpOnly evita leerlas desde JS, no enviar una petición usando esa sesión.
6. **¿Qué pasa si se revoca mientras otra petición trabaja?** Las operaciones mantienen locks y revalidan dentro de la unidad. Una revocación no deshace una operación que ya terminó/confirmó.
7. **¿Un Staff puede editar descripciones cambiando React?** No: require_permission devuelve 403. Se probó en una ruta ficticia sin abrir el endpoint real.
8. **¿Por qué no desplegarlo aún?** El limitador es de un proceso local y faltan revisión del escritor, dominios, operación y pruebas reales de navegador/infraestructura.
9. **¿Se cambiaron Neon o las tablas de fase 1?** No. Se conservan modelo/migración y sólo se usan bases temporales para pruebas.

La fase 2 termina con este módulo local y su evidencia. No autoriza fase 3, migraciones reales ni despliegues.
