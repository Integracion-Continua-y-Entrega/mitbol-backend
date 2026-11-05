# Etapa 3F: preparación del backend público en Render

Revisión local: 2026-11-04. Este documento prepara el despliegue; no crea el
servicio ni autoriza cambios remotos, commits o push.

## Cierre de 3E

El operador confirmó los resultados de `verify_neon_after_sync.ps1` para
`neondb_owner` y `mitbol_render_reader`: ambos devolvieron
`catalog_verified_after_sync` y `catalog_data_verified_after_sync`, TLS 1.3,
SSL activo, `verify-full`, `channel_binding=require` y auditoría read only.
El lector confirmó además `default_transaction_read_only=on`.

Conteos: Products=3, ProductVariants=5, ProductModifiers=2,
ProductComplements=5, ProductModifierAssignments=4, ProductImages=0, Tags=0,
ProductTags=0. Duplicados, huérfanos y tombstones importados=0;
available_variants=5. Alembic: `20261102_0003`.

Etapa 3E cerrada con esa evidencia del operador. No repetir sincronización,
migraciones, creación del lector ni las auditorías ya aprobadas.

## Resultado de la preparación local

- FastAPI se sirve mediante `app.main:app`; Uvicorn está incluido en
  `requirements.txt`. Las dependencias están fijadas y `pip check` pasó.
- Python local: 3.13.13. CI utiliza la serie 3.13; fijar 3.13.13 en Render.
- Cloud valida base/host y rechaza `ADMIN_ENABLED=true`. No carga `.env`
  ni exige un token Loyverse. La API no registra el router de sincronización.
- Las conexiones son perezosas: arrancar y responder `/` no abre PostgreSQL.
  Cada conexión física cloud valida destino/TLS y rechaza permisos de escritura
  sobre el catálogo; las transacciones de la API se configuran como read only.
- La revisión previa a integrar pasó 860 pruebas, sin fallos ni casos omitidos;
  ocho casos que invocan comandos upgrade/downgrade de Alembic (online y offline) fueron
  excluidos por la restricción de esta etapa. Se usaron PostgreSQL 18 temporales,
  metadata sintética y HTTP simulado, sin Neon, mitbol_dev ni el `.env` real.
  Incluye HU-008, HU-001, HU-003, HU-005, HU-007, cloud y el lector. Hay dos
  avisos de deprecación del cliente de pruebas, sin fallos de aplicación.
- Una comprobación adicional sin conexión validó el hostname pooled esperado,
  rechazó usar el directo en su lugar y comprobó raíz, OpenAPI y rutas cerradas.
- Uvicorn arrancó con app.main:app, host 0.0.0.0 y puerto variable, con conexiones
  reales bloqueadas. Raíz y OpenAPI respondieron por HTTP. pip check, actionlint,
  enlaces locales de Markdown y comprobaciones de whitespace pasaron.
- No se ha probado desde Render la conectividad al pooler ni la instalación
  de los paquetes en Linux; se comprobarán en el primer despliegue autorizado.

No se necesita cambiar el código de la API para preparar este despliegue.
No hacen falta Dockerfile, Procfile ni render.yaml para este procedimiento.

## Código que recibirá Render

Rama local: `chore/neon-sync-preflight`, con cambios previos sin commit y
archivos nuevos. HEAD y las referencias locales de develop apuntan a
`9edcaa4bf367e1e05b98ed8b139d6f6d817f1e1c`.

Se consultó GitHub Actions por su API pública: [Backend CI para develop en
9edcaa4](https://github.com/Integracion-Continua-y-Entrega/mitbol-backend/actions/runs/41957637328)
terminó en `success`. Esto no cubre los cambios locales posteriores.

Antes de crear el servicio, revisar el diff, autorizar qué cambios se publican,
integrarlos mediante PR a develop y exigir `Backend tests` aprobado para el
commit elegido. No cambiar de rama ni descartar cambios para preparar Render.
El workflow ejecuta push en develop/feature/** y PR a develop/main; un push
a chore/** por sí solo no activa ese workflow. No se hizo commit, push ni PR.

La revisión abarca 21 archivos (9 modificados y 12 nuevos), sin eliminaciones ni
staging. No contiene credenciales reales, manifiestos de sesiones, logs o dumps.
Las URLs con contraseña en ejemplos/pruebas utilizan marcadores o credenciales
ficticias. Hostnames, nombres de roles e identificadores de la tienda son datos
de configuración, no secretos de autenticación. Se mantienen los ignores de
dotenv, claves/certificados privados, pgpass, storage y cachés.

Se corrigieron supuestos locales en las pruebas: ruta Windows en un mock,
herencia de la selección de cluster al simular CI y selección de la fixture del
esquema. Las auditorías SQL de prueba también admiten el servicio aislado de CI
y le pasan su contraseña efímera por entorno. No exigen el formato de conninfo
de psql 18 al cliente de pruebas de Ubuntu; el script operativo sigue exigiendo
psql 18 y sus verificaciones TLS completas, cubiertas por las pruebas específicas.

Propuesta de commits, todavía no ejecutada:

1. `fix(config): prevent dotenv reads in cloud and test environments`
   - app/core/config.py
   - tests/test_cloud_configuration.py
2. `feat(sync): guard initial Neon sync and verify imported catalog`
   - app/cli/sync_catalog.py
   - app/services/catalog_manifest.py
   - app/services/sync_service.py
   - docs/database/verify_neon_catalog_after_sync.sql
   - docs/loyverse/neon_stage3e.md
   - docs/loyverse/neon_stage3e_execute.md
   - docs/loyverse/verify_neon_after_sync.ps1
   - tests/conftest.py
   - tests/test_ci_configuration.py
   - tests/test_postgresql_schema.py
   - tests/test_sync_catalog_cli.py
   - tests/test_catalog_approval.py
   - tests/test_catalog_execution_powershell.py
   - tests/test_catalog_post_sync_sql.py
   - tests/test_catalog_post_sync_tls.py
   - tests/test_catalog_tombstones.py
   - tests/test_stage3e_powershell.py
3. `docs(deploy): close stage 3E and document Render rollout`
   - README.MD
   - docs/deployment/render_stage3f.md

Título propuesto del PR hacia develop:
`feat: proteger la carga inicial en Neon y preparar Render`

Descripción propuesta:

> Protege la carga inicial del catálogo mediante autorización del destino,
> manifiesto aprobado y comprobación del vaciado bajo locks antes del primer DML.
> Añade auditoría posterior de catálogo con TLS del cliente y transporte UTF-8/
> Base64, manejo temporal del token en PowerShell y aislamiento de dotenv en
> cloud/test. Adapta las pruebas al servicio aislado de CI y al cluster temporal.
>
> Documenta el cierre verificado de 3E y el despliegue manual de FastAPI en Render
> con conexión pooled del lector, administración deshabilitada y Auto-Deploy Off.
> No cambia modelos ni revisiones de Alembic.
>
> Validación local: 860 pruebas aprobadas; ocho casos de migraciones excluidos
> por restricción de esta revisión. PostgreSQL 18 temporal y HTTP simulado;
> pip check, actionlint y arranque Uvicorn correctos. Sin conexiones a Neon o
> mitbol_dev, sincronizaciones reales, migraciones ejecutadas o servicios creados.

Pendiente: autorizar staging/commits/push, abrir el PR y exigir Backend tests
aprobado para su nuevo commit antes de autorizar el despliegue de Render.

## Configuración del servicio

| Campo | Valor |
| --- | --- |
| Tipo | Web Service |
| Repositorio | Integracion-Continua-y-Entrega/mitbol-backend |
| Rama | develop, en el commit revisado y aprobado por CI |
| Runtime / Language | Python 3 |
| Root Directory | Vacío; requirements.txt y app/ están en la raíz |
| Región propuesta | Ohio, próxima al Neon en us-east-2 |
| Instancia inicial | Free para desarrollo; confirmar su disponibilidad en el panel |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/` |
| Auto-Deploy | Off |
| Pre-Deploy Command | Vacío |

No añadir migraciones, sincronización, reintentos ni `--reload` al arranque.
Render inyecta PORT; el comando anterior usa la sintaxis del shell Linux de
Render, no la de PowerShell.

## Variables del Web Service

| Variable | Valor / tratamiento |
| --- | --- |
| `PYTHON_VERSION` | `3.13.13` |
| `APP_ENV` | `cloud` |
| `ADMIN_ENABLED` | `false` |
| `EXPECTED_DATABASE` | `mitbol_cloud_dev` |
| `EXPECTED_DATABASE_HOST` | `ep-green-hill-bddfbmgh-pooler.c-8.us-east-2.aws.neon.tech` |
| `DATABASE_URL` | Secreto del lector, con el formato de abajo; introducir únicamente en Render |
| `CORS_ORIGINS` | `["http://localhost:5173"]` para el frontend local ya contemplado; agregar el origen HTTPS exacto cuando exista frontend desplegado |
| `PORT`, `RENDER` | Los proporciona Render; no introducir valores manuales |

Formato público, con contraseña ficticia de marcador (no usar literalmente):

```text
postgresql+psycopg2://mitbol_render_reader:CLAVE_CODIFICADA@ep-green-hill-bddfbmgh-pooler.c-8.us-east-2.aws.neon.tech:5432/mitbol_cloud_dev?sslmode=verify-full&channel_binding=require
```

Obtener el hostname pooled en Neon > Connect, rama production, base
mitbol_cloud_dev, con Connection pooling activo. Confirmar el hostname mostrado
antes de introducirlo en Render. El sufijo esperado es `-pooler` en el ID del
endpoint. Elegir `mitbol_render_reader` si se ofrece en el selector; en cualquier
caso, verificar que la URL use exactamente ese login y su propia contraseña.
No reutilizar una URL del operador cambiando sólo su nombre de usuario.

Conservar la codificación URL de los caracteres especiales de la contraseña.
No codificar de nuevo una contraseña que ya esté codificada en la URL.
La URL y EXPECTED_DATABASE_HOST deben contener el mismo hostname pooled exacto;
no usar el host directo ni un comodín. Cambiar `sslmode=require` de una plantilla
de Neon por `verify-full`, conservando `channel_binding=require`.

La aplicación usa el bundle CA de `certifi` instalado en Render y verifica
certificado y hostname. No copiar una ruta sslrootcert de Windows. El código
fuerza channel binding, deshabilita GSS encryption y conserva su mínimo TLS 1.2;
TLS 1.3 fue observado en la auditoría directa. La sesión pooled todavía debe
validarse desde el despliegue: la auditoría directa no prueba esa conexión.
No rebajar TLS o channel binding para sortear un error.

Psycopg2 utiliza `set_session(readonly=True)` sin autocommit, que inicia cada
transacción con BEGIN READ ONLY. Es compatible con el pooling por transacción;
no depende de SET persistentes en una sesión del pooler. El pool SQLAlchemy
actual usa 3 conexiones, hasta 2 adicionales y pool_pre_ping.

No configurar LOYVERSE_ACCESS_TOKEN, LOYVERSE_STORE_ID, ADMIN_API_KEY, variables
MITBOL_SYNC_*, credenciales del owner ni overrides PGHOSTADDR/PGSERVICE/
PGSERVICEFILE/PGOPTIONS. No importar `.env` ni usar un grupo compartido de
variables que incluya esas credenciales. Pegar CORS_ORIGINS como JSON literal,
sin comillas exteriores de PowerShell. El rol lector no se reutilizará para
futuros endpoints de pedidos/escritura.

## Pasos del panel, pendientes de autorización

1. Confirmar el commit publicado con CI aprobado y revisar esta configuración.
2. En Render, New > Web Service, conectar únicamente este repositorio de GitHub.
3. Seleccionar develop, Python 3, raíz vacía, región y plan de la tabla.
4. Introducir build/start, health check y variables. Mantener Auto-Deploy Off y
   Pre-Deploy vacío. No crear un PostgreSQL de Render: se utilizará Neon.
5. Revisar especialmente usuario lector, host pooled y ausencia de secretos del
   operador/Loyverse. No pegar URLs o capturas con credenciales en el chat.
6. Detenerse ANTES de Create Web Service / Deploy Web Service. La creación
   puede iniciar el primer build incluso con Auto-Deploy Off.
7. Sólo con autorización de creación/despliegue, pulsar el botón final y
   comprobar posteriormente que Auto-Deploy sigue Off. Si se necesita un
   despliegue manual adicional, analizar primero la causa y autorizarlo.

No se ha creado ni configurado remotamente ningún servicio durante esta etapa.

## Pruebas después del primer despliegue autorizado

No ejecutar estos comandos antes de disponer del servicio autorizado. Sólo
consultan HTTP; no requieren contraseña PostgreSQL ni token Loyverse.

```powershell
$base = 'https://NOMBRE-DEL-SERVICIO.onrender.com'
Invoke-RestMethod -Uri "$base/" -Method Get
$menu = Invoke-RestMethod -Uri "$base/api/menu" -Method Get
$variants = @($menu.products | ForEach-Object { $_.variants })
if ($menu.currency -cne 'MXN' -or @($menu.products).Count -ne 3 -or $variants.Count -ne 5) {
    throw 'El catalogo publicado no coincide con la carga inicial; no repetir sincronizacion.'
}
foreach ($product in $menu.products) {
    Invoke-RestMethod -Uri "$base/api/menu/$($product.product_id)" -Method Get
}
```

Resultados esperados:

- GET `/`: 200 y `mensaje` indicando que la API funciona. Es salud del proceso,
  no prueba de disponibilidad de PostgreSQL; la conexión es perezosa.
- GET `/api/menu`: 200, MXN, 3 productos y 5 variantes. Revisar precios como
  cadenas decimales y disponibilidad; no asumir IDs internos consecutivos.
- GET de cada detalle: 200; grupos/complementos y asociaciones corresponden
  al catálogo aprobado. Sin imágenes/etiquetas: image_url=null y listas vacías.
- `/docs` y `/openapi.json`: accesibles; operaciones de aplicación sólo GET,
  sin rutas administrativas ni sincronización. GET `/api/admin/tags` y GET
  `/api/v1/sync/loyverse` deben devolver 404. No probar una sincronización POST.
- Probar GET/OPTIONS del menú con Origin http://localhost:5173: debe autorizar
  ese origen, método GET y no credenciales. Un origen ajeno no debe recibir
  Access-Control-Allow-Origin. Añadir el futuro frontend sólo con su HTTPS exacto.
- Revisar Logs: arranque correcto, sin migraciones/sincronización y sin secretos.
  `/` en 200 con `/api/menu` en 500/503 NO completa el despliegue.
- Si falla certificado, channel binding, identidad o privilegios, detenerse y
  corregir la configuración; no cambiar al owner ni relajar los controles.

Las solicitudes del menú abren la conexión pooled y ejecutan las comprobaciones
del controlador y del lector. No se necesita repetir la auditoría completa de
Neon para verificar que el backend puede leer el catálogo.

Free puede suspenderse tras 15 minutos sin tráfico y tardar alrededor de un
minuto en despertar. Su filesystem es efímero. Con ProductImages vacía no se
necesita disco persistente para esta publicación; la futura carga de fotografías
requerirá almacenamiento duradero antes de habilitarla.

## Referencias oficiales consultadas

- [FastAPI en Render](https://render.com/docs/deploy-fastapi)
- [Versión de Python](https://render.com/docs/python-version)
- [Despliegues y Auto-Deploy](https://render.com/docs/deploys)
- [Health checks](https://render.com/docs/health-checks)
- [Regiones](https://render.com/docs/regions)
- [Plan Free](https://render.com/docs/free)
- [Pooling de Neon](https://neon.com/docs/connect/connection-pooling)
- [TLS de Neon](https://neon.com/docs/connect/connect-securely)
- [Psycopg2 set_session](https://www.psycopg.org/docs/connection.html#connection.set_session)
