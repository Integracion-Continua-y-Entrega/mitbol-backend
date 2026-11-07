# MITBOL — Preflight de migraciones de autenticación en Neon

Fecha: 6 de noviembre de 2026, America/Mexico_City. Base de código auditada:
`388556f5a422a71cd71107aee560891f5844ca16`, integrada en develop. Trabajo local en
`audit/neon-auth-migration-preflight`; develop no se modifica.

**Estado actual: NO-GO para reintentar hasta diagnosticar el fallo de 0004.** El
operador confirmó después del intento: Alembic sigue en `20261102_0003` y ninguna
de las siete tablas de 0004 existe. El ejecutor anterior sólo informó `stopped`;
no conservó etapa/tipo/SQLSTATE y no permite atribuir una causa. Usar 6.7, sólo
lectura, antes de proponer otro intento. No repetir A–E ni restaurar el snapshot.

El GO técnico previo se basó en identidad/revisión, estructura y A–E aprobadas. El snapshot
ya fue creado y verificado por el operador: `mitbol-before-auth-migrations`,
production, 39.95 MB, expiración Never y opción Restore disponible. No hay otro
preflight pendiente. Creación confirmada: **2026-11-06 19:20 UTC** (13:20 CST,
UTC−6). El nombre/rama/instante identifican el punto en Console; su ID interno
no es obligatorio para este procedimiento, que no utiliza la API de snapshots.
Antes del primer DDL se confirmará el endpoint directo de ejecución y se
obtendrá autorización explícita. No se repite la creación ni se borra el
snapshot; no se exige un ensayo nuevo. No se ha probado una restauración.

**Compatibilidad inmediata comprobada:** develop local y remoto coinciden en
`388556f5a422a71cd71107aee560891f5844ca16` (consulta git ls-remote de sólo lectura,
sin fetch). Migraciones, env.py, validation.py y connection.py locales coinciden
con ese develop. Alembic/SQLAlchemy/psycopg2 y CA certifi están disponibles.

La evidencia real fue aportada por el operador, sin conexión autenticada del
agente. Los ensayos PostgreSQL temporales pertenecen a la etapa anterior.
Este documento sólo prepara acciones futuras: no autoriza por sí mismo backups,
migraciones, ramas, restauraciones, permisos o cuentas. El backend público sigue
con administración apagada. Los scripts externos y la política TLS no cambian.

## 1. Evidencia real, histórica y pendiente

| Dato | Fuente y alcance |
| --- | --- |
| Backend nuevo desplegado, Swagger público y menú 200 | Confirmado por el usuario al solicitar esta fase. No prueba la revisión Alembic ni la ausencia de tablas de identidad. |
| HTTP público observado en esta auditoría | GET /, /api/menu y detalle de un ID obtenido del menú: 200. Menú: 3 productos y 5 variantes. OpenAPI: cuatro rutas, sólo GET, sin rutas auth/admin. Es comprobación real por API pública, no acceso directo al esquema de Neon. |
| Rama production, base mitbol_cloud_dev, owner, PostgreSQL 18.6, public, sólo lectura | Confirmado por el operador en bloque 0; sin conexión del agente. |
| Alembic 20261102_0003 | Confirmado por el operador tras ejecutar correctamente el bloque 1 corregido. |
| Estructura comercial del bloque 2 | Operador recuperó ocho tablas, columnas/tipos/NOT NULL/defaults, 14 índices con indisvalid/indisready=true y restricciones mostradas con convalidated=true. También metadatos de alembic_version. Sin anomalías comunicadas; no se revisaron todas las celdas originales. No repetir estas consultas. |
| Ventana de restauración: 6 horas, para todas las ramas incluida production | Confirmación del usuario desde Settings → Postgres → History window, sin guardar cambios. Es evidencia aportada por el usuario, no una consulta API del agente. |
| Plan Free | Confirmado manualmente por el usuario; snapshot existente documentado abajo. Sin restauración ensayada. |
| Acceso disponible al agente | Sin conector Neon autenticado, CLI Neon ni variables cloud de conexión. El único .env inspeccionado tiene DATABASE_URL local de mitbol_dev; no se utiliza ni se publica su contraseña. No se buscaron credenciales en otros almacenes. |
| A: baseline de datos | Operador registró conteos y SHA-256 de las ocho tablas. Los valores completos permanecen en sus evidencias; no se inventan ni se copian datos comerciales aquí. |
| B: conflictos | conflicts=0, según ejecución del operador. |
| C/D: permisos | Operador confirmó permisos de creación/versión y restricciones del lector; any_write=false y any_sequence_privilege=false en todas las filas. |
| E: automatismos DDL | alerts=0; sin hallazgos de defaults relevantes o event triggers habilitados en esa inspección. |
| Backup & Restore | Operador vio selección de momento para Restore from history, Preview data, Restore y Create snapshot en production. No ha ejecutado esos cambios. La interfaz acredita controles disponibles; no una restauración ensayada ni capacidad/permisos efectivos de todas las operaciones. |
| Snapshot creado y verificado | mitbol-before-auth-migrations, production, creado 2026-11-06 19:20 UTC / 13:20 CST (UTC−6), 39.95 MB, Never, listado con Restore. ID interno no necesario. No se restauró. Hubo un intento fallido de 0004; la comprobación posterior del operador confirma 0003 y ausencia de las siete tablas. |
| Datos previos del catálogo | Etapa 3F documentó 3 productos, 5 variantes, 2 grupos, 5 complementos, 4 asignaciones y 0 imágenes/tags/relaciones de tags, en 0003. Son evidencia histórica; no constituyen el baseline actual. |

La disponibilidad de psql/pg_dump 18 local permite preparar comandos, pero no
concede acceso a Neon. La rama llamada production no demuestra por sí sola que
sea una root branch ni identifica de manera inequívoca proyecto/branch ID.

## 2. Auditoría de migraciones

Cadena única, sin bifurcaciones:

```text
20261102_0003 -> 20261105_0004 -> 20261106_0005
```

Los sufijos de los nombres de archivo no son los revision IDs para Alembic.

| Revisión | DDL / restricciones / índices | Datos iniciales |
| --- | --- | --- |
| [0004](../../migrations/versions/20261105_0004_auth_core.py) | Siete tablas: Persons, PersonalAccounts, Employees, Roles, EmployeeRoles, AccountSessions, AuditEvents. Cuatro secuencias SERIAL/BIGSERIAL, PK/UQ/FK, CHECK de identidad/sesión/auditoría. Índices explícitos ix_AccountSessions_account_id, ix_AccountSessions_expires_at e ix_AuditEvents_actor_created. | Exactamente dos filas en Roles: códigos admin y staff. No son roles PostgreSQL. No crea personas, cuentas, empleados ni contraseñas. |
| [0005](../../migrations/versions/20261106_0005_persistent_auth_limits.py) | **Dos tablas**, AuthRateLimitPolicy y AuthRateLimits. PK de política única policy_id=1, fingerprints/keys hexadecimales de 64 caracteres, attempts>0, expires_at>started_at, índice ix_AuthRateLimits_expires_at. Sin secuencias nuevas. | Ninguna fila. La política/contadores se insertarán sólo al usar el servicio administrativo futuro. |

Las fechas usan timestamp with time zone. Las FK sólo enlazan las tablas nuevas;
no enlazan ni alteran el catálogo. No hay ALTER/DROP/TRUNCATE/UPDATE/DELETE de
tablas comerciales en los upgrades, llamadas externas, grants o CREATE ROLE.
Alembic actualiza su propia fila de versión en cada paso. Los INSERT de Roles
son DML deliberado, separado conceptualmente del DDL.

Los nombres conservan mayúsculas y comillas de PostgreSQL. Se revisaron también
los índices PK/UQ implícitos, las cuatro secuencias y los tipos compuestos de las
tablas: un objeto preexistente con esos nombres en public bloquea la operación.
Un nombre igual en otro schema se inventariaría pero no se altera; queda sujeto a
revisión del inventario y de search_path.

Los modelos comerciales siguen en Base y los nueve modelos nuevos en AuthBase.
El laboratorio contrastó tipos, nullability, defaults, restricciones, relaciones
e índices mediante compare_metadata: sin diferencias en 0005. La convención de
nombres PK/UQ/FK coincide con las restricciones explícitas de las revisiones.

**Schema:** las migraciones no especifican schema=public. Dependen del schema
de creación de la sesión. El procedimiento fija SET LOCAL search_path=public
en la misma transacción/conexión suministrada a Alembic; la inspección rechaza
un schema inicial distinto. No se cambia persistentemente el rol ni la base.

No hace falta retirar ni ampliar permisos del lector. Revisar defaults globales
y de schema del creador neondb_owner y de PUBLIC: los grants futuros indebidos
podrían exponer las tablas recién creadas. El verificador del lector existente
comprueba defaults, membresías, ownership y permisos efectivos. No ejecutar
prepare_render_reader.sql de nuevo: el lector ya existe.

Los downgrades sí son destructivos: 0005 elimina límites/política y 0004 elimina
identidad, sesiones y auditoría. No constituyen un backup.

## 3. Consultas de lectura que debe realizar el operador

**Preflight mínimo completado:** 0/1, estructura de 2 y A–E están confirmados.
No volver a ejecutarlos ahora. A se reutilizará para comparar el contenido tras
cada revisión y D para verificar permisos, porque entonces habrá un estado nuevo.
Lo siguiente conserva la variante exhaustiva de psql como referencia opcional;
no es un requisito adicional ni sustituye el procedimiento mínimo del apartado 6.
El guard TLS de ese archivo no se ha retirado: si un cliente externo lo rechaza,
no forzarlo ni usar Console para certificar TLS externo. La función de migración
del apartado 6 verifica TLS efectivo en el driver antes de cualquier DDL.

Se preparó [neon_auth_migration_preflight.sql](../database/neon_auth_migration_preflight.sql).
Sólo usa transacción REPEATABLE READ READ ONLY, SELECT, ajustes locales, DO de
validación/lectura y ROLLBACK. Timeout por sentencia 15 s y lock timeout 3 s.
Errores detienen psql con código no cero; no basta buscar una frase de éxito.

Acepta únicamente los revision IDs 0003/0004/0005 indicados arriba y exige el
esperado en cada invocación. Antes de consultar objetos valida base, ambas
identidades, PostgreSQL 18, public, TLS y transacción de lectura. Comprueba ocho
tablas ordinarias de catálogo; antes de 0004 rechaza 31 nombres de relaciones
previstas y tipos compuestos conflictivos. Tras cada paso comprueba las siete o
nueve tablas, los dos códigos iniciales y la ausencia de filas administrativas
no esperadas mientras la administración sigue apagada.

Informa schemas/objetos/owners, columnas, PK/FK/UQ/CHECK, índices y su validez,
roles/membresías, permisos efectivos del lector, defaults, extensiones y event
triggers. Defaults, CHECK e índices se muestran con huellas para no volcar
literales inesperados. Para cada tabla comercial emite CATALOG_FINGERPRINT con
conteo y SHA-256 del contenido ordenado; emite CATALOG_SEQUENCE con last_value
e is_called sin usar nextval/setval. No lee datos de cuentas, passwords o tokens.
Las huellas requieren lectura completa del catálogo; detenerse ante timeout y
revisar volumen/costo antes de ajustar límites. No cambiar el script por un dump
de tablas sensibles para resolver un timeout.

Los resultados son un inventario para revisión, no una autorización automática:
revisar event triggers activos, extensiones/defaults inesperados, objetos de otro
schema y cualquier permiso ampliado. El SQL no demuestra que el endpoint sea de
la rama production: eso requiere confirmar Console y su correspondencia TLS.

**Preparación local del operador, sin cambiar Render.** Usar una terminal nueva,
sin transcripción de secretos. Confirmar primero en Console proyecto/branch IDs,
root/parent de production, base y endpoint directo. El hostname siguiente procede
de la documentación histórica; si Console no coincide, detenerse y revisar este
procedimiento antes de conectarse. No reutilizar la URL pooled de Render.

```powershell
$ErrorActionPreference = 'Stop'
$NeonPsql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$NeonDirectHost = 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
if ((Read-Host 'Host directo de production confirmado en Console') -cne $NeonDirectHost) {
    throw 'Endpoint no coincide: revisar antes de conectar.'
}
foreach ($key in @('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGPASSWORD','PGPASSFILE')) {
    if ([Environment]::GetEnvironmentVariable($key,'Process')) { throw "Limpiar/revisar override $key en esta terminal." }
}
$NeonCa = & .\.venv\Scripts\python.exe -B -c 'import certifi; print(certifi.where())'
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $NeonCa)) { throw 'CA no disponible.' }
$NeonCommon = "host=$NeonDirectHost port=5432 dbname=mitbol_cloud_dev sslmode=verify-full sslrootcert='$NeonCa' channel_binding=require gssencmode=disable ssl_min_protocol_version=TLSv1.2 connect_timeout=10 options='-c default_transaction_read_only=on -c search_path=public'"
& $NeonPsql -X -W --dbname "$NeonCommon user=neondb_owner" -v ON_ERROR_STOP=1 -v expected_revision=20261102_0003 -f .\docs\database\neon_auth_migration_preflight.sql
if ($LASTEXITCODE -ne 0) { throw 'Preflight owner rechazado: no migrar.' }
& $NeonPsql -X -W --dbname "$NeonCommon user=mitbol_render_reader" -v ON_ERROR_STOP=1 -f .\docs\database\verify_render_reader.sql
if ($LASTEXITCODE -ne 0) { throw 'Perfil lector no aprobado: no migrar.' }
```

psql solicita la contraseña en entrada oculta; no va en argumentos/archivos.
Registrar salidas saneadas fuera del repo. No usar app.cli.verify_neon como
preflight incremental: ese CLI exige base vacía y ninguna revisión y por diseño
rechaza una base válida en 0003. Tampoco usar el CLI de sincronización Loyverse.

## 4. Ensayo aislado ejecutado

[test_neon_auth_migration_preflight.py](../../tests/test_neon_auth_migration_preflight.py)
crea bases propias mitbol_test_auth_UUID en un PostgreSQL 18 temporal validado;
reconstruye 0003 mediante Alembic y siembra sólo datos ficticios en las ocho
tablas, incluyendo precios decimales, IDs Loyverse ficticios, tags e imagen.
Crea el lector **antes** de 0004, con los permisos existentes de catálogo.

Se aplican 0004 y 0005 en transacciones separadas y se comprueba después de cada
commit: filas completas, precios/IDs, columnas/PK/FK/UQ/CHECK/índices y estado de
secuencias comerciales idénticos; raíz/menú/detalle con login real del lector;
404 de login; SELECT a cada tabla de seguridad rechazado; verificador independiente
del lector aprobado. Tras 0005 se ensaya exclusivamente en laboratorio el escritor
mínimo y su verificador: precios e IDs Loyverse continúan prohibidos.

La copia de inspección de laboratorio sustituye exclusivamente base/operador y
la condición TLS: initdb trust en loopback no proporciona TLS. El archivo remoto
conserva TLS obligatorio y se prueba que rechaza ese laboratorio sin sustituciones.
Esto no acredita TLS o privilegios reales de Neon.

Las pruebas negativas cubren argumento ausente, revisión incorrecta, destino
remoto guardado, schema equivocado y conflictos de tabla/índice/tipo. Se inyectan
fallos tras DDL parcial en ambas revisiones y se comprueba rollback de tablas y
alembic_version. También se demuestra que los downgrades borran registros
sintéticos de auditoría/límites aunque conserven el catálogo; re-upgrade no los
recupera.

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra --tb=short tests/test_neon_auth_migration_preflight.py tests/test_auth_persistence.py tests/test_admin_writer.py tests/test_auth_phase5.py
```

Resultado final: **168 aprobadas**, cero errores/fallos/omisiones, 80.78 s; dos
deprecaciones existentes de Starlette/AnyIO. Quince casos nuevos más 153 regresiones.
Ambos clústeres detenidos. Evidencia:
`%TEMP%\mitbol-sync-preflight-b3917d581ab042c1b6868f173ba0b1b5\test-run.log` y
`%TEMP%\mitbol-neon-auth-preflight-complete-20261106.xml`.
El primer ensayo encontró tres salidas erróneas del nuevo script: psql ignora un
argumento de código de salida en quit. Se reemplazó por excepciones SQL con
ON_ERROR_STOP, se añadió el caso sin argumento y se repitió la selección completa.
Las migraciones originales no se modificaron.

Se comprobó que el inventario cubre las 31 relaciones derivadas de los modelos,
incluido el índice UNIQUE de token_hash de sesiones. Otra regresión verifica que
el laboratorio no solicita la fixture de desarrollo ni carga .env cuando falta
la identificación del clúster temporal. Las plantillas de permisos existentes,
dependencias, enlaces locales y sintaxis de todos los comandos PowerShell/Python
del documento también se verificaron. Estos últimos sólo se analizaron; no se
ejecutaron contra Neon. No se repitió la suite completa en esta auditoría.

## 5. Respaldo y recuperación: ventana confirmada de 6 horas

**Datos confirmados:** plan Free, seis horas en Console y snapshot previo creado.
No se presupone backup programado ni recursos ilimitados para restaurar.
La [documentación de history window](https://neon.com/docs/postgres/backup-restore/history-window)
indica que en Free el máximo es seis horas y la historia tiene
un límite de 1 GB. Si se necesita PITR como alternativa, comprobar el instante
realmente seleccionable: la configuración no garantiza todo el rango. Esto no
exige repetir consultas para la ejecución con el snapshot ya identificado.

**Punto previo ya creado:** `mitbol-before-auth-migrations`, production, 39.95 MB,
expiración **Never**, creado **2026-11-06 19:20 UTC**, disponible con Restore
según el operador. No volver a crearlo. Nombre/rama/instante quedan vinculados
al baseline A guardado. Registrar ID/branch ID sólo si la interfaz los muestra:
no son un requisito del ejecutor Alembic ni de la selección manual en Console.
No consultar API ni pedir claves para obtener un identificador que no usamos. Su
existencia está confirmada por el usuario; no se afirma restauración ensayada.
Free incluye un snapshot manual según [planes de Neon](https://neon.com/docs/introduction/plans).
Mantener acceso y cupo para recuperación; si surge un impedimento real para usarlo,
detenerse. No borrar ni reemplazar el snapshot al finalizar estas migraciones.

El snapshot preserva el estado anterior mientras siga retenido y puede permitir
recuperarlo fuera de las seis horas; no es un respaldo externo independiente ni
una garantía de retención ilimitada. Véase la explicación de
[snapshots de Neon](https://neon.com/blog/three-ways-to-use-your-snapshots).
No pulsar Preview data o Restore como requisito de creación: una previsualización
no equivale a un ensayo y algunas rutas aprovisionan recursos. La recuperación
de snapshot puede crear una rama y, al restaurar sobre un destino existente,
finalizar moviendo su compute; eso requiere revisar destino/alcance y autorización
propia, según el [flujo oficial de recuperación](https://neon.com/blog/neon-sdk).
No prometer que cualquier Restore devuelve production sin otros pasos.

No es necesario añadir pg_dump, otra rama o un ensayo completo a esta
intervención mínima. Si el snapshot falla, no queda disponible o no puede usarse,
detenerse y acordar otra vía; no migrar basándose sólo en los botones de Console.
PITR queda como alternativa dentro del historial real, con sus requisitos root,
permisos/cupo y alcance de rama. El usuario creó el snapshot; no se ha utilizado
para restaurar. El agente no realizó cambios en Neon.

La [restauración de Neon](https://neon.com/docs/postgres/backup-restore/branch-restore)
requiere comprobar que la rama sea root y que el instante/LSN exista en historia.
Restaura el estado de todas las bases de la rama, reemplaza cambios posteriores
y corta temporalmente conexiones. Por eso debe autorizarse como operación de
infraestructura, con inventario de otras bases afectadas. No equivale a deshacer
sólo 0004/0005 y puede interrumpir el menú. No se ejecutó ni se creó rama alguna.

| Mecanismo | Qué recupera / condición |
| --- | --- |
| Rollback transaccional de una revisión fallida | DDL/seed/version de esa transacción; comprobar estado tras error. No deshace una revisión anterior ya confirmada. |
| Downgrade Alembic | Elimina objetos nuevos; pierde identidad/auditoría/contadores. Sólo candidato tras autorización distinta y comprobación de ausencia de datos que conservar. |
| PITR dentro de la ventana | Estado histórico de rama completa, sujeto a disponibilidad/root/permisos y aceptación de pérdida posterior. Registrar t0 UTC/LSN y margen operativo antes del primer DDL. |
| Snapshot manual previo | Creado/verificado por el operador, identificado por nombre/rama/UTC. Conservarlo; restaurarlo no forma parte de la ejecución de migraciones. |
| pg_dump custom protegido + restauración ensayada | Copia independiente para conservar más allá de seis horas. Debe aprobarse su creación, ubicación y prueba de restauración, sin subir datos a Git. |
| Recuperación selectiva desde copia histórica | Extraer sólo datos necesarios de un entorno de recuperación autorizado y reconciliar cambios posteriores; no restaurar toda production de forma automática. |

Alternativa opcional si se necesita una copia externa: aprobar un dump protegido
y su restauración en destino separado. No es otro bloqueo del procedimiento con
snapshot disponible. La [guía Neon de pg_dump](https://neon.com/docs/manage/backup-pg-dump)
indica usar conexión directa. pg_dump/pg_restore 18 están disponibles localmente;
eso no prueba que exista un respaldo restaurable. Un dump de base no sustituye
el inventario de roles/grants de clúster ni respalda archivos externos de imágenes.

Comandos preparados para **otra autorización**, no ejecutados. La carpeta debe
preexistir fuera del repo con acceso restringido; no usar salida estándar para
transportar un archivo binario en PowerShell:

```powershell
$BackupDir = Read-Host 'Directorio protegido aprobado fuera del repositorio'
$ResolvedBackupDir = (Resolve-Path -LiteralPath $BackupDir).Path
$RepoDir = (Resolve-Path -LiteralPath '.').Path.TrimEnd('\')
if ($ResolvedBackupDir -eq $RepoDir -or $ResolvedBackupDir.StartsWith($RepoDir + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'No guardar backups en el repo.' }
$DumpFile = Join-Path $ResolvedBackupDir ("mitbol-before-auth-" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '.dump')
if (Test-Path -LiteralPath $DumpFile) { throw 'El archivo ya existe; no sobrescribir un respaldo.' }
& 'C:\Program Files\PostgreSQL\18\bin\pg_dump.exe' -W --dbname "$NeonCommon user=neondb_owner" --format=custom --file $DumpFile
if ($LASTEXITCODE -ne 0) { throw 'Backup fallido: no migrar.' }
Get-FileHash -LiteralPath $DumpFile -Algorithm SHA256
& 'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe' --list $DumpFile
if ($LASTEXITCODE -ne 0) { throw 'Archivo no legible: no migrar.' }
```

Listar el archivo **no prueba su restauración**. El ensayo de pg_restore debe
definir y autorizar por separado un destino vacío aislado, propietarios/roles,
comprobaciones de contenido/ACL y tratamiento de datos; no se propone un comando
que pueda apuntar por accidente a production. Sin ese destino y aprobación no
se crea ni restaura nada. No hay instrucción de restauración destructiva automática.

Para la ventana de seis horas: escoger t0 justo antes del cambio y completar
migración y validación en una intervención corta atendida. Propuesta de margen:
si consume una hora sin cierre satisfactorio, detener avances y decidir recuperación
mientras t0 sigue disponible. No esperar al límite de seis horas. Verificar la
historia real y cambios concurrentes antes de restaurar; no ampliar retención ni
cambiar el plan durante este preflight.

## 6. Procedimiento real futuro: ejecutar sólo después de autorización explícita

### 6.1 Condiciones inmediatamente anteriores

1. Revisar esta rama y pruebas. Fijar SHA aprobado del código, checksums de las
   revisiones y revisión esperada 0003. No usar upgrade head ni autogenerate/stamp.
   Conservar A; si los datos cambiaron desde ese baseline, refrescar sólo A
   antes del snapshot y coordinar que no cambien hasta capturarlo.
2. Conservar el nombre/rama/UTC del snapshot existente junto a A.
   Elegir intervención corta atendida sin escrituras concurrentes del catálogo.
   No repetir Create snapshot ni pulsar Restore. Las seis horas siguen siendo
   sólo el límite del historial PITR, no la expiración Never del snapshot.
3. Confirmar en Render, sin cambiarlo: AUTH_ENABLED=false, ADMIN_ENABLED=false,
   DATABASE_URL del lector. Coordinar que ningún operador/job/sync cambie catálogo
   durante la comparación. Registrar respuestas públicas y un ID real del menú.
4. No repetir ahora el preflight aprobado. Al abrir la conexión de ejecución,
   la función 6.3 vuelve a validar destino externo/driver TLS, roles, PG18 y
   revisión inmediatamente anterior. Confirmar el mismo branch/endpoint directo
   en Console: SQL no distingue por sí solo ramas con nombres de base iguales.
5. Guardar juntos SHA, A y snapshot nombre/UTC/origen. Si cambia código/destino/
   esquema/permisos, reevaluar sólo lo afectado. No sincronizar Loyverse como
   preparación ni recuperación.

El punto principal es el snapshot registrado, no una consulta de reloj o un
nombre escrito en este documento. Registrar también la hora UTC justo anterior
al DDL si se conserva PITR como alternativa; sólo se usará si ese instante sigue
disponible en Console y se autoriza la restauración. No hace falta otro SELECT
de preflight para crear el punto.

### 6.2 Preparación local sin conexión

**Preparación local segura:** ejecutar sólo este bloque antes de autorizar DDL.
Lee este documento, carga la función 6.3 y compila 6.4 como scriptblock, sin
invocarlo. No abre conexión, no pide contraseña y no escribe en Neon. Mantener
la rama de trabajo actual; no cambiar a develop ni hacer fetch/push.

```powershell
Set-Location 'C:\Proyectos\mitbol\mitbol-backend'
$ErrorActionPreference = 'Stop'
$MitbolExpectedCommit = '388556f5a422a71cd71107aee560891f5844ca16'
$MitbolActualCommit = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $MitbolActualCommit -cne $MitbolExpectedCommit) { throw 'Codigo distinto del revisado.' }
git diff --quiet $MitbolExpectedCommit -- app migrations
if ($LASTEXITCODE -ne 0) { throw 'Cambios de backend/migraciones sin revisar.' }
$MitbolDocument = Get-Content -LiteralPath .\docs\security\neon_auth_migration_preflight.md -Raw -Encoding UTF8
$MitbolBlocks = @{}
foreach ($MitbolSection in @('6.3','6.4')) {
    $MitbolPattern = '(?ms)^### ' + [regex]::Escape($MitbolSection) + '[^\r\n]*\r?\n.*?^```powershell\r?\n(?<code>.*?)^```'
    $MitbolMatch = [regex]::Match($MitbolDocument, $MitbolPattern)
    if (-not $MitbolMatch.Success) { throw "No se encontro el bloque $MitbolSection." }
    $MitbolBlocks[$MitbolSection] = [scriptblock]::Create($MitbolMatch.Groups['code'].Value)
}
$MitbolFunction = $MitbolBlocks['6.3']
$MitbolStatements = $MitbolFunction.Ast.EndBlock.Statements
if ($MitbolStatements.Count -ne 1 -or
    $MitbolStatements[0] -isnot [System.Management.Automation.Language.FunctionDefinitionAst] -or
    $MitbolStatements[0].Name -cne 'Invoke-MitbolAuthRevision' -or
    $MitbolFunction.Ast.BeginBlock -or $MitbolFunction.Ast.ProcessBlock) { throw 'Definicion inesperada.' }
. $MitbolFunction
$MitbolRun = $MitbolBlocks['6.4']
Write-Output 'Preparacion local lista. No se ejecuto ninguna migracion.'
```

Para diagnóstico usar únicamente 6.7 con `-Diagnose`. La ejecución de DDL requiere
autorización explícita. El scriptblock 6.4 pedirá la contraseña mediante entrada
oculta y restaurará las variables del proceso al terminar cada llamada. No imprimir DATABASE_URL, no usar Start-Transcript,
no escribir secretos en argv, SQL, .env ni archivos del repo. No compartirlos.

### 6.3 Función controlada para una revisión concreta

```powershell
function Invoke-MitbolAuthRevision {
    param([Parameter(Mandatory=$true)][ValidateSet('20261105_0004','20261106_0005')][string]$Revision)
    @'
import json, logging, os, sys
logging.disable(logging.CRITICAL)
stage = 'imports'
failure_report = None
engine = None
try:
    from scripts.diagnose_neon_auth_migration import failure_report
    from alembic import command
    from alembic.config import Config
    from app.db.connection import cloud_target_from_environment, create_database_engine

    stage = 'configuration'
    revision = sys.argv[1]
    previous = {'20261105_0004':'20261102_0003','20261106_0005':'20261105_0004'}[revision]
    target = cloud_target_from_environment()
    if target is None or target.expected_database != 'mitbol_cloud_dev' or target.expected_host != 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech':
        raise ValueError('Destino no aprobado')
    url = target.validate_url(os.environ['DATABASE_URL'])
    if url.username != 'neondb_owner' or '-pooler.' in url.host:
        raise ValueError('Operador/endpoint no autorizado')
    stage = 'engine'
    engine = create_database_engine(url, target=target, read_only=False)
    stage = 'connect'
    with engine.begin() as db:
        stage = 'target_tls'
        target.validate_connection(db)
        stage = 'transaction_setup'
        db.exec_driver_sql('SET LOCAL search_path=public')
        db.exec_driver_sql("SET LOCAL lock_timeout='5s'")
        db.exec_driver_sql("SET LOCAL statement_timeout='60s'")
        stage = 'identity'
        if db.exec_driver_sql('SELECT current_user,session_user').one() != ('neondb_owner','neondb_owner'):
            raise ValueError('Identidad inesperada')
        stage = 'postgres_version'
        if int(db.exec_driver_sql('SHOW server_version_num').scalar_one()) // 10000 != 18:
            raise ValueError('Version inesperada')
        stage = 'migration_lock'
        if not db.exec_driver_sql('SELECT pg_try_advisory_xact_lock(5569076195127487815)').scalar_one():
            raise ValueError('Otra operacion de migracion en curso')
        stage = 'revision_before'
        if db.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalars().all() != [previous]:
            raise ValueError('Revision anterior inesperada')
        stage = 'alembic_upgrade'
        config = Config('alembic.ini')
        config.attributes['connection'] = db
        command.upgrade(config, revision)
        stage = 'revision_after'
        if db.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalars().all() != [revision]:
            raise ValueError('Revision final inesperada')
        stage = 'commit'
    print(json.dumps({'status':'committed','revision':revision}))
except Exception as error:
    report = failure_report(error, stage) if failure_report else {
        'status':'stopped', 'stage':'imports', 'code':'DIAGNOSTIC_UNAVAILABLE',
        'error_type':'UnexpectedError', 'sqlstate':None}
    print(json.dumps(report))
    sys.exit(1)
finally:
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            print(json.dumps({'status':'stopped','stage':'engine','code':'CLEANUP_FAILED'}))
            sys.exit(1)
'@ | & .\.venv\Scripts\python.exe -B - $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Migracion detenida: verificar estado, no continuar al siguiente paso.' }
}
```

Cada llamada confirma una sola revisión. La misma conexión validada y transacción
contienen search_path, DDL, seeds y alembic_version. El advisory lock coordina
operadores que usen este procedimiento; no sustituye coordinar otros clientes.
Los timeouts limitan bloqueos; no se amplían para forzar el cambio. La función no
crea roles PostgreSQL ni toca Render. Las validaciones de Alembic se conservan.

### 6.4 Secuencia de operación autorizada

El snapshot ya existe. **Invocar sin -Diagnose exige autorización de DDL.** La
opción `-Diagnose` ejecuta exclusivamente el módulo de sólo lectura, sin llamar
a la función de migración. Cada llamada admite una revisión concreta y termina restaurando
el entorno, sin ejecutar la siguiente revisión. Conserva la función 6.3 y su TLS,
destino, transacción y timeouts. El prompt PREPARADO registra comprobación humana
de los datos del snapshot; no sustituye autorización ni consulta Neon.

```powershell
param(
    [Parameter(Mandatory=$true)][ValidateSet('20261105_0004','20261106_0005')][string]$Revision,
    [switch]$Diagnose
)
$ErrorActionPreference = 'Stop'
$NeonDirectHost = 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
if ((Read-Host 'Host DIRECTO de production confirmado en Console') -cne $NeonDirectHost) { throw 'Endpoint no coincide: detenerse.' }
foreach ($key in @('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGPASSWORD','PGPASSFILE')) {
    if ([Environment]::GetEnvironmentVariable($key,'Process')) { throw "Revisar override $key en esta terminal." }
}
if (-not $Diagnose -and (Read-Host 'Snapshot nombre/rama/UTC/disponibilidad y baseline registrados; escribir PREPARADO') -cne 'PREPARADO') { throw 'Punto de recuperacion no confirmado.' }
$SavedAuthMigrationEnv = @{}
foreach ($key in @('APP_ENV','EXPECTED_DATABASE','EXPECTED_DATABASE_HOST','DATABASE_URL')) {
    $SavedAuthMigrationEnv[$key] = [Environment]::GetEnvironmentVariable($key,'Process')
}
$PasswordPointer = [IntPtr]::Zero
$MigrationPassword = $null
$EncodedPassword = $null
try {
    $env:APP_ENV = 'cloud'
    $env:EXPECTED_DATABASE = 'mitbol_cloud_dev'
    $env:EXPECTED_DATABASE_HOST = $NeonDirectHost
    $MigrationPassword = Read-Host 'Contraseña neondb_owner (entrada oculta)' -AsSecureString
    $PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($MigrationPassword)
    $EncodedPassword = [uri]::EscapeDataString([Runtime.InteropServices.Marshal]::PtrToStringBSTR($PasswordPointer))
    $env:DATABASE_URL = "postgresql+psycopg2://neondb_owner:${EncodedPassword}@${NeonDirectHost}:5432/mitbol_cloud_dev?sslmode=verify-full&channel_binding=require"
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer)
    $PasswordPointer = [IntPtr]::Zero
    $EncodedPassword = $null

    if ($Diagnose) {
        & .\.venv\Scripts\python.exe -B -m scripts.diagnose_neon_auth_migration --revision $Revision
        if ($LASTEXITCODE -ne 0) { throw 'Diagnostico detenido: compartir solo el JSON sanitizado.' }
    }
    else {
        Invoke-MitbolAuthRevision -Revision $Revision
    }
}
finally {
    if ($PasswordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer) }
    $EncodedPassword = $null
    if ($MigrationPassword) { $MigrationPassword.Dispose() }
    foreach ($key in $SavedAuthMigrationEnv.Keys) {
        if ($null -eq $SavedAuthMigrationEnv[$key]) { Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath "Env:$key" -Value $SavedAuthMigrationEnv[$key] }
    }
}
```

Después de autorización y preparación 6.2, el comando de escritura para sólo
0004 es `& $MitbolRun -Revision '20261105_0004'`. Tras su confirmación y todas las
verificaciones 6.5/6.6/A/D aprobadas, el comando separado para sólo 0005 es
`& $MitbolRun -Revision '20261106_0005'`. No pegarlos juntos como lote. Una futura
autorización puede cubrir ambos si exige ese control intermedio; no se pedirá
otra aprobación si ya está expresamente cubierta. Los secretos sólo existen
transitoriamente en memoria y entorno del proceso operador.

Tras 0004 deben existir siete tablas nuevas, dos roles de aplicación y las demás
tablas nuevas vacías. Tras 0005 deben existir nueve tablas nuevas en total, las
dos de límites vacías y su índice de expiración. Comparar A con el baseline:
todos los conteos/huellas comerciales deben coincidir. En D, las nuevas tablas
de seguridad deben tener table_select=false para el lector y todo any_write y
any_sequence_privilege debe seguir false; las ocho tablas comerciales conservan
SELECT. Cualquier diferencia es un criterio de parada. B no se repite después
de migrar: los nombres que antes estaban ausentes ahora deben existir.

### 6.5 SELECT posteriores: uno por Run, sólo cuando se autorice y ejecute el DDL

Después de 0004: siete filas; Roles=2 y las otras seis=0,
roles_seed=admin,staff y revisión=20261105_0004.

```sql
WITH counts(table_name,row_count) AS (
    SELECT 'Persons',count(*) FROM public."Persons"
    UNION ALL SELECT 'PersonalAccounts',count(*) FROM public."PersonalAccounts"
    UNION ALL SELECT 'Employees',count(*) FROM public."Employees"
    UNION ALL SELECT 'Roles',count(*) FROM public."Roles"
    UNION ALL SELECT 'EmployeeRoles',count(*) FROM public."EmployeeRoles"
    UNION ALL SELECT 'AccountSessions',count(*) FROM public."AccountSessions"
    UNION ALL SELECT 'AuditEvents',count(*) FROM public."AuditEvents")
SELECT table_name,row_count,
       (SELECT version_num FROM public.alembic_version) AS revision,
       (SELECT string_agg(code,',' ORDER BY code) FROM public."Roles") AS roles_seed
FROM counts ORDER BY table_name;
```

Después de 0005: revisión=20261106_0005, ambos conteos=0 e índice válido/listo.

```sql
SELECT (SELECT version_num FROM public.alembic_version) AS revision,
       (SELECT count(*) FROM public."AuthRateLimitPolicy") AS policy_rows,
       (SELECT count(*) FROM public."AuthRateLimits") AS limit_rows,
       EXISTS(SELECT 1 FROM pg_catalog.pg_index i
              JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
              WHERE c.relnamespace='public'::regnamespace
                AND c.relname='ix_AuthRateLimits_expires_at'
                AND i.indrelid='public."AuthRateLimits"'::regclass
                AND i.indisvalid AND i.indisready) AS limiter_index_ok;
```

Después de cada revisión se ejecutan además A y D del archivo Console y los GET
de 6.6. Son verificaciones del estado nuevo; no otra ronda del preflight previo.

### 6.6 Verificación pública posterior

Comandos GET, sin login ni operaciones de escritura:

```powershell
$PublicBase = 'https://mitbol-backend.onrender.com'
Invoke-RestMethod "$PublicBase/"
$PublicMenu = Invoke-RestMethod "$PublicBase/api/menu"
if (-not $PublicMenu.products.Count) { throw 'Comparar con baseline: menu vacio inesperado.' }
$PublicProductId = $PublicMenu.products[0].product_id
Invoke-RestMethod "$PublicBase/api/menu/$PublicProductId"
```

Comparar JSON/precios/IDs contra baseline, HTTPS, origen CORS público ya aprobado,
Swagger sin rutas privadas y GET de /api/auth/me con 404. La raíz sola no verifica
PostgreSQL. No cambiar CORS a un comodín porque el dominio frontend esté pendiente.
El final de esta intervención sigue siendo AUTH_ENABLED=false y ADMIN_ENABLED=false.
No ejecutar preparaciones de escritor, aprovisionamiento ni sincronizaciones.

### 6.7 Diagnóstico del intento fallido: sólo lectura

No volver a usar la definición antigua de la función cargada en PowerShell.
Este bloque recarga 6.2 desde el archivo actualizado en la misma sesión (dot
sourcing conserva función y scriptblock), y ejecuta únicamente el diagnóstico:

```powershell
Set-Location 'C:\Proyectos\mitbol\mitbol-backend'
$ErrorActionPreference = 'Stop'
$MitbolGuide = Get-Content -LiteralPath .\docs\security\neon_auth_migration_preflight.md -Raw -Encoding UTF8
$MitbolPrep = [regex]::Match($MitbolGuide, '(?ms)^### 6\.2[^\r\n]*\r?\n.*?^```powershell\r?\n(?<code>.*?)^```')
if (-not $MitbolPrep.Success) { throw 'No se encontro preparacion local.' }
. ([scriptblock]::Create($MitbolPrep.Groups['code'].Value))
& $MitbolRun -Revision '20261105_0004' -Diagnose
```

Solicita host directo y contraseña oculta, sin pedir PREPARADO ni ejecutar DDL.
El módulo `scripts/diagnose_neon_auth_migration.py` no contiene ninguna llamada
upgrade/downgrade/stamp. No carga .env ni exige una base vacía: acepta sólo la
revisión inmediatamente anterior. Establece READ ONLY antes de los SELECT de
diagnóstico y siempre revierte/cierra; no toma el advisory lock de migración.

Comprueba en esa conexión la misma política externa de TLS/CA/channel binding,
identidad, PG18, esquema, permisos mínimos de creación/versión y lectura Alembic.
No vuelve a consultar A–E ni las tablas comerciales. `pg_stat_ssl` del proxy no
se utiliza para sustituir la validación TLS efectiva del cliente.

Compartir sólo el JSON final. `stage`, `code`, `error_type`, `sqlstate`,
`checks_passed` y versiones de runtime son seguros; no se devuelven mensajes,
SQL, datos, DSN, contraseñas, traceback ni nombres de excepción arbitrarios.
Un éxito `inspected_readonly` no prueba que el DDL/lock/COMMIT vaya a funcionar:
esas etapas sólo se pueden observar en un intento autorizado. Las excepciones
de ese futuro intento ya conservarán etapa y SQLSTATE aunque Alembic las envuelva.

Validación local de esta corrección: 17 pruebas aprobadas mediante
`python -B scripts/test_auth_phase1.py tests/test_neon_auth_executor.py -q` en
PostgreSQL 18 desechable y detenido al finalizar. Incluye rechazo de destinos
no aprobados, no exposición de secretos, prohibición efectiva de DDL en READ
ONLY, error SQL anidado en CommandError con rollback a 0003 y aplicación aislada
de sólo 0004 con catálogo intacto. El adaptador de ese ensayo sustituye TLS/destino
únicamente dentro del test; no reproduce ni acredita la conexión TLS real a Neon.
No se reprodujo aún el fallo original ni se alteraron migraciones o política TLS.

| Resultado | Siguiente paso sin escrituras |
| --- | --- |
| configuration / TARGET_VALIDATION_FAILED | Revisar APP_ENV y destino aprobado; no imprimir URL/entorno. |
| connect / DNS_RESOLUTION_FAILED, CONNECTION_TIMEOUT o CONNECTION_REFUSED | Revisar conectividad al endpoint directo. No cambiar a pooler/IP ni relajar TLS. |
| connect / TLS_* o CHANNEL_BINDING_* | Revisar CA/reloj/runtime; conservar verify-full y channel_binding=require. |
| AUTHENTICATION_FAILED / 28P01 o 28000 | Revisar la credencial oculta del operador; no crear roles ni otorgar permisos. |
| permissions / 42501 o OPERATOR_PERMISSIONS_MISSING | Confirmar operador/destino; no conceder privilegios como arreglo automático. |
| DEFAULT_READONLY_ENABLED / 25006 | Identificar por qué el operador hereda sólo lectura; no forzar READ WRITE automáticamente. |
| revision_before / PREVIOUS_REVISION_MISMATCH | Detenerse y leer estado nuevo; no stamp ni repetir. |
| inspected_readonly | Conservar JSON y evaluar si falta evidencia del fallo de DDL. No reintentar hasta autorización explícita. |

## 7. Errores y recuperación

| Situación | Acción |
| --- | --- |
| Destino/revisión/schema/conflictos/ACL/recuperación no aprobados | No iniciar DDL. Conservar backend público actual y evidencias saneadas. |
| 0004 falla dentro de su transacción | Esperar rollback y consultar con conexión nueva. Debe seguir 0003 sin tablas parciales. Si no, detenerse; nunca stamp ni borrar objetos para forzar. |
| 0004 confirma y 0005 falla | Debe quedar 0004 con sus siete tablas/seed, sin tablas parciales de límites. El público sigue compatible. Investigar, aprobar corrección y reintento sólo de 0005. No borrar identidad automáticamente. |
| Pérdida de conexión durante COMMIT | Estado incierto. Releer revisión y objetos antes de repetir; no asumir que hubo rollback. |
| Invariante comercial o permisos cambian | Detener avances y otras escrituras autorizadas, mantener administración apagada, investigar DDL/triggers/defaults y elegir recuperación aprobada. No conceder permisos al lector como parche. |
| Error de aplicación con catálogo sano | Volver al código público conocido si se autoriza; dejar tablas nuevas sin usar. Deshacer código no requiere downgrade de base. |
| Pérdida real de datos/esquema | Evaluar instante/LSN disponible y cambios posteriores, obtener autorización para PITR o restauración aislada/selectiva. No ejecutar automáticamente; recordar alcance de rama e interrupción de conexiones. |
| t0 fuera de historia / recuperación no disponible | No prometer PITR. Utilizar respaldo verificable autorizado o escalar al operador/proveedor; no fabricar datos ni re-sincronizar Loyverse como recuperación. |

## 8. Bloqueos y cierre

- [x] Identidad del destino y revisión 0003 confirmadas por el operador en 0/1.
- [x] Estructura comercial recuperada del bloque 2: ocho tablas, 14 índices
  válidos/listos y restricciones mostradas validadas, según evidencia comunicada.
- [x] A–E aprobadas por el operador: baseline registrado, conflicts=0,
  permisos correctos, lector sin escritura/secuencias y alerts=0.
- [x] Recuperación anunciada por Console: historial seleccionable y botones
  Preview data/Restore/Create snapshot en production; Free/seis horas confirmados.
- [x] Snapshot creado/verificado por el usuario: mitbol-before-auth-migrations,
  production, 39.95 MB, Never y Restore disponible. No eliminarlo ni recrearlo.
- [x] Registro completo para Console: nombre/rama y creación 2026-11-06 19:20 UTC;
  ID interno no mostrado y no necesario. Conservar A; sin restauración ensayada.
- [x] Compatibilidad con develop remoto/local 388556f5 comprobada; dependencias
  y CA disponibles. No se repitió el preflight SQL.
- [x] Procedimiento compatible y preparación local probada sin conexión; código
  fijado a 388556f5, baseline conservado y criterios de parada documentados.
- [ ] Identificar el fallo original mediante 6.7 y/o evidencia de una reproducción
  aislada. La salida antigua genérica no demuestra una causa concreta.
- [ ] Obtener autorización explícita para reintentar y abrir la intervención atendida. Una
  instrucción puede autorizar ambas revisiones con comprobación intermedia;
  ningún comando avanza automáticamente a 0005.

No son bloqueos de estas migraciones: dominio frontend, writer PostgreSQL,
cuentas administrativas, activación cloud o almacenamiento de futuras imágenes.
Son tareas posteriores que conservan sus propias restricciones.

**Conclusión:** preflight SQL, compatibilidad y snapshot confirmados por las
fuentes indicadas. NO-GO para otro intento mientras se investiga el error y falta
la autorización explícita del usuario. El agente no modificó Neon, ninguna
de sus ramas, mitbol_dev, Render, frontend ni configuración remota. Sin cuentas,
roles, backups o restauraciones ejecutados por el agente, commits o push. El trabajo termina en
preflight; las escrituras descritas son propuestas para una autorización futura.
