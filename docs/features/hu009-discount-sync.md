# HU-009 — Descuentos comerciales de Loyverse

El backend sincroniza descuentos existentes, conserva su identidad local y
expone su consulta administrativa. La revisión Alembic `20261111_0006` añade
`Discounts` y sucede a `20261106_0005`. FastAPI no crea ni migra tablas al iniciar.

No crea descuentos en Loyverse, calcula beneficios o modifica pedidos. La
administración de ejecuciones y su historial corresponden a HU-010. Esta
entrega no incluye pantalla de frontend ni activa servicios externos.

## Reglas de importación

- `loyverse_id` es la identidad única. Repetir una importación no duplica filas
  ni reemplaza `discount_id` o la fecha de creación local.
- Los tipos fijos conservan su importe o porcentaje exacto como Decimal.
  Variables y `DISCOUNT_BY_POINTS` conservan `value=null`. Los puntos se
  almacenan solo para consulta; no se convierten a dinero ni se aplican.
- Regla acordada para tiendas: una lista `stores` vacía aplica a todas; una
  lista con IDs aplica solo a esas tiendas. Fuera de la tienda elegida se
  almacena el registro como inactivo. Una baja explícita siempre lo desactiva.
- `restricted_access` no determina activación. `is_active` tampoco indica
  elegibilidad para aplicar promociones.
- Si no hay evidencia del alcance producto/ticket, se guarda `scope="unknown"`.
  Las tiendas no permiten deducir ese alcance. Una actualización desconocida
  conserva un alcance conocido que ya estuviera almacenado.
- El lote completo se valida antes de escribir. Los duplicados se resuelven
  con la política descrita abajo. Fechas sin zona o importes inválidos
  rechazan el lote; no se sustituyen por cero. Una colección vacía o una
  ausencia no autoriza eliminar registros.
- Los cambios se confirman en una sola transacción. Se comparte el bloqueo
  comercial con el catálogo para evitar importaciones concurrentes en otro
  orden. Un error previo al commit revierte el lote completo.

La lectura usa `GET /discounts` de la [API de Loyverse](https://developer.loyverse.com/docs/),
incluye bajas con `show_deleted=true` y no inventa paginación para ese recurso.
Una respuesta con cursor inesperado se rechaza para no importar un lote truncado.
La inspección de sólo lectura identificó un ID activo/eliminado contradictorio
y el tipo no documentado `TEYA_DISCOUNT`. No se han importado descuentos reales.

## Resolución y cuarentena

La lectura agrupa por UUID normalizado antes de aplicar el transformador
estricto. Copias idénticas producen una sola fila. Versiones contradictorias
requieren `GET /discounts/{uuid}`: la respuesta debe ser válida, del mismo ID
y coincidir con una representación observada. No se decide por posición,
fecha más reciente o preferencia por activos. Un cambio concurrente no observado,
un 404 o una respuesta individual inválida aíslan el ID completo. Un 404 no
autoriza una baja. Los errores de autenticación, transporte, suscripción o
descarga incompleta abortan la ejecución; no se ocultan como omisiones parciales.

El tipo desconocido conserva su representación original en cuarentena. No se
añade al enum, no se inventan valores ni se inserta en `Discounts`. La identidad,
estructura común, fechas, tiendas y campos obligatorios de toda la colección
deben ser válidos; una identidad imposible de aislar o estructura malformada
rechaza todo el lote. Antes de descargar se contrasta la tienda con `/stores`.

El modo estricto sigue siendo el predeterminado: cualquier ID aislado rechaza
la importación completa. Para conservar las versiones descartadas durante una
resolución también se requiere un informe privado antes de aplicar el lote.
Las descargas sin anomalías conservan el flujo anterior sin exigir un archivo.

`--allow-partial` es una autorización explícita que sólo se acepta junto con
`--execute --quarantine-dir`. Además de las guardas existentes, exige descarga
completa con bajas y tienda verificada. Si cualquier ID omitido ya existe en
`Discounts`, se rechaza TODO el lote: no se conservan datos potencialmente
obsoletos como si acabaran de sincronizarse. Los IDs nuevos aislados no se
insertan ni eliminan. Las filas válidas mantienen el UPSERT, la transacción y
el bloqueo comercial compartido con HU-008/HU-010. No hay cambios de esquema.

El operador prepara una carpeta absoluta y privada fuera de cualquier checkout
Git. En POSIX debe pertenecer al usuario actual y tener modo `0700`; el informe
se crea con `0600`. En Windows se comprueba propietario y ACL, admitiendo sólo
el propietario, SYSTEM y Administradores en la carpeta; el archivo nuevo queda
restringido al usuario actual. No se cambian las ACL de carpetas existentes.
Se rechazan enlaces, reparse points, rutas TEMP y entornos efímeros de GitHub
Actions/Render: un archivo local del runner no acredita conservación persistente.

El JSON guarda contexto, fecha, cantidades, IDs aislados, originales, motivos,
respuesta seleccionada y versiones descartadas. Los decimales se etiquetan como
`{"$decimal":"valor exacto"}` para evitar pérdida de precisión. Se retiran campos
de credenciales y conexiones. Sólo los contadores y un identificador aleatorio
del informe se imprimen en el CLI; no se publican payloads, IDs aislados o rutas.

El archivo se prepara con permisos privados, se sincroniza con `fsync` y se
publica con un rename atómico dentro de la misma carpeta, sin reemplazar informes
previos. Se verifica su contenido antes del primer DML. Un fallo de archivo
aborta la importación. El informe declara `commercial_commit` como
`not_attested_prepared_before_dml`: puede existir aunque PostgreSQL haya hecho
rollback. No acredita COMMIT, respaldo externo ni recuperación ante una caída.
La retención del archivo y su respaldo siguen siendo responsabilidad del operador.

Ejemplo preparado (no ejecutado en servicios reales), con variables privadas del
proceso y sustituyendo los argumentos de ejemplo:

```text
python -m app.cli.sync_discounts --expected-database BASE_CLOUD --expected-host HOST_DIRECTO --store-id UUID_TIENDA --preflight
python -m app.cli.sync_discounts --expected-database BASE_CLOUD --expected-host HOST_DIRECTO --store-id UUID_TIENDA --execute --quarantine-dir DIRECTORIO_ABSOLUTO_PRIVADO
python -m app.cli.sync_discounts --expected-database BASE_CLOUD --expected-host HOST_DIRECTO --store-id UUID_TIENDA --execute --allow-partial --quarantine-dir DIRECTORIO_ABSOLUTO_PRIVADO
```

Preflight continúa sin HTTP ni escritura de informes y no acepta autorización
parcial. Una ejecución parcial confirmada devuelve `synchronized_with_quarantine`,
`partial=true` y cantidades de registros válidos/aislados; no equivale a una
sincronización completa. `DiscountSyncService.synchronize()` conserva su retorno
entero de filas procesadas; el resumen sólo se publica después del COMMIT.

Las pruebas permiten informes temporales únicamente con `APP_ENV=test`, host
loopback y base aislada con el patrón autorizado. Se etiquetan como
`isolated_test_temporary`; no son evidencia persistente de un entorno real.
La automatización preparada abajo usa una política explícita de compatibles;
el destino manual de cuarentena sigue rechazando runners efímeros. Publicar el
código no activa el flag de descuentos.

## Automatización de compatibles con GitHub Actions

Estado comunicado por el operador: LOGIN, perfil y preflight de
`mitbol_discount_worker` pasaron en `0008`; la primera importación guardó dos
descuentos válidos y aisló `TEYA_DISCOUNT`. Las notas de propuesta/auditoría
anteriores son históricas. Esta preparación sólo modifica archivos locales.

Se reutiliza `sync_discounts --execute --compatible-only`, con una política
**explícita** distinta del modo manual `--allow-partial --quarantine-dir`:

- Valida la colección completa, todas sus páginas y la tienda antes del DML.
- Resuelve el duplicado 10%/20% con GET individual y sólo acepta una versión
  observada, del mismo UUID y válida. Nunca escoge por posición o fecha.
- Omite exclusivamente tipos desconocidos cuyos IDs todavía NO existen en
  Discounts. No transforma `TEYA_DISCOUNT` en porcentajes o importes.
- Si un ID omitido ya existe, aborta TODO el lote con
  `DISCOUNT_OMITTED_ID_EXISTS`, conservando sus datos previos.
- Si queda un conflicto sin resolver (404, versión no observada, detalle
  inválido), aborta TODO el lote con `DISCOUNT_UNRESOLVED_CONFLICT`, aunque
  el ID sea nuevo. No oculta estos casos como éxito parcial.
- Errores de autenticación, tienda, proveedor, transporte, paginación y campos
  inválidos siguen abortando. No hay borrados por ausencia.
- Conserva UPSERT por ID original, identidad local/created_at, transacción y
  advisory lock compartido con HU-008/HU-010. No añade tablas ni migraciones.

El modo estricto predeterminado y la cuarentena local existente conservan sus
validaciones. `--compatible-only` requiere `--execute` y rechaza combinarse con
`--allow-partial` o `--quarantine-dir`; no elimina la garantía de archivo del
modo manual. Un resultado compatible confirmado usa
`status=synchronized_compatible_only`, `partial=true` cuando hubo omisiones,
`quarantine_report_written=false` y `evidence=redacted_summary_only`.
No se presenta como una importación íntegra con cuarentena conservada.

### Incidencias sin infraestructura adicional

Los logs y el resumen nativo de Actions muestran códigos, cantidades y hasta
20 referencias SHA-256 truncadas a 16 caracteres del UUID normalizado. Las
referencias son estables y sirven para contrastar un ID de forma privada;
no son claves comerciales. Los tipos sólo se etiquetan con las constantes
`TEYA_DISCOUNT` u `OTHER_UNSUPPORTED`, nunca con texto arbitrario del proveedor.
Si hay más incidencias, se indica truncamiento y se conservan sus cantidades
por código. No se publican nombres, importes, UUID originales, payloads,
conexiones, tokens, rutas privadas ni excepciones sin filtrar.

GitHub emite warning para tipos omitidos, notice para duplicados resueltos y
error ante un fallo. El workflow conserva el exit code no cero del importador,
no lo reintenta y escribe el resumen con campos permitidos. Un resultado JSON
inválido, ausente o imposible de resumir falla con
`DISCOUNT_RESULT_INVALID_OR_UNAVAILABLE`, sin mostrar su contenido. Si falló
la conexión/COMMIT o el resumen después del DML, inspeccionar la base antes de
repetir: el resumen no promete rollback ni es un registro transaccional.

**Cambio de garantía acordado:** el modo automático no conserva los originales
de incidencias o versiones descartadas. Por tanto, no acredita auditoría
completa, conservación por 90 días ni retención indefinida hasta cerrar HU-009.
La consistencia comercial depende de la validación y la transacción, no de un
archivo de cuarentena. Los resúmenes/logs siguen la política de acceso/retención
del repositorio. Si se requiere guardar originales, la alternativa mínima son
artifacts privados de Actions, previa revisión del acceso y la retención; eso
no garantiza conservación indefinida. No se añaden artifacts ni almacenamiento
R2 a esta implementación, y no se altera el R2 de fotografías.

### Workflow y configuración mínima

El workflow continúa en **main** y hace checkout de **develop**. Su job
`synchronize` permanece idéntico. El job `discounts` requiere que termine bien
el catálogo; ambos usan el grupo de concurrencia existente, sin cancelar una
transacción en curso. Un fallo de descuentos no revierte el catálogo ya
confirmado ni modifica sus flags. El job separado tiene timeout de 10 minutos
y usa únicamente las credenciales independientes en su paso operativo.

`MITBOL_DISCOUNT_SYNC_ENABLED` ausente equivale a `false`. El horario y la
opción manual `process-queue` sólo escriben descuentos si vale exactamente
`true`. Una ejecución manual `dry-run` con `discount_preflight=true` ejecuta
únicamente el preflight de descuentos, sin token Loyverse ni escrituras.
El dry-run existente del catálogo mantiene sus propios requisitos.

`--require-discount-worker` exige host directo/base production exactos,
usuario `mitbol_discount_worker`, TLS verify-full con channel binding, proyecto,
rama, endpoint, revisión 0008 y el perfil SQL completo de permisos mínimos.
Se verifica en READ ONLY antes de descargar. El CLI exige también el flag
para escribir con esa opción y vuelve a hacer preflight en la invocación de
escritura. No requiere MITBOL_RECURRENT_SYNC_APPROVAL, propio del catálogo.

| Tipo | Nombre exacto | Valor/uso |
| --- | --- | --- |
| Secret nuevo | DISCOUNT_DATABASE_URL | Conexión directa de mitbol_discount_worker, con sslmode=verify-full y channel_binding=require |
| Secret nuevo | DISCOUNT_LOYVERSE_ACCESS_TOKEN | Token de lectura de tiendas y descuentos, separado de la configuración del catálogo |
| Variable nueva | MITBOL_DISCOUNT_SYNC_ENABLED | false inicialmente |
| Variable existente | EXPECTED_DATABASE | mitbol_cloud_dev |
| Variable existente | EXPECTED_DATABASE_HOST | ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech |
| Variable existente | LOYVERSE_STORE_ID | UUID autorizado existente |

El workflow inyecta APP_ENV=cloud, ADMIN_ENABLED=false y mapea los secretos
independientes a DATABASE_URL/LOYVERSE_ACCESS_TOKEN sólo en el paso de descuentos.
No necesita variables ni secretos R2, nuevo bucket, permisos PostgreSQL,
contraseñas ni servicios adicionales.

```bash
args=(--expected-database "$EXPECTED_DATABASE" --expected-host "$EXPECTED_DATABASE_HOST" --store-id "$LOYVERSE_STORE_ID" --require-discount-worker)
python -m app.cli.sync_discounts "${args[@]}" --preflight
# Sólo después de autorización comercial y con flag=true:
python -m app.cli.sync_discounts "${args[@]}" --execute --compatible-only
```

### Publicar después de revisar, sin activar escrituras

1. Publicar primero backend/pruebas/SQL del perfil en un PR hacia **develop**.
   Incluir `verify_discount_worker.sql`: es dependencia runtime del verificador.
   Los scripts y pruebas del rol ya preparados se conservan; no se reaplican.
2. Tras integrar ese PR, publicar únicamente workflow y prueba offline en un PR
   hacia **main**, desde el checkout aislado existente. No fusionar todo develop
   en main ni cambiar la rama predeterminada. No se necesita desplegar Render.
3. Configurar posteriormente sólo los dos secretos y la variable nueva de la
   tabla; conservar los secretos del catálogo y dejar el flag en false.
4. Actions → MITBOL catalog synchronization → Run workflow → main →
   `mode=dry-run` y `discount_preflight=true`. Esperar `preflight_ok`,
   `discount_worker_policy=verified`, `writes=false`, `loyverse_contacted=false`.
   Esto prueba identidad/permisos, no hace una lectura comercial de Loyverse.
5. Revisar CI y autorizar por separado las escrituras automáticas. Sólo entonces
   cambiar `MITBOL_DISCOUNT_SYNC_ENABLED=true`; descuentos se ejecutará después
   del catálogo. Revisar warnings/resumen en la primera ejecución. Para parar
   futuras ejecuciones, volver a false; no cancela una transacción ya iniciada.

Comandos de publicación **preparados, no ejecutados**. Revisar el diff antes
de cada commit y conservar cambios ajenos. El segundo bloque se ejecuta sólo
después de integrar el backend compatible en develop:

```powershell
Set-Location C:\Proyectos\mitbol\mitbol-backend
git switch -c feature/discount-sync-compatible
git add app/cli/sync_discounts.py app/db/discount_worker_policy.py app/services/discount_sync_service.py
git add docs/features/hu009-discount-sync.md docs/database/prepare_discount_worker.sql docs/database/verify_discount_worker.sql scripts/test_auth_phase1.py
git add tests/test_discount_worker.py tests/test_discount_automation_cli.py tests/test_discount_compatible.py
git diff --cached --check
git diff --cached
git commit -m "feat(discounts): sync compatible records with explicit incident reporting"
git push -u origin feature/discount-sync-compatible
gh pr create --base develop --head feature/discount-sync-compatible --title "feat(discounts): automate compatible discount sync" --body "Reuses existing validation, transactions and isolated role. Reports unsupported new IDs without persistent quarantine. Automation stays disabled."

Set-Location C:\Users\ferch\AppData\Local\Temp\mitbol-discount-workflow-20261117
git branch --show-current
# Rama existente ci/discount-sync-private-quarantine; nombre histórico, ya sin R2.
git add .github/workflows/catalog-sync.yml tests/workflows/test_discount_sync_workflow.py
git diff --cached --check
git diff --cached
git commit -m "ci(discounts): prepare disabled compatible sync after catalog"
git push -u origin ci/discount-sync-private-quarantine
gh pr create --base main --head ci/discount-sync-private-quarantine --title "ci(discounts): prepare disabled compatible sync" --body "Adds discount sync after the unchanged catalog job, using develop and independent credentials. No R2 dependencies."
```

El horario de GitHub es de mejor esfuerzo; no garantiza puntualidad cada cinco
minutos. En esta preparación no se activa el flag ni se ejecutan CLI comerciales,
conexiones reales, publicaciones, commits, push o PR.

Validación local de la propuesta simplificada: **540 pruebas aprobadas** de
descuentos, permisos, HU-008/HU-010, fotografías R2 y seguridad cloud, en
155.56 segundos. PostgreSQL 18 fue temporal/local y todo HTTP fue simulado.
**23 pruebas offline del workflow aprobadas**; YAML válido con actionlint 1.7.12,
`git diff --check` correcto y revisión de secretos sin hallazgos. El job del
catálogo coincide exactamente con origin/main. Se mantienen los dos avisos
existentes de deprecación de Starlette/TestClient.

## Propuesta del trabajador independiente

Se preparan [prepare_discount_worker.sql](../database/prepare_discount_worker.sql)
y [verify_discount_worker.sql](../database/verify_discount_worker.sql).
**No están aplicados en Neon.** No cambian `mitbol_catalog_worker`, el backend,
las migraciones, el workflow ni secretos de GitHub. Crear este rol tampoco
autoriza una importación. El preflight comprueba permisos necesarios, pero no
rechaza todos los posibles permisos sobrantes: se debe ejecutar además el
verificador estricto desde un LOGIN nuevo del trabajador.

El UPSERT actual escribe siete campos, actualiza seis y conserva la identidad
local y `created_at`. La lectura cubre el listado y los IDs omitidos de cuarentena.
La reflexión del esquema consulta catálogos PostgreSQL, sin necesitar SELECT
sobre datos de productos, categorías, variantes, autenticación o `SyncRuns`.

| Objeto | Permisos del rol propuesto |
| --- | --- |
| Base autorizada | CONNECT |
| Esquema public | USAGE |
| alembic_version | SELECT únicamente de version_num |
| Discounts | SELECT de discount_id, loyverse_id, name, discount_type, value, scope, is_active, synced_at y created_at |
| Discounts | INSERT de loyverse_id, name, discount_type, value, scope, is_active y synced_at |
| Discounts | UPDATE de name, discount_type, value, scope, is_active y synced_at |
| Discounts_discount_id_seq | USAGE; sin SELECT ni UPDATE/setval |

Son **23 permisos por columna y USAGE en una secuencia**. No hay DELETE,
TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, DDL, membresías, grant options,
propiedad de objetos ni escritura de `loyverse_id`, `discount_id` o `created_at`.
No se concede acceso a ninguna otra tabla de aplicación. Las funciones y
catálogos estándar de PostgreSQL mantienen sus permisos públicos habituales.

Auditoría de solo lectura del 17 de noviembre de 2026: proyecto `mitbol-dev`
(`delicate-glade-05265146`), rama `production` (`br-snowy-king-bd0n9w42`), endpoint
directo `ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech`, base
`mitbol_cloud_dev`, PostgreSQL 18 y revisión `20261115_0008`. Se contrastaron los
IDs mediante PostgreSQL con la evidencia privada de Console, y se usó
`verify-full` con channel binding requerido. El contrato puente pasó;
`Discounts` tenía cero filas y el rol propuesto no existía. El administrador
tenía solo SELECT sobre sus nueve columnas; lector y trabajador de catálogo
no tenían permisos sobre descuentos. PUBLIC tenía CONNECT/USAGE, sin
CREATE/TEMP, acceso a tablas de aplicación ni rutinas de aplicación ejecutables.
Este resultado debe reconfirmarse antes de cualquier creación futura.
El operador `neondb_owner` conserva CREATEROLE, es propietario de Discounts y
su secuencia y puede conceder SELECT/INSERT/UPDATE y USAGE, respectivamente.
No se encontraron triggers de usuario habilitados ni reglas de reescritura
sobre Discounts.

Validación de la propuesta: **90 pruebas aprobadas**, incluidas 41 del perfil
nuevo y las regresiones seleccionadas de CLI, resolución y cuarentena parcial.
Se usó PostgreSQL 18 temporal con el mecanismo seguro existente y LOGIN
independiente local; se verificaron UPSERT repetido, preflight sin token ni
consumo de secuencia, rechazo de escrituras ajenas/DDL y privilegios sobrantes,
guardas de destino/revisión/aprobación y PUBLIC inseguro. No se ejecutó el SQL
de preparación en Neon. LOGIN y preflight del rol remoto siguen pendientes de
su creación y provisionamiento autorizados.

Procedimiento preparado, **no ejecutarlo todavía**:

1. Obtener autorización específica, reconfirmar destino/IDs/0008 y usar el
   operador privado por conexión directa con TLS efectivo `verify-full`, CA
   válida y `channel_binding=require`. Nunca enviar la URL o contraseña como
   argumento de un proceso ni copiarla al SQL. Usar un cliente privado sin
   logging de SQL/parámetros, o PGPASSFILE protegido fuera de Git.
2. En esa sesión del operador ya contrastada, fijar
   `SET mitbol.external_tls_verified='verify-full-channel-binding';` y
   `SET mitbol.approved_discount_worker_write='hu009-discount-worker-reviewed';`.
   Ejecutar el archivo de preparación completo con parada ante errores
   (`ON_ERROR_STOP` en psql). El archivo gestiona su propia transacción.
   Las marcas no demuestran TLS ni autorización por sí solas. El SQL comprueba
   además los IDs reales de Neon y rechaza otro destino, otro operador, un rol
   existente, un esquema inesperado o permisos peligrosos de PUBLIC. Ante
   error, revisar el resultado y revertir la transacción abierta; no reintentar
   automáticamente ni corregir PUBLIC ampliando el alcance del procedimiento.
3. El rol nace con LOGIN y PASSWORD NULL, sin autenticación por contraseña.
   Inicializar una contraseña aleatoria nueva mediante ALTER ROLE con texto
   original, en otra operación privada autorizada; sin SCRAM precomputado.
   No intentar el reset de Console sobre una contraseña NULL: ese caso ya falló
   en el ensayo anterior. No crear el rol desde Console/API con privilegios
   administrativos ni conceder `neon_superuser`. No incluir una contraseña
   literal en este archivo ni en el historial. La protección del cliente no
   garantiza ausencia de registros administrados de Neon; acordar ese riesgo
   antes de provisionar la credencial. No se suministra ni ejecuta aquí ningún
   comando de inicialización de contraseña.
4. Guardar una conexión independiente en un archivo privado fuera de Git, por
   ejemplo `C:\mitbol-secrets\neon-production-discount-worker.env`, con acceso
   restringido al operador. Construir DATABASE_URL mediante URL.create o
   codificación URL correcta de usuario/contraseña, conservando host directo,
   base, `sslmode=verify-full` y `channel_binding=require`. No reutilizar el
   archivo del trabajador de catálogo ni modificar el .env del backend.
5. Abrir un LOGIN real nuevo del trabajador, comprobar TLS y la misma identidad
   de proyecto/rama/endpoint/base, fijar solo la marca de TLS de sesión y ejecutar
   verify_discount_worker.sql. Debe responder `DISCOUNT_WORKER_POLICY_OK`.
   Es READ ONLY: contrasta exactamente columnas, secuencia, atributos,
   membresías, DDL, propietarios, objetos ajenos y permisos efectivos de PUBLIC.
   No prueba contraseñas ni permisos con SET ROLE y no consume secuencias.
6. Cargar exclusivamente en el proceso operativo las variables de abajo y
   ejecutar el preflight. No usa token Loyverse, HTTP, cuarentena ni DML.
   No fijar `default_transaction_read_only=on` al rol: el preflight comprueba
   que sea un operador de escritura, aunque su inspección sea READ ONLY.

| Variable de proceso | Configuración para preflight |
| --- | --- |
| APP_ENV | cloud |
| DATABASE_URL | Conexión privada de mitbol_discount_worker |
| EXPECTED_DATABASE | mitbol_cloud_dev |
| EXPECTED_DATABASE_HOST | ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech |
| LOYVERSE_STORE_ID | UUID de la tienda autorizada existente |

El CLI no carga archivos .env. Cargar únicamente ese archivo privado en el
entorno del proceso, sin imprimirlo. Con las variables anteriores configuradas:

```powershell
.\.venv\Scripts\python.exe -B -m app.cli.sync_discounts --expected-database $env:EXPECTED_DATABASE --expected-host $env:EXPECTED_DATABASE_HOST --store-id $env:LOYVERSE_STORE_ID --preflight
```

Debe devolver `preflight_ok`, revisión `20261115_0008`, `writes=false` y
`loyverse_contacted=false`. No necesita MITBOL_RECURRENT_SYNC_APPROVAL. Aún no
usar --execute ni añadir el token; la primera importación requiere autorización
separada y almacenamiento privado persistente para la cuarentena. Los permisos
no sustituyen la validación comercial: un SQL directo con la credencial puede
alterar los campos autorizados y avanzar la secuencia. Restringir su custodia.
El perfil se verifica en esta base; no promete aislamiento de otras bases del
mismo endpoint que pudieran tener concesiones propias de PUBLIC. Revalidar si
cambian el esquema, funciones, propietarios o privilegios predeterminados.

## Consulta administrativa

`GET /api/admin/discounts?limit=50&offset=0`

Se publica junto con el catálogo administrativo: `ADMIN_ENABLED` para el modo
local legado, o `AUTH_ENABLED` y `AUTH_CATALOG_ENABLED` para sesiones
individuales. Este último exige `catalog:read`, disponible para Admin y Staff
activos. No hay un endpoint público de descuentos ni endpoint web de sincronización.

`limit` admite 1–100, por defecto 50; `offset` es un entero no negativo.
La respuesta es una lista ordenada por `discount_id` ascendente e incluye
inactivos. Una tabla o página vacía devuelve `[]`.

```json
[
  {
    "discount_id": 1,
    "loyverse_id": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
    "name": "Descuento comercial",
    "discount_type": "FIXED_AMOUNT",
    "value": "35.25",
    "scope": "unknown",
    "is_active": true,
    "synced_at": "2026-11-12T12:00:00Z",
    "created_at": "2026-11-12T12:00:00Z"
  }
]
```

Los valores decimales se serializan como cadenas exactas. `value` y `synced_at`
son obligatorios en el contrato aunque admiten null. Errores: 401 sin sesión,
403 sin permiso, 422 por paginación inválida, 503 si falta la tabla o no está
disponible la base, y 500 seguro ante fallos inesperados. No se exponen SQL o secretos.

## Operación manual

`python -m app.cli.sync_discounts` usa exclusivamente variables del proceso;
no carga `.env`. Requiere `DATABASE_URL` del operador, `APP_ENV` y
`LOYVERSE_STORE_ID`. El UUID pasado en `--store-id` debe coincidir con esa
variable. Se admiten `20261111_0006`, `20261114_0007` y `20261115_0008`,
con PostgreSQL 18 y el contrato de esquema correspondiente verificado.

Con las variables configuradas, sustituir los valores de ejemplo:

```powershell
.\venv\Scripts\python.exe -B -m app.cli.sync_discounts --expected-database mitbol_dev --store-id UUID_DE_LA_TIENDA --preflight
```

El preflight es el modo predeterminado. Solo lee revisión, identidad, permisos
y cantidad de descuentos; no llama a Loyverse ni consume secuencias. No necesita
token. Si falla, no se inicia la descarga.

La ejecución requiere además `LOYVERSE_ACCESS_TOKEN` en el entorno y cambiar
`--preflight` por `--execute`. El resultado informa registros procesados, no
cuántos fueron nuevas inserciones. Ante un error de conexión o commit se debe
comprobar el estado antes de repetir; no se promete ausencia de commit si la
conexión se pierde al confirmarlo.

Cloud requiere `APP_ENV=cloud`, `EXPECTED_DATABASE`, `EXPECTED_DATABASE_HOST`
y `--expected-host` coincidentes, endpoint directo sin pooler y TLS
verify-full/channel-binding. La terminal del operador tiene su propia
configuración: no se cambian las credenciales del Web Service público.
El token Loyverse nunca debe ponerse en React o en Render para servir consultas.

El operador necesita SELECT de `public.alembic_version` y de las columnas de
Discounts, INSERT/UPDATE de las columnas comerciales y USAGE en la secuencia;
no necesita DELETE. El rol web
`mitbol_admin_writer` solo recibe SELECT por columna de Discounts. El lector
público no recibe acceso a esta tabla. `mitbol_catalog_worker` excluye Discounts:
agregarle grants rompería su perfil estricto de catálogo. No se cambian ACL;
queda pendiente preparar un rol independiente `mitbol_discount_worker`.
La ejecución necesita un token con lectura de tiendas y descuentos; el
preflight PostgreSQL no necesita token.

## Validación e integración

Las pruebas cubren validación/HTTP del proveedor simulado, importación repetida,
bajas, puntos, conservación de datos ante fallos, bloqueo, revisión/destino,
consulta paginada, login real y ACL de PostgreSQL con roles independientes.
El laboratorio usa bases y clústeres temporales; no consulta `.env`, Loyverse
real, `mitbol_dev` ni Neon.

Verificación final del 12 de noviembre de 2026: **1,662 pruebas aprobadas**,
sin fallos, errores u omisiones, en 592.41 segundos sobre Windows, Python 3.13
y PostgreSQL 18. Se observaron dos avisos de deprecación de Starlette/TestClient.
Comando equivalente al de CI:

```text
python -B scripts/test_auth_phase1.py --with-reader -ra --junitxml=test-results/pytest.xml
```

Validación local de cuarentena del 17 de noviembre de 2026: **2,095 casos
cubiertos**. La suite general aprobó 2,083; las otras 12 pruebas documentales
fallaron al crear PowerShell con `-EncodedCommand` (`WinError 5`), antes de sus
aserciones. Las 12 aprobaron al ejecutar el mismo contenido y aserciones desde
archivos temporales mediante `-File`, sin cambiar ExecutionPolicy ni archivos
versionados. Esto no equivale a una ejecución general sin fallos ni a CI remoto.
Los 215 casos de descuentos de la suite aprobaron. Tras los últimos ajustes
se comprobaron nuevamente los 23 casos de cuarentena/importación parcial;
además aprobaron seis comprobaciones POSIX en WSL. Todo HTTP fue simulado y
PostgreSQL fue temporal/local, incluidas revisiones 0006, 0007 y 0008. Se
verificaron rollback, ausencia de DML ante errores de archivo y preservación de
los originales y versiones descartadas. Los XML y archivos del laboratorio
quedan en TEMP; no acreditan retención de evidencia comercial real.

El laboratorio requiere los binarios PostgreSQL 18 accesibles mediante
`MITBOL_TEST_PG_BIN`. En Windows se puede dirigir TEMP/TMP a un directorio
local con espacio suficiente: los clústeres se detienen y sus archivos se
conservan para diagnóstico. Los informes temporales no se versionan.

La publicación y actualización del rol existente se describen en
[procedimiento HU-009](../deployment/hu009_rollout.md). La activación externa,
la revisión de frontend y el check remoto de CI deben confirmarse por separado.
