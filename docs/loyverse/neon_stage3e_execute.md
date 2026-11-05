# Etapa 3E: ejecución manual única y verificación posterior

**Estado final: completada y auditada.** Ambos logins confirmaron
`catalog_verified_after_sync`, TLS 1.3, los conteos 3/5/2/5/4/0/0/0 y cero
duplicados, huérfanos y tombstones importados. Los bloques siguientes documentan
el procedimiento aplicado; no repetir A/B ni las auditorías aprobadas.
Continuar con [la preparación de Render, Etapa 3F](../deployment/render_stage3f.md).

Autorización: mitbol_cloud_dev, host directo
ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech, neondb_owner, revisión
20261102_0003; tienda I.C.E, 9e30730f-a5b4-4485-8cc4-bc499cc6e239.
Sólo el usuario ejecuta estos bloques en su PowerShell configurado. Codex no
ejecuta la sincronización ni obtiene sus credenciales. No hay migraciones,
cambios de esquema/modelos/roles/permisos ni despliegue Render.

La rama de preparación es chore/neon-sync-preflight y contiene cambios pendientes;
no descartarlos ni hacer commits/push automáticamente. No cambiar de versión
entre los bloques. La opción de escritura sigue siendo `--execute`, con los
argumentos existentes de host/base/tienda. Ahora requiere además la variable
temporal `MITBOL_SYNC_APPROVED_PREFLIGHT`, poblada por el bloque B.

El resumen del preflight anterior no guardaba precios, nombres, IDs y relaciones
completos ni huella. No puede demostrarse retrospectivamente que todos esos
campos siguen iguales sólo con sus conteos. El bloque A muestra el manifiesto
público completo para revisarlo; exige los conteos autorizados. Detenerse si algo
no corresponde al catálogo autorizado, aunque los conteos coincidan. El bloque B
repite el preflight y compara su huella con A. La descarga de la ejecución también
se compara, dentro de la transacción, antes del primer DML. Los timestamps de
consulta, orden de páginas y escala decimal equivalente no cambian la huella.

## A. Definir ayudas y repetir el preflight; no escribe

Pegar el bloque completo desde la raíz del repositorio. No cargar .env ni mostrar
DATABASE_URL. El token se pide oculto; no forma parte de argumentos ni archivos.

```powershell
& {
Set-Location C:\Proyectos\mitbol\mitbol-backend

function global:Assert-MitbolNeonContext {
    if ($env:APP_ENV -cne 'cloud' -or $env:ADMIN_ENABLED -cne 'false') { throw 'Se requieren APP_ENV=cloud y ADMIN_ENABLED=false.' }
    if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) { throw 'Falta DATABASE_URL operativa; no cargar .env.' }
    if ($env:EXPECTED_DATABASE -cne 'mitbol_cloud_dev' -or $env:EXPECTED_DATABASE_HOST -cne 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech') { throw 'Destino no autorizado.' }
    if ($env:LOYVERSE_STORE_ID -cne '9e30730f-a5b4-4485-8cc4-bc499cc6e239') { throw 'Tienda no autorizada.' }
}

function global:Invoke-MitbolWithHiddenToken {
    param([scriptblock]$Action)
    $ErrorActionPreference = 'Stop'
    $previousLoyverseToken = [Environment]::GetEnvironmentVariable('LOYVERSE_ACCESS_TOKEN', 'Process')
    $loyverseSecret = $null
    $loyversePointer = [IntPtr]::Zero
    try {
        $loyverseSecret = Read-Host 'Token Loyverse (entrada oculta)' -AsSecureString
        if ($loyverseSecret.Length -eq 0) { throw 'El token no puede estar vacío.' }
        $loyversePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($loyverseSecret)
        $env:LOYVERSE_ACCESS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($loyversePointer)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($loyversePointer)
        $loyversePointer = [IntPtr]::Zero
        & $Action
    }
    finally {
        $env:LOYVERSE_ACCESS_TOKEN = $previousLoyverseToken
        $previousLoyverseToken = $null
        if ($loyversePointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($loyversePointer)
            $loyversePointer = [IntPtr]::Zero
        }
        if ($null -ne $loyverseSecret) { $loyverseSecret.Dispose() }
        $loyverseSecret = $null
    }
}

function global:Invoke-MitbolCatalogCLI {
    param([ValidateSet('Preflight', 'Execute')][string]$Mode)
    $modeFlag = if ($Mode -eq 'Execute') { '--execute' } else { '--preflight' }
    $cliOutput = & .\.venv\Scripts\python.exe -B -m app.cli.sync_catalog $modeFlag --expected-database mitbol_cloud_dev --expected-host ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech --store-id 9e30730f-a5b4-4485-8cc4-bc499cc6e239
    if ($LASTEXITCODE -ne 0) { $cliOutput | Write-Host; throw 'CLI fallida: detenerse y analizar; no reintentar.' }
    return ($cliOutput | ConvertFrom-Json)
}

function global:Assert-MitbolInitialPreflight {
    param($Report)
    if ($Report.status -cne 'ready_for_initial_sync' -or $Report.writes -ne $false -or $Report.loyverse_contacted -ne $true) { throw 'Preflight incompleto.' }
    if ($Report.app_env -cne 'cloud' -or $Report.admin_enabled -ne $false -or $Report.user -cne 'neondb_owner') { throw 'Entorno u operador incorrecto.' }
    if ($Report.database -cne 'mitbol_cloud_dev' -or $Report.host -cne 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech' -or $Report.revision -cne '20261102_0003') { throw 'Destino o revisión incorrectos.' }
    if ($Report.tls.protocol -cne 'TLSv1.3' -or $Report.tls.sslmode -cne 'verify-full' -or $Report.tls.channel_binding -cne 'require') { throw 'TLS no coincide con lo autorizado.' }
    if ($Report.store.id -cne '9e30730f-a5b4-4485-8cc4-bc499cc6e239' -or $Report.store.name -cne 'I.C.E') { throw 'Identidad de tienda incorrecta.' }
    if ($Report.write_permissions_verified -ne $true -or $Report.default_transaction_read_only -cne 'off') { throw 'Permisos o sesión operativa incorrectos.' }
    if (@($Report.table_counts.PSObject.Properties).Count -ne 8 -or @($Report.table_counts.PSObject.Properties | Where-Object { $_.Value -ne 0 }).Count -ne 0) { throw 'Las ocho tablas deben estar vacías.' }
    $expectedInserts = @{ Products = 3; ProductVariants = 5; ProductModifiers = 2; ProductComplements = 5; ProductModifierAssignments = 4 }
    foreach ($tableName in $expectedInserts.Keys) {
        if ($Report.planned_initial_inserts.$tableName -ne $expectedInserts[$tableName]) {
            [pscustomobject]@{ table = $tableName; expected = $expectedInserts[$tableName]; received = $Report.planned_initial_inserts.$tableName } | ConvertTo-Json | Write-Host
            throw 'Cambió el conteo autorizado; revisar antes de continuar.'
        }
    }
    if ($Report.commercially_available_variants -ne 5 -or $Report.commercially_unavailable_variants -ne 0 -or $Report.tombstones.products -ne 2 -or $Report.tombstones.variants -ne 0 -or $Report.tombstones.modifiers -ne 0 -or $Report.tombstones.complements -ne 0) { throw 'Cambió disponibilidad o bajas; revisar la salida.' }
    if ($Report.catalog_sha256 -notmatch '^[a-f0-9]{64}$' -or $null -eq $Report.catalog_manifest) { throw 'Falta manifiesto completo y huella.' }
}

Assert-MitbolNeonContext
if ($null -eq (Get-Variable -Name mitbolNeonInitialSyncAttempted -Scope Global -ErrorAction SilentlyContinue)) { $global:mitbolNeonInitialSyncAttempted = $false }
if ($global:mitbolNeonInitialSyncAttempted) { throw 'Ya se intentó esta operación; auditar antes de repetir.' }
$global:mitbolNeonApprovedPreflight = $null
Invoke-MitbolWithHiddenToken {
    $report = Invoke-MitbolCatalogCLI -Mode Preflight
    $report | ConvertTo-Json -Depth 20 | Write-Host
    Assert-MitbolInitialPreflight $report
    $global:mitbolNeonApprovedPreflight = $report
}
}
```

Esperado: ready_for_initial_sync, ocho ceros, 3/5/2/5/4 (19), cinco disponibles,
dos bajas de productos y cero otras bajas. Revisar nombres, precios, IDs, padres,
asociaciones y bajas en catalog_manifest. No compartir credenciales. Si hay
diferencias respecto a lo autorizado, no ejecutar B; compartir sólo el manifiesto
público y los cambios observados. No se compara por nombre para resolver IDs.

## B. Repetir comprobaciones y ejecutar exactamente una vez

Usar el mismo PowerShell, código y manifiesto A revisado. No ejecutar si A falló.
El token se vuelve a introducir oculto, para no dejarlo cargado durante la revisión.
No añadir bucles, reintentos ni una segunda llamada a Execute.

```powershell
& {
Assert-MitbolNeonContext
if ($null -eq $global:mitbolNeonApprovedPreflight) { throw 'Falta el preflight A revisado.' }
if ($global:mitbolNeonInitialSyncAttempted) { throw 'Ya hubo un intento; verificar el estado antes de repetir.' }
$global:mitbolNeonInitialSyncAttempted = $true

Invoke-MitbolWithHiddenToken {
    $fresh = Invoke-MitbolCatalogCLI -Mode Preflight
    $fresh | ConvertTo-Json -Depth 20 | Write-Host
    Assert-MitbolInitialPreflight $fresh
    if ($fresh.catalog_sha256 -cne $global:mitbolNeonApprovedPreflight.catalog_sha256) {
        [pscustomobject]@{ expected = $global:mitbolNeonApprovedPreflight.catalog_manifest; received = $fresh.catalog_manifest } | ConvertTo-Json -Depth 20 | Write-Host
        throw 'El catálogo cambió desde A. Detenerse sin ejecutar.'
    }
    $previousApproval = [Environment]::GetEnvironmentVariable('MITBOL_SYNC_APPROVED_PREFLIGHT', 'Process')
    try {
        $env:MITBOL_SYNC_APPROVED_PREFLIGHT = $fresh | ConvertTo-Json -Depth 20 -Compress
        $result = Invoke-MitbolCatalogCLI -Mode Execute
        $result | ConvertTo-Json -Depth 20 | Write-Host
        if ($result.status -cne 'synchronized' -or $result.writes -ne $true) { throw 'Resultado no confirmado; auditar sin repetir.' }
    }
    finally {
        $env:MITBOL_SYNC_APPROVED_PREFLIGHT = $previousApproval
    }
}
}
```

Antes del primer DML, el servicio existente valida el nuevo plan contra A/B,
revalida destino/revisión/operador/permisos y modo de escritura, toma locks
SHARE ROW EXCLUSIVE NOWAIT sobre las ocho tablas y repite el vaciado. Los locks
no cambian datos, permiten SELECT y evitan otro DML hasta el commit. Si no se
pueden adquirir o algo cambió, se aborta. El bloqueo advisory existente sigue
cubriendo desde antes del GET hasta el commit. No hay reintentos HTTP en esta CLI
controlada (max_attempts=1), ni reintentos de transacción.
Se exige READ COMMITTED para que el conteo posterior a los locks vea cualquier
escritura confirmada durante la descarga; no se cambia el aislamiento automáticamente.

Esperado tras commit: status=synchronized, writes=true, store_id autorizado,
products_processed=3, variants_processed=5, modifiers_processed=2,
complements_processed=5, assignments_inserted=4, assignments_removed=0 y retired
todo cero. catalog_changed muestra diferencias y writes=false, sin DML.
Las otras excepciones no prueban por sí mismas si el commit llegó a completarse.

Si falla antes de commit, la transacción revierte filas; un corte durante commit
puede dejar resultado incierto (todo o nada), nunca asumir éxito o rollback sólo
por un error de conexión. No repetir A/B: ejecutar C y analizar. Las secuencias
pueden avanzar aun con rollback o UPSERT en conflicto. No repararlas manualmente.
Los UPSERT por loyverse_id y la PK compuesta evitan duplicados; se conservan
enriquecimiento, preferencias locales y padres históricos. La regla de precios
y disponibilidad sigue siendo la de HU-008. Una sincronización futura del servicio
sigue siendo idempotente; este procedimiento inicial exige vaciado y no se reutiliza
para una segunda carga.

## C. Verificar con SQL de sólo lectura y un login nuevo del lector

No usar verify_neon ni verify_neon_stage3c: exigen condiciones previas que ya no
corresponden. El SQL nuevo compara las filas con el manifiesto aprobado, verifica
los ocho conteos, duplicados, siete FK, precios, disponibilidad y ausencia de bajas
importadas/enriquecimiento. Termina con ROLLBACK. Un fallo devuelve exit no cero;
la conexión cerrada revierte la transacción de lectura.

Corrección para Neon: pg_stat_ssl describe el salto observado por el backend,
no necesariamente el TLS del cliente al proxy. Se conserva como diagnóstico
backend_ssl_observed/backend_tls_version_observed, sin usarlo como veto TLS.
El bloque verifica la conexión del mismo proceso psql mediante `\conninfo` CSV,
con nombres de campos estables de psql 18 y LC_ALL=C. Exige SSL Connection=true,
SSL Protocol=TLSv1.3 y host/base/usuario/puerto exactos. libpq sigue imponiendo
verify-full, CA, channel_binding=require, gssencmode=disable y mínimo TLS 1.3
antes de ejecutar SQL. La identidad y la lectura se comprueban además en SQL.
No se vuelve a ejecutar A/B ni se necesita el token Loyverse para esta auditoría.

El bloque C delega esas mismas comprobaciones en `verify_neon_after_sync.ps1`;
no agrega otra auditoria. Ejecutar unicamente C en el PowerShell que conserva
`mitbolNeonApprovedPreflight`. El manifiesto publico se serializa como JSON,
se codifica explicitamente en UTF-8 y viaja como Base64 ASCII por la variable
temporal `MITBOL_SYNC_APPROVED_MANIFEST_B64`. El SQL fija `\encoding UTF8`,
sustituye `:'approved_manifest_b64'` fuera de bloques DO/$$ y decodifica con
`convert_from(decode(..., 'base64'), 'UTF8')::jsonb`. No se interpola JSON crudo.
Se restauran el entorno temporal y la codificacion de salida de PowerShell.

Causa reproducida en psql 18 para Windows: `\getenv` con JSON que contiene
acentos puede entregar bytes de la pagina de codigos nativa, incompatibles con
la conexion UTF-8. El escape del literal falla con `invalid multibyte character`
y deja `:'approved_manifest'` sin sustituir; por eso aparece despues el error
cerca de `:`. No era una referencia dentro de DO en la version revisada.
Fijar solo PGCLIENTENCODING=UTF8 no corrige esos bytes; Base64 evita el problema.

```powershell
& {
    Set-Location C:\Proyectos\mitbol\mitbol-backend
    $approved = Get-Variable -Name mitbolNeonApprovedPreflight -Scope Global -ErrorAction SilentlyContinue
    if ($null -eq $approved -or $null -eq $approved.Value.catalog_manifest) {
        throw 'Falta el manifiesto aprobado conservado; no repetir A/B ni la sincronizacion.'
    }
    & .\docs\loyverse\verify_neon_after_sync.ps1 -ApprovedManifest $approved.Value.catalog_manifest
}
```

Esperado: 3 Products, 5 ProductVariants, 2 ProductModifiers, 5 ProductComplements,
4 ProductModifierAssignments y cero ProductImages/Tags/ProductTags. Alembic
20261102_0003 se verifica con el owner; el lector no necesita SELECT sobre
alembic_version. SQL devuelve catalog_data_verified_after_sync; sólo tras comprobar
el TLS y la identidad del cliente, PowerShell devuelve catalog_verified_after_sync.
Cada login debe mostrar cero
duplicate_ids/orphan_relationships/tombstones_imported y available_variants=5.
La consulta efectiva de las ocho tablas desde el login nuevo prueba su lectura
sin conceder privilegios nuevos. backend_ssl_observed=false no sustituye ni
contradice la evidencia TLS del cliente al proxy. No certifica ausencia absoluta de cualquier
efecto persistente posible fuera del catálogo (riesgo residual aceptado en 3D).

Compartir únicamente JSON de A/B (sin credenciales), status/exit de la ejecución,
conteos y diagnósticos SQL, revisión, nombres de los logins y los dos resultados
catalog_verified_after_sync. Si hay error, compartir su código/mensaje seguro y
detenerse. No compartir DATABASE_URL, DSN con contraseña, tokens ni passwords.
No avanzar a Render hasta analizar esos resultados.

## Validación local del procedimiento

388 pruebas aprobadas; dos excluidas por crear roles o invocar upgrade, con dos
advertencias existentes de Starlette/httpx y AnyIO. La persistencia y el SQL se
probaron en PostgreSQL 18 temporal con datos sintéticos, metadata de fixture y
sin migraciones. Los casos de reader_mode simulan identidad/TLS sólo en copias
temporales del SQL, sin crear roles: el login real del lector queda para C.
Se probaron cambios de precio con iguales conteos, bajas, nombres, IDs, grupos,
disponibilidad, fallos previos a DML, vaciado bajo locks, rollback, ejecución
única y restauración de secretos. Un precio alterado hace fallar la auditoría
SQL con exit 3. En psql 18 no se usa `\quit 3`, que ignora su argumento; se
propaga un error SQL de lectura con ON_ERROR_STOP y cierre con rollback.
No se abrió el .env real ni se conectó con Neon, mitbol_dev o Loyverse real.

Corrección final del Bloque C: 37 pruebas aprobadas y una prueba de
sincronización sintética excluida. El recorrido real PowerShell -> entorno ->
psql se probó en PostgreSQL 18 temporal con nombres acentuados, apóstrofos,
dos puntos y dollar-quotes dentro de los datos. Se rechazaron cambios de
nombre, precio, padre y tienda con iguales conteos; también manifiestos
ausentes, vacíos, Base64/UTF-8/JSON inválidos. La identidad y la lectura se
verificaron con SQL real; TLS/conninfo, mediante mocks de psql que incluyen
cliente TLS 1.3/backend false, SSL inactivo, TLS 1.2, evidencia ausente y
destino/usuario incorrectos. Se verificó restauración del entorno y de la
codificación de salida en éxito y error. No se usaron contraseñas reales ni
se repitió la sincronización inicial.

Referencias: [psql 18 y conninfo](https://www.postgresql.org/docs/18/app-psql.html),
[verify-full y certificados](https://www.postgresql.org/docs/18/libpq-ssl.html),
[pg_stat_ssl por backend](https://www.postgresql.org/docs/18/monitoring-stats.html#MONITORING-PG-STAT-SSL-VIEW).
