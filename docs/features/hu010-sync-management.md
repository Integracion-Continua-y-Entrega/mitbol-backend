# HU-010 — Administración de sincronizaciones

## Backend puente para 0007 y 0008

La rama `fix/backend-schema-0007-0008-compat` parte de `origin/develop`
(`afbf671cfcc122854e811b7d23630ff1cd733606`). Es una publicación intermedia,
separada del PR #15 de categorías. No se ha podido verificar el SHA que Render
tiene desplegado: el SHA anterior identifica la base del cambio en Git.

El verificador administrativo anterior comparaba la revisión con `0007` y
rechazaba `0008` con `WRITER_QUEUE_SCHEMA`. También había controles exclusivos
de revisión en HU-010, HU-008, descuentos y el trabajador, además de matrices
de permisos que no contemplaban el delta aditivo de categorías.

El puente conserva los modelos, rutas, consultas y sincronización de `0007`.
No incluye la migración `0008`, endpoints de categorías ni solicitudes nuevas
a Loyverse. `app/db/schema_compatibility.py` comprueba explícitamente `0007` o
`0008`, columnas/tipos/nulabilidad, ausencia de RLS, claves, restricciones
validadas e índices del contrato. Inspecciona los catálogos PostgreSQL; no
requiere lectura de filas de `Categories`. Una revisión desconocida o una
extensión incompleta se rechazan. El pool administrativo cloud, el trabajador
cloud y la cola utilizan este control. Los flujos locales históricos de HU-008
y descuentos conservan sus listas explícitas de revisiones anteriores para
mantener la compatibilidad de las herramientas existentes; no habilitan esas
revisiones en los perfiles operativos cloud del puente.

El CLI de carga inicial cloud también exige el esquema completo `0007` o
`0008`. Conserva su requisito de catálogo vacío y la aprobación del manifiesto;
una revisión histórica aceptada por una herramienta local no autoriza una
carga cloud. Su preflight informa el rechazo antes de contactar al proveedor.

Se admiten tres estados operativos:

| Estado | Administrador | Trabajador | Lector |
| --- | --- | --- | --- |
| `0007` | Perfil mínimo existente. | Perfil comercial/cola existente, cuatro secuencias. | Catálogo existente, sin escrituras. |
| `0008`, antes del delta | Mismo perfil, sin exigir `category_id`. | El SELECT de Products a nivel tabla abarca automáticamente la columna nueva; sin escritura en categorías. | Sin exigir SELECT de Categories. |
| `0008`, después del delta revisado | Admite únicamente el SELECT adicional de Products.category_id. | Admite exactamente Categories, INSERT/UPDATE comercial de Products.category_id y USAGE de su quinta secuencia. | Admite únicamente category_id/name/color/is_active de Categories. |

Los verificadores siguen rechazando permisos adicionales, DDL, membresías,
grant options y escrituras en campos locales protegidos. No aplican grants ni
crean roles. Los permisos de categorías permiten la transición, pero las
consultas y escrituras comerciales del puente no los utilizan ni alteran
relaciones de categorías existentes. Autenticación, sesiones, roles, Origin,
CSRF y almacenamiento R2 se conservan.

### Verificación aislada del puente

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader tests/test_schema_bridge.py -q
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q
.\.venv\Scripts\python.exe -B scripts/render_admin_deployment_sql.py --check
git diff --check
```

El ejecutor crea PostgreSQL 18 local desechable y sanea el entorno, sin cargar
`.env` privados. La matriz reproduce `0007`, `0008` antes del delta y `0008`
después del delta con LOGIN reales y permisos limitados. El contrato SQL de
`tests/fixtures/schema_0008_bridge.sql` reproduce el upgrade revisado de #15 y
se protege con nombre aleatorio de base, operador de pruebas y loopback; no
es una migración operativa. Se comprueban menú, sesiones, roles, Origin/CSRF,
solicitudes/historial y consumo durable, preservación de categorías,
subida/lectura/borrado R2 simulados, rechazo de revisiones/esquemas inválidos
y de permisos adicionales en los tres roles. Sólo el transporte cloud se
adapta a loopback. R2 y Loyverse utilizan proveedores simulados.

La regresión local final del puente aprobó **342 pruebas** con PostgreSQL
18.2: matriz de esquemas/permisos, CLI y carga inicial, descuentos,
destacados, autenticación cloud, preparación 0007, HU-010 y R2. Incluye todos
los casos afectados detectados por la primera ejecución completa. El SQL
generado, `pip check` y `git diff --check` pasaron. Dos avisos de deprecación
proceden del TestClient y sus dependencias. La CI del PR ejecuta nuevamente
la suite completa con PostgreSQL 18 en Linux.

### Orden del despliegue posterior, sujeto a autorización

1. Revisar el PR del puente, exigir CI aprobado y fusionarlo primero a
   `develop`. Mantener el PR #15 abierto y Auto-Deploy desactivado.
2. En Render confirmar la rama y el SHA fusionado; desplegar manualmente ese
   SHA mientras Neon continúa en `20261114_0007`. No añadir migraciones a
   build/start/pre-deploy ni cambiar credenciales o controles de seguridad.
3. Comprobar salud y menú con acceso real a PostgreSQL, inicio/cierre de
   sesión, permisos y CSRF, estado/historial de HU-010 y fotografías R2 con
   recursos de prueba autorizados. No iniciar sincronizaciones comerciales
   para comprobar el estado de la cola.
4. Con autorización independiente para producción, confirmar proyecto,
   Branch ID/endpoint directo, TLS, base y revisión exacta `0007`. Crear un
   respaldo nuevo `pg_dump -Fc` fuera del repositorio; verificar SHA-256,
   `pg_restore --list` y restauración aislada con PostgreSQL 18 compatible.
   Conservar el respaldo anterior y registrar filas, relaciones, secuencias
   y ACL. No continuar si falla la recuperación.
5. Desde el código revisado de #15, aplicar únicamente `20261115_0008` y
   confirmar COMMIT. Aplicar por separado el delta revisado
   `docs/database/update_catalog_categories.sql`, con sus comprobaciones y
   aprobación explícita; no volver a ejecutar migraciones ni recrear roles.
6. Confirmar revisión `0008`, conservación de datos y secuencias, esquema y
   verificadores con nuevas conexiones de los tres roles. Verificar que el
   mismo backend puente mantiene menú, autenticación, administración y R2.
   Antes de reactivar el trabajador, renovar su archivo privado de aprobación
   para la revisión efectiva `0008`; una aprobación que diga `0007` se rechaza.
7. Actualizar la rama de #15 desde `develop`, revisar los conflictos de
   validadores, matrices SQL y orquestación y ejecutar nuevamente toda la CI.
   El código completo de categorías debe seguir exigiendo su esquema `0008`;
   no copiar sin revisión la tolerancia transitoria del puente.
8. Con autorización, fusionar #15 y desplegar manualmente el backend completo
   en Render, verificando el SHA desplegado y Auto-Deploy desactivado.
9. Comprobar categorías, filtros, menú, administración, cola y fotografías.
   Ejecutar cualquier sincronización comercial sólo con su autorización y
   configuración operativa independientes.

Después de migrar la base a `0008`, la versión de recuperación del backend es
el puente validado. No volver al backend anterior incompatible ni ejecutar
downgrades destructivos para revertir un despliegue. Ante errores de base,
detener operaciones, conservar evidencias y evaluar recuperación desde el
respaldo en un destino aislado antes de intervenir la base compartida.

Estado al 14 de noviembre de 2026: implementado y preparado para el perfil cloud
`restaurant`, deshabilitado por defecto. Migraciones y permisos sólo se ensayan
en PostgreSQL temporal. No se migró Neon, modificó Render, sincronizó Loyverse
real ni se crearon webhooks o servicios programados. HU-008 y R2 se conservan.

## Implementado y alcance

- Estado administrativo, petición manual asíncrona, consulta individual e
  historial paginado, usando sesiones individuales, permisos, Origin y CSRF.
- Cola durable en PostgreSQL, compartida por solicitudes manuales y polling
  programado. Reiniciar FastAPI no elimina trabajos aceptados.
- Consumidor externo de un trabajo por invocación. Reutiliza el orquestador,
  validación, UPSERT y bloqueos HU-008. FastAPI no usa `BackgroundTasks` para
  descargar/escribir el catálogo ni mantiene peticiones abiertas durante ello.
- Resultados sanitizados, tres intentos como máximo, recuperación de procesos
  interrumpidos y confirmación atómica del catálogo y resultado exitoso.
- Primitiva de firma OAuth2 preparada y probada, **sin recepción pública**.

La migración `20261114_0007`, descendiente de `20261111_0006`, añade únicamente
`public."SyncRuns"` y sus restricciones/índices. No altera tablas, filas,
credenciales o grants existentes. No se aplica automáticamente al arrancar.
Su downgrade explícito elimina la cola y su historial; no se ejecuta contra
bases compartidas. `SyncBase` mantiene separados los metadatos anteriores.

Cada fila almacena UUID, origen (`manual`, `scheduled`; `webhook` reservado),
tienda, estado, fechas, solicitante mediante ID histórico, intentos, token de
intento, contadores/huella del resultado y un código de error conocido. No
almacena cookies, contraseñas, tokens Loyverse, URLs privadas ni cuerpos HTTP.

## Activación local y separación de credenciales

La API requiere `AUTH_SYNC_ENABLED=true`, `AUTH_ENABLED=true` y
`AUTH_CATALOG_ENABLED=true`. Exige `EXPECTED_DATABASE`, `EXPECTED_DATABASE_HOST`
y `LOYVERSE_STORE_ID` explícitos; el host/base deben corresponder a
`AUTH_DATABASE_URL`. Se mantienen las restricciones locales de autenticación:
loopback y base `mitbol_auth_local` o `mitbol_test_auth_<32 hex>`.

El perfil `auth_catalog=True` se usa explícitamente en el trabajador HU-010 para
admitir esas mismas bases locales. El servicio y CLI HU-008 siguen usando su
lista anterior por defecto. Todos los perfiles conservan verificación de
conexión real, identidad, autorización del alcance y bloqueo comercial.

El proceso web solo necesita consultar y encolar; **no necesita el token de
Loyverse ni credenciales del operador comercial**. Su sesión autentica al
administrador y confirma cola/auditoría antes de devolver `202`.

El proceso consumidor necesita las variables operativas HU-008: `APP_ENV`,
`ADMIN_ENABLED=false`, `DATABASE_URL`, `EXPECTED_DATABASE`,
`EXPECTED_DATABASE_HOST`, `LOYVERSE_STORE_ID`, `LOYVERSE_ACCESS_TOKEN` y
`MITBOL_RECURRENT_SYNC_APPROVAL`. Esta última autoriza exactamente operación,
host, base, usuario, tienda y revisión `20261114_0007`. No se guarda en la cola.
API y consumidor deben utilizar **la misma base y tienda**.

El CLI solo utiliza variables del proceso; no carga `.env`. La API conserva la
política `Settings` existente: `.env` local cuando corresponde, nunca en test o
cloud. Las pruebas no leen el `.env` privado ni lo modifican.

**Cloud opt-in, todavía sin activar:** `restaurant` admite `AUTH_SYNC_ENABLED=true`
con sesión/catálogo habilitados, destino TLS y tienda explícitos. Se mantiene
`ADMIN_ENABLED=false`: ninguna clave compartida autoriza administración remota.
`frontend_dev` conserva HU-010 cerrado. Los módulos siguen en false por defecto.
El pool administrativo reutiliza el verificador SQL estricto actualizado; al
existir `SyncRuns`, exige 0007 y las concesiones mínimas de la cola. Con permisos
incompletos o adicionales falla cerrado. El trabajador cloud reutiliza su propio
verificador estricto antes de descargar o escribir; requiere el LOGIN separado
`mitbol_catalog_worker`, sin identidad owner ni roles web.

Permisos PostgreSQL que deberá contemplar esa revisión:

| Identidad | Permisos adicionales HU-010 |
| --- | --- |
| Escritor administrativo | SELECT de `SyncRuns`; INSERT únicamente de `run_id`, `store_id`, `source`, `requested_by`. SELECT de `alembic_version.version_num` y de `synced_at` en modificadores/complementos. Sin UPDATE/DELETE de resultados. |
| Trabajador separado | SELECT de `SyncRuns`; INSERT de los cuatro campos de solicitud; UPDATE sólo de estado, tiempos operativos, intentos, token de intento, resultado y código de error. Permisos HU-008 por columnas comerciales y USAGE de cuatro secuencias. Sin DDL, grants, autenticación, Discounts ni escritura de fotos/etiquetas o configuraciones locales. |
| Lector del menú | Ningún permiso sobre `SyncRuns`. |

El migrador requiere sus permisos DDL habituales. La migración no crea roles ni
otorga privilegios, y las pruebas verifican el rechazo de un lector sin INSERT.

## Contratos administrativos para React

Solo el rol de aplicación `admin` recibe `sync:read` y `sync:execute`. `staff`
no puede consultar ni ejecutar estas operaciones. Las rutas no admiten
`X-Admin-Key` como sustituto de la sesión individual.
La ampliación de permisos cambia el contexto CSRF del administrador; las
sesiones emitidas con la política anterior pueden requerir un nuevo login.

| Método y ruta | Permiso | Resultado |
| --- | --- | --- |
| `GET /api/admin/sync/status` | `sync:read` | Última fecha comercial, trabajo activo, último resultado/éxito y bloqueos. |
| `POST /api/admin/sync/runs` | `sync:read`, `sync:execute`, CSRF | `202`, encola o devuelve el trabajo activo; cuerpo `{}`. |
| `GET /api/admin/sync/runs?limit=20&offset=0` | `sync:read` | `runs` y `next_offset`; límite 1–100, offset 0–10000. |
| `GET /api/admin/sync/runs/{run_id}` | `sync:read` | Estado, fechas, intentos, resultado/error; `404` si no existe. |

Todas las respuestas administrativas llevan `Cache-Control: no-store`.
Si el módulo está deshabilitado, las rutas no se registran y responden `404`;
React debe ocultar sus controles en ese caso. `503` indica cola/esquema/permisos
no preparados, `401` sesión inválida, `403` permisos/CSRF y `422` entrada inválida.
Los contratos completos están en `app/schemas/sync.py` y en OpenAPI cuando se
habilita el módulo. No se aceptan destinos, tokens, tiendas o aprobaciones
proporcionados por el navegador. Entradas inválidas responden sin reflejar sus
valores.
Una contención breve al encolar o una tienda incompatible responde `409`, sin
esperar a que termine otra sincronización.

El botón «Sincronizar ahora» envía `POST` JSON `{}`, `credentials: 'include'`
y `X-CSRF-Token` vigente, desde un Origin autorizado. La respuesta tiene:

```json
{
  "accepted": true,
  "created": true,
  "run": {
    "run_id": "e3bc623f-ea77-41d0-8e50-d4bb0a147ad5",
    "source": "manual",
    "status": "queued",
    "created_at": "2026-11-14T12:00:00Z",
    "started_at": null,
    "finished_at": null,
    "available_at": "2026-11-14T12:00:00Z",
    "attempts": 0,
    "result": null,
    "error": null
  }
}
```

`created=false` significa que se reutilizó el trabajo activo: no se añade otro.
`Location` indica la consulta individual; React también puede construirla con
el UUID del cuerpo sin depender de exponer esa cabecera por CORS. Consultar su
estado cada 2–5 segundos mientras está pendiente. `202` confirma persistencia en
cola, no la finalización ni que exista ya un consumidor ejecutándose.

Estados: `queued`, `running`, `retry_wait`, `succeeded`, `failed`. El resultado
exitoso contiene `changes` de HU-008 por entidad (`new`, `updated`, `unchanged`,
`retired`, `not_returned`), asociaciones insertadas/retiradas, `catalog_sha256`
y `snapshot_at`. El error contiene exclusivamente código y mensaje conocidos.
Una ejecución fallida permite volver a pulsar el botón y genera otra fila;
no se destruye el historial anterior.

El estado incluye `catalog_locked`, `worker_connected`, `is_running` y
`recovery_pending`. Este último distingue un `running` interrumpido que deberá
recuperar el siguiente consumidor. `last_successful_run.finished_at` sirve para
mostrar la última ejecución gestionada confirmada; `last_catalog_synced_at`
también observa cambios de fecha hechos mediante el CLI HU-008 independiente.

El historial informa `history_scope="managed_runs"`: solo registra operaciones
de esta cola. Las ejecuciones del CLI histórico conservan compatibilidad, pero
sus contadores anteriores no se pueden reconstruir. Para futuras operaciones
con historial se debe usar el consumidor HU-010.

## Consumo, concurrencia y recuperación

Plantillas operativas; sustituir los marcadores y preparar un proceso aislado
antes de usarlas. No se ejecutaron contra servicios reales:

```text
python -m app.cli.sync_catalog_jobs --expected-database BASE --expected-host HOST --store-id TIENDA --dry-run
python -m app.cli.sync_catalog_jobs --expected-database BASE --expected-host HOST --store-id TIENDA --execute
python -m app.cli.sync_catalog_jobs --expected-database BASE --expected-host HOST --store-id TIENDA --execute --enqueue-scheduled
```

La simulación genera la misma plantilla de autorización HU-008, inicialmente
`approved=false`, sin encolar, escribir historial o consumir secuencias. Revisar
el alcance y configurar su autorización explícita en el proceso consumidor antes
de ejecutar. Sin `--dry-run` o `--execute`, el comando rechaza la operación.

La segunda invocación consume un trabajo; la tercera encola/coalesce polling y
consume un trabajo disponible. Cada proceso termina: no inicia un planificador.
Utiliza códigos 0 éxito/idle, 2 validación, 3 bloqueo, 4 proveedor/reintento,
5 PostgreSQL, 6 fallo inesperado/recuperación agotada.

Un bloqueo corto serializa encolados y un índice único parcial impide más de un
trabajo activo por base. Otro bloqueo PostgreSQL de sesión admite un consumidor.
El bloqueo comercial HU-008 protege la descarga y escritura frente a cualquier
otro sincronizador. Una colisión con el CLI histórico conserva la cola sin
consumir intentos. Consultar estado no toma esos bloqueos.

El consumidor confirma `running` antes de descargar. Si muere, PostgreSQL libera
su bloqueo de sesión y la próxima invocación recupera la fila. Cada intento
obtiene un token nuevo: un intento antiguo no puede confirmar ni catálogo ni
resultado. El éxito se escribe mediante el callback transaccional HU-008 y se
confirma con el catálogo. Ante commit incierto se consulta la fila antes de
registrar un fallo; si PostgreSQL confirmó éxito, no se sobrescribe.

Los fallos temporales se reintentan en invocaciones posteriores, con espera de
30 y 60 segundos y tres intentos como máximo, incluidos reinicios interrumpidos.
No se duerme dentro de una petición HTTP. Errores de respuesta inválida,
autorización o configuración terminan sin reintentos automáticos. Un consumidor
debe volver a ejecutarse para recuperar trabajos: desplegar solo la API no basta.

La fuente comercial sigue siendo la consulta actual de Loyverse. El UPSERT no
sobrescribe descripciones locales, disponibilidad local, fotos R2, etiquetas,
destacados o novedades. Solo procesa las bajas explícitas ya admitidas. No se
añadieron categorías ni nuevas reglas de descuentos; HU-009 únicamente admite
la revisión aditiva nueva para conservar compatibilidad.

## Webhooks: preparado y bloqueado por seguridad

La [referencia oficial de Loyverse](https://developer.loyverse.com/docs/#section/Webhooks-overview)
indica que `items.update` notifica altas, cambios y bajas. Las notificaciones
creadas con PAT no llevan firma; las creadas con OAuth2 sí. La firma es HMAC-SHA1
del cuerpo original, en hexadecimal minúsculo, usando Client Secret; se compara
en tiempo constante.

`loyverse_webhook_signature.py` prepara únicamente esa verificación. No hay
router público, opción para aceptar PAT sin firma, registro real de suscripciones,
inbox, deduplicación o procesamiento de eventos. Enviar una firma a
`/api/webhooks/loyverse` devuelve `404`; el origen `webhook` de la cola se rechaza.

Para activar la vía casi en tiempo real faltan:

1. Autorizar una aplicación OAuth2, callback HTTPS con validación de `state`,
   permisos de lectura necesarios y custodia/renovación segura de tokens.
2. Vincular comercio y tienda esperados a esa autorización y verificar su acceso.
   Registrar después la suscripción mediante OAuth2, nunca mediante PAT.
3. Implementar recepción acotada que verifique bytes originales antes de parsear
   y valide comercio, evento y versión; no registrar cuerpo, firma o secreto.
4. Preparar inbox durable con deduplicación, protección frente a replay y una
   actualización posterior garantizada si llega otro evento mientras se procesa
   el trabajo activo. La coalescencia manual actual no sustituye esa garantía.
5. Consumir notificaciones como señales: consultar siempre el catálogo vigente
   bajo bloqueo HU-008, evitando aplicar datos antiguos de eventos fuera de orden.
6. Probar entrega repetida/fuera de orden, recuperación del inbox y operación
   real autorizada antes de habilitar el endpoint. Actualmente no se declara
   probado ni habilitado ese flujo.

## Alternativa periódica y preparación de Render

Como punto inicial se propone polling cada cinco minutos, ajustado según tamaño,
duración y consumo compartido. El cliente usa páginas de 250; una ejecución sin
reintentos necesita aproximadamente `1 + max(1, ceil(items/250)) +
max(1, ceil(modifiers/250))` solicitudes. Loyverse publica un límite de **300
solicitudes por 300 segundos por cuenta**; hay que presupuestar también otras
integraciones y los reintentos. [Referencia oficial](https://developer.loyverse.com/docs/#section/API-rate-limits).

Un futuro Render Cron Job podría invocar el tercer comando. Render documenta
horarios UTC y una ejecución activa por servicio; el bloqueo PostgreSQL coordina
además procesos externos/manuales. [Render Cron Jobs](https://render.com/docs/cronjobs).
Para menor latencia manual o webhooks futuros se necesitaría un consumidor
supervisado que consulte la cola con mayor frecuencia; no se creó ese servicio.

Pendiente antes de Neon/Render: ensayar en una rama aislada los perfiles SQL
probados localmente, autorizar migraciones/grants, configurar secretos separados y aprobar alcance del operador,
provisionar consumidor, elegir frecuencia/alertas/retención y validar una
simulación seguida de una ejecución autorizada. Vigilar `retry_wait`, `failed`,
colas sin consumidor y `recovery_pending`. Los reintentos siempre consultan datos
actuales y conservan UPSERT/bloqueos; no añaden otro origen de datos comerciales.

React queda pendiente: botón, polling, fecha, resultados e historial usando los
contratos anteriores. No se modificó el frontend.

## Preparación Neon 0005 → 0007 (sin ejecución real)

El operador confirmó `20261106_0005` y ausencia de UPDATE de `is_featured` e
`is_new` en la base auditada `mitbol_cloud_dev`, rama `production`. La auditoría
con `mitbol_render_reader` comprobó el mismo endpoint conocido, PostgreSQL 18.7,
TLS verificado, ausencia de `Discounts`/`SyncRuns` y permisos efectivos. Ese lector
no puede leer `alembic_version`: la revisión procede de la consulta del operador,
no de una consulta nueva del agente. Antes de escribir se debe volver a validar
en Console la correspondencia proyecto/rama/endpoint y leer la revisión con el
operador; el nombre de rama no se deduce del nombre de la base ni de SQL.

No se usa `C:\mitbol-secrets\neon-audit.env` para migraciones ni respaldos:
su autorización es exclusivamente de lectura y su rol no puede respaldar todas
las tablas. Tampoco se modifica el `.env` local. Los pasos siguientes son una
receta pendiente de autorización, no operaciones ejecutadas.

### Artefactos y permisos

| Archivo en `docs/database/` | Uso |
| --- | --- |
| `update_admin_writer_highlights.sql` | Delta de las dos columnas locales; admite 0005/0006/0007 y conserva su aprobación/dirección explícitas. |
| `update_admin_writer_discounts.sql` | Sólo SELECT de Discounts después de 0006 o 0007. |
| `update_admin_writer_sync.sql` | Delta HU-010 del escritor existente, sólo en 0007. Reejecutable; no recrea roles. |
| `prepare_admin_writer_local.sql` / `prepare_admin_writer_neon.sql` | Preparación de escritor NUEVO; rechaza un rol existente. No usarlos para actualizar el escritor de producción. |
| `verify_admin_writer_local.sql` / `verify_admin_writer_neon.sql` | Perfil estricto por columnas; conserva 0005/0006 y exige el perfil ampliado cuando existe SyncRuns. |
| `prepare_catalog_worker.sql` | Crea `mitbol_catalog_worker` sin contraseña utilizable; no sobrescribe un rol existente ni cambia PUBLIC/lector. |
| `verify_catalog_worker.sql` | Verifica con LOGIN real identidad, esquema, columnas/secuencias, ausencia de ownership/membresías, defaults y privilegios adicionales. |

El trabajador necesita INSERT comercial de los campos usados por `SyncService`
y UPDATE sólo de nombres, precios, orden y estado/fechas comerciales. INSERT de
`ProductVariants.is_available` inicializa variantes nuevas; no recibe UPDATE de
esa preferencia local. No puede editar descripciones, destacados/novedades,
fotografías o etiquetas, reasignar padres o cambiar IDs originales. Dispone de
USAGE de las cuatro secuencias comerciales, sin SELECT/UPDATE/setval. La cola
concede INSERT de cuatro columnas y UPDATE de ocho columnas operativas; nunca
DELETE ni cambios de tienda, origen, solicitante o fecha de creación.

`catalog_safety` y `inspect_queue` verifican esas columnas, sin exigir DML de
tabla completa. El trabajador cloud reutiliza el DO de `verify_catalog_worker`
antes de contactar Loyverse; el pool web reutiliza su verificador propio.
Los SQL conservan guards de identidad, PG18, revisión y aprobación; una marca
SQL de TLS no sustituye la validación externa efectiva del cliente.

### Respaldo y comprobación de recuperación

1. Tras autorización, confirmar en Console `production`, su endpoint DIRECTO,
   base y operador de respaldo. Registrar el historial/PITR realmente disponible
   y conservar el snapshot histórico; no asumir que su antigüedad o botones de
   Console prueban una restauración actual. Coordinar pausa de escrituras si se
   exige que el dump y las referencias de R2 representen el mismo punto.
2. Usar una terminal privada nueva, sin transcripción ni overrides `PGSERVICE`,
   `PGHOSTADDR` o `PGOPTIONS`. Configurar host directo, puerto, base y usuario
   aprobados mediante `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`; `PGSSLMODE=verify-full`,
   `PGSSLROOTCERT` con la CA válida y `PGCHANNELBINDING=require`. Usar `PGPASSFILE`
   privado con ACL restringida para la contraseña, nunca una URL en argumentos.
   El archivo de respaldo debe quedar fuera del repositorio en almacenamiento
   protegido/cifrado; contiene datos administrativos sensibles.
3. Con herramientas PostgreSQL 18, ejecutar únicamente tras autorizar el respaldo:

```powershell
$MitbolDump = 'C:\mitbol-secrets\backups\mitbol-before-0007.dump'
pg_dump --no-password --format=custom --file $MitbolDump
if ($LASTEXITCODE -ne 0) { throw 'Respaldo incompleto; detener.' }
Get-FileHash -Algorithm SHA256 -LiteralPath $MitbolDump
pg_restore --list $MitbolDump
if ($LASTEXITCODE -ne 0) { throw 'Archivo de respaldo ilegible; detener.' }
```

Registrar por separado roles, membresías, ownership, ACL y defaults sin hashes de
contraseñas. `pg_dump` no incluye roles globales. `pg_dumpall --roles-only
--no-role-passwords` es una opción si el servicio permite esa lectura; no intentar
elevar privilegios ni restaurar indiscriminadamente roles gestionados de Neon.
El dump guarda referencias de fotografías, no objetos R2.

4. Autorizar y crear en Console la rama `mitbol-0007-rehearsal` desde el estado
   respaldado. Registrar su endpoint directo DIFERENTE del de production y
   seleccionar esa rama explícitamente. No conectar Render a ella ni arrancar
   sincronizaciones o escrituras R2 reales.
5. Para comprobar el dump, usar ese host de ensayo y crear una base VACÍA separada:

```powershell
# PGHOST ya corresponde exclusivamente a la rama de ensayo confirmada.
createdb --no-password --owner=neondb_owner mitbol_restore_check_0005
if ($LASTEXITCODE -ne 0) { throw 'No se creó el destino vacío; detener.' }
pg_restore --no-password --exit-on-error --single-transaction --dbname=mitbol_restore_check_0005 $MitbolDump
if ($LASTEXITCODE -ne 0) { throw 'Restauración no validada; detener.' }
```

Comparar filas/huellas, secuencias, esquema, ACL y revisión 0005 con el baseline.
`--list` sólo valida lectura del archivo; no acredita esta recuperación. No usar
`--clean`, no restaurar sobre la base utilizada por Render y no omitir fallos.

### Ensayo controlado de las revisiones y permisos

En otro proceso privado, preparar `APP_ENV=cloud`, `DATABASE_URL` de OPERADOR
exclusivamente del ensayo con TLS verify-full/channel binding, `EXPECTED_DATABASE=mitbol_cloud_dev`,
`EXPECTED_DATABASE_HOST` igual al host directo de esa rama y
`MITBOL_NEON_REHEARSAL_BRANCH=mitbol-0007-rehearsal`. La credencial se obtiene por
canal privado; no reutilizar el archivo de auditoría ni pegar la URL en comandos.

Esta función sólo prepara el ensayo: rechaza el endpoint conocido de production,
pooler, identidad distinta del operador y cualquier revisión anterior inesperada.
La rama y su correspondencia con el host requieren confirmación en Console.
No es un ejecutor para actualizar production.

```powershell
function Invoke-MitbolRehearsalRevision {
    param([string]$Previous, [string]$Next)
    @'
import os, sys
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from app.db.connection import CloudDatabaseTarget, create_database_engine
engine = None
try:
    previous, following = sys.argv[1:]
    assert (previous,following) in {('20261106_0005','20261111_0006'),('20261111_0006','20261114_0007')}
    assert os.environ['APP_ENV']=='cloud'
    assert os.environ['MITBOL_NEON_REHEARSAL_BRANCH']=='mitbol-0007-rehearsal'
    host = os.environ['EXPECTED_DATABASE_HOST']
    assert host.endswith('.neon.tech') and '-pooler.' not in host
    assert not host.startswith('ep-green-hill-bddfbmgh.')
    target = CloudDatabaseTarget('mitbol_cloud_dev',host)
    assert os.environ['EXPECTED_DATABASE']==target.expected_database
    engine = create_database_engine(os.environ['DATABASE_URL'],target=target)
    with engine.begin() as db:
        target.validate_connection(db)
        assert db.exec_driver_sql('SELECT current_user,session_user').one()==('neondb_owner','neondb_owner')
        assert int(db.exec_driver_sql('SHOW server_version_num').scalar_one())//10000==18
        assert db.exec_driver_sql('SHOW default_transaction_read_only').scalar_one()=='off'
        db.exec_driver_sql('SET LOCAL search_path=public')
        db.exec_driver_sql("SET LOCAL lock_timeout='5s'")
        db.exec_driver_sql("SET LOCAL statement_timeout='30s'")
        assert db.exec_driver_sql('SELECT pg_try_advisory_xact_lock(5569076195127487815)').scalar_one()
        assert MigrationContext.configure(db).get_current_heads()==(previous,)
        config = Config('alembic.ini')
        config.attributes['connection']=db
        command.upgrade(config,following)
        assert MigrationContext.configure(db).get_current_heads()==(following,)
    print('Revision de ensayo confirmada: '+following)
except Exception:
    print('NO-GO: no continuar; consultar estado con conexion nueva, sin exponer SQL ni credenciales.')
    raise SystemExit(2) from None
finally:
    if engine is not None:
        engine.dispose()
'@ | .\.venv\Scripts\python.exe -B - $Previous $Next
    if ($LASTEXITCODE -ne 0) { throw 'Ensayo detenido.' }
}
Invoke-MitbolRehearsalRevision '20261106_0005' '20261111_0006'
```

Detenerse para verificar Discounts, baseline y revisión 0006. Sólo si coinciden,
invocar por separado en la misma terminal:

```powershell
Invoke-MitbolRehearsalRevision '20261111_0006' '20261114_0007'
# Verificar SyncRuns, sus restricciones/indices y revision 0007.
```

Con `psql -X --no-password -v ON_ERROR_STOP=1`, en el mismo destino de ENSAYO y
con TLS verificado, ejecutar cada delta en un proceso independiente; comprobar
el código de salida antes del siguiente. No ejecutar `prepare_admin_writer_neon`
porque el escritor ya existe. Los `-c` siguientes sólo preparan la sesión del
archivo `-f` en esa misma conexión:

```powershell
psql -X --no-password -v ON_ERROR_STOP=1 -c "SET mitbol.external_tls_verified='verify-full-channel-binding'; SET mitbol.approved_admin_write='hu006-reviewed'; SET mitbol.hu006_direction='upgrade';" -f docs/database/update_admin_writer_highlights.sql
if ($LASTEXITCODE -ne 0) { throw 'Detener delta destacados.' }
psql -X --no-password -v ON_ERROR_STOP=1 -c "SET mitbol.external_tls_verified='verify-full-channel-binding'; SET mitbol.approved_admin_write='hu009-reviewed';" -f docs/database/update_admin_writer_discounts.sql
if ($LASTEXITCODE -ne 0) { throw 'Detener delta descuentos.' }
psql -X --no-password -v ON_ERROR_STOP=1 -c "SET mitbol.external_tls_verified='verify-full-channel-binding'; SET mitbol.approved_admin_write='hu010-reviewed';" -f docs/database/update_admin_writer_sync.sql
if ($LASTEXITCODE -ne 0) { throw 'Detener delta cola.' }
psql -X --no-password -v ON_ERROR_STOP=1 -c "SET mitbol.external_tls_verified='verify-full-channel-binding'; SET mitbol.approved_worker_write='hu010-worker-reviewed';" -f docs/database/prepare_catalog_worker.sql
if ($LASTEXITCODE -ne 0) { throw 'Detener preparacion trabajador.' }
```

Provisionar después la contraseña del trabajador por canal privado, sin literals
en SQL versionado, logs o argumentos. Cada verificador debe ejecutarse mediante
una conexión NUEVA del LOGIN correspondiente (writer, worker, reader), nunca
`SET ROLE`. Usar TLS efectivo verificado y su marca local de sesión para los dos
verificadores cloud. Comparar de nuevo el baseline: concesiones y migraciones
no deben alterar filas existentes ni consumir sus secuencias.

```powershell
# Mismo host/base de ENSAYO y PGPASSFILE privado con entrada de cada LOGIN.
$env:PGUSER = 'mitbol_admin_writer'
psql -X --no-password -v ON_ERROR_STOP=1 -c "SET mitbol.external_tls_verified='verify-full-channel-binding';" -f docs/database/verify_admin_writer_neon.sql
if ($LASTEXITCODE -ne 0) { throw 'Perfil web no validado.' }
$env:PGUSER = 'mitbol_catalog_worker'
psql -X --no-password -v ON_ERROR_STOP=1 -c "SET mitbol.external_tls_verified='verify-full-channel-binding';" -f docs/database/verify_catalog_worker.sql
if ($LASTEXITCODE -ne 0) { throw 'Perfil trabajador no validado.' }
$env:PGUSER = 'mitbol_render_reader'
psql -X --no-password -v ON_ERROR_STOP=1 -f docs/database/verify_render_reader.sql
if ($LASTEXITCODE -ne 0) { throw 'Perfil lector no validado.' }
```

Antes de habilitar producción faltan: ensayo/restauración aprobados, respaldo
fresco y retención verificada, ventana sin escritores, autorización explícita del
host production y de cada cambio, y un ejecutor controlado para ese destino.
Repetir sólo las revisiones/deltas pendientes con sus checks intermedios; nunca
`upgrade head`, `stamp`, ni migraciones al arrancar FastAPI. Si un COMMIT queda
incierto, reconectar y comprobar revisión/objetos antes de repetir.

### Activación posterior y recuperación

`restaurant` conserva HTTPS/orígenes fijos, cookies Secure/HttpOnly/Partitioned,
sesiones, roles, limitador PostgreSQL, Origin y CSRF. Tras permisos/despliegue
autorizados, habilitar `AUTH_ENABLED`, `AUTH_CATALOG_ENABLED`,
`AUTH_IMAGES_ENABLED` y `AUTH_SYNC_ENABLED` según el alcance aprobado.
Fotografías requieren R2 configurado y privado, con prefijo exclusivo de esa
base; no se aceptan escrituras cloud al almacenamiento local efímero.
API y trabajador comparten tienda/base, con credenciales diferentes. El proceso
web no requiere el token Loyverse. No configurar aún cron, webhooks ni secretos
en Render. Los orígenes del restaurante no se sustituyen por localhost.

Si el esquema/datos están sanos y falla la aplicación, mantener flags apagados y
las tablas aditivas mientras se corrige/retrocede el código autorizado. No usar
downgrade automático: destruiría Discounts y el historial de SyncRuns. Ante daño
de datos, restaurar primero a un destino aislado, validar y autorizar el cambio
de destino o recuperación selectiva; PITR pierde las escrituras posteriores al
punto elegido. No restaurar production ni eliminar recursos como reacción
automática. La limpieza de rama/base de ensayo se autoriza después de conservar
evidencias y comprobar que no la usa ningún servicio.

Fuentes operativas: [Neon: pg_dump y conexión directa](https://neon.com/docs/manage/backup-pg-dump),
[PostgreSQL: pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html) y
[Neon: aislamiento de ramas](https://neon.com/docs/introduction/branching).

## Verificación aislada

Resultado de esta preparación: **639 pruebas aprobadas, cero fallos y cero
omitidas**, incluyendo 42 casos nuevos de `test_neon_release_0007`. Dos avisos
previos de deprecación corresponden a TestClient/dependencias. PostgreSQL temporal
quedó detenido. Informe local ignorado por Git:
`storage/neon-preparation-0007-tests.xml`. El generador SQL pasó `--check`,
`pip check` no encontró incompatibilidades y `git diff --check` pasó.

La regresión ejecutada comprendió 18 módulos: `test_neon_release_0007`,
`test_auth_cloud`, `test_frontend_remote`, `test_admin_writer`, `test_auth_phase5`,
`test_discount_auth_integration`, `test_sync_management`, `test_recurring_catalog_sync`,
`test_r2_image_storage`, `test_r2_images_api`, `test_cloud_configuration`,
`test_cloud_database`, `test_highlights_http`, `test_sync_catalog_cli`,
`test_catalog_approval`, `test_catalog_tombstones`, `test_discount_migration`
y `test_migrations`.

La preparación 0007 añade `tests/test_neon_release_0007.py`: ensayo transaccional
0005→0006→0007 con cuentas/fotografías sintéticas, comparación ORM y huellas de
las 17 tablas previas, rollback DDL/revisión, deltas idempotentes, LOGINs mínimos
reales, rechazo de permisos adicionales y SQL sin aprobación/revisión correcta.
Prueba dos ejecuciones del trabajador con Loyverse simulado y el perfil
restaurant con sesión, Origin, CSRF, 202/historial y subida/lectura/borrado con R2
simulado. El transporte cloud se adapta a loopback sólo dentro del test; no se
sustituyen la autenticación ni los permisos PostgreSQL reales.

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_neon_release_0007.py -q
```

Para reproducir las pruebas específicas desde la raíz del backend:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_sync_management.py -q
```

Se usa `scripts/test_auth_phase1.py`: PostgreSQL 18 temporal, migraciones solo
en bases desechables, cuentas/sesiones y CSRF reales, HTTP y almacenamiento R2
simulados. La suite `tests/test_sync_management.py` no utiliza overrides de
autenticación. Incluye aceptación durable, permisos/CSRF, historial, reinicio,
concurrencia real, reintentos, rollback catálogo/resultado, protección contra
intentos antiguos, conservación local, configuración cerrada y ausencia del
receptor público. Se ejecutan también regresiones de HU-008, HU-009, identidad,
migraciones y R2.

Verificación anterior de HU-010: **551 pruebas aprobadas, cero fallos y cero omitidas**; 33 casos
de HU-010 y 51 regresiones de R2. Dos avisos de deprecación proceden del
TestClient y sus dependencias. La base temporal se elimina y el clúster queda
detenido; el ejecutor conserva sus archivos de diagnóstico locales. `pip check`
no encontró dependencias incompatibles y `git diff --check` no encontró errores.

La ejecución completa incluyó `test_sync_management`, `test_recurring_catalog_sync`,
`test_sync_catalog_cli`, `test_catalog_approval`, `test_catalog_sync_postgresql`,
`test_catalog_tombstones`, `test_auth_api`, `test_auth_admin_api`,
`test_auth_persistence`, `test_auth_security`, `test_auth_phase2_core`,
`test_migrations`, `test_discount_migration`, `test_discount_cli`,
`test_r2_image_storage`, `test_r2_images_api` y `test_image_inventory`.
Las funciones comerciales originales `_upsert`, `_write`, `_retire`,
`_retire_child` y `_plan` de `SyncService` se conservaron sin cambios de AST.

## Archivos de esta implementación

| Área | Archivos |
| --- | --- |
| HTTP y contrato React | `app/api/admin_sync.py`, `app/schemas/sync.py`, registro opt-in en `app/main.py`. |
| Configuración y permisos | `app/core/config.py`, `app/core/permissions.py`. |
| Persistencia y consumo | `app/models/sync_run.py`, `app/services/sync_jobs.py`, `app/cli/sync_catalog_jobs.py`. |
| Reutilización HU-008 | `app/services/recurring_catalog_sync.py`, `app/services/sync_service.py`: preflight reutilizable, perfil local explícito y callback de éxito dentro de la transacción. |
| Compatibilidad HU-009 | `app/services/discount_sync_service.py`, `app/cli/sync_discounts.py`: admitir revisión aditiva y devolver la revisión efectiva. |
| Webhooks preparados | `app/services/loyverse_webhook_signature.py`; sin router público. |
| Migración | `migrations/versions/20261114_0007_hu010_sync_runs.py`, registro de metadatos en `migrations/env.py`. |
| Pruebas | `tests/test_sync_management.py`; expectativas de permisos/head actualizadas en `test_auth_security.py`, `test_auth_persistence.py`, `test_migrations.py`, `test_discount_migration.py`. |
| Documentación | Este documento y enlace/revisión compatible en `docs/learning/hu008/README.md`. |

Los **20 archivos de esta preparación 0007**, respecto al working tree recibido,
son los siguientes; se conservan los cambios previos sin restaurar archivos:

| Área | Archivos nuevos o modificados |
| --- | --- |
| Flags y validación | `.env.example`, `app/core/config.py`. |
| Preflight y ejecución | `app/services/catalog_safety.py`, `app/services/sync_jobs.py`, `app/services/recurring_catalog_sync.py`, nuevo `app/db/worker_policy.py`. |
| Escritor: preparación/verificación | `docs/database/prepare_admin_writer_local.sql`, `prepare_admin_writer_neon.sql`, `verify_admin_writer_local.sql`, `verify_admin_writer_neon.sql` y `scripts/render_admin_deployment_sql.py`. |
| Escritor: deltas | `docs/database/update_admin_writer_highlights.sql`, `update_admin_writer_discounts.sql`, nuevo `update_admin_writer_sync.sql`. |
| Trabajador | Nuevos `docs/database/prepare_catalog_worker.sql`, `verify_catalog_worker.sql`. |
| Pruebas | Nuevo `tests/test_neon_release_0007.py`, `tests/test_sync_management.py`. |
| Documentación | Este documento y `docs/learning/hu008/README.md`. |

No se modificaron adaptadores R2, endpoints de imágenes, relaciones de fotografías,
inventario, dependencias, `.env` privado ni las reglas comerciales de UPSERT/bajas.
Los cambios locales anteriores de configuración y arranque recibieron únicamente
las extensiones necesarias para HU-010.
