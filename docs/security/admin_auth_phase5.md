# MITBOL — Autenticación administrativa, fase 5

Fecha: 6 de noviembre de 2026, America/Mexico_City. Rama conservada: `design/admin-auth-security`.

Actualización operativa: las migraciones 0004/0005 en Neon ya fueron completadas
y verificadas por el usuario. También confirmó el escritor con contraseña y
`WRITER_POLICY_OK_REVIEWED_PROFILE`, el lector con `reader_permissions_verified`
y la API pública operativa. No repetir esos procedimientos. El trabajo actual,
en `audit/neon-auth-migration-preflight`, prepara las primeras cuentas mediante
el CLI existente: [sección 6.1](#61-primeras-cuentas-en-neon--preparación-sin-escrituras).
Actualización: el usuario creó y verificó la cuenta ficticia Admin 1 mediante
el SELECT de 6.3: cuenta/empleado activos, Argon2id, rol `{admin}` y auditoría de
creación presentes. No repetir esa consulta ahora ni crear Staff ficticio en
production. Su desactivación queda preparada, sin ejecutar, en 6.5.
La sección 7 se conserva
como referencia histórica del aprovisionamiento SQL ya completado.

**Preparación local, sin despliegue ni cambios reales.** El usuario confirmó que todavía no hay dominio propio aprobado y solicitó mantener cloud bloqueado. Esta revisión conserva el rechazo de `AUTH_ENABLED=true` en cloud y la restricción HTTP loopback. No selecciona dominios, contrata servicios ni habilita acceso administrativo remoto.

Se implementan el limitador PostgreSQL persistente, aprovisionamiento por operador, registro gradual local y separación CORS público/administrativo. Se preparan SQL y procedimientos para revisión posterior. **La revisión puede integrarse/desplegarse con administración apagada; abrirla requiere otra revisión de dominio/proxy/perfil remoto y autorización. No basta cambiar un flag.**

## 1. Bloqueos anteriores y estado al cierre

| Bloqueo | Estado real |
| --- | --- |
| Límites en memoria que se pierden al reiniciar y no coordinan instancias | Resuelto localmente con PostgreSQL, clave estable, política compartida, concurrencia y errores cerrados. Tablas/ACL listas en Neon; login cloud sigue apagado. |
| Permisos SQL del almacenamiento de límites | Migración 0005 y perfil del escritor verificados por el usuario. |
| Primera cuenta y cambios de acceso sin herramienta operativa | CLI de operador implementado/probado con datos sintéticos. Ninguna cuenta real creada. |
| Activación que registraba todo el catálogo/imágenes | Gates separados con defaults false y pruebas locales. |
| CORS administrativo que desplazaba orígenes del menú | CORS por ámbito implementado; el menú mantiene sus orígenes independientes. |
| Comprobación de permisos antes de recibir multipart | Adaptada a los contextos de rutas de FastAPI 0.141.1 y probada sin permitir una sola lectura del cuerpo de Staff. |
| Dominio del frontend/API y comportamiento real de cookies | Pendiente por decisión explícita del usuario. No se usa SameSite=None como atajo. |
| Validación del proxy/IP/HTTPS remoto y perfil remoto del escritor | Pendiente de revisión contra la topología real de Render; los guards locales siguen cerrados a acceso remoto. |
| Almacenamiento duradero de imágenes | Pendiente. No se habilitan cargas en Render. |
| Migraciones y escritor | Completados/verificados por el usuario; sin nuevas migraciones ni grants para el bootstrap. |
| Cuentas reales y apertura de autenticación | Alta por operador preparada; cuentas no creadas y autenticación cloud bloqueada. API pública operativa según el usuario. |

Las siete tablas de identidad de [fase 1](admin_auth_phase1.md) no se rediseñan. [Fase 2](admin_auth_phase2.md) describe sesiones; [fase 3](admin_auth_phase3.md), contratos; [fase 4](admin_auth_phase4.md), la matriz original del escritor. El nuevo head es `20261106_0005`, sobre `20261105_0004`.

## 2. Implementación y archivos

| Archivo | Cambio |
| --- | --- |
| [models/auth_limits.py](../../app/models/auth_limits.py) / [models/__init__.py](../../app/models/__init__.py) | AuthRateLimitPolicy y AuthRateLimits en AuthBase; Base conserva ocho tablas comerciales. |
| [0005_persistent_auth_limits.py](../../migrations/versions/20261106_0005_persistent_auth_limits.py) | Dos tablas y un índice de expiración; sin credenciales, grants, roles SQL ni modificaciones comerciales. No se edita 0004. |
| [services/persistent_auth_limiter.py](../../app/services/persistent_auth_limiter.py) | Admisión transaccional independiente, reloj DB, HMAC, política común, capacidad y vencimiento. |
| [services/auth_service.py](../../app/services/auth_service.py) | Selección del backend de límites; se mantiene el semáforo Argon2 por proceso. |
| [core/config.py](../../app/core/config.py) | Backend/clave del limitador; multiworker sólo con PostgreSQL; gates de catálogo/imágenes apagados por defecto. Sigue bloqueando cloud. |
| [core/scoped_cors.py](../../app/core/scoped_cors.py) / [main.py](../../app/main.py) | Orígenes públicos y administrativos separados, Retry-After expuesto y registro gradual. |
| [core/security.py](../../app/core/security.py) | Consulta contextos efectivos de rutas incluidas y comprueba sus permisos antes de recibir el cuerpo; rechaza rutas no registradas. |
| [api/admin_catalog.py](../../app/api/admin_catalog.py) | Puede omitir las tres escrituras de imágenes, conservando la consulta de referencias. Contratos existentes sin cambios. |
| [services/account_provisioning.py](../../app/services/account_provisioning.py) | Alta y cambio de acceso auditados dentro de la transacción del operador. |
| [cli/admin_accounts.py](../../app/cli/admin_accounts.py) | Terminal interactiva, getpass sin fallback con eco, sin secretos en argv/.env, guardas de operador/destino y errores saneados. |
| [prepare_admin_writer_local.sql](../database/prepare_admin_writer_local.sql) / [verify_admin_writer_local.sql](../database/verify_admin_writer_local.sql) | Perfil 0005, dos tablas adicionales y verificación efectiva del escritor. |
| [render_admin_deployment_sql.py](../../scripts/render_admin_deployment_sql.py) | Generación offline de plantillas Neon a partir de las ACL locales probadas, con guardas diferentes. No conecta a PostgreSQL. |
| [prepare_admin_writer_neon.sql](../database/prepare_admin_writer_neon.sql), [verify_admin_writer_neon.sql](../database/verify_admin_writer_neon.sql), [preflight_admin_neon.sql](../database/preflight_admin_neon.sql) | Preparación/perfil ya confirmados por el usuario. No volver a ejecutar para crear cuentas de aplicación. |
| [admin_api_client.ts](../examples/admin_api_client.ts) | Ejemplo independiente para compañeros React, sin dependencias nuevas ni cambios en frontend. |
| [test_auth_phase5.py](../../tests/test_auth_phase5.py), [test_account_provisioning.py](../../tests/test_account_provisioning.py) | Límites, configuración, CORS/gates, operador, rollback y plantillas. |

Se actualizan los tests de persistencia/migraciones y las fixtures del escritor para el nuevo head. Las regresiones de catálogo siguen usando 0003. El ejecutor aislado existente se reutiliza; no se añaden servicios o librerías importantes.

## 3. Limitador persistente

Se evaluó conservar memoria (sólo local), añadir Redis (otra dependencia/operación) o aprovechar PostgreSQL. Se elige PostgreSQL para el volumen de un panel pequeño: ya existe infraestructura transaccional y se pueden probar ACL, disponibilidad y concurrencia sin contratar servicios. No se presenta como limitador de borde para tráfico masivo.

### Datos y admisión

- `AuthRateLimits`: key_hash SHA-256/HMAC, started_at, expires_at, attempts; PK de hash, CHECK de valores e índice de expiración. No almacena correo/IP en claro.
- `AuthRateLimitPolicy`: fila única policy_id=1 y fingerprint HMAC del secreto/parámetros/versionado. Impide que una instancia con otra clave o límites reinicie silenciosamente contadores en otro espacio.
- Los hashes usan una clave aleatoria estable de 32 bytes, URL-safe canónica de 43 caracteres, compartida por todas las instancias. No es la contraseña ni el secreto de sesión ni la credencial SQL. No se genera/configura una clave real en esta fase.
- Dos contadores por operación: cuenta normalizada como el login e IP IPv4 canónica/red IPv6 /64. Login y cambio de contraseña tienen namespaces distintos. Rotar interfaz IPv6 dentro de la misma red no evita el límite de red; cambiar IP no evita el de cuenta.
- Una transacción corta independiente intenta el advisory lock global `0x4D4954424F4C524C`, verifica política, obtiene `clock_timestamp()`, elimina filas vencidas, comprueba ambos límites/capacidad e incrementa o inserta ambos contadores atómicamente. No mantiene el lock durante Argon2 o la operación administrativa.
- Si el lock está ocupado, 429 con Retry-After=1. Si se agota cuota/capacidad, 429 con plazo. Si falla DB/migración/permisos o cambia la política, 503, sin fallback a memoria. El menú no utiliza estas tablas.
- Una admisión se confirma antes del login: contraseña incorrecta, fallo posterior de auditoría o semáforo ocupado no la revierten. Éxitos también cuentan; acertar no borra el límite. Reiniciar el servicio conserva filas/política.

Es una ventana fija anclada al primer intento admitido; puede admitir una ráfaga a ambos lados de una frontera de ventana. No promete una ventana deslizante estricta. No aumenta el contador cuando ya se ha denegado, evitando extender indefinidamente una ventana por reintentos.

El global lock serializa sólo admisiones y limita rendimiento; una tormenta de peticiones aún puede agotar conexiones/CPU. La capacidad acotada evita crecimiento activo ilimitado pero un atacante distribuido puede ocupar claves y provocar rechazo legítimo. El límite por cuenta permite bloqueo temporal dirigido; una red compartida/NAT puede agrupar empleados. No se implementan bloqueos permanentes, evasión por éxito o expulsión de límites vivos para liberar espacio.

El semáforo Argon2 existente conserva 64 MiB por trabajo y un máximo por proceso; dimensionar workers/RAM y protección de borde antes de publicar. PostgreSQL necesita autovacuum/monitorización de churn, latencia y tamaño; el número de filas vivas se acota, no se promete un tamaño físico exacto en disco.

En cloud la IP sólo será fiable cuando se apruebe el contrato de proxy y se validen cabeceras de la conexión real. No se acepta X-Forwarded-For arbitrario como identidad del cliente. Un cambio de Host, correo en mayúsculas o IP dentro del mismo /64 no debe abrir cuotas nuevas para la misma identidad relevante.

### Cambios de política/clave

No rotar la clave o variar límites por instancia durante un rolling deploy: la política distinta devuelve 503. Para un cambio autorizado: cerrar/drain todas las instancias administrativas, conservar contadores hasta que transcurra la mayor ventana, verificar que no quedan filas vigentes, retirar filas vencidas y la fila de política con el operador, configurar una única política/clave nueva y arrancar coordinadamente. La primera admisión fija la nueva política. El escritor web no puede borrar/actualizar AuthRateLimitPolicy. No ejecutar ese mantenimiento automáticamente en cada reinicio.

### ACL añadidas al escritor

| Tabla | SELECT | INSERT | UPDATE | DELETE / secuencia |
| --- | --- | --- | --- | --- |
| AuthRateLimitPolicy | policy_id, fingerprint | policy_id, fingerprint | Ninguno | Ninguno; sin secuencia |
| AuthRateLimits | key_hash, started_at, expires_at, attempts | Las cuatro columnas | attempts | DELETE para vencimiento; sin secuencia |

El SQL no impone `DELETE WHERE expires_at<=now` al rol: lo aplica el servicio. Una credencial SQL comprometida podría borrar contadores y operar cuentas dentro de sus grants; el perfil del escritor limita al proceso y no sustituye seguridad de credenciales/consultas. Imponer invariantes incluso al SQL directo requeriría funciones/RLS adicionales, no implementadas aquí. El lector no recibe acceso a ninguna de las dos tablas. La [matriz de fase 4](admin_auth_phase4.md) se conserva para las otras tablas/columnas.

## 4. Configuración, defaults y apertura gradual

| Variable | Default / regla implementada |
| --- | --- |
| APP_ENV | local/test/cloud; cloud/test no cargan .env. Render exige cloud. |
| DATABASE_URL | Lector público, validación TLS/destino y perfil read-only en cloud. No sustituir por writer/owner. |
| AUTH_ENABLED | false; **true sigue rechazado en cloud**. |
| ADMIN_ENABLED | false; true rechazado en cloud e incompatible con AUTH_ENABLED. |
| AUTH_CATALOG_ENABLED | false; sólo registra /api/admin en modo individual cuando también AUTH_ENABLED=true. |
| AUTH_IMAGES_ENABLED | false; requiere catálogo y sólo controla las tres escrituras locales de imágenes. No habilita cloud. |
| AUTH_DATABASE_URL | Conexión administrativa independiente; el módulo actual sólo admite la base local aislada. Perfil remoto pendiente. |
| AUTH_LIMIT_BACKEND | memory por defecto para desarrollo local; postgresql selecciona el nuevo backend. Memoria rechaza múltiples workers. |
| AUTH_LIMIT_SECRET | Obligatoria/canónica para backend PostgreSQL habilitado. SecretStr, nunca valor literal versionado. |
| AUTH_LOGIN_ATTEMPTS / AUTH_IP_ATTEMPTS | 5 / 30 por ventana, éxitos y fallos. |
| AUTH_LIMIT_WINDOW_SECONDS / AUTH_LIMIT_MAX_KEYS | 900 / 2000; compartidos entre instancias y incluidos en fingerprint. |
| AUTH_HASH_CONCURRENCY | 2 trabajos Argon2 por proceso. |
| AUTH_COOKIE_SECURE | true; false sólo explícito para HTTP local y rechazado en cloud. |
| AUTH_ORIGINS | Lista exacta administrativa; en este módulo sólo loopback. No comodines, null, credenciales o rutas. |
| CORS_ORIGINS | Orígenes del menú público; independiente de AUTH_ORIGINS. Mantener localhost:5173 si se desarrolla contra Render. |
| AUTH_ABSOLUTE_SECONDS / AUTH_IDLE_SECONDS / AUTH_TOUCH_SECONDS | Máximo 8 h / 30 min por defecto / renovación mínima 60 s; sin ampliación absoluta. |

Para ensayos administrativos: APP_ENV=test, base temporal explícita, un directorio temporal de imágenes exclusivo y cuentas sintéticas; AUTH_ENABLED=true, backend postgresql con clave efímera de la prueba, y optar por catálogo/imágenes según la fase ensayada. No reutilizar mitbol_dev ni su almacenamiento.

El CORS público usa GET, sin credenciales. El privado admite métodos/cabeceras concretos y credentials=true sólo para sus orígenes; expone Retry-After para React. Un preflight aceptado no crea una sesión ni habilita una ruta desregistrada. Las dependencias vuelven a comprobar sesión/CSRF/permisos antes de ejecutar la operación, además del guard previo al cuerpo.

Las cookies conservan HttpOnly, Path=/, sin Domain, SameSite=Lax y nombre __Host-mitbol_session cuando Secure. No se cambia a SameSite=None. El perfil HTTP local usa otro nombre, mitbol_session_local, y no se lleva a producción.

## 5. Integración React–FastAPI

**Estado autorizado actual:** React http://localhost:5173 puede continuar consultando el menú público https://mitbol-backend.onrender.com con `credentials:"omit"`. Las operaciones administrativas se prueban con FastAPI local y PostgreSQL aislado. No se prueba login cloud ni se propone compartir cookies de producción con localhost.

Localhost y onrender.com son sitios diferentes. `credentials:"include"` solicita enviar/aceptar credenciales, pero no anula SameSite. Lax no envía la cookie en el fetch administrativo entre esos sitios. SameSite=None exigiría Secure y aun así los navegadores pueden bloquear cookies de terceros; habilitar CORS no las restaura. Secure se refiere a HTTPS del destino y no convierte dos sitios en uno. Véanse [Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie) y [CORS con credenciales](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS).

Los destinos ahora conocidos, `https://mitbol-frontend.onrender.com` y
`https://mitbol-backend.onrender.com`, también son **sitios distintos**:
`onrender.com` figura en la [Public Suffix List oficial](https://publicsuffix.org/list/public_suffix_list.dat).
Compartir ese sufijo no hace same-site el fetch. La cookie Lax actual no sirve
para ese login entre sitios, aun usando CORS con credenciales. No establecer
`Domain=onrender.com`; además de perder el aislamiento, es un sufijo público.

Recomendación pendiente de aprobación: frontend/API bajo un dominio propio del mismo sitio y ambos HTTPS; preferir mismo origen con /api mediante infraestructura revisada, o subdominios panel/api del mismo dominio registrable. Subdominios pueden ser same-site pero siguen siendo cross-origin y necesitan CORS explícito. No se selecciona ni compra un dominio ni un proxy/hosting adicional en esta fase. Un preview administrativo remoto debe usar el sitio aprobado, no abrir la cookie de producción a cualquier localhost.

**NO-GO para habilitar login en Render con el código actual.** Además de cookies:

- `Settings.validate_auth_configuration()` rechaza cloud, URL administrativa
  remota y orígenes no loopback; `validate_auth_request()` rechaza cloud,
  clientes/Host remotos y cabeceras de proxy. Son bloqueos deliberados.
- `auth_session_factory_for()` sólo crea el pool local. Falta integrar el
  destino TLS remoto y comprobar identidad/perfil del escritor al abrirlo,
  conservando el pool público con el lector. No basta retirar un `if`.
- Falta implementar/probar el contrato de proxy de Render para HTTPS, Host e
  IP real del limitador, sin confiar en cabeceras aportadas por cualquiera.
- Para la futura apertura: backend de límites PostgreSQL, secreto HMAC estable
  compartido, presupuesto de conexiones/RAM y `AUTH_ORIGINS` exacto del sitio
  elegido. Mantener HttpOnly/Secure, controles Origin/CSRF y CORS por ámbito.

Cambiar a SameSite=None sería una decisión distinta que exige pruebas de
navegador y puede fallar por bloqueo de cookies de terceros. No se hace aquí.
Crear cuentas ahora no elimina estos bloqueos ni habilita endpoints en Render.

Para compañeros: [cliente TypeScript independiente](../examples/admin_api_client.ts) y [los 15 contratos existentes](admin_auth_phase3.md). Flujo:

1. POST /api/auth/login JSON con credentials:include y X-CSRF-Protection:1. Conservar csrf_token sólo en memoria. Confirmar /me: un 200 de login no demuestra que el navegador haya almacenado la cookie.
2. Tras recarga: GET /me y GET /csrf con credentials:include; recuperar permisos/CSRF, sin intentar leer HttpOnly ni poner la cookie en localStorage/Authorization.
3. Escrituras: Origin lo añade el navegador; enviar X-CSRF-Token y payload existente. Multipart con FormData y sin fijar Content-Type/boundary. Las cargas seguirán cerradas hasta su almacenamiento aprobado.
4. 401: limpiar contexto de sesión y volver a login. 403: permiso, CSRF u origen/contexto rechazado; no asumir que repetir login concede permisos. 429: respetar Retry-After; no bucle automático ni reenvío masivo. 503: no repetir creaciones/escrituras sin consultar estado, porque puede haber commit incierto.
5. Logout/logout-all requieren CSRF; no anunciar revocación de servidor si la petición falló. La UI puede ocultar controles por permissions, pero FastAPI siempre decide el acceso.

No se modifica el repositorio React. No se afirma haber probado Chrome/Safari/Firefox, DNS, TLS o cookies reales de terceros; TestClient no reproduce sus políticas.

## 6. Aprovisionamiento seguro

Comandos disponibles, sin contraseña, URL o datos personales como argumentos:

```powershell
.\.venv\Scripts\python.exe -B -m app.cli.admin_accounts --help
.\.venv\Scripts\python.exe -B -m app.cli.admin_accounts create
.\.venv\Scripts\python.exe -B -m app.cli.admin_accounts access <ACCOUNT_ID>
```

No ejecutar create/access en infraestructura real ahora. Para un ensayo aislado, usar APP_ENV=test, MITBOL_PROVISION_OPERATOR=mitbol_ci y MITBOL_PROVISION_DATABASE_URL de la base temporal autorizada. El CLI rechaza modo local/.env, mitbol_dev y los logins del lector/escritor; valida conexión real. Para la futura operación remota usa APP_ENV=cloud, destino EXPECTED_DATABASE/HOST y operador explícitos, URL TLS verificada y login independiente. No se configura esa credencial en FastAPI/Render.

`create` pide roles admin/staff, nombre/teléfono reales, email, código laboral, fecha laboral y activación explícita; solicita contraseña y confirmación por getpass, 15–1024 caracteres. No hay credenciales predeterminadas. Rechaza entrada redirigida y cualquier fallback con eco; no muestra contraseña ni hash. El dato password se entrega a Argon2id y no se recorta. Los datos se recogen antes de abrir la transacción.

La transacción inserta Person, PersonalAccount, Employee, EmployeeRoles y AuditEvents. UNIQUE de email normalizado y employee_code rechaza duplicados y revierte todo, sin Persona huérfana. Revisar manualmente identidad existente: teléfono/nombre no son identificadores únicos y el CLI no fusiona personas ni vincula futuros clientes. No se implementan funcionalidades de clientes/pedidos/pagos.

`access` bloquea la cuenta, aplica explícitamente estados de cuenta/empleado y roles, incrementa auth_version, revoca sesiones y audita en la misma unidad. Cualquier fallo revierte también las autorizaciones/revocaciones. Incluso un cambio sin diferencia efectiva invalida las sesiones de forma conservadora. No usar UPDATE manual que omita este protocolo.

Para bootstrap no existe aún un actor de aplicación: AuditEvents.actor_account_id es null y la acción incluye el login SQL real, por ejemplo `operator.<rol_sql>.account.create` o `operator.<rol_sql>.access.change`, con entidad/cuenta, resultado y request_id. El CLI imprime únicamente cuenta y referencia después del commit. Un operador compartido identifica ese rol, no a una persona física: conservar el acta/autorización operativa o aprobar logins personales de operador para atribución individual. No es auditoría criptográfica frente a una credencial SQL comprometida.

El propietario requiere datos laborales legítimos porque el modelo exige Employees.hired_at y employee_code. Si ejerce trabajo desde una fecha real, el operador usa esa fecha/código aprobados. **No usar hoy, 1970 o una fecha ficticia como sustituto.** Si el concepto laboral no aplica al propietario, detener su alta y acordar primero cómo modelar ese perfil; esta fase no vuelve nullable hired_at ni inventa datos. No se crea cuenta real alguna aquí.

### 6.1 Primeras cuentas en Neon — preparación sin escrituras

**GO para usar el bootstrap por operador tras autorización; no con el escritor.**
El escritor no tiene INSERT en Persons/PersonalAccounts/Employees/EmployeeRoles
y el servicio lo rechaza explícitamente. Reutilizar `neondb_owner` únicamente
en esta sesión local controlada; no añadir grants ni ponerlo en Render.

Primer comando, desde la raíz del backend; **no conecta ni modifica Neon**:

```powershell
& .\.venv\Scripts\python.exe -B -m app.cli.admin_accounts --help
```

Preparar dos identidades aprobadas distintas: email, nombre/teléfono reales,
código laboral único y fecha laboral legítima; contraseña de aplicación única
de 15–1024 caracteres guardada en el gestor de contraseñas. El modelo actual
exige Employee incluso al propietario: si sus datos laborales no están
definidos, no inventarlos ni crear esa cuenta hasta resolverlos.

En PowerShell interactivo, sin depurador, grabación de pantalla, transcripción
ni redirección de salida, definir la función siguiente. **Definirla no conecta
ni escribe. No invocarla antes de autorizar el alta.** Usa el host DIRECTO ya
aprobado; no toma un DSN del portapapeles, .env, argumentos o archivos.

```powershell
function Invoke-MitbolAccountOperation {
    param(
        [ValidateSet('create','access')][string]$Operation = 'create',
        [ValidateRange(1,2147483647)][int]$AccountId = 1
    )
    $ErrorActionPreference = 'Stop'
    if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
        throw 'Ejecutar desde C:\Proyectos\mitbol\mitbol-backend.'
    }
    foreach ($key in @('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGPASSWORD','PGPASSFILE')) {
        if ([Environment]::GetEnvironmentVariable($key, 'Process')) {
            throw 'Hay variables libpq heredadas; usar una consola limpia, sin imprimirlas.'
        }
    }
    $keys = @('APP_ENV','EXPECTED_DATABASE','EXPECTED_DATABASE_HOST',
              'MITBOL_PROVISION_OPERATOR','MITBOL_PROVISION_DATABASE_URL')
    $previous = @{}
    foreach ($key in $keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    }
    $secret = $null
    $pointer = [IntPtr]::Zero
    $encoded = $null
    try {
        $env:APP_ENV = 'cloud'
        $env:EXPECTED_DATABASE = 'mitbol_cloud_dev'
        $env:EXPECTED_DATABASE_HOST = 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
        $env:MITBOL_PROVISION_OPERATOR = 'neondb_owner'
        $secret = Read-Host 'Contraseña SQL de neondb_owner (oculta)' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $encoded = [uri]::EscapeDataString([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $pointer = [IntPtr]::Zero
        $env:MITBOL_PROVISION_DATABASE_URL = 'postgresql+psycopg2://neondb_owner:' + $encoded + '@' + $env:EXPECTED_DATABASE_HOST + ':5432/mitbol_cloud_dev?sslmode=verify-full&channel_binding=require'
        $encoded = $null
        # Ambas operaciones ESCRIBEN en Neon: ejecutar sólo tras autorización.
        if ($Operation -eq 'access') {
            & .\.venv\Scripts\python.exe -B -m app.cli.admin_accounts access $AccountId
        }
        else {
            & .\.venv\Scripts\python.exe -B -m app.cli.admin_accounts create
        }
        if ($LASTEXITCODE -ne 0) {
            throw 'Operación no confirmada; consultar estado antes de repetir.'
        }
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        if ($null -ne $secret) { $secret.Dispose() }
        $encoded = $null
        foreach ($key in $keys) {
            [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
        }
    }
}
```

La credencial SQL existe transitoriamente en memoria y en el entorno del
proceso/hijo, accesible a procesos con suficientes privilegios locales. No se
imprime ni guarda; limpiar referencias no garantiza borrar copias inmutables
de memoria. La contraseña **de aplicación** se introduce sólo en `getpass` de
Python y no entra en el DSN ni en SQL: se inserta su hash Argon2id (64 MiB,
tres pasadas, un hilo). El CLI no activa logging SQL y oculta parámetros de
errores; no podemos garantizar ausencia de registros del lado del proveedor
de los hashes/datos insertados. Tratar también esos hashes como confidenciales.

### 6.2 Altas autorizadas — ESCRIBEN EN NEON, no ejecutadas

Referencia para futuras cuentas legítimas. La prueba Admin 1 ya está verificada;
**no ejecutar esta sección ni crear un Staff ficticio ahora**.

Después de autorización explícita, ejecutar **una vez para Admin**:

```powershell
Invoke-MitbolAccountOperation -Operation create
```

Responder a los prompts: `admin`, datos reales aprobados, fecha `AAAA-MM-DD`,
`si` para activar cuenta/perfil laboral y contraseña nueva/confirmación ocultas.
No poner la contraseña en un comando. Resultado esperado tras COMMIT:
`Operación confirmada. Cuenta <id>; referencia de auditoría <uuid>.`
Registrar únicamente ID, rol y referencia. La activación de la cuenta en DB
no activa autenticación ni administración en Render.

Verificar el alta con el SELECT de 6.3. Luego invocar **una vez para Staff**:

```powershell
Invoke-MitbolAccountOperation -Operation create
```

Responder `staff`, su propia identidad/código/email y contraseña distinta,
activación `si`. Verificar nuevamente en 6.3. No asignar `admin,staff` por
defecto y no reutilizar identidades o contraseñas entre las dos personas.

El email se normaliza; email y código laboral tienen UNIQUE. Un duplicado o
fallo de auditoría revierte toda el alta, incluida Person. Si aparece
`Operación no confirmada`, se pierde la conexión o falta confirmación del
COMMIT, **parar y consultar el estado antes de repetir**; no crear otra identidad
para eludir UNIQUE. El CLI no fusiona personas con emails/códigos diferentes.
No hacer DELETE, restauración, cambio manual de roles ni reintento automático.
Cerrar esta consola al terminar; no guardar la credencial del operador en Render.

### 6.3 Verificar cuentas y roles — sólo lectura

En Neon SQL Editor, production / mitbol_cloud_dev, como operador, ejecutar
este único bloque después de cada alta. No muestra identidad personal,
contraseñas, hashes ni tokens y no repite la auditoría del lector/escritor:

```sql
BEGIN READ ONLY;
SELECT a.account_id,
       a.is_active AS account_active,
       coalesce(e.is_active, false) AS employee_active,
       a.password_hash LIKE '$argon2id$%' AS argon2id,
       ARRAY(SELECT r.code
             FROM public."EmployeeRoles" er
             JOIN public."Roles" r ON r.role_id = er.role_id
             WHERE er.person_id = a.person_id ORDER BY r.code) AS roles,
       EXISTS(SELECT 1 FROM public."AuditEvents" v
              WHERE v.action = 'operator.neondb_owner.account.create'
                AND v.outcome = 'success' AND v.resource_type = 'account'
                AND v.resource_id = a.account_id::text) AS creation_audited
FROM public."PersonalAccounts" a
LEFT JOIN public."Employees" e ON e.person_id = a.person_id
ORDER BY a.account_id;
ROLLBACK;
```

Esperado: IDs iguales a los confirmados por el CLI; `{admin}` y `{staff}`
respectivamente, cuatro booleanos true. No consultar `/login` en la nube ni
probar contraseñas en SQL. Si hay discrepancias, detener la segunda alta o la
configuración posterior y revisar sólo esas cuentas con el operador.

### 6.4 Advertencia de funciones — interpretación, sin nuevas consultas en Neon

Evidencia aportada por el usuario: cero SECURITY DEFINER en esquemas de usuario;
17 funciones con EXECUTE público/efectivo y `prosecdef=false`; comprobación del
lector finalizada con `reader_permissions_verified` y ROLLBACK. Las filas no
identifican esquema/extensión; no se inventan esos datos ni se vuelven a pedir.

`has_function_privilege(..., 'EXECUTE')` sólo demuestra permiso de invocación:

- Slots de replicación requieren comprobaciones internas de superusuario o
  REPLICATION. Control de sesiones depende del rol del proceso destino,
  membresías/pg_signal_backend y protección de superusuarios; EXECUTE no permite
  por sí solo terminar cualquier proceso. Puede permitir controlar sesiones
  propias. Véase [PostgreSQL 18, funciones administrativas](https://www.postgresql.org/docs/18/functions-admin.html).
- `set_config` aplica las restricciones de SET. No convierte al lector en
  escritor ni permite asumir roles privilegiados; sí permite cambiar parámetros
  ordinarios. `default_transaction_read_only` es un default modificable por el
  cliente, no una ACL inmutable.
- No todas las filas son inocuas: las funciones de objetos grandes pueden
  crear/modificar objetos propios con sus ACL independientes; véase
  [implementación de objetos grandes](https://www.postgresql.org/docs/18/lo-implementation.html).
  `pg_logical_emit_message` no equivale a crear un slot ni tiene su misma
  comprobación de REPLICATION en [el código PostgreSQL 18](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/replication/logical/logicalfuncs.c).
  Puede generar WAL; un mensaje no transaccional no desaparece con ROLLBACK.

Reproducción **exclusivamente en PostgreSQL 18 temporal local**: lector/escritor
reciben 42501 al controlar la sesión privilegiada o borrar un slot; pueden
crear/escribir su objeto grande y emitir un mensaje WAL sintético al abrir
READ WRITE, manteniendo denegado UPDATE de precio. El objeto se revierte y el
clúster de laboratorio se detiene. No se ejecutaron esas funciones en Neon.

No hay evidencia de acceso a tablas privadas/comerciales fuera del perfil ni
un bloqueo para estas altas controladas. Sí queda un alcance residual frente
al robo de credenciales SQL: los grants del catálogo no garantizan cero efectos
secundarios en toda la base. Registrar esta limitación para la apertura remota;
no revocar PUBLIC, cambiar privilegios o repetir verificadores ahora.

### 6.5 Cerrar la prueba de la cuenta ficticia 1 — preparado, no ejecutado

La comprobación de 6.3 ya fue satisfactoria según el usuario: `account_id=1`,
`account_active=true`, `employee_active=true`, `argon2id=true`, `roles={admin}`
y `creation_audited=true`. No repetirla antes de la desactivación.

1. **Preparación sin conexión:** definir la función actualizada de 6.1,
   `Invoke-MitbolAccountOperation`, sin invocarla. No definir/copiar secretos.
2. **Detenerse aquí hasta autorizar la escritura.** Después de autorización,
   ejecutar una sola vez en esa misma consola PowerShell:

   ```powershell
   Invoke-MitbolAccountOperation -Operation access -AccountId 1
   ```

   La función solicita primero la contraseña SQL del operador mediante entrada
   oculta. El CLI ejecutado es exactamente:
   `python -B -m app.cli.admin_accounts access 1`. Responder:

   - Roles explícitos: `admin` (conservar la asignación existente).
   - Activar cuenta: `no`.
   - Activar perfil laboral: `no`.

   No solicita ni cambia la contraseña de aplicación. Se desactivan cuenta y
   empleado, se incrementa auth_version, se revocan sus sesiones y se registra
   `operator.neondb_owner.access.change` en una única transacción. No se elimina
   la persona, cuenta, asignación de rol ni auditoría de creación.
3. Esperar `Operación confirmada. Cuenta 1; referencia de auditoría <uuid>` y
   conservar esa referencia junto a la de creación. Sólo **después** reutilizar
   el SELECT de 6.3 sustituyendo `ORDER BY a.account_id` por
   `WHERE a.account_id = 1`. Esperado: cuenta y empleado false; Argon2id,
   `{admin}` y auditoría de creación se conservan. Si falla o no hay confirmación,
   detenerse y consultar el estado; no reintentar automáticamente.

Este procedimiento no habilita login en Render. Admin/Staff y revocación se
prueban con cuentas sintéticas exclusivamente en PostgreSQL temporal local,
sin una segunda cuenta ficticia en production.

## 7. Aprovisionar el escritor en Neon — completado por el usuario, referencia

Estado confirmado por el usuario el 2026-11-06: production/mitbol_cloud_dev está
en 0005; sus nueve tablas, catálogo, lector y API pública ya fueron verificados.
El escritor también está creado, con contraseña y perfil verificado.
**No repetir los pasos 7.1–7.5, preflight ni migraciones para crear cuentas.**
Snapshot conservado:
mitbol-before-auth-migrations, 2026-11-06 19:20 UTC. Esta sección sustituye el
procedimiento anterior que todavía partía de 0003.

Se reutilizan prepare_admin_writer_neon.sql, verify_admin_writer_neon.sql y su
generador, sin nuevas migraciones, arquitectura o scripts ejecutables. El perfil
es el de las operaciones actuales de auth/sesiones, limitador, descripción,
disponibilidad, etiquetas e imágenes. Admin/Staff se distinguen en FastAPI,
no mediante dos logins SQL. El escritor no aprovisiona personas ni asigna roles.

Corrección de transporte: pg_stat_ssl observa la conexión del backend PostgreSQL,
que puede ser interna al proxy, y no acredita el TLS externo del cliente. El
procedimiento exige TLS en libpq con verify-full, CA y channel_binding=require.
Los SQL conservan destino/identidades/PG18 y requieren además una marca explícita
de sesión. **Esa marca no es una prueba criptográfica ni una autorización**:
sólo debe emitirse desde estos comandos con los parámetros TLS obligatorios.
No ejecutar los archivos en SQL Editor ni fijar la marca para eludir TLS.
Las guardas LOCAL y todos los GRANT/REVOKE existentes se mantienen.

### 7.1 Preparación local — sin conexión ni cambios en Neon

Primer comando, comprobación offline de los SQL ya generados:

```powershell
Set-Location 'C:\Proyectos\mitbol\mitbol-backend'
& .\.venv\Scripts\python.exe -B scripts/render_admin_deployment_sql.py --check
if ($LASTEXITCODE -ne 0) { throw 'SQL desactualizado: detenerse.' }
```

Resultado esperado: SQL propuesto comprobado. Preparar la misma terminal:

```powershell
$ErrorActionPreference = 'Stop'
$MitbolPsql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
if (-not (Test-Path -LiteralPath $MitbolPsql)) { throw 'Falta psql 18.' }
foreach ($MitbolKey in @('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGPASSWORD','PGPASSFILE')) {
    if ([Environment]::GetEnvironmentVariable($MitbolKey,'Process')) { throw "Revisar override $MitbolKey en esta terminal." }
}
$MitbolHost = 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
if ((Read-Host 'Host DIRECTO de production confirmado en Neon Console') -cne $MitbolHost) { throw 'Destino no coincide.' }
$MitbolCa = & .\.venv\Scripts\python.exe -B -c 'import certifi; print(certifi.where())'
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $MitbolCa)) { throw 'CA no disponible.' }
$MitbolCa = $MitbolCa.Replace('\','/')
if ($MitbolCa.Contains("'")) { throw 'Ruta CA inesperada.' }
$MitbolConnection = "host=$MitbolHost port=5432 dbname=mitbol_cloud_dev sslmode=verify-full sslrootcert='$MitbolCa' channel_binding=require gssencmode=disable ssl_min_protocol_version=TLSv1.2 connect_timeout=10 client_encoding=UTF8"
$MitbolPsqlArgs = @('-X','-W','-v','ON_ERROR_STOP=1','-d',$MitbolConnection)
```

No cargar .env, imprimir variables de conexión, usar Start-Transcript ni pegar
contraseñas en comandos. `-W` solicita la contraseña sin eco; no se guarda en
argv, DATABASE_URL, Git ni archivos. No exportar credenciales del operador a Render.
No reutilizar configuraciones TLS menos estrictas ante un error.

### 7.2 Identidad y existencia del rol — sólo lectura

```powershell
& $MitbolPsql @MitbolPsqlArgs -U neondb_owner -c '\conninfo' -c "BEGIN READ ONLY; SELECT current_database() AS database_name,current_user,session_user,current_setting('server_version') AS postgres_version,EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='mitbol_admin_writer') AS writer_exists; ROLLBACK;"
if ($LASTEXITCODE -ne 0) { throw 'Inspeccion detenida; no crear el rol.' }
```

Esperado: TLS establecido en conninfo, base mitbol_cloud_dev, ambas identidades
neondb_owner, PostgreSQL 18 y writer_exists=false. Si ya existe, detenerse sin
sobrescribirlo. No se vuelven a consultar tablas, huellas ni revisión Alembic.

**DETENERSE AQUÍ: aún no se ha escrito en Neon. Crear el rol y configurar su
contraseña requieren autorización explícita del usuario. Los bloques siguientes
son acciones separadas y no deben pegarse como un lote.**

### 7.3 Crear rol y permisos — ESCRIBE EN NEON, sólo tras autorización

```powershell
& $MitbolPsql @MitbolPsqlArgs -U neondb_owner -c "SET mitbol.external_tls_verified='verify-full-channel-binding'" -c "SET mitbol.approved_admin_write='phase5-reviewed'" -f .\docs\database\prepare_admin_writer_neon.sql
if ($LASTEXITCODE -ne 0) { throw 'Creacion detenida: consultar estado antes de cualquier nuevo intento.' }
```

Esperado: COMMIT, sin errores. El archivo crea LOGIN PASSWORD NULL, de momento
sin contraseña utilizable, con atributos mínimos y grants por columna. Comprueba
0005 dentro de su guard de ejecución para no aplicar ACL sobre otro esquema;
no vuelve a migrar ni otorga acceso a alembic_version. No sobrescribe un rol existente.

REVOKE CREATE/TEMPORARY de PUBLIC en esta base y CREATE en public impide que el
writer herede DDL; no revoca SELECT del lector ni concede permisos a ese lector.
No se toca ningún dato comercial ni se crean cuentas de aplicación. Registrar
el resultado antes de continuar; no cambiar Render.

### 7.4 Contraseña del rol existente — ESCRIBE EN NEON, sólo tras autorización

7.3 ya terminó con COMMIT según el operador: **no repetirlo**. Console rechazó
Reset password con `cannot update password for role without password`. La ruta
anterior no cubría PASSWORD NULL. Se inicializa la contraseña del mismo rol por
SQL y después se utiliza Reset password para obtener la definitiva. No se elimina
ni recrea el rol, ni se cambia un GRANT, membresía o atributo distinto de PASSWORD.

[Neon](https://neon.com/docs/manage/roles) documenta ALTER ROLE con contraseña
en texto plano; no admite recibir su hash. Por eso no se usa `\password`, que
en [psql](https://www.postgresql.org/docs/18/app-psql.html) calcula el verificador
en el cliente. Inicializar con una clave aleatoria transitoria, nunca mostrada,
evita enviar la definitiva en nuestro ALTER ROLE. Son dos escrituras separadas
dentro de este paso: inicialización SQL y reset posterior en Console.

**Registro residual:** psycopg2 adapta `%s` en el cliente; PostgreSQL/Neon recibe
la sentencia con la contraseña transitoria en texto plano dentro de TLS. El
parámetro evita inyección, no garantiza ocultación en el servidor. PostgreSQL
[advierte que los logs pueden contener contraseñas](https://www.postgresql.org/docs/18/runtime-config-logging.html).
También puede existir captura por actividad, estadísticas/auditoría, proxy u
observabilidad del proveedor; no se ha comprobado su redacción ni retención en
este proyecto. El código evita SQL/logs/tracebacks de cliente, pero **no promete
cero registros del lado de Neon**. No desactiva auditoría, borra logs, altera
parámetros globales ni concede privilegios para hacerlo. Tras el reset la clave
transitoria deja de autenticar conexiones nuevas; un reset no cierra sesiones
que ya estuvieran abiertas. Ejecutar ambos pasos de forma atendida y consecutiva;
ante sesiones inesperadas, detenerse, sin terminarlas automáticamente. Neon
también procesa la definitiva en el reset; no se certifican sus logs internos.

Preparar una contraseña SQL literal en PowerShell, SQL Editor, .env, archivos,
historial o argumentos de proceso queda excluido. Usar una terminal interactiva
local, sin Start-Transcript, grabación o depurador. El operador se autentica por
entrada oculta; se rechaza cualquier fallback con eco. No reutilizar la contraseña
del operador para el writer. No hay lectura de .env ni DATABASE_URL.

**Preparación local, sin conexión:** cargar este bloque completo. Sólo define
código fijo; aún no se solicitan secretos ni se ejecuta ALTER ROLE.

```powershell
$MitbolInitialPasswordCode = @'
import getpass, json, logging, os, secrets, sys, warnings
logging.disable(logging.CRITICAL)
engine = None
operator_password = bootstrap_password = url = None
stage = 'imports'
diagnostic = None
result = None
exit_code = 1
try:
    from sqlalchemy.engine import URL
    from app.db.connection import CloudDatabaseTarget, create_database_engine
    from app.cli.verify_neon import connection_diagnostic
    diagnostic = connection_diagnostic
    stage = 'terminal'
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('INTERACTIVE_TERMINAL_REQUIRED')
    if any(os.getenv(key) for key in ('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGPASSWORD','PGPASSFILE')):
        raise ValueError('LIBPQ_OVERRIDE_REJECTED')
    stage = 'operator_password'
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        operator_password = getpass.getpass('Password neondb_owner (hidden): ')
    if not operator_password:
        raise ValueError('OPERATOR_PASSWORD_REQUIRED')
    target = CloudDatabaseTarget('mitbol_cloud_dev', 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech')
    url = URL.create('postgresql+psycopg2', username='neondb_owner', password=operator_password,
        host=target.expected_host, port=5432, database=target.expected_database,
        query={'sslmode':'verify-full','channel_binding':'require'})
    stage = 'connect'
    engine = create_database_engine(url, target=target, read_only=False)
    role_sql = "SELECT oid,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls,rolconfig FROM pg_catalog.pg_roles WHERE rolname='mitbol_admin_writer'"
    with engine.begin() as db:
        stage = 'identity_tls'
        target.validate_connection(db)
        if db.exec_driver_sql('SELECT current_user,session_user').one() != ('neondb_owner',)*2:
            raise ValueError('OPERATOR_IDENTITY_MISMATCH')
        if int(db.exec_driver_sql('SHOW server_version_num').scalar_one()) // 10000 != 18:
            raise ValueError('POSTGRES_VERSION_MISMATCH')
        db.exec_driver_sql('SET LOCAL search_path=pg_catalog,public')
        db.exec_driver_sql("SET LOCAL lock_timeout='5s'")
        db.exec_driver_sql("SET LOCAL statement_timeout='15s'")
        stage = 'existing_role'
        before = db.exec_driver_sql(role_sql).one()
        if tuple(before[1:8]) != (True,False,False,False,False,False,False):
            raise ValueError('WRITER_ATTRIBUTES_UNEXPECTED')
        bootstrap_password = secrets.token_urlsafe(48)
        stage = 'initial_password'
        db.exec_driver_sql('ALTER ROLE mitbol_admin_writer PASSWORD %s', (bootstrap_password,))
        stage = 'preserved_identity'
        if db.exec_driver_sql(role_sql).one() != before:
            raise ValueError('WRITER_IDENTITY_OR_ATTRIBUTES_CHANGED')
        stage = 'commit'
    result = {'status':'initial_password_committed','next':'Reset password in Console, then step 7.5'}
    exit_code = 0
except (Exception, KeyboardInterrupt) as error:
    result = {'status':'stopped','stage':stage}
    if diagnostic is not None:
        result.update(diagnostic(error, stage))
    else:
        result.update({'code':'CLIENT_IMPORT_FAILED','sqlstate':None})
finally:
    operator_password = bootstrap_password = url = None
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass
print(json.dumps(result))
raise SystemExit(exit_code)
'@
Write-Output 'Paso 7.4 preparado; ninguna conexion ni escritura ejecutada.'
```

**DETENERSE para autorización explícita de ambas escrituras.** No ejecutar el
bloque siguiente como parte de cargar la preparación. La confirmación de host
no sustituye esa autorización. El intérprete recibe código fijo, no secretos,
por `-c`; las entradas de getpass son directamente de la consola, no una tubería.
Base64 sólo transporta ese código fijo para conservar comillas en Windows
PowerShell 5 y PowerShell 7; no codifica ni protege ninguna contraseña.

```powershell
if ((Read-Host 'Host DIRECTO de production confirmado en Console') -cne 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech') { throw 'Destino no coincide.' }
$MitbolInitialPasswordEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($MitbolInitialPasswordCode))
& .\.venv\Scripts\python.exe -B -c "import base64;exec(base64.b64decode('$MitbolInitialPasswordEncoded'))"
if ($LASTEXITCODE -ne 0) { throw 'Inicializacion no confirmada: no repetir ni continuar con el reset automaticamente.' }
```

Esperado: JSON con status=initial_password_committed. Ninguna clave, DSN,
sentencia interpolada ni traceback se muestra. El bootstrap sólo existe en
memoria del proceso cliente y se envía por TLS; Python no garantiza borrado
criptográfico de sus copias en memoria. No intentar recuperarlo ni imprimirlo.

Tras ese éxito, **segunda escritura autorizada**: Neon Console → production →
Roles → mitbol_admin_writer existente → Reset password. Ahora ya tiene contraseña;
si Console sigue rechazándolo, detenerse y comunicar sólo el error, sin repetir
el inicializador. Guardar la definitiva generada directamente en el gestor de
contraseñas. No usar Add role, conceder neon_superuser ni configurar Render.
La ruta de reset se apoya en la documentación de Neon, pero no se ha ensayado
contra este proyecto. Si hubo error/timeout en COMMIT, el resultado es incierto:
no sobrescribir de nuevo; investigar el estado del rol antes de otro cambio.

Continuar con **7.5 sin cambios**, usando la definitiva del reset en el prompt
`psql -W`. Esperado: WRITER_POLICY_OK_REVIEWED_PROFILE. Si se perdió la terminal,
recargar sólo las variables sin secretos de 7.1; no repetir 7.2/7.3 ni migraciones.

Validación de esta corrección: cuatro pruebas focalizadas aprobadas en PostgreSQL
18 desechable (2.66 s), con rol operador no superusuario y credenciales ficticias.
Se comprobó la contraseña adaptada con comillas/barra, OID/atributos/ACL idénticos,
perfil limitado vigente, rechazo sin terminal/sin entrada oculta y errores sin
secretos. Preparación PowerShell y carga del código fijo por Base64 compiladas
sin conectar. El clúster se detuvo; no se ejecutó la inicialización ni el reset
contra Neon, ni se modificaron scripts SQL o código de backend por esta corrección.

### 7.5 Login independiente y ACL — sólo lectura, sin datos de prueba

Introducir ahora la contraseña del escritor, no la del operador:

```powershell
& $MitbolPsql @MitbolPsqlArgs -U mitbol_admin_writer -c '\conninfo' -c "SET mitbol.external_tls_verified='verify-full-channel-binding'" -f .\docs\database\verify_admin_writer_neon.sql
if ($LASTEXITCODE -ne 0) { throw 'Writer no aprobado: no configurar Render ni ampliar privilegios.' }
```

Esperado: WRITER_POLICY_OK_REVIEWED_PROFILE. Revisa permisos efectivos por
columna y tabla, DELETE, secuencias, PUBLIC, ownership, membresías, grant option,
defaults y funciones propias. Incluye denegación de precios/IDs Loyverse, altas
de cuentas/asignación de roles, DDL y acceso a alembic_version. No usa SET ROLE.

Comprobar que el aprovisionamiento conservó el lector con su propio login:

```powershell
& $MitbolPsql @MitbolPsqlArgs -U mitbol_render_reader -f .\docs\database\verify_render_reader.sql
if ($LASTEXITCODE -ne 0) { throw 'Lector no aprobado: detener avances e investigar.' }
```

Esperado: reader_permissions_verified, sin errores. Son verificaciones posteriores al nuevo
grant, no otra inspección de migraciones. No insertan filas ni avanzan secuencias.
Las pruebas funcionales positivas/negativas y rollback se ejecutan localmente
con servicios reales y logins limitados mediante test_admin_writer.py y
test_auth_phase5.py. No crear personas/sesiones/filas de prueba en production:
incluso un INSERT seguido de ROLLBACK puede avanzar una secuencia. Un ensayo de
escritura remota requeriría autorización y alcance aparte; no es necesario para
ejecutar este verificador de ACL.

### 7.6 Si falla un paso

Detenerse; conservar sólo errores saneados y SQLSTATE. No reintentar, borrar rol,
restaurar snapshot, hacer downgrade o conceder permisos automáticamente. Si la
creación pierde conexión durante COMMIT, releer writer_exists con conexión nueva;
no asumir rollback. Si existe, revisar su estado en lugar de ejecutar otra vez
CREATE. Si falla el reset, el rol puede existir sin credencial utilizable: no
recrear ni rotar de nuevo automáticamente. Si falla el verificador, no usar el
writer en Render ni debilitarlo para obtener OK. La API pública continúa con el
lector, AUTH_ENABLED=false y ADMIN_ENABLED=false. El snapshot anterior a las
migraciones no debe restaurarse para deshacer un error de permisos del escritor.

## 8. Procedimiento Render y orden de despliegue — NO ejecutado

1. Revisar diff, migraciones offline, ACL y reporte. Preparar un PR de design/admin-auth-security hacia develop **después de autorizar commits/push**, ejecutar CI completo y revisar el resultado. No se creó PR, commit ni push en esta fase.
2. Fusionar mediante el proceso aprobado. Auto-Deploy permanece Off: la fusión no debe habilitar administración ni sustituir un despliegue manual verificado.
3. Completar primero backups/migraciones/ACL y ensayos de Neon descritos arriba. Desplegar manualmente esta revisión como API pública, con AUTH_ENABLED=false, ADMIN_ENABLED=false y gates de catálogo/imágenes false. Mantener DATABASE_URL con mitbol_render_reader. El backend público no depende de crear AuthService cuando está apagado.
4. En Dashboard elegir Manual Deploy para el commit aprobado y registrar su SHA. Verificar /, /api/menu, detalle/medios, CORS público y 404 administrativos. No se ejecuta esa operación desde este trabajo. Véase [deploys manuales de Render](https://render.com/docs/deploys).
5. Resolver dominio/HTTPS, confianza de proxy/IP y perfil remoto administrativo en una revisión posterior, con tests y ensayo. El guard de esta revisión rechaza cloud incluso con PostgreSQL persistente. No retirar ese rechazo por comodidad ni cambiar a cookie insegura.
6. Sólo después, configurar AUTH_DATABASE_URL de writer como secreto separado y AUTH_LIMIT_SECRET estable/compartido; backend postgresql; parámetros de política comunes; hosts/destinos TLS, orígenes y cookies aprobados. La contraseña del owner/operador no se incorpora al servicio. Verificar permisos/identidad al abrir el pool remoto y fallo cerrado, sin fallback al lector.
7. La primera apertura futura debe registrar sólo autenticación (catálogo/imágenes false), luego probar sesiones/limitador y roles, y después habilitar catálogo. Esta fase agrupa HU-005/HU-007 en AUTH_CATALOG_ENABLED; una separación más fina de esas capacidades requerirá otro gate si se aprueba. Mantener AUTH_IMAGES_ENABLED=false en producción hasta almacenamiento duradero y guardas específicas revisadas; no usar filesystem efímero de Render como solución definitiva.
8. Probar login incorrecto/correcto, cookie real en navegador del sitio aprobado, /me, CSRF, expiración/logout/replay, 429 compartido/reinicio e IP real no falsificable; Admin/Staff, escritura local permitida y cambios comerciales rechazados. Confirmar separación de pools y ausencia de secretos en logs.

Cambiar variables puede disparar un redeploy según la opción elegida aunque Auto-Deploy esté Off. Render distingue Save only, Save and deploy y Save/rebuild/deploy; acordar la secuencia y usar Save only durante preparación si corresponde, validando luego el despliegue manual. No se cambió ninguna variable aquí. Véase [variables/secretos de Render](https://render.com/docs/configure-environment-variables).

## 9. Verificación, reversión y recuperación

Para las primeras cuentas, reutilizar la evidencia existente de esquema,
lector/escritor y catálogo; verificar sólo el resultado del alta según 6.3.
Para una futura apertura remota, diferenciar salud pública de preparación
administrativa: / no valida el writer/limitador; un 200 del menú no aprueba
login. No se añade un health endpoint que exponga configuración o credenciales.

Si falla la preparación: no habilitar AUTH_ENABLED. Si falla una futura apertura: cerrar AUTH_CATALOG_ENABLED y AUTH_ENABLED, mantener ADMIN_ENABLED=false e imágenes false; desplegar la configuración/código público conocido, verificar menú y reader, y retirar/rotar sólo el secreto administrativo cuando corresponda. No cambiar DATABASE_URL al owner para resolver un error.

No hacer downgrade automático de 0004/0005 después de crear cuentas/contadores. Se puede volver al código público anterior manteniendo las tablas/grants sin usar. 0004 downgrade elimina identidad/auditoría; 0005 elimina protección persistente y contadores. Cualquier restauración requiere evaluar pérdida de auditoría, contraseñas/versiones/sesiones y revocación posterior, además del respaldo aprobado.

No borrar contadores en una reversión/reinicio normal: conservar límites. Un commit de alta/operación incierto se investiga con cuenta/estado y referencia de auditoría antes de repetir; duplicados no se solucionan desactivando restricciones. Conservar evidencias y no registrar passwords/DSN/tokens en el diagnóstico.

## 10. Pruebas ejecutadas

Actualización de primeras cuentas y advertencia de funciones (2026-11-06):
**155 pruebas seleccionadas aprobadas**, en dos ejecuciones aisladas; dos
deprecaciones preexistentes por ejecución. No se repitió la suite completa ni
se conectó a Neon/Render. Comandos:

```powershell
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_account_provisioning.py tests/test_auth_security.py tests/test_cloud_configuration.py tests/test_auth_api.py tests/test_auth_phase5.py::test_public_cors_keeps_local_react_independent_from_admin_origins tests/test_auth_phase5.py::test_local_stage_options_cannot_bypass_cloud_block -q --tb=short
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_account_provisioning.py -q -k function_warning --tb=short
```

Primera ejecución: 152 aprobadas, 47.65 s; alta Admin/Staff con roles exactos,
Argon2id sin plaintext en parámetros SQL ni salida, prompts inseguros/argv
rechazados, duplicados/rollback/auditoría/revocación, destino y operador,
configuración cloud, sesiones/CSRF y CORS. Segunda: 3 aprobadas, 19 no
seleccionadas, 2.35 s; comprobaciones internas de señalización/slots y alcance
residual de objetos grandes/WAL, sólo en PostgreSQL 18 temporal. Ambos
clústeres detenidos. Evidencias `test-run.log` bajo:

- `TEMP/mitbol-sync-preflight-c42bad4065294702ba14a62444b64ee3/`
- `TEMP/mitbol-sync-preflight-9d68e3107683408c92cfbd1037fb232a/`

Sólo se amplió `tests/test_account_provisioning.py` y esta guía en esta revisión;
no se cambió runtime, migraciones, grants ni configuración de Render.
Los dos bloques PowerShell de preparación de 6.1 pasaron análisis sintáctico;
se evaluó únicamente la definición de la función, sin invocarla, introducir
credenciales o conectar. `git diff --check` correcto.

Actualización del aprovisionamiento del escritor (2026-11-06): **135 pruebas
aprobadas**, dos deprecaciones preexistentes, 59.02 s. Comando:

```powershell
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_admin_writer.py tests/test_auth_phase5.py -q --tb=short
```

PostgreSQL 18 exclusivo en TEMP, detenido al finalizar. Incluye una copia efímera
de ambos SQL Neon, rechazo sin ambas marcas, alta/perfil exacto, rechazo de
repetición, login propio, precio/ID/DDL prohibidos, sesiones, limitador, lector y
rollback de las operaciones junto con auditoría. Evidencia:
`TEMP/mitbol-sync-preflight-3b5a3ad3330046eea8b7c219cc0cd93e/test-run.log`.
Los seis bloques PowerShell de la sección 7 pasaron análisis sintáctico y su
preparación fue evaluada sin conectar ni introducir contraseñas. `--check` del
generador y `git diff --check` correctos. Backend, migraciones, SQL LOCAL y SQL
del lector sin cambios. No se probó el transporte real a Neon ni se ejecutó allí
el aprovisionamiento. Los resultados siguientes corresponden al cierre original
de fase 5, no a una nueva ejecución de toda la suite.

Focalizadas de límites/operador, migraciones y escritor: **177 aprobadas**, dos deprecaciones existentes, 61.03 s; se añadieron después casos adicionales de CLI para la suite completa. Evidencia: `TEMP/mitbol-sync-preflight-30825566b5aa49d79db94f7a165bfc70/test-run.log`.

Suite completa:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra --maxfail=1 --tb=short
```

El ejecutor crea clústeres PG18 exclusivos, limpia variables heredadas, evita .env/Neon/mitbol_dev, aplica revisiones sólo en sus bases temporales y usa roles/cuentas/datos ficticios. Los fixtures prueban login SQL independiente, invariancia comercial y rollback de nueve escrituras, además de todo lo previo.

Cobertura nueva: contadores compartidos bajo concurrencia; cambio de instancias de AuthService/reinicio lógico; reloj de DB y TTL; HMAC sin identificadores claros; normalización y /64; namespaces; capacidad sin evicción; política/secretos inconsistentes 503; fallos de permisos/almacenamiento 503 con menú público operativo; contadores que sobreviven rollback de login; configuración insegura/multiworker; lector sin tablas de límites; writer sin reset de política/ventanas; gates y CORS separados; rechazo de Staff antes de recibir multipart; SQL remoto derivado/guardas; creación/duplicados/rollback del operador; cambios de acceso y sesiones; password fuera de argv/eco; migración/metadata y downgrade/upgrade.

Resultado completo: **1252 pruebas aprobadas, cero fallos, cero errores y cero omitidas**, en 360.43 s, con las dos deprecaciones preexistentes de Starlette/AnyIO. Son 1210 casos anteriores más 42 de fase 5. Ambos clústeres temporales quedaron detenidos. Evidencia: `TEMP/mitbol-sync-preflight-12efb0316b3d4662a1c7363e72754d76/test-run.log`.

`pip check` no detectó incompatibilidades y `git diff --check` terminó correctamente. El ejemplo TypeScript recibió revisión sintáctica con Node 22 y las plantillas se comprueban con --check. No se instaló un compilador/dependencia frontend ni se hicieron pruebas de navegador/Neon/Render reales.

## 11. Decisiones y dependencias pendientes

- Arquitectura de publicación: ya se conocen frontend/backend en onrender.com,
  pero son sitios distintos. Resolver la topología/cookie descrita en sección 5;
  dominio propio sigue pendiente y cloud continúa bloqueado.
- Contrato de proxy, peer real, Host/HTTPS e IP de cliente de Render. No usar confianza universal en forwarded headers ni una IP suministrada libremente por el cliente.
- Perfil de conexión administrativa remoto con TLS/host/base/rol/capacidades comprobados; los pools locales probados no certifican esa topología.
- Secreto estable y política operativa de límites aprobados, presupuesto de conexiones/RAM y controles de borde/monitorización. PostgreSQL evita el bypass por reinicio, no elimina DoS.
- Migraciones/escritor/lector/catálogo y snapshot mitbol-before-auth-migrations
  ya confirmados por el usuario. No repetir verificaciones aprobadas ni exigir
  otro snapshot para el bootstrap; falta autorizar/crear las cuentas de sección 6.
- Funciones con EXECUTE público: limitación residual descrita en 6.4. No bloquea
  el bootstrap; no afirmar que los grants de tablas impiden todo efecto SQL.
- Datos laborales del propietario/empleados y operador autorizado. No hay cuentas reales ni aprovisionamiento público.
- Almacenamiento duradero para imágenes, gestión de recuperación de cuentas y rehash/retención operativa; no se contrata ni se amplía identidad/clientes/pagos aquí.

## 12. Lista obligatoria antes de aprobar apertura real

- [ ] Usuario autoriza por separado escrituras de Neon y despliegue/configuración de Render.
- [ ] Dominio HTTPS/sitio aprobados, CORS/Origin explícitos y cookie real verificada en navegadores soportados.
- [ ] Proxy/IP/HTTPS confiables implementados y probados; Host y cabeceras no falsificables.
- [ ] Revisión cloud posterior que habilite únicamente el perfil validado; no se elude el bloqueo actual.
- [x] 0004/0005 aplicadas/verificadas por el usuario, revisión 0005, catálogo intacto
  y snapshot disponible. No se ensayó una restauración; no se exige otra ahora.
- [x] Writer mínimo y lector independiente verificados por el usuario; credencial
  de operador separada, nunca en el servicio web. Advertencia de funciones en 6.4.
- [ ] Backend PostgreSQL del limitador, clave/política comunes, permisos, TTL, concurrencia y fallos cerrados probados en ensayo remoto autorizado.
- [ ] Cuentas reales creadas por operador con datos legítimos, contraseña segura y permisos correctos; cambios de acceso invalidan sesiones y se auditan.
- [ ] CI completo y revisión del PR aprobados; SHA y despliegue manual acordados; Auto-Deploy Off confirmado.
- [ ] Menú público local→Render preservado, público sin usar writer, salud/latencias y recuperación verificadas.
- [ ] Apertura gradual: autenticación primero; catálogo después; imágenes cerradas hasta dependencia duradera aprobada.
- [ ] Rollback operativo sin borrar identidad/auditoría/contadores y sin ampliar privilegios, con ensayo/evidencias.

La fase termina con mejoras y pruebas locales, propuestas de ejecución remota y bloqueos explícitos. No se realizó ninguna escritura en infraestructura real ni cambio de rama, commit o push.
