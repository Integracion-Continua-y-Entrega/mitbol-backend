# MITBOL — Autenticación administrativa, fase 5

Fecha: 6 de noviembre de 2026, America/Mexico_City. Rama conservada: `design/admin-auth-security`.

**Preparación local, sin despliegue ni cambios reales.** El usuario confirmó que todavía no hay dominio propio aprobado y solicitó mantener cloud bloqueado. Esta revisión conserva el rechazo de `AUTH_ENABLED=true` en cloud y la restricción HTTP loopback. No selecciona dominios, contrata servicios ni habilita acceso administrativo remoto.

Se implementan el limitador PostgreSQL persistente, aprovisionamiento por operador, registro gradual local y separación CORS público/administrativo. Se preparan SQL y procedimientos para revisión posterior. **La revisión puede integrarse/desplegarse con administración apagada; abrirla requiere otra revisión de dominio/proxy/perfil remoto y autorización. No basta cambiar un flag.**

## 1. Bloqueos anteriores y estado al cierre

| Bloqueo | Estado real |
| --- | --- |
| Límites en memoria que se pierden al reiniciar y no coordinan instancias | Resuelto localmente con PostgreSQL, clave estable, política compartida, concurrencia y errores cerrados. No aplicado a Neon. |
| Permisos SQL del almacenamiento de límites | Migración 0005 y ACL explícitas añadidas/probadas en laboratorio. |
| Primera cuenta y cambios de acceso sin herramienta operativa | CLI de operador implementado/probado con datos sintéticos. Ninguna cuenta real creada. |
| Activación que registraba todo el catálogo/imágenes | Gates separados con defaults false y pruebas locales. |
| CORS administrativo que desplazaba orígenes del menú | CORS por ámbito implementado; el menú mantiene sus orígenes independientes. |
| Comprobación de permisos antes de recibir multipart | Adaptada a los contextos de rutas de FastAPI 0.141.1 y probada sin permitir una sola lectura del cuerpo de Staff. |
| Dominio del frontend/API y comportamiento real de cookies | Pendiente por decisión explícita del usuario. No se usa SameSite=None como atajo. |
| Validación del proxy/IP/HTTPS remoto y perfil remoto del escritor | Pendiente de revisión contra la topología real de Render; los guards locales siguen cerrados a acceso remoto. |
| Almacenamiento duradero de imágenes | Pendiente. No se habilitan cargas en Render. |
| Migraciones, escritor y cuentas reales; despliegue | Procedimientos y scripts propuestos; no ejecutados. |

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
| [prepare_admin_writer_neon.sql](../database/prepare_admin_writer_neon.sql), [verify_admin_writer_neon.sql](../database/verify_admin_writer_neon.sql), [preflight_admin_neon.sql](../database/preflight_admin_neon.sql) | Propuestas remotas no ejecutadas. Preparación escribe; verificación/preflight sólo leen catálogos. |
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

Recomendación pendiente de aprobación: frontend/API bajo un dominio propio del mismo sitio y ambos HTTPS; preferir mismo origen con /api mediante infraestructura revisada, o subdominios panel/api del mismo dominio registrable. Subdominios pueden ser same-site pero siguen siendo cross-origin y necesitan CORS explícito. No se selecciona ni compra un dominio ni un proxy/hosting adicional en esta fase. Un preview administrativo remoto debe usar el sitio aprobado, no abrir la cookie de producción a cualquier localhost.

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

## 7. Procedimiento Neon — preparado, NO ejecutado

### Preparación y sólo lectura

1. Aprobar el cambio, versión de código y ventana operativa; confirmar Auto-Deploy off y AUTH_ENABLED/ADMIN_ENABLED false. No habilitar escrituras como parte de esta fase.
2. Verificar backup/recuperación real: disponibilidad/retención de branch, snapshot o PITR según plan, y/o dump protegido fuera del repo. Probar restauración en un entorno autorizado. Crear una rama/snapshot o producir un dump son acciones operativas aparte; no se realizaron ni se asume que el plan las incluye.
3. En una consola de operador independiente, configurar PGHOST del endpoint directo aprobado, PGPORT=5432, PGDATABASE=mitbol_cloud_dev, PGUSER del operador, PGSSLMODE=verify-full, CA y PGCHANNELBINDING=require. Obtener la contraseña de un canal protegido, sin literal en historial/argv/archivos del repo. No reutilizar variables de ese operador en Render.
4. Ejecutar, sólo tras autorizar esa consulta remota:

```powershell
psql -X -w -v ON_ERROR_STOP=1 -f docs/database/preflight_admin_neon.sql
psql -X -w -v ON_ERROR_STOP=1 -f docs/database/verify_render_reader.sql
```

El segundo comando usa un **login independiente del lector**, no SET ROLE dentro del operador. Preflight muestra revisión/schema/owners/memberships/defaults/extensiones/TLS, sin datos personales o hashes. Confirmar la revisión real: se espera 0003 según contexto, no se consultó ahora. Confirmar PostgreSQL 18; si es otra versión, ensayar/revisar compatibilidad y aprobar esa decisión, sin hacer una actualización de servidor automática.

5. Generar/revisar SQL offline con una URL sintácticamente válida ficticia y APP_ENV=test (nunca apuntar a una base real para este paso):

```powershell
# Sesión de revisión sin credenciales reales. La generación --sql no conecta.
$env:APP_ENV = 'test'
$env:DATABASE_URL = 'postgresql+psycopg2://offline.invalid/mitbol_unused'
.\.venv\Scripts\python.exe -B -m alembic upgrade 20261102_0003:20261106_0005 --sql
.\.venv\Scripts\python.exe -B scripts/render_admin_deployment_sql.py --check
```

Revisar siete tablas de 0004, dos tablas/índice de 0005, roles de aplicación y ausencia de alteraciones comerciales/grants de migración. Los objetos ya existentes se conservan. Las ACL remotas propuestas derivan de los GRANT locales probados; no suponen que Neon tenga los mismos defaults/extensiones que el laboratorio.

### Pasos que modificarían Neon — requieren autorización posterior

6. Aplicar las revisiones con operador de migraciones, conexión directa y destino TLS explícito. En una sesión nueva APP_ENV=cloud, EXPECTED_DATABASE=mitbol_cloud_dev, EXPECTED_DATABASE_HOST aprobado y DATABASE_URL del operador cargada de forma protegida:

```powershell
.\.venv\Scripts\python.exe -B -m alembic current
.\.venv\Scripts\python.exe -B -m alembic upgrade 20261106_0005
```

`current` consulta; `upgrade` escribe tablas/índices y versión. No usar el writer web para Alembic ni añadir un comando de migración al arranque de Render.

7. Confirmar esquema/head y volver a verificar el lector: sólo sus ocho tablas, sin lectura de identidad/límites ni secuencias/escritura. Si los defaults expondrían las tablas nuevas, detener y corregir ACL con revisión antes de continuar.
8. Crear el escritor por **SQL revisado**, no mediante un alta administrativa genérica con privilegios Neon amplios. Revisar el archivo propuesto y efectos de REVOKE de PUBLIC, owners, membresías y funciones. El script exige operador neondb_owner, base exacta, TLS, PG18, head 0005 y una marca de ejecución deliberada; la marca no sustituye la autorización del usuario:

```powershell
# Sólo en la sesión psql del operador y tras aprobación real del cambio.
psql -X -w -v ON_ERROR_STOP=1 -c "SET mitbol.approved_admin_write='phase5-reviewed'" -f docs/database/prepare_admin_writer_neon.sql
```

No contiene contraseña, crea LOGIN con PASSWORD NULL y no sobrescribe un escritor existente. Configurar su contraseña después mediante `\password mitbol_admin_writer` en psql interactivo protegido. Neon documenta diferencias entre roles creados por SQL y altas administrativas de Console/API/CLI; véase [Manage roles](https://neon.com/docs/manage/roles). No conceder neon_superuser/membresías ni ownership al escritor.

9. Abrir un login nuevo como writer y verificar, sólo leyendo catálogos:

```powershell
psql -X -w -v ON_ERROR_STOP=1 -f docs/database/verify_admin_writer_neon.sql
```

El verificador rechaza grants efectivos excesivos, incluidos PUBLIC, tablas/columnas futuras, grant option, defaults y rutinas propias accesibles no revisadas. Si extensiones de Neon hacen fallar esa política, revisar la dependencia concreta; no quitar comprobaciones para obtener un OK.

10. Ejecutar las pruebas activas positivas/negativas en una rama/base remota **de ensayo autorizada** con filas sintéticas y rollback. Es otra operación de escritura, aunque haga rollback; las secuencias pueden avanzar. Usar como referencia `test_admin_writer.py` y `test_auth_phase5.py`: descripción/disponibilidad sí; precio/IDs/estados/DDL/migraciones/escalada no; sesiones/auditoría/límites sí; lector no escribe/lee seguridad. No apuntar el ejecutor local automáticamente a Neon ni usar datos comerciales reales para ensayar.
11. Aprovisionar el primer Admin y luego empleados con la CLI, operador/roles/datos legítimos aprobados. Esto escribe identidad/auditoría; no se hizo aquí. Confirmar activación, Argon2id y permisos. El writer web no adquiere permisos de alta/gestión de roles por haber añadido la CLI.

Los SQL remotos son propuestas guardadas, no scripts certificados contra una instancia Neon real. Las guardas de laboratorio siguen limitando sus archivos LOCAL al PostgreSQL temporal. No ejecutar preparaciones por sustituciones improvisadas de nombres/roles.

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

Antes/después de cada paso autorizado: comparar revisión de esquema, perfil efectivo del lector/writer, menú y campos comerciales, y revisar auditoría sin valores secretos. Diferenciar salud pública de preparación administrativa: / no valida el writer/limitador; un 200 del menú no aprueba login. No se añade un health endpoint que exponga configuración o credenciales.

Si falla la preparación: no habilitar AUTH_ENABLED. Si falla una futura apertura: cerrar AUTH_CATALOG_ENABLED y AUTH_ENABLED, mantener ADMIN_ENABLED=false e imágenes false; desplegar la configuración/código público conocido, verificar menú y reader, y retirar/rotar sólo el secreto administrativo cuando corresponda. No cambiar DATABASE_URL al owner para resolver un error.

No hacer downgrade automático de 0004/0005 después de crear cuentas/contadores. Se puede volver al código público anterior manteniendo las tablas/grants sin usar. 0004 downgrade elimina identidad/auditoría; 0005 elimina protección persistente y contadores. Cualquier restauración requiere evaluar pérdida de auditoría, contraseñas/versiones/sesiones y revocación posterior, además del respaldo aprobado.

No borrar contadores en una reversión/reinicio normal: conservar límites. Un commit de alta/operación incierto se investiga con cuenta/estado y referencia de auditoría antes de repetir; duplicados no se solucionan desactivando restricciones. Conservar evidencias y no registrar passwords/DSN/tokens en el diagnóstico.

## 10. Pruebas ejecutadas

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

- Dominio y arquitectura de publicación: **usuario decidió dejarlo pendiente y mantener cloud bloqueado**. No hay dominios reales inventados en configuración.
- Contrato de proxy, peer real, Host/HTTPS e IP de cliente de Render. No usar confianza universal en forwarded headers ni una IP suministrada libremente por el cliente.
- Perfil de conexión administrativa remoto con TLS/host/base/rol/capacidades comprobados; los pools locales probados no certifican esa topología.
- Secreto estable y política operativa de límites aprobados, presupuesto de conexiones/RAM y controles de borde/monitorización. PostgreSQL evita el bypass por reinicio, no elimina DoS.
- Backup/restauración disponible en el plan real, versión PostgreSQL y extensiones/defaults/memberships efectivos de Neon; requieren revisión antes de ejecutar plantillas.
- Datos laborales del propietario/empleados y operador autorizado. No hay cuentas reales ni aprovisionamiento público.
- Almacenamiento duradero para imágenes, gestión de recuperación de cuentas y rehash/retención operativa; no se contrata ni se amplía identidad/clientes/pagos aquí.

## 12. Lista obligatoria antes de aprobar apertura real

- [ ] Usuario autoriza por separado escrituras de Neon y despliegue/configuración de Render.
- [ ] Dominio HTTPS/sitio aprobados, CORS/Origin explícitos y cookie real verificada en navegadores soportados.
- [ ] Proxy/IP/HTTPS confiables implementados y probados; Host y cabeceras no falsificables.
- [ ] Revisión cloud posterior que habilite únicamente el perfil validado; no se elude el bloqueo actual.
- [ ] Backup/restauración y revisión Alembic/versión del servidor comprobados; 0004 y 0005 revisadas/aplicadas con operador, sin cambios comerciales.
- [ ] Writer mínimo y lector independiente verificados con logins nuevos; sin ownership/membresías/grants amplios ni owner en web.
- [ ] Backend PostgreSQL del limitador, clave/política comunes, permisos, TTL, concurrencia y fallos cerrados probados en ensayo remoto autorizado.
- [ ] Cuentas reales creadas por operador con datos legítimos, contraseña segura y permisos correctos; cambios de acceso invalidan sesiones y se auditan.
- [ ] CI completo y revisión del PR aprobados; SHA y despliegue manual acordados; Auto-Deploy Off confirmado.
- [ ] Menú público local→Render preservado, público sin usar writer, salud/latencias y recuperación verificadas.
- [ ] Apertura gradual: autenticación primero; catálogo después; imágenes cerradas hasta dependencia duradera aprobada.
- [ ] Rollback operativo sin borrar identidad/auditoría/contadores y sin ampliar privilegios, con ensayo/evidencias.

La fase termina con mejoras y pruebas locales, propuestas de ejecución remota y bloqueos explícitos. No se realizó ninguna escritura en infraestructura real ni cambio de rama, commit o push.
