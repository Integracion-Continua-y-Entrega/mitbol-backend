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
La automatización de descuentos permanece pendiente hasta acordar almacenamiento
privado persistente y el rol operativo; no se modifican el workflow ni sus flags.

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
