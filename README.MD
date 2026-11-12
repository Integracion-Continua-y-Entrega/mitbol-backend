# ⚙️ Backend - API Menú Digital mitbol

Este repositorio contiene la capa de lógica de negocio del sistema de menú digital para el restaurante **mitbol**. Expone una API RESTful que alimenta la interfaz de los comensales y el panel de administración.

La integración local de sesiones individuales y permisos Admin/Staff con HU-005 y
HU-007 está documentada en [Autenticación administrativa, fase 3](docs/security/admin_auth_phase3.md),
incluidos contratos para React, auditoría y pruebas aisladas. `AUTH_ENABLED=false`
por defecto; cloud mantiene autenticación y administración deshabilitadas. La
clave compartida legada no sustituye una sesión en el modo individual.

La [fase 4](docs/security/admin_auth_phase4.md) documenta y prueba en PostgreSQL
temporal el escritor con permisos por columna y su separación del lector. Incluye
la matriz SQL y el procedimiento futuro para Neon/Render; no habilita cloud ni
crea roles en infraestructura real.

La [fase 5](docs/security/admin_auth_phase5.md) añade límites persistentes en
PostgreSQL, aprovisionamiento por operador, CORS separado y gates administrativos
apagados por defecto. Incluye scripts y guías Neon/Render/React. El perfil
actual de integración, cookies particionadas y activación cloud opt-in está en
[Integración administrativa con React](docs/security/admin_frontend_integration.md).
No se han activado las variables ni creado cuentas definitivas en Render/Neon.

Para desarrollar React en `http://localhost:5173` contra el **Render académico
actual**, seguir [Frontend local → API Render](docs/security/frontend_remote_dev.md).
`AUTH_PROFILE=frontend_dev` reutiliza `mitbol-backend.onrender.com` y Neon
`production / mitbol_cloud_dev`, sin crear servicios ni ramas. Los compañeros
sólo ejecutan React; no necesitan backend/PostgreSQL ni un frontend publicado.

La [HU-004 — Código QR del menú](docs/learning/hu004/README.md) publica la URL
estable del frontend mediante `GET /api/menu/config` y `PUBLIC_MENU_URL`
opcional. No necesita PostgreSQL ni autenticación. Generación, descarga e
impresión del QR corresponden al frontend; no se modifican Neon ni Render.

La [HU-002 — Buscar y filtrar productos](docs/learning/hu002/README.md) añade
`q` opcional y filtros repetibles `tag_id` a `GET /api/menu`, junto con
`GET /api/menu/tags`. Conserva las reglas de disponibilidad y las variantes
completas del producto. Categorías permanece pendiente de soporte relacional.

La [HU-006 — Destacados y novedades](docs/learning/hu006/README.md) incorpora
consulta/edición administrativa de `is_featured` e `is_new` y los publica en menú
y detalle. Su [procedimiento de publicación](docs/deployment/hu006_rollout.md)
coordina la actualización del escritor SQL existente con el backend en Render.

La [HU-009 — Descuentos comerciales](docs/features/hu009-discount-sync.md) añade
sincronización manual transaccional y `GET /api/admin/discounts` protegido.
La migración `20261111_0006` y el SELECT mínimo del rol web se coordinan en el
[procedimiento de publicación HU-009](docs/deployment/hu009_rollout.md).
No incluye aplicar descuentos a pedidos ni activa despliegues automáticamente.

## 🚀 Tecnologías Utilizadas

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
- **Servidor ASGI:** Uvicorn
- **Base de Datos:** PostgreSQL
- **ORM:** SQLAlchemy
- **Integración:** API de Loyverse POS

## 📁 Estructura del Proyecto

El proyecto sigue una arquitectura por capas dentro del directorio `/app`:

- `api/`: Controladores y definición de endpoints.
- `schemas/`: Validación de datos para peticiones y respuestas.
- `models/`: Definición de tablas relacionales.
- `services/`: Lógica de integración externa.

## Integración continua con GitHub Actions

[`.github/workflows/backend-ci.yml`](.github/workflows/backend-ci.yml) ejecuta la
suite completa al hacer push a `develop`, y al abrir, reabrir o
actualizar un Pull Request hacia `develop` o `main`. CI verifica automáticamente
que los cambios mantienen las pruebas aprobadas; esta etapa no despliega la API.

El job **Backend tests** usa Ubuntu 24.04, Python 3.13 (la misma serie que el
entorno local verificado) y binarios PostgreSQL 18 instalados desde el repositorio
oficial PGDG con firma verificada. Ejecuta el mismo laboratorio temporal que en
local, sin servicio Docker ni NAT. El PostgreSQL preinstalado en la imagen del
runner no determina la versión de las pruebas. Se instalan las dependencias
fijadas en `requirements.txt`.

Después de descargar el código y configurar Python, se ejecuta:

```bash
python -m pip install -r requirements.txt
python -m pip check
MITBOL_TEST_PG_BIN=/usr/lib/postgresql/18/bin python -B scripts/test_auth_phase1.py --with-reader -ra --junitxml=test-results/pytest.xml
```

El ejecutor activa `MITBOL_SYNC_POSTGRES_TESTS=1` y `MITBOL_POSTGRES_TESTS=1`,
crea dos clústeres con `initdb -U mitbol_ci` y escucha sólo en `127.0.0.1`, en
puertos aleatorios. El segundo clúster separa los roles de las pruebas del lector.
Sanea el entorno heredado, incluyendo `GITHUB_ACTIONS` en el proceso pytest para
seleccionar las fixtures de clúster temporal, y establece `APP_ENV=test`.
Las fixtures contrastan el directorio real con `SHOW data_directory`, la versión
18 y el operador antes de crear bases. No leen `.env` ni consultan `mitbol_dev`.
Las validaciones de la ruta alternativa de servicio CI siguen cubiertas por tests.

Las fixtures crean bases `mitbol_test_hu008_<identificador>` con catálogo
sintético equivalente a `20261102_0003` y otras bases vacías para comprobar las
migraciones reales de HU-005/HU-007. Las verificaciones de esquema reutilizan el
catálogo temporal en modo de sólo lectura. Se conservan las comprobaciones de
migraciones, las transacciones y los savepoints; no hace falta ejecutar Alembic
aparte ni crear bases manualmente.

Las pruebas de autenticación crean además su propia base temporal
`mitbol_test_auth_<identificador>`, aplican allí la revisión histórica `20261106_0005` (incluye identidad de 0004) y
comprueban sesiones, permisos y auditoría con cuentas sintéticas. Nunca aplican
esa migración en Neon o mitbol_dev. JUnit registra `auth_control_target` y
`auth_isolated_target`: versión, base, current_user, session_user, dirección y
puerto vistos por PostgreSQL, sin URLs ni secretos. El guard SQL del escritor
conserva sus restricciones de PostgreSQL 18, nombre temporal, operador y TCP
loopback. El ejecutor detiene ambos clústeres en `finally` y conserva los archivos
temporales para diagnóstico.

Las pruebas de HU-009 aplican además el head actual `20261111_0006` en bases
temporales para comprobar descuentos, commits reales y permisos de lectura.

Las pruebas de HU-008, HU-001, HU-003, HU-005 y HU-007 usan estas fixtures.
Loyverse se simula con RESPX, `httpx.MockTransport` y snapshots existentes. El
ejecutor excluye los tokens heredados, mantiene `ADMIN_ENABLED=false` y
`AUTH_ENABLED=false` y no configura una clave administrativa global.
Las pruebas administrativas crean aplicaciones aisladas
con claves temporales. No se inicia un servidor HTTP ni una sincronización real.

Las acciones oficiales están fijadas a commits de sus versiones publicadas.
El token de GitHub sólo tiene `contents: read` y checkout no conserva sus
credenciales. No se usan secretos del repositorio ni `pull_request_target`.
El reporte JUnit se conserva siete días como artefacto **backend-pytest-results**,
incluso si pytest falla; el código de salida de pytest determina el fallo del job.

Para revisar una ejecución, abre **Actions → Backend CI → ejecución → Backend
tests → Run complete test suite**. Allí aparecen las pruebas aprobadas, fallidas,
errores y omisiones. El resumen de la ejecución permite descargar el artefacto.
La instalación PGDG y la ejecución en Ubuntu deben confirmarse en la siguiente
corrida real de Actions. La corrección, evidencia y límites de validación están
en el [reporte del error de preparación del escritor](docs/security/admin_auth_ci_fix.md).

Los cambios se revisan mediante Pull Request hacia `develop`; las ramas
dependientes deben revisar primero su base. Actualizar el PR activa el workflow
mediante `pull_request.synchronize`. Esperar el check **Backend tests** antes de
fusionar. El pipeline no hace merge ni despliegue automáticamente.

Después de comprobar la primera ejecución, un administrador puede ir a
**Settings → Branches → Add branch protection rule**, indicar `develop`, activar
**Require a pull request before merging** y **Require status checks to pass
before merging**, y seleccionar **Backend tests** (workflow **Backend CI**).
Puede activar también **Require branches to be up to date before merging** y
restringir los bypass. La disponibilidad depende de la visibilidad y el plan del
repositorio. Usa el nombre del check que aparezca en el PR.

## Preparación de Render + Neon (Etapa 2)

El código está preparado para una API pública de consulta. Esta etapa no crea
servicios, no aplica migraciones a `mitbol_dev`/Neon y no sincroniza datos reales.
La administración temporal de HU-005/HU-007 sigue siendo exclusivamente local.

### Entornos y secretos

`APP_ENV` admite `local` (predeterminado), `test` y `cloud`. `local` conserva la
carga de `.env`; `test` y `cloud` sólo usan variables del proceso o argumentos
explícitos de `Settings`. No completan valores desde `.env` ni archivos de
secretos. `APP_ENV=cloud` debe declararse en el proceso; no se activa desde `.env`.
En Render, `RENDER=true` también obliga a declarar `APP_ENV=cloud`.

`LOYVERSE_ACCESS_TOKEN` es opcional para iniciar y consultar la API. El cliente
Loyverse y el comando de sincronización lo exigen antes de contactar el proveedor.
No se debe configurar ese token en el Web Service público.

Variables del Web Service:

| Variable | Valor o propósito |
| --- | --- |
| `APP_ENV` | `cloud` |
| `ADMIN_ENABLED` | `false`; `true` provoca rechazo de arranque en cloud |
| `DATABASE_URL` | Secreto: URL PostgreSQL pooled con usuario exclusivamente lector |
| `EXPECTED_DATABASE` | Nombre exacto de la base compartida, por ejemplo `mitbol_cloud_dev` |
| `EXPECTED_DATABASE_HOST` | Host exacto del endpoint pooled de esa base |
| `CORS_ORIGINS` | JSON explícito, por ejemplo `["http://localhost:5173"]` |
| `PYTHON_VERSION` | `3.13.13`, compatible con la serie 3.13 de CI |
| `PORT` | Lo proporciona Render; no fijarlo en el código |

Para añadir el frontend desplegado se agrega su origen HTTPS exacto a la lista
JSON. No se admiten comodines, rutas, credenciales ni orígenes remotos HTTP.
HTTP sólo se permite para React en localhost/loopback. La API pública permite
CORS para GET y Content-Type, sin cookies ni credenciales CORS. CORS controla
navegadores: no es autenticación ni evita que otros clientes lean una API pública.

No configurar en Render `ADMIN_API_KEY`, `LOYVERSE_ACCESS_TOKEN`, ni credenciales
de migración o sincronización. No introducir secretos en React, Git, argumentos
CLI, logs o capturas. `.env` y `.env.*` están ignorados, salvo `.env.example`.

### PostgreSQL y verificación del destino

Ejemplo de formato (los valores son ficticios; no pegar secretos en este archivo):

```text
postgresql+psycopg2://USUARIO:CONTRASENA_CODIFICADA@HOST/BASE?sslmode=verify-full&channel_binding=require
```

Se conservan los caracteres especiales de usuario/contraseña codificados en la
URL. `EXPECTED_DATABASE` y `EXPECTED_DATABASE_HOST` son una autorización
independiente: no se derivan automáticamente de `DATABASE_URL`.

Cloud rechaza `mitbol_dev`, bases `mitbol_test*`, bases de mantenimiento, hosts
locales/IP y parámetros que puedan redirigir la conexión (`host`, `hostaddr`,
`dbname`, `service`, `options`, duplicados, etc.). También rechaza los overrides
de entorno `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE` y `PGOPTIONS`.
Las migraciones online y la sincronización fuera de modo cloud admiten sólo
conexiones loopback: omitir APP_ENV no permite operar una base remota sin su
autorización. La generación SQL offline conserva los destinos ficticios de pruebas.

El controlador verifica el certificado y hostname con `sslmode=verify-full`,
channel binding obligatorio y TLS mínimo 1.2. La CA predeterminada es el bundle
de `certifi` ya incluido en requirements; `sslrootcert` permite seleccionar un
archivo CA explícito accesible en el entorno operador. No se admite degradar a
`require`, `prefer` o `disable`.

Al abrir una conexión cloud se contrastan host, puerto, usuario, base y TLS
efectivos del controlador, además de `SELECT current_database()`. No se compara
la IP de PostgreSQL con el DNS público: Neon puede utilizar proxies. La identidad
del servidor se acredita con la verificación TLS del hostname autorizado.

El pool tiene tres conexiones, hasta dos adicionales, espera máxima de diez
segundos y connect timeout de diez segundos. `pool_pre_ping=True` descarta
conexiones cerradas por una suspensión antes de reutilizarlas. No reintenta una
transacción ya iniciada ni repite escrituras automáticamente.

La API cloud exige una cuenta sin permisos de escritura: comprueba los privilegios
del rol y de tablas/columnas/secuencias públicas, y configura sus sesiones como
read only. El rol requiere CONNECT, USAGE sobre public y SELECT sobre las tablas
del catálogo; no debe ser owner, superusuario ni miembro de roles privilegiados.
En la Etapa 3 se crearán y verificarán los grants; el código no los concede.
El chequeo se hace al abrir una conexión física. No cambiar permisos del lector
durante la operación; si se cambian, hay que reiniciar el servicio para revalidarlos.

### Primer despliegue manual desde Render Dashboard

El cierre confirmado de 3E y la preparación actual de 3F están en
[`docs/deployment/render_stage3f.md`](docs/deployment/render_stage3f.md), con la
configuración concreta de Neon, CI y las pruebas posteriores. Crear/desplegar el
servicio sigue pendiente de autorización.

Neon ya está migrado y el catálogo y el lector están auditados. Los siguientes
pasos preparan el primer servicio; su creación/despliegue requiere autorización:

1. Integrar mediante PR hacia `develop` los cambios revisados y esperar el check
   **Backend tests**. Render utilizará ese código de `develop`.
2. En Render Dashboard elegir **New > Web Service** y conectar GitHub únicamente
   al repositorio `mitbol-backend`. Seleccionar la rama `develop`.
3. Elegir Python y el plan Free de desarrollo. Elegir una región cercana a la
   región de Neon. Dejar Root Directory vacío: `requirements.txt` y `app/` están
   en la raíz del repositorio, no dentro de `app/` como raíz de ejecución.
4. Completar build/start y las variables de la tabla anterior antes del primer
   build. Fijar `PYTHON_VERSION=3.13.13`; no depender del Python predeterminado de
   Render. El start debe apuntar a `app.main:app` y utilizar el PORT de Render.
5. Usar la conexión **pooled del lector** en DATABASE_URL. EXPECTED_DATABASE_HOST
   debe ser el mismo host pooled exacto, con `-pooler`, no el host directo del
   operador. Mantener `sslmode=verify-full&channel_binding=require`. No copiar
   rutas sslrootcert de Windows: el bundle certifi del servidor es el default.
6. Pegar CORS_ORIGINS como JSON literal en el campo de Render, por ejemplo
   `["http://localhost:5173"]`, sin comillas exteriores de PowerShell. No añadir
   otros orígenes hasta autorizarlos. No configurar credenciales de owner,
   sincronización, ADMIN_API_KEY ni token Loyverse en el Web Service.
7. Fijar Health Check Path `/` y Auto-Deploy **Off**. Revisar también Settings
   después de la creación para confirmar que permanece Off. No configurar
   comandos de migración en build/start ni agregar CD o deploy hooks.
8. Revisar todos los valores y solicitar autorización antes de pulsar el botón
   que crea/despliega el servicio: la creación puede iniciar el primer build.
   Auto-Deploy Off evita despliegues posteriores por commit; no es una pausa
   garantizada del despliegue inicial. Si hace falta dispararlo manualmente,
   usar **Manual Deploy > Deploy latest commit** sólo con autorización.

| Opción | Configuración |
| --- | --- |
| Tipo / runtime | Web Service / Python |
| Rama | `develop` |
| Directorio raíz | Raíz de este backend |
| Build | `pip install -r requirements.txt` |
| Start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/` |
| Auto-Deploy | `Off` para el primer despliegue |

No hace falta `render.yaml`. `/` comprueba que responde el proceso HTTP; no
certifica el estado de PostgreSQL. Las conexiones siguen siendo perezosas: antes
de compartir la URL hay que verificar `/api/menu` y un detalle con datos reales,
además de CORS, HTTPS y 404 en administración/sincronización.

El plan gratuito puede suspenderse tras 15 minutos sin tráfico, tarda en despertar
y pierde archivos locales al reiniciar/desplegar/suspenderse. No ofrece shell,
disco persistente ni pre-deploy command gratuito. Las migraciones y la carga
inicial se ejecutarán desde un entorno operador controlado, nunca en el build o
start de Render. La API requiere HTTPS; Render termina TLS en su proxy.

Las auditorías posteriores de 3E ya confirmaron esquema, catálogo, lectura y TLS
para el operador y el lector; no repetirlas. Para Render quedan la revisión y
publicación autorizada del código elegido, su CI verde y la configuración pooled
del lector. La preparación local no sustituye la comprobación de instalación y
conectividad del primer build autorizado de Render. El estado concreto se
mantiene en la guía de 3F enlazada arriba.

### Lector de Render: Etapa 3D completada

El SQL exacto está en
[`docs/database/prepare_render_reader.sql`](docs/database/prepare_render_reader.sql).
El rol ya se creó y verificó en `mitbol_cloud_dev`; no repetir el script ni la
asignación de contraseña. Se conserva como referencia del procedimiento aprobado.
Para otra base requerirá autorización independiente y una conexión directa TLS
verificada; el script comprueba base, operador y revisión y rechaza un rol existente.

El rol `mitbol_render_reader` tiene LOGIN y PASSWORD NULL inicialmente. Después
de aprobar/aplicar el SQL, establecer su contraseña con el comando
`\password mitbol_render_reader` desde psql (entrada oculta), sin guardarla en Git ni en el
historial. Crear el rol mediante SQL evita asignarle los privilegios elevados
que reciben los roles creados por las herramientas de administración de Neon.

Se concede únicamente CONNECT, USAGE sobre public y SELECT sobre Products,
ProductVariants, ProductModifiers, ProductComplements, ProductModifierAssignments,
ProductImages, Tags y ProductTags. No se concede SELECT sobre alembic_version,
ni acceso a secuencias, escritura, DDL, ownership o membresías. El default read
only del rol es una defensa adicional; los permisos reales impiden el DML sobre
el catálogo incluso si un cliente desactiva ese default. Esto no certifica ausencia
absoluta de efectos persistentes por funciones internas: se conserva el riesgo
residual documentado y aceptado en la Etapa 3D.

El script retira CREATE/TEMPORARY de PUBLIC en **esta base** y CREATE de PUBLIC
en public: PostgreSQL no permite negar individualmente permisos que un usuario
recibe de PUBLIC. Estos cambios también afectan a otros usuarios que dependan de
esos grants; el owner conserva sus derechos y los operadores legítimos podrán
recibir grants explícitos. La verificación final aborta si persisten permisos
de escritura o creación heredados.

No se concede SELECT automático a todas las tablas futuras, que podrían contener
datos privados de otras historias. Los defaults del owner de migraciones no
otorgan permisos al lector. Cuando una tabla nueva sea necesaria para un endpoint
público, se revisará y concederá su SELECT explícitamente. Si cambia el rol que
ejecuta Alembic, habrá que revisar también sus default privileges. El lector nunca
ejecutará migraciones ni sincronizaciones.

### Migraciones: referencia para una base nueva

La etapa inicial documentó `mitbol_cloud_dev` en `20261102_0003`; ese registro
histórico no acredita su revisión actual. No repetir el verificador de base
vacía en una instalación existente: para HU-009 seguir su procedimiento de
actualización. Esta sección se aplica a otra base nueva autorizada.

Antes de migrar una base Neon nueva, usar `python -m app.cli.verify_neon --validate-url`
para validar la configuración sin conectarse. Después, `python -m app.cli.verify_neon`
comprueba host, base, usuario (por defecto `neondb_owner`), TLS efectivo, transacción
READ ONLY y ausencia de revisiones/objetos de aplicación. No carga `.env`, no
consulta Loyverse, no aplica migraciones y termina con rollback. Devuelve sólo
metadatos sanitizados; nunca la URL ni la contraseña. `ready_for_migrations` es un
resultado de inspección: todavía requiere autorización para migrar. El preflight
de `sync_catalog` se utiliza después, pues exige 0003 o una revisión compatible
hasta `20261111_0006`.

Si falla, repetir `python -m app.cli.verify_neon --diagnose`. Este modo añade
resolución DNS y apertura/cierre TCP antes de la misma conexión PostgreSQL segura.
Devuelve etapa, categoría, código fijo, SQLSTATE cuando existe, comprobaciones
completadas y versiones de Python/psycopg2/libpq/SQLAlchemy. No devuelve mensajes
del controlador, SQL, contraseñas, DSN ni traceback y no altera variables de
entorno. Un TCP correcto no acredita TLS ni autenticación: esas comprobaciones
continúan mediante libpq con los mismos controles obligatorios. Un error sigue
bloqueando cualquier avance a migraciones.

Usar un proceso separado con `APP_ENV=cloud`, `DATABASE_URL` **directa** (sin
pooler), `EXPECTED_DATABASE` y `EXPECTED_DATABASE_HOST` del destino autorizado.
La credencial de ese proceso necesita permisos de esquema; no es el lector de
Render. Cargar los secretos mediante el gestor de secretos o entrada oculta,
sin guardarlos en el historial del shell ni modificar el `.env` local.

Después de confirmar proyecto/branch/endpoint de Neon y base nueva vacía:

```text
python -m alembic current
python -m alembic upgrade 20261111_0006
python -m alembic current
python -m alembic check
```

`current` inicial no tendrá revisión en una base nueva. El resultado final debe
ser `20261111_0006` y `check` no debe detectar operaciones pendientes. Se validan
destino real y TLS antes de que Alembic escriba. La revisión inicial conserva la
protección que rechaza objetos de aplicación preexistentes. No usar `stamp` como
sustituto de migrar. No se modificaron las tres revisiones aprobadas.

### Sincronización manual y preflight

`python -m app.cli.sync_catalog` no importa FastAPI, no utiliza la sesión global y
no carga `.env`. Requiere `APP_ENV=cloud`, `ADMIN_ENABLED=false`, URL directa,
`EXPECTED_DATABASE` y `EXPECTED_DATABASE_HOST`. Los argumentos deben coincidir
con esa autorización. La carga inicial ya se completó: no repetirla.

Inspección predeterminada, sin token, sin tienda, sin contactar Loyverse y con una
transacción PostgreSQL READ ONLY:

```text
python -m app.cli.sync_catalog --expected-database mitbol_cloud_dev --expected-host HOST_DIRECTO --preflight
```

Verifica destino, TLS y revisión compatible entre `20261102_0003` y
`20261111_0006`. Es una operación de
lectura de la base remota, por lo que sólo se ejecutará con el destino autorizado
durante la Etapa 3. Omitir `--preflight` mantiene este mismo modo seguro.

El procedimiento completo aplicado está en
[`docs/loyverse/neon_stage3e_execute.md`](docs/loyverse/neon_stage3e_execute.md).
El CLI de carga inicial exige las ocho tablas vacías, token y tienda explícitos,
y un `MITBOL_SYNC_APPROVED_PREFLIGHT` válido en memoria para el modo `--execute`.
Compara el manifiesto aprobado con la descarga real y vuelve a comprobar el
vaciado bajo locks antes del primer DML. No basta con añadir `--execute` al
preflight. No se usa este procedimiento para una segunda carga o sincronización
periódica; ambas requerirían diseño y autorización independientes.

El ID debe coincidir con `LOYVERSE_STORE_ID` y con una tienda activa accesible en
Loyverse. Destino y revisión se vuelven a comprobar dentro de la transacción de
SyncService antes de contactar Loyverse. Se conservan el bloqueo, UPSERT,
idempotencia, rollback, disponibilidad comercial/local y enriquecimientos locales.
Si falla la tienda, el esquema, TLS o el destino, no se aplican datos.
No hay cron, tarea al arrancar ni endpoint público de sincronización. El router
antiguo `app/api/sync.py` permanece desregistrado y no debe habilitarse.

### Fotografías y secuencia pendiente

La base cloud ya contiene el catálogo comercial. `ProductImages`
permanece vacía: no se copiaron referencias/archivos locales. El contrato actual
devuelve `image_url=null` y galerías vacías; administración y cargas están apagadas.
No se modificaron archivos de `storage/` ni el proveedor local de almacenamiento.

Riesgo fuera de ese escenario: el menú anuncia referencias registradas aunque
falte el archivo; medios devuelve 404. Importar referencias sin archivos volvería
a producir URLs rotas. Antes de hacerlo habrá que adaptar almacenamiento externo
persistente y/o filtrar referencias inexistentes, conservando el contrato público.

Neon ya está sincronizado y auditado. Quedan la integración del código y el
primer despliegue autorizado de Render con su conexión pooled del lector.
Auto-Deploy permanece Off. Una futura habilitación de `After CI Checks Pass`
requerirá autorización aparte, conservando PR y el check Backend tests.
No se añadieron CD ni secretos a GitHub.

### Integrar el código y preparar el primer despliegue

1. Revisar el diff y los archivos nuevos de `chore/neon-sync-preflight`, incluidas
   pruebas aisladas y ausencia de secretos. No cambiar de rama ni descartar cambios.
2. Sólo con autorización, seleccionar los archivos revisados, realizar los
   commits/push y abrir el PR hacia `develop`. Exigir Backend tests aprobado.
3. Preparar en Render los valores documentados, usando exclusivamente la conexión
   pooled de `mitbol_render_reader`, sin credenciales del operador o Loyverse.
4. Autorizar y realizar el primer despliegue manual, con Auto-Deploy Off.
   No repetir migraciones, creación del lector, carga inicial o auditorías aprobadas.
5. Comprobar HTTPS, `/`, `/api/menu`, detalle válido, 404 de producto inexistente,
   CORS desde el origen autorizado y 404 en administración/sincronización. `/`
   por sí solo no acredita conectividad a PostgreSQL.
6. Compartir con frontend la URL base HTTPS y el contrato OpenAPI. Frontend no
    recibe contraseñas, claves administrativas ni token Loyverse.

La rama de trabajo ya existe; no crear otra ni cambiar de rama para integrar
estos cambios. Revisar también `git diff --cached` después de autorizar el staging.
El push a `chore/*` no ejecuta este workflow; la apertura/actualización del PR a
`develop` sí ejecuta Backend tests. No hacen falta secretos de Neon/Render en
GitHub para estas pruebas. No se ejecutan esos pasos Git automáticamente.

Referencias: [FastAPI en Render](https://render.com/docs/deploy-fastapi),
[Python en Render](https://render.com/docs/python-version),
[plan gratuito](https://render.com/docs/free),
[despliegues condicionados a CI](https://render.com/docs/deploys),
[TLS de Neon](https://neon.com/docs/connect/connect-securely),
[SQLAlchemy en Neon](https://neon.com/docs/guides/sqlalchemy).

## 🛠️ Instalación y Uso Local

Sigue estos pasos para correr el proyecto en tu entorno local de Windows:

1. **Clona el repositorio y entra a la carpeta:**

```bash
   git clone https://github.com/Integracion-Continua-y-Entrega/mitbol-backend.git
   cd mitbol-backend
```

2. **Crea y activa un entorno virtual:**

```
   python -m venv venv
   venv\Scripts\activate
```

3. **Instala las dependencias:**

```bash
   pip install -r requirements.txt
```

4. **Configura las variables de entorno:**
   - En la raíz del proyecto, haz una copia del archivo de ejemplo para crear tu propio archivo de configuración local ejecutando este comando:

```
     copy .env.example .env
```

- Abre el nuevo archivo `.env` y reemplaza los valores de ejemplo con tus credenciales reales.

5. **Revisa las migraciones antes de aplicarlas:**

   Los modelos vigentes corresponden a las cinco tablas del catálogo de HU-008.
   Alembic administra el esquema; iniciar FastAPI ya no crea tablas.
   En el entorno de desarrollo verificado se creó `mitbol_dev` y se aplicó la
   revisión `20261029_0001` con autorización. La conexión local apunta a esa base;
   `mitbol_db` conserva íntegramente las tablas y datos del prototipo.

   Consulta la [guía del esquema, pruebas y comandos](docs/database/hu008_preparation.md)
   y el [SQL de la migración inicial](docs/database/hu008_initial.sql).

   Para ver el SQL sin ejecutarlo:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic upgrade head --sql
   ```

   Para comprobar la revisión y la correspondencia con los modelos:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic current
   .\.venv\Scripts\python.exe -m alembic check
   ```

   Las pruebas de PostgreSQL son optativas y sólo consultan `mitbol_dev`:

   ```powershell
   $env:MITBOL_POSTGRES_TESTS = '1'
   .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
   ```

6. **Levanta el servidor de desarrollo:**

```bash
   uvicorn app.main:app --reload
```

La documentación automática de la API estará disponible en: `http://localhost:8000/docs`.

## Consulta de Loyverse — HU-008, Bloque 1

El diagnóstico local consulta tiendas, artículos con variantes y grupos con sus
opciones mediante GET. No escribe en PostgreSQL ni utiliza el servicio de
sincronización del prototipo. Configura `LOYVERSE_ACCESS_TOKEN` y el identificador
de la tienda en `LOYVERSE_STORE_ID` dentro de `.env`.

```powershell
.\.venv\Scripts\python.exe -m app.cli.probe_loyverse --page-size 2
```

La [guía del Bloque 1](docs/loyverse/hu008_block1.md) explica el código, las pruebas,
la procedencia de los precios y la diferencia observada entre `modifier_ids` y
`modifiers_ids`. El [informe de lectura real](docs/loyverse/hu008_block1_report.json)
contiene el catálogo observado, sin credenciales ni cabeceras.

## Persistencia del catálogo — HU-008, Bloque 2

La transformación y los UPSERT están implementados y verificados con PostgreSQL
aislado y datos simulados. El Bloque 3 aplicó después una primera carga real
autorizada, documentada en el apartado siguiente. No hay un endpoint de escritura.

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Las pruebas de persistencia crean otra base `mitbol_test_hu008_<id>`, aplican la
migración existente y revierten sus datos mediante transacciones y savepoints.
No escriben en el catálogo de desarrollo. La base de pruebas se conserva.

Consulta la [guía del Bloque 2](docs/loyverse/hu008_block2.md) y el
[informe de aislamiento](docs/loyverse/hu008_block2_test_database.json).

## Primera carga real — HU-008, Bloque 3

Se realizó una sola sincronización autorizada en `mitbol_dev`, desde la tienda
I.C.E: 3 productos, 5 variantes, 2 grupos, 5 complementos y 4 asociaciones.
La verificación posterior fue de sólo lectura: no encontró duplicados,
referencias huérfanas ni precios inválidos. La revisión sigue siendo `20261029_0001`.

La [guía del Bloque 3](docs/loyverse/hu008_block3.md) explica la ejecución y los
resultados. El [informe](docs/loyverse/hu008_block3_report.json) conserva el plan
previo y las filas confirmadas. Puedes ejecutar las
[consultas SQL de verificación](docs/loyverse/hu008_block3_verify.sql) en pgAdmin
conectado a `mitbol_dev`; son de sólo lectura.

La repetición real y la actualización de precios quedan para el Bloque 4.

## Idempotencia real — HU-008, Bloque 4, primera parte

Se repitió una sola sincronización real con el catálogo de I.C.E sin cambios.
Se conservaron los conteos 3/5/2/5/4, todos los IDs internos y externos,
precios, relaciones y campos locales. Sólo cambió `synced_at` en las filas
comerciales; las secuencias avanzaron por los UPSERT sin reasignar los IDs.
La suite completa dio 124 pruebas aprobadas.

Consulta la [explicación de idempotencia](docs/loyverse/hu008_block4_idempotency.md)
y el [informe antes/después](docs/loyverse/hu008_block4_idempotency_report.json).
Después de esta prueba, el usuario confirmó el cambio manual de Doble de 119
a 129 en Loyverse. Su importación se verificó en la segunda parte siguiente.

## Actualización comercial real — HU-008, Bloque 4, segunda parte

Se confirmó que I.C.E devuelve Doble a 129.00 y se aplicó una sincronización
controlada en `mitbol_dev`. La variante conservó el ID 5, su UUID de Loyverse,
el producto padre 3 y sus asociaciones. Los demás precios y campos locales
permanecieron iguales, sin duplicados y con los conteos 3/5/2/5/4.

La suite dio 125 pruebas aprobadas y las siete comprobaciones posteriores
también pasaron. Consulta la [explicación de actualización de precios](docs/loyverse/hu008_block4_price_update.md),
el [informe antes/después](docs/loyverse/hu008_block4_price_update_report.json)
y las [consultas para pgAdmin](docs/loyverse/hu008_block4_price_update_verify.sql).
El esquema y las migraciones se conservaron; no hay sincronización periódica.

## 👥 Equipo de Desarrollo

- José Fernando Enríquez Maldonado
- Pedro Alonso Montes Fabian
