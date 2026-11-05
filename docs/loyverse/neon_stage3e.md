# Etapa 3E: preparación de la primera sincronización en Neon

**Estado final: completada y auditada.** La carga inicial y las auditorías del
operador y del lector ya finalizaron correctamente. Este documento conserva el
procedimiento de preparación; no volver a ejecutarlo sobre el catálogo cargado.
Continuar con [la preparación de Render, Etapa 3F](../deployment/render_stage3f.md).

Este procedimiento prepara y consulta; no ejecuta la sincronización. Utiliza
HU-008 existente, sin migraciones, cambios de modelos, nuevas tablas en Neon,
usuarios, permisos ni servicios Render. Ejecútalo desde la raíz del repositorio
en el PowerShell que ya contiene la conexión directa del operador.

## Implementación revisada

`sync_catalog` no importa Settings, FastAPI ni la conexión global. Sólo toma
variables del proceso. El motor operativo se crea con `read_only=False`; el
preflight usa exclusivamente transacciones `READ ONLY`, sin cambiar el modo
predeterminado del motor. Cada conexión cloud comprueba host/base/usuario
efectivos, certificados con `verify-full`, CA explícita o certifi y
`channel_binding=require`. Conserva el mínimo TLS 1.2; Neon ha negociado TLS 1.3.
Se rechazan parámetros que redirigen libpq y el endpoint `-pooler`.

Opciones reales: `--expected-database`, `--expected-host`, `--store-id`,
`--preflight`, `--execute`. Sin modo explícito se hace preflight.

- Sin `--store-id`: inspección de PostgreSQL; `preflight_ok`, sin contactar Loyverse.
- Con `--preflight --store-id`: comprueba tablas vacías, descarga y valida el
  catálogo, muestra el plan y repite el chequeo de PostgreSQL. Sólo entonces
  devuelve `ready_for_initial_sync`, con `writes=false`.
- `--execute` es la vía existente de escritura y requiere autorización posterior.

Ambos preflights exigen `APP_ENV=cloud`, `ADMIN_ENABLED=false` explícito,
coincidencia entre argumentos y variables de destino y revisión `20261102_0003`.
Se comprueban identidad operativa, ausencia de lectura heredada/réplica,
resolución de las tablas en `public`, ausencia de RLS, CONNECT/USAGE, SELECT en las
ocho tablas y Alembic, INSERT/UPDATE en las cuatro entidades comerciales,
INSERT/DELETE en asignaciones y acceso nextval a las cuatro secuencias utilizadas.
Los permisos se inspeccionan; no se ejecuta DML de prueba ni nextval.
El rol operativo puede tener permisos más amplios: esta comprobación no los
revoca ni convierte `neondb_owner` en un rol de permisos mínimos.

El preflight completo necesita `LOYVERSE_ACCESS_TOKEN` y coincidencia exacta
entre `--store-id` y `LOYVERSE_STORE_ID`. Consulta sólo GET `/stores`, `/items`
y `/modifiers`; estos dos últimos con `show_deleted=true` y toda su paginación.
Las variantes y opciones vienen anidadas. El ID debe corresponder a una tienda
activa accesible; revisa también su nombre en la salida. Los precios y
`available_for_sale` proceden exclusivamente de la configuración de esa tienda.
Un catálogo inicial sin productos o variantes importables no se declara listo.

## Datos que cambiará una ejecución posteriormente autorizada

| Tabla | Inserciones / actualizaciones existentes |
| --- | --- |
| `Products` | Inserta `loyverse_id`, `name`, `is_active=true`, `synced_at`, PK por secuencia y defaults locales. En conflicto actualiza sólo `name`, `is_active`, `synced_at`. Una baja marca `is_active=false`. |
| `ProductVariants` | Inserta `loyverse_id`, `name`, precio de tienda, `product_id`, `is_available=true`, `is_commercially_available=available_for_sale`, `synced_at`, PK por secuencia. En conflicto sólo cambia `name`, `price`, `is_commercially_available`, `synced_at`. Las bajas ponen disponibilidad comercial en false. |
| `ProductModifiers` | Inserta identidad, `name`, `display_order`, `synced_at`, PK por secuencia. En conflicto actualiza esos tres últimos campos. Una baja conserva el grupo y actualiza `synced_at`. |
| `ProductComplements` | Inserta identidad, grupo padre, `name`, `price`, `display_order`, `is_active=true`, `synced_at`, PK por secuencia. En conflicto cambia sólo los campos comerciales, nunca el padre. Una baja marca `is_active=false`. |
| `ProductModifierAssignments` | Inserta parejas variante/grupo sin duplicarlas; elimina únicamente asociaciones comerciales obsoletas o afectadas por bajas explícitas. |
| `ProductImages`, `Tags`, `ProductTags` | Ninguna escritura. Se cuentan para verificar que las ocho tablas sigan vacías antes de la primera importación. |

Tampoco se escribe en `alembic_version`. `Products.description`, `is_available`,
`is_featured`, `is_new`, las preferencias `ProductVariants.is_available` existentes,
imágenes y etiquetas se conservan. Nuevos productos usan description NULL,
is_available/is_active/is_new true e is_featured false. Un grupo dado de baja
inactiva sus complementos y retira sus asignaciones. Una baja de producto afecta
la disponibilidad comercial de sus variantes y sus asignaciones, sin borrar
sus filas ni enriquecimiento.

Las cuatro identidades se resuelven por `loyverse_id` UNIQUE; las asignaciones
por PK compuesta. UPSERT conserva PK y rechaza cambios de padre histórico.
La ausencia de un registro no equivale a una baja: se requieren tombstones.

### Revisión de los dos tombstones de producto

`tombstones.products` es `len(plan.deleted_products)`, no el número de filas
modificadas. `_deleted()` clasifica el artículo por `deleted_at`: ausente o NULL
significa que no es baja; cualquier fecha parseable con zona horaria significa
baja explícita. No compara esa fecha con la hora actual. Un valor inválido o sin
zona aborta la validación. No usa `is_active`, `available_for_sale`, falta de stock
ni ausencia del artículo en otra respuesta para deducir una baja.

El preflight solicita `show_deleted=true`, por lo que puede recibir tanto artículos
activos como bajas. El transformador separa cada baja antes de crear productos o
recorrer sus variantes. Por eso las dos bajas no forman parte de los tres
productos ni de las cinco variantes previstas para insertar. Cero tombstones de
variante no prueba que los artículos dados de baja nunca tuvieran variantes.

En una base vacía no se insertan filas para esas dos identidades. El servicio
intentará un UPDATE por cada `loyverse_id`; al no existir la fila, no cambia nada.
Si la identidad ya existiera, marcaría `Products.is_active=false`, actualizaría
`synced_at`, pondría `is_commercially_available=false` en sus variantes y retiraría
sus asociaciones comerciales. No borraría productos, variantes, imágenes o
etiquetas, ni cambiaría la preferencia local `is_available`.

Los informes y dos snapshots locales históricos de I.C.E, obtenidos el
3 de noviembre de 2026 (UTC), registran las siguientes bajas, ambas con
`deleted_at=2026-11-02T02:00:04.000Z`:

| Nombre histórico | ID Loyverse |
| --- | --- |
| Hamburguesa Clásica | `159eb16e-c383-439d-a839-3cec3612a31e` |
| Refresco de cola | `93d066a2-d9d0-47b5-ab5c-382f3f2105e0` |

La salida resumida del preflight actual sólo conserva el conteo, no los IDs de
baja ni sus nombres. Dos bajas actuales no prueban que sean exactamente las dos
históricas. No se hizo otra consulta remota para identificarlas. La separación
entre bajas e inserciones se aplica a cualquier identidad validada; no se cruza
por nombre. IDs duplicados o contradictorios en la descarga abortan el plan.

## Errores, interrupciones y límites

El cliente rechaza colecciones ausentes, JSON inválido, cursores repetidos,
páginas rotas y más de 1.000 páginas. El transformador rechaza identidades
duplicadas, relaciones inválidas, precios desconocidos/negativos/no finitos,
precios variables y configuraciones de tienda incompletas; no sustituye precios.
Sólo se reintentan GET ante errores de red, timeout, HTTP 408/429/5xx, hasta tres
intentos por solicitud, respetando Retry-After con límite de 300 segundos.
401/402/403 y otros errores permanentes abortan sin reintento. Los mensajes
omiten tokens, cuerpos HTTP, contraseñas y URL de conexión.

`SyncService` valida destino/revisión y adquiere un bloqueo advisory transaccional
antes del primer GET. Descarga, transforma y aplica todo en una sola transacción.
Un error antes del commit revierte las filas y libera el bloqueo; no se reintenta
automáticamente una transacción de escritura. Las secuencias pueden avanzar aun
con rollback o conflicto: los saltos no implican filas duplicadas
([PostgreSQL 18](https://www.postgresql.org/docs/18/functions-sequence.html)).

Una repetición conserva identidades y datos locales; actualiza timestamps y los
datos comerciales vigentes. Ante una interrupción durante el commit, el resultado
puede ser incierto: primero auditar filas/destino; no repetir ciegamente.

La API remota no ofrece aquí una instantánea transaccional entre endpoints;
una omisión silenciosa del proveedor no siempre puede detectarse. El preflight
no bloquea otros escritores y sólo acredita el estado observado. La ejecución
futura vuelve a descargar el catálogo: no congela el plan mostrado. Coordinar una
ventana sin otros escritores ni cambios comerciales reduce esa diferencia.
Este procedimiento no vuelve a certificar toda la auditoría de esquema 3C.

## PowerShell: primero PostgreSQL, sin token

No uses la URL del lector. Conserva tu `DATABASE_URL` operativa ya configurada.
No cargues `.env`, no hagas `Get-ChildItem Env:` ni imprimas la URL.

```powershell
Set-Location C:\Proyectos\mitbol\mitbol-backend

if ($env:APP_ENV -cne 'cloud') { throw 'Se requiere APP_ENV=cloud en este PowerShell.' }
$env:ADMIN_ENABLED = 'false'
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) { throw 'Falta DATABASE_URL operativa; no se usará .env.' }
if ($env:EXPECTED_DATABASE -cne 'mitbol_cloud_dev') { throw 'EXPECTED_DATABASE no coincide.' }
if ($env:EXPECTED_DATABASE_HOST -cne 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech') { throw 'EXPECTED_DATABASE_HOST no coincide.' }

$neonCheck = & .\.venv\Scripts\python.exe -B -m app.cli.sync_catalog --preflight --expected-database mitbol_cloud_dev --expected-host ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech
if ($LASTEXITCODE -ne 0) { $neonCheck; throw 'Falló el preflight PostgreSQL. Detenerse.' }
$neonReport = $neonCheck | ConvertFrom-Json
$neonReport | ConvertTo-Json -Depth 8
if ($neonReport.user -cne 'neondb_owner') { throw 'La identidad no es el operador autorizado.' }
if (@($neonReport.table_counts.PSObject.Properties | Where-Object { $_.Value -ne 0 }).Count -ne 0) {
    throw 'Alguna de las ocho tablas contiene filas. No preparar una importación inicial.'
}
```

Esperado: exit 0, `preflight_ok`, destino exacto, `user=neondb_owner`, revisión
`20261102_0003`, TLS 1.3 (o una negociación válida >=1.2), `verify-full`,
`channel_binding=require`, `write_permissions_verified=true`, modo predeterminado
`off`, ocho conteos cero, `writes=false`, `loyverse_contacted=false`.

## PowerShell: tienda y catálogo, con token temporal

Usa el ID de tienda que ya autorizaste en Loyverse; no lo deduzcas eligiendo la
primera tienda. Si falta la variable, el siguiente bloque solicita únicamente ese
ID. El token se introduce mediante entrada oculta y se restaura la variable
anterior al terminar. No se guarda en archivos ni se incorpora a Render.
La asignación a `LOYVERSE_ACCESS_TOKEN` debe ejecutarse antes de liberar el BSTR;
el proceso Python hereda esa variable temporal. `finally` restaura el valor previo
(o elimina la variable si no existía) antes de limpiar los objetos auxiliares.

```powershell
if ([string]::IsNullOrWhiteSpace($env:LOYVERSE_STORE_ID)) {
    $env:LOYVERSE_STORE_ID = Read-Host 'ID exacto de la tienda Loyverse autorizada'
}

$previousLoyverseToken = [Environment]::GetEnvironmentVariable('LOYVERSE_ACCESS_TOKEN', 'Process')
$loyverseSecret = $null
$loyversePointer = [IntPtr]::Zero
try {
    $loyverseSecret = Read-Host 'Token Loyverse (entrada oculta)' -AsSecureString
    if ($loyverseSecret.Length -eq 0) { throw 'El token no puede estar vacío.' }
    $loyversePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($loyverseSecret)
    # Asignar antes de liberar el BSTR; no imprimir este valor.
    $env:LOYVERSE_ACCESS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($loyversePointer)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($loyversePointer)
    # Evitar que finally vuelva a liberar la misma dirección.
    $loyversePointer = [IntPtr]::Zero

    & .\.venv\Scripts\python.exe -B -m app.cli.sync_catalog --preflight --expected-database mitbol_cloud_dev --expected-host ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech --store-id $env:LOYVERSE_STORE_ID
    if ($LASTEXITCODE -ne 0) { throw 'Falló el preflight completo. No autorizar escrituras.' }
}
finally {
    [Environment]::SetEnvironmentVariable('LOYVERSE_ACCESS_TOKEN', $previousLoyverseToken, 'Process')
    $previousLoyverseToken = $null
    if ($loyversePointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($loyversePointer)
        $loyversePointer = [IntPtr]::Zero
    }
    if ($null -ne $loyverseSecret) { $loyverseSecret.Dispose() }
    $loyverseSecret = $null
}
```

Esperado: exit 0 y `ready_for_initial_sync`, `writes=false`,
`loyverse_contacted=true`, ID/nombre de la tienda correcta, los ocho conteos cero,
conteos `planned_initial_inserts`, disponibilidad comercial y tombstones. Los
conteos remotos se calculan; no se asumen iguales a los de pruebas históricas.
Los tombstones no insertan filas en una base vacía. El preflight no actualiza
filas ni consume secuencias. `preflight_ok` solo no acredita la tienda ni el plan.

## Autorización y ejecución posterior

Para la autorización de una única carga ya concedida, usar los bloques completos
de [ejecución controlada](neon_stage3e_execute.md). `--execute` ahora requiere
MITBOL_SYNC_APPROVED_PREFLIGHT en memoria para comparar la descarga real antes
de escribir; no basta con reemplazar el modo de este bloque de preparación.

Detenerse tras revisar esa salida. Para la etapa siguiente confirmar expresamente
el host, `mitbol_cloud_dev`, `neondb_owner`, ID/nombre de tienda, revisión y conteos
del plan. Propuesta: «Autorizo la sincronización inicial HU-008 del catálogo de
la tienda [ID/nombre] exclusivamente hacia mitbol_cloud_dev en el host autorizado,
con neondb_owner, según el preflight ready_for_initial_sync revisado».

Sólo con esa autorización se repetirán las comprobaciones y la entrada oculta
del token, y se utilizará la opción existente `--execute` en lugar de `--preflight`,
manteniendo los mismos argumentos de destino y tienda. Después se auditarán
conteos, relaciones, duplicados, enriquecimiento y revisión sin migrar.
No ejecutar ese modo durante la preparación 3E.
La ejecución posterior debe reutilizar el bloque completo, incluidos entrada
oculta, rechazo de token vacío, asignación temporal, reinicio inmediato del
puntero después de liberarlo y restauración en `finally`. El token sólo viaja
al hijo por entorno; no debe añadirse a argumentos, salidas ni archivos.

## Pruebas de esta etapa

Se prueban HTTP con MockTransport y persistencia con datos ficticios en un cluster
PostgreSQL 18 desechable, accesible sólo por loopback. La ruta opcional de fixtures
`MITBOL_SYNC_TEST_CLUSTER` exige directorio TEMP `mitbol-sync-preflight-<uuid>/data`,
puerto explícito, usuario mitbol_ci y coincidencia del data_directory real.
Construye exclusivamente el esquema sintético de pruebas desde metadata y una
marca de revisión de fixture; no ejecuta migraciones ni consulta mitbol_dev/.env.
Las pruebas que crean roles o invocan upgrade se excluyen de esta corrida.

Resultado de la corrida del 4 de noviembre de 2026: **357 passed, 2 deselected**
en 17,50 s. Incluye CLI, servicio HU-008, cliente HTTP, probe, transformación,
modelos de catálogo/enriquecimiento, configuración cloud, destino/TLS y diagnóstico
Neon. Una barrera de acceso a archivos impidió abrir el `.env` real durante toda
la corrida. No hubo llamadas reales a Loyverse o Neon. Las dos advertencias son
deprecaciones existentes de Starlette/httpx y del alias BlockingPortal de AnyIO.

Se corrigió además un defecto de `Settings`: Pydantic construía y leía la fuente
dotenv antes de excluirla en `settings_customise_sources`. Ahora se desactiva
esa lectura antes de construir las fuentes en cloud/test/Render. Cuatro pruebas
nuevas comprueban la ausencia de apertura con modo explícito o por entorno.

La revisión final de tombstones y memoria pasó **135 pruebas**, con ocho
integraciones PostgreSQL omitidas. Se usaron HTTP y sesiones simulados y un hijo
Python sin acceso a servicios. Incluye doce escenarios PowerShell que cuentan
liberaciones del BSTR (éxito, código de error, excepción, entrada vacía, fallo de
reserva y fallo de conversión; variable anterior presente o ausente). El puntero
ya se reiniciaba en el camino normal; se explicitó también el reinicio inmediato
en la limpieza de `finally`. No se cambiaron las reglas comerciales de HU-008.
