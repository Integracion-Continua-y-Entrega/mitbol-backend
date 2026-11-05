# Auditoria de solo lectura. No descarga Loyverse ni ejecuta sincronizaciones.
param([Parameter(Mandatory = $true)][object]$ApprovedManifest)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..\..')
if ($env:APP_ENV -cne 'cloud' -or $env:ADMIN_ENABLED -cne 'false') { throw 'Se requieren APP_ENV=cloud y ADMIN_ENABLED=false.' }
if ($env:EXPECTED_DATABASE -cne 'mitbol_cloud_dev' -or $env:EXPECTED_DATABASE_HOST -cne 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech') { throw 'Destino no autorizado.' }
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) { throw 'Falta DATABASE_URL; no cargar .env.' }
$neonPsql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
if (-not (Test-Path -LiteralPath $neonPsql)) { throw 'No se encontró psql; revisar su ubicación antes de continuar.' }
$psqlVersion = & $neonPsql --version
if ($LASTEXITCODE -ne 0 -or $psqlVersion -notmatch '^psql .*18\.') { throw 'Este bloque requiere psql 18.' }
foreach ($overrideName in @('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS')) {
    if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($overrideName, 'Process'))) { throw 'Existe una sobrescritura libpq no autorizada.' }
}
$neonCa = & .\.venv\Scripts\python.exe -B -c "import os; from app.db.connection import cloud_target_from_environment, connection_arguments; t=cloud_target_from_environment(); u=t.validate_url(os.environ['DATABASE_URL']); print(connection_arguments(u,t)['sslrootcert'])"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $neonCa)) { throw 'No se pudo obtener la CA validada.' }
$quotedNeonCa = $neonCa.Replace('\', '\\').Replace("'", "\'")
$neonBaseConnection = "host=ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech port=5432 dbname=mitbol_cloud_dev sslmode=verify-full channel_binding=require sslrootcert='$quotedNeonCa' gssencmode=disable ssl_min_protocol_version=TLSv1.3 connect_timeout=10"
$previousConsoleEncoding = [Console]::OutputEncoding
$savedAuditEnvironment = @{}
foreach ($variableName in @('MITBOL_SYNC_APPROVED_MANIFEST_B64', 'LC_ALL', 'LC_MESSAGES')) {
    $savedAuditEnvironment[$variableName] = [Environment]::GetEnvironmentVariable($variableName, 'Process')
}
try {
    $json = $ApprovedManifest | ConvertTo-Json -Depth 20 -Compress
    $env:MITBOL_SYNC_APPROVED_MANIFEST_B64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $env:LC_ALL = 'C'
    $env:LC_MESSAGES = 'C'
    foreach ($login in @('neondb_owner', 'mitbol_render_reader')) {
        $readerMode = if ($login -ceq 'mitbol_render_reader') { 'true' } else { 'false' }
        Write-Host "Contraseña de $login (entrada oculta de psql)."
        $auditOutput = @(& $neonPsql -X -W --dbname "$neonBaseConnection user=$login" --set ON_ERROR_STOP=1 --set HISTFILE=NUL --set "reader_mode=$readerMode" --file '.\docs\database\verify_neon_catalog_after_sync.sql')
        if ($LASTEXITCODE -ne 0) { $auditOutput | Write-Host; throw "Auditoría SQL fallida para $login; no repetir la sincronización." }
        $start = [array]::IndexOf($auditOutput, 'MITBOL_CLIENT_CONNINFO_BEGIN')
        $end = [array]::IndexOf($auditOutput, 'MITBOL_CLIENT_CONNINFO_END')
        if ($start -lt 0 -or $end -le ($start + 1)) { throw 'Falta evidencia TLS del cliente psql.' }
        $clientInfo = @{}
        $csvRows = @($auditOutput[($start + 1)..($end - 1)] | ConvertFrom-Csv -ErrorAction Stop)
        foreach ($row in $csvRows) {
            if ([string]::IsNullOrWhiteSpace($row.Parameter) -or $clientInfo.ContainsKey($row.Parameter)) { throw 'Formato conninfo inesperado.' }
            $clientInfo[$row.Parameter] = $row.Value
        }
        if ($clientInfo['SSL Connection'] -cne 'true' -or $clientInfo['SSL Protocol'] -cne 'TLSv1.3') { throw 'El cliente psql no confirma TLS 1.3 activo.' }
        if ($clientInfo['Host'] -cne 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech' -or $clientInfo['Server Port'] -cne '5432' -or $clientInfo['Database'] -cne 'mitbol_cloud_dev' -or $clientInfo['Client User'] -cne $login) { throw 'La identidad de la conexión del cliente no coincide.' }
        $auditOutput | Write-Host
        [pscustomobject]@{ status = 'catalog_verified_after_sync'; login = $login; client_tls = $clientInfo['SSL Protocol']; sslmode = 'verify-full'; channel_binding = 'require' } | ConvertTo-Json
    }
}
finally {
    [Console]::OutputEncoding = $previousConsoleEncoding
    foreach ($variableName in $savedAuditEnvironment.Keys) {
        Set-Item -LiteralPath "Env:$variableName" -Value $savedAuditEnvironment[$variableName]
    }
}
