# Etapa 3C: revisión previa al usuario lector de Render

Estado: preparación; **no autoriza ejecutar la creación**. Las revisiones instaladas
en Neon y el resultado de `alembic check` proceden de la confirmación del operador.
Esta revisión no conecta a Neon ni a mitbol_dev ni consulta sus credenciales.
Las pruebas se ejecutan únicamente en PostgreSQL 18 temporal, en otro puerto y
con su data_directory comprobado. Crean fixtures sintéticas desde los modelos,
sin ejecutar Alembic, SyncService ni Loyverse. No prueban privilegios reales de Neon.

## Estructura esperada en 20261102_0003

| Tabla | Clave primaria | Relaciones y restricciones únicas | Defaults de negocio |
|---|---|---|---|
| Products | product_id | UNIQUE loyverse_id | is_available=true; is_active=true; is_featured=false; is_new=true |
| ProductVariants | product_variant_id | product_id → Products; UNIQUE loyverse_id | is_available=true; is_commercially_available=false |
| ProductModifiers | product_modifier_id | UNIQUE loyverse_id | ninguno |
| ProductComplements | product_complement_id | product_modifier_id → ProductModifiers; UNIQUE loyverse_id | is_active=true |
| ProductModifierAssignments | (product_variant_id, product_modifier_id) | → ProductVariants y ProductModifiers | ninguno |
| ProductImages | product_image_id | product_variant_id → ProductVariants | created_at=now() |
| Tags | tag_id | UNIQUE name | created_at=now() |
| ProductTags | (product_variant_id, tag_id) | → ProductVariants y Tags | ninguno |

Son 42 columnas, 8 PK, 7 FK y 5 UNIQUE en el modelo. Todas las FK usan
`NO ACTION` para UPDATE/DELETE, están validadas y no son diferibles.
Solo `Products.description` y `ProductModifiers.synced_at` admiten NULL.
Los seis IDs simples son INTEGER con secuencias SERIAL propias; las dos PK
compuestas no tienen secuencia. Se esperan inicio/incremento/mínimo/cache=1,
máximo=2147483647 y cycle=false. Hay 13 índices de PK/UNIQUE en el modelo,
más el índice de la PK de `alembic_version`: 14 en total.

`ProductVariants.is_commercially_available` es BOOLEAN NOT NULL DEFAULT false.
Los precios son NUMERIC sin precisión/escala explícitas y sin CHECK de signo.
La normalización de Tags.name ocurre en SQLAlchemy; no es un CHECK de PostgreSQL.
No se esperan tablas de pedidos, objetos legacy, vistas, políticas RLS ni rutinas
de MITBOL. Los timestamps son TIMESTAMP WITH TIME ZONE.

## Evaluación de prepare_render_reader.sql

La propuesta concede CONNECT a la base, USAGE a public y SELECT solo a las ocho
tablas. Es lo que usan GET /api/menu, GET /api/menu/{product_id} y el endpoint
público de imágenes. No necesitan INSERT/UPDATE/DELETE, secuencias, DDL, funciones
de aplicación ni lectura de alembic_version. SELECT cubre todas las filas/columnas:
los filtros comerciales actuales se aplican en la API; no constituyen RLS.

El rol se crea mediante SQL, con LOGIN y PASSWORD NULL, sin atributos
administrativos, sin herencia y sin membresías. NOINHERIT solo no impediría
SET ROLE si hubiera membresías; el bloque final comprueba que no las haya.
El rol no debe ser dueño de tablas/esquemas ni miembro de neon_superuser.
PASSWORD NULL impide autenticación por contraseña hasta configurarla.
La transacción comprueba base/operador, revisión exacta, rol inexistente y
permisos efectivos de escritura antes del COMMIT. No es un script idempotente:
si el rol ya existe se requiere revisar su estado, no reejecutarlo.

**Controles que deben pasar antes del COMMIT y con un login nuevo:**

- LOGIN sin SUPERUSER/CREATEDB/CREATEROLE/INHERIT/REPLICATION/BYPASSRLS; ninguna
  membresía, aunque sea NOINHERIT o permita SET ROLE.
- CONNECT en mitbol_cloud_dev, USAGE en public y SELECT completo en exactamente
  las ocho tablas base autorizadas. Las lecturas normales de catálogos del
  sistema no se consideran acceso comercial no autorizado.
- Ningún INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER/MAINTAIN en relaciones
  de ningún esquema de usuario; también se comprueban los grants por columna.
  Sin SELECT por tabla o columna fuera de la lista, incluido alembic_version.
- Sin CREATE en ningún esquema ni CREATE/TEMPORARY en la base autorizada;
  ninguna concesión sobre secuencias, ni siquiera SELECT.
- Sin propiedad de bases, esquemas, relaciones, funciones, tipos, extensiones
  u objetos grandes; sin permisos delegables de objetos actuales.
- Sin defaults que concedan tablas, secuencias, objetos grandes o CREATE de
  esquemas a PUBLIC o al lector, para cualquier rol creador en esta base.
  Los REVOKE por esquema no anulan grants globales ni los de PUBLIC.
- Sin lectura/escritura de objetos grandes existentes, con
  lo_compat_privileges=off. Esto no impide por sí solo crearlos mediante funciones.
- Sin rutinas propias SECURITY DEFINER invocables: no forman parte del menú.
  Las rutinas de extensiones/sistema se informan para revisión de su alcance.
- Configuración del rol default_transaction_read_only=on y sin override off
  del rol en esta base. El verificador exige además que ambos valores efectivos,
  default_transaction_read_only y transaction_read_only, sean on en el login nuevo.

La misma política se comprueba antes del COMMIT y en el archivo posterior.
Una excepción READER_* detiene el procedimiento: no configura Render, no
amplía grants y no vuelve a ejecutar escrituras automáticamente. Se requiere
PostgreSQL 18, que incorpora has_largeobject_privilege; MAINTAIN también se comprueba.

### Interpretación de resultados y funciones

| Resultado | Significado y acción |
|---|---|
| ACL_OBSERVADA / *_observed | Un permiso/configuración leído del catálogo; no se invocó la rutina. |
| POTENCIAL_NO_EJECUTADO | EXECUTE podría permitir efectos; también puede haber controles internos que lo impidan. Analizar su alcance. |
| REQUIERE_REVISION_NO_EJECUTADO | Rutina SECURITY DEFINER o volátil: revisar propietario, extensión, permisos y uso antes de autorizar. |
| READER_* | Desviación crítica confirmada por la comprobación de permisos/identidad/configuración; detenerse. No significa que se haya ejecutado un ataque. |
| reader_permissions_verified | Pasó la política de privilegios sobre el catálogo; no certifica ausencia absoluta de escrituras en todo PostgreSQL. |

El inventario es dirigido, no un análisis exhaustivo de todas las rutinas ni
de sus implementaciones internas. Se auditan pg_catalog.lo_create, lo_creat, lo_from_bytea y otras funciones de
objetos grandes, archivos, replicación, control de sesiones y set_config.
**No se ejecutan ni se muestran sus cuerpos ni contenidos.** EXECUTE=true de
PUBLIC es normal para varias de ellas y no provoca automáticamente un fallo.
En particular, las funciones de objetos grandes pueden crear datos persistentes
fuera de las ocho tablas si el cliente abre una transacción de escritura.
Esto es un riesgo residual del motor que debe aceptarse de forma explícita;
no demuestra modificación posible de Products/Tags ni de tablas ajenas.

default_transaction_read_only es un valor inicial modificable por el cliente,
y la sesión cloud de la API también aplica lectura. La protección de tablas
depende de sus ACL y de no introducir vías de elevación mediante funciones.
Los filtros comerciales de la API no son RLS. Un usuario con credenciales puede
usar otro cliente: EXPECTED_DATABASE y el hostname validado protegen la conexión
de MITBOL, pero no son un DENY de PostgreSQL sobre las otras bases.

No se revoca EXECUTE masivamente de pg_catalog, funciones de extensiones ni
roles internos. Si se exige bloquear toda escritura persistente, hace falta
otro diseño y autorización; esta etapa no declara esa garantía.

### Consecuencias de revocar permisos a PUBLIC

PUBLIC incluye todos los roles presentes y futuros, incluso roles NOINHERIT.
No existe un DENY individual que anule sus permisos. Por eso no basta con
REVOKE TEMPORARY FROM mitbol_render_reader si PUBLIC conserva TEMPORARY.

| Revocación propuesta | Alcance y efecto |
|---|---|
| CREATE ON DATABASE mitbol_cloud_dev | Quita a quienes dependan de PUBLIC la creación de esquemas y operaciones que requieran ese permiso. No es el permiso de crear bases; ese es CREATEDB. |
| TEMPORARY ON DATABASE mitbol_cloud_dev | Impide nuevas tablas temporales a quienes dependan de PUBLIC. No bloquea SELECT/JOIN/ORDER BY ni los archivos temporales internos del motor; no elimina tablas existentes. |
| CREATE ON SCHEMA public | Impide crear nuevos objetos en public a quienes dependan de PUBLIC. No revoca USAGE/SELECT ni elimina objetos existentes. |

Los grants directos o heredados de otros roles permanecen. Revisar sus ACL antes
de afirmar qué operadores perderán permisos; con ACL normales, el propietario
conserva los suyos. Las migraciones posteriores ejecutadas por neondb_owner
deberían seguir funcionando. Otro rol migrador o una herramienta que cree tablas
temporales podría necesitar grants explícitos, autorizados por separado.
PostgreSQL moderno normalmente ya restringe CREATE de public; no asumirlo sin
consultar el catálogo.

Los REVOKE propuestos solo afectan esa base y su esquema public. **La creación
del rol y ALTER ROLE son compartidos por las bases de la rama de Neon**. El
lector podría recibir CONNECT/TEMPORARY en otras bases mediante PUBLIC. Esta
propuesta no garantiza aislamiento entre bases ni autoriza cambiar sus ACL.
La auditoría lista esos permisos sin conectarse a esas otras bases.

**Decisión aceptada: conservar intactos CONNECT/TEMPORARY de PUBLIC en neondb y
postgres.** CONNECT no concede SELECT ni DML sobre sus tablas; TEMPORARY permite
tablas temporales y consumo de recursos. No hay evidencia que justifique cambiar
sus ACL. Consultar pg_database no audita su contenido, funciones, grants ni
propiedad de objetos: esos puntos no pueden certificarse desde mitbol_cloud_dev.
Los roles internos pueden tener grants propios; no asumir que todos eludan ACL,
ni cambiarlos por precaución. Una revisión de otras bases requerirá autorización
independiente. Una rama nueva copia inicialmente roles y datos; un proyecto
separado puede ser apropiado si se necesita mayor aislamiento.

### Futuros endpoints de pedidos

Usarán otro rol con permisos explícitos sobre sus propias tablas y secuencias,
otra credencial y una conexión/sesión de escritura separada, con autorización
de la API. El pool cloud actual impone lectura: requerirá diseño específico
para esos endpoints. El lector no recibirá permisos de pedidos ni membresías
del escritor. No conceder privilegios por defecto a PUBLIC ni al lector. Su
contraseña será solo para el backend Render, nunca para React, migraciones o
sincronización. Una futura tabla del menú necesita revisión, GRANT SELECT
explícito y actualización de la lista autorizada; las de pedidos no se añaden.

## Paso 1: auditoría de solo lectura desde PowerShell

Ejecutar desde la raíz del repositorio en la PowerShell que ya contiene las
variables de Neon. El bloque no cambia esas variables ni imprime DATABASE_URL.
Usa el certificado validado por el proyecto. psql pide la contraseña del owner
con entrada oculta; tomarla del gestor de secretos, nunca pegarla en el chat.
No pasar la URI mediante --dbname, -d o argumentos del proceso.

```powershell
Set-Location -LiteralPath 'C:\Proyectos\mitbol\mitbol-backend'
$neonPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$neonPsql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
if (-not (Test-Path -LiteralPath $neonPython) -or -not (Test-Path -LiteralPath $neonPsql)) {
    throw 'Falta el Python del proyecto o psql 18; revisar su ubicacion.'
}

$neonCa = @'
import os
import sys
from app.db.connection import cloud_target_from_environment, connection_arguments
try:
    required = ("APP_ENV", "DATABASE_URL", "EXPECTED_DATABASE", "EXPECTED_DATABASE_HOST")
    if not all(os.environ.get(name) for name in required):
        raise ValueError()
    target = cloud_target_from_environment()
    if target is None or target.expected_database != "mitbol_cloud_dev" or target.expected_host != "ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech":
        raise ValueError()
    url = target.validate_url(os.environ["DATABASE_URL"])
    if url.username != "neondb_owner" or url.port != 5432:
        raise ValueError()
    print(connection_arguments(url, target)["sslrootcert"])
except Exception:
    sys.stderr.write("Configuracion cloud ausente o no autorizada; no se abrio ninguna conexion.\n")
    sys.exit(1)
'@ | & $neonPython -B -
if ($LASTEXITCODE -ne 0) { throw 'Detenido antes de conectar.' }
$neonCa = ([string]$neonCa).Trim().Replace('\', '/')
if ($neonCa -match "[\r\n']" -or -not (Test-Path -LiteralPath $neonCa)) {
    throw 'Ruta CA no utilizable por este procedimiento.'
}
$neonConnection = "host=ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech port=5432 dbname=mitbol_cloud_dev user=neondb_owner sslmode=verify-full sslrootcert='$neonCa' channel_binding=require gssencmode=disable ssl_min_protocol_version=TLSv1.2 connect_timeout=10"

& $neonPsql -X -W --dbname $neonConnection --set ON_ERROR_STOP=1 --set HISTFILE=NUL --pset pager=off --command '\conninfo' --file '.\docs\database\verify_neon_stage3c.sql'
if ($LASTEXITCODE -ne 0) { throw 'Auditoria fallida. No continuar con la creacion.' }
```

Comprobar manualmente todos los resultados; un exit code 0 no equivale a aprobar
todas las diferencias. Las dos consultas de diferencias deben devolver cero
filas; las ocho cuentas, los siete contadores de huérfanos y los siete de
duplicados deben ser cero. Revisar inventario, índices y secuencias contra la
estructura descrita. El rol lector debe estar ausente.

conninfo debe mostrar host directo, base, usuario y TLS 1.2/1.3. La conexión falla
si no puede verificar el certificado o negociar channel binding. pg_stat_ssl
solo describe la conexión visible al backend: Neon puede terminar TLS en el
proxy y esa vista no reemplaza la comprobación TLS del cliente.
No ejecutar verify_neon como comprobación posterior a migraciones: exige una
base sin tablas/revisiones y ahora la rechazará de forma prevista.

Revisar la lista de operadores/ACL y los defaults de todos los creadores. Los
permisos normales de funciones del sistema se evalúan por su alcance: no exigir
cero filas/EXECUTE=false en esas secciones. Documentar cuáles se aceptan como
riesgo residual; detenerse ante grants inesperados, rutinas de extensión sin
revisar o diferencias estructurales. En caso de error no repetir
escrituras ni retirar controles TLS.

## Paso 2: autorización expresa, después de revisar la salida

Compartir únicamente los resultados sin credenciales. La autorización debe
incluir el nombre del rol y el impacto compartido de PUBLIC. Texto sugerido:

> Autorizo crear mediante SQL el rol mitbol_render_reader en la rama production
> de Neon para mitbol_cloud_dev, con SELECT únicamente en las ocho tablas del
> catálogo, sin membresías ni permisos administrativos/de escritura. Autorizo
> revocar CREATE y TEMPORARY de PUBLIC en mitbol_cloud_dev y CREATE de PUBLIC
> en el esquema public de esa base, tras revisar el efecto en otros roles.
> Autorizo configurar su contraseña mediante entrada oculta y verificar el
> acceso con un nuevo login. Conservo CONNECT/TEMPORARY de PUBLIC en neondb y
> postgres y acepto los riesgos residuales revisados de funciones normales del
> sistema, sin afirmar ausencia absoluta de escrituras persistentes.
> No autorizo cambios en otras bases, migraciones,
> sincronizaciones, privilegios de pedidos ni despliegues de Render.

El CONNECT/TEMPORARY conocido en las otras bases es la decisión aceptada, no un
error pendiente. Una exposición nueva a datos o una función adicional no revisada
requiere resolver su alcance antes de aprobar. No asumir aislamiento absoluto.

## Paso 3: creación y contraseña, SOLO después de la autorización

Repetir el Paso 1 justo antes, en esa misma sesión de PowerShell. Detenerse ante
cualquier diferencia. Este bloque es una instrucción futura; **no ejecutarlo
durante la preparación**.

```powershell
$neonWriteMode = & $neonPsql -X -W --dbname $neonConnection --set ON_ERROR_STOP=1 --set HISTFILE=NUL --tuples-only --no-align --command 'SHOW transaction_read_only'
if ($LASTEXITCODE -ne 0 -or ([string]$neonWriteMode).Trim() -ne 'off') {
    throw 'La sesion no permite escrituras; detenerse y analizar sin cambiar sus controles.'
}
# No cambiar el modo automaticamente ni reutilizar la conexion del verificador.

& $neonPsql -X -W --dbname $neonConnection --set ON_ERROR_STOP=1 --set HISTFILE=NUL --pset pager=off --file '.\docs\database\prepare_render_reader.sql'
if ($LASTEXITCODE -ne 0) { throw 'Creacion fallida: detenerse y analizar; no reintentar automaticamente.' }

& $neonPsql -X -W --dbname $neonConnection --set ON_ERROR_STOP=1 --set HISTFILE=NUL --command "SET password_encryption = 'scram-sha-256'" --command '\password mitbol_render_reader'
if ($LASTEXITCODE -ne 0) { throw 'No se confirmo la contrasena; detenerse y revisar el estado del rol.' }
```

La creación es una sola transacción; un error antes de COMMIT revierte sus
cambios al desconectar. Un fallo de contraseña ocurre después: el rol puede
existir con PASSWORD NULL, pero aún no autenticar. No recrearlo automáticamente.
Usar una contraseña aleatoria fuerte, por ejemplo 32 caracteres generados por
un gestor, y conservarla solo en ese gestor y, cuando se autorice, en los
secretos de Render. No escribir ALTER ROLE ... PASSWORD '...' manualmente:
\password evita contraseñas en texto claro en comandos/historial.

## Paso 4: login nuevo del lector, SOLO después de su creación autorizada

```powershell
$neonReaderConnection = $neonConnection.Replace('user=neondb_owner ', 'user=mitbol_render_reader ')
& $neonPsql -X -W --dbname $neonReaderConnection --set ON_ERROR_STOP=1 --set HISTFILE=NUL --pset pager=off --command '\conninfo' --file '.\docs\database\verify_render_reader.sql'
if ($LASTEXITCODE -ne 0) { throw 'Verificacion del lector fallida; no configurar Render.' }
```

Introducir la contraseña del lector, no la del owner. Confirmar:
default_transaction_read_only=on, identidad del lector, CONNECT/USAGE=true,
todos los atributos administrativos/herencia=false, ninguna membresía, sin
CREATE/TEMPORARY, SELECT solo en las ocho tablas, ningún permiso de escritura,
MAINTAIN, grant option, propiedad o secuencia, y cero filas en las tablas.
reader_permissions_verified confirma los controles críticos. Revisar aparte las
rutinas y la clasificación de riesgos; EXECUTE normal de pg_catalog no se trata
como permiso DML sobre el catálogo. No hacer INSERT/UPDATE/DDL ni invocar
funciones mutadoras para probar permisos en Neon. Un resultado crítico inesperado
impide continuar; conservar su código READER_* sin compartir credenciales.

El siguiente paso será configurar Render con la credencial del lector, nunca
con neondb_owner, después de su autorización correspondiente. La sincronización
Loyverse seguirá necesitando autorización separada.


## Errores que detienen el procedimiento

| Código o condición | Qué revisar |
|---|---|
| Destino/operador/revisión incorrectos, rol ya existente | Variables del proceso, conexión directa y estado real. No recrear ni stamp. |
| READER_IDENTITY / READER_READ_ONLY_CONFIGURATION / READER_DEFAULT_CONFIGURATION | Login nuevo del lector y configuración efectiva; no cambiar controles para ocultar el fallo. |
| READER_ADMIN_ATTRIBUTES / READER_MEMBERSHIP / READER_OBJECT_OWNER | Atributos, dirección de membresías y propiedad. |
| READER_REQUIRED_ACCESS / READER_REQUIRED_SELECT | Falta CONNECT, USAGE o SELECT autorizado. No conceder ALL. |
| READER_CREATE_OR_TEMP / READER_TABLE_OR_COLUMN_PRIVILEGES / READER_SEQUENCE_PRIVILEGES | Permisos efectivos, incluidos PUBLIC, columnas y otros esquemas de usuario. |
| READER_GRANT_OPTION / READER_UNSAFE_DEFAULT_ACL | Concesiones delegables o exposición automática de objetos futuros. |
| READER_LARGE_OBJECT_ACCESS / READER_UNREVIEWED_DEFINER | Objetos grandes existentes/compatibilidad o rutina propia privilegiada no autorizada. |

Un error previo al COMMIT revierte también el CREATE ROLE y los REVOKE de esa
transacción al desconectar. La contraseña se configura después; su fallo no
deshace una creación ya confirmada. Nunca repetir escrituras fallidas sin
analizar el estado. Las funciones potenciales informadas no son, por sí solas,
un error READER_*.

## Validación local de esta mejora

Las pruebas del archivo tests/test_render_reader.py comprueban ambos scripts,
rechazos por permisos inseguros, rollback y un login real del rol temporal.
No usan la fixture de sincronización ni migraciones, y no abren mitbol_dev.
La prueba positiva conserva EXECUTE normal del sistema y verifica que las ACL
de la base postgres del cluster temporal permanezcan iguales. La auditoría y
el verificador se ejecutan realmente con psql 18 en transacciones de lectura.
Esto valida sintaxis y comportamiento en PostgreSQL estándar temporal; la
compatibilidad efectiva con las ACL/extensiones de Neon se comprobará solo en
la auditoría autorizada del operador, sin invocar funciones con efectos.

## Referencias oficiales revisadas

- [Privilegios PostgreSQL 18](https://www.postgresql.org/docs/18/ddl-priv.html)
- [Funciones de comprobación de permisos](https://www.postgresql.org/docs/18/functions-info.html)
- [Funciones de objetos grandes](https://www.postgresql.org/docs/18/lo-funcs.html)
- [ALTER DEFAULT PRIVILEGES](https://www.postgresql.org/docs/current/sql-alterdefaultprivileges.html)
- [psql: -X, -W y password](https://www.postgresql.org/docs/current/app-psql.html)
- [Roles en Neon: SQL frente a Console/API/CLI](https://neon.com/docs/manage/roles)

Para el lector usar CREATE ROLE por SQL: Neon concede neon_superuser a roles
creados mediante Console/API/CLI. Crear el lector por esas interfaces daría
permisos que esta propuesta busca evitar.
