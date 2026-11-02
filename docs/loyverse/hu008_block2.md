# HU-008 — Bloque 2: transformación y persistencia

## 1. Qué implementamos

Se implementó un servicio que transforma una lectura completa de Loyverse y
persiste sus datos comerciales en las cinco tablas existentes. En este bloque
se ejecutó únicamente con datos simulados y PostgreSQL aislado. No se ejecutó
la primera sincronización real ni se modificaron modelos o migraciones.

La suite final dio **123 passed in 22.54s**: las 75 pruebas previas, 18 nuevas
de transformación y 30 de persistencia en PostgreSQL. El
[informe de la base de pruebas](hu008_block2_test_database.json) confirma que
las cinco tablas de `mitbol_dev` tenían y conservan cero filas.

## 2. Cómo funciona

### Lectura y transformación

1. `SyncService` recibe una fábrica de sesiones, una base esperada y una tienda
   explícitas. No importa la conexión global ni toma automáticamente `.env`.
2. Comprueba `current_database()` y adquiere un bloqueo transaccional de catálogo.
3. `synchronize()` reutiliza `fetch_catalog()` y el cliente HTTP. Solicita
   `show_deleted=true` para artículos y modificadores, conservando su paginación.
4. La lectura completa queda en `CatalogSnapshot`. Este objeto es de Python;
   no representa una tabla ni agrega columnas a PostgreSQL.
5. `transform_catalog()` separa registros activos y bajas explícitas, y reutiliza
   `describe_catalog()` para validar tienda, precios y relaciones.
6. Construye un `CatalogPlan` con datos comerciales permitidos y precios `Decimal`.
   La transformación no modifica la lectura original ni ejecuta consultas SQL.
7. El servicio aplica el plan y confirma una única transacción. Una excepción
   provoca rollback de toda la ejecución y libera su bloqueo.

La transacción permanece abierta durante la descarga en `synchronize()` para
que una segunda ejecución no descargue y aplique una instantánea fuera de orden.
La contrapartida es mantener una conexión ocupada mientras responde Loyverse;
los tiempos de espera y reintentos del cliente siguen limitados. No hay tarea
periódica ni endpoint administrativo nuevo.

`persist_snapshot()` es la entrada interna para una instantánea completa ya
obtenida y para las pruebas. No recibe el JSON del informe del Bloque 1: ese
informe es diagnóstico y no incluye bajas. Para la operación remota futura se
utilizará `synchronize()` con el bloqueo desde antes de la descarga.

### Operaciones concretas sobre cada tabla

| Tabla | Inserción y actualización comercial | Datos protegidos |
| --- | --- | --- |
| `Products` | UPSERT por `loyverse_id`; actualiza `name`, `is_active`, `synced_at` | `description`, `is_available`, `is_featured`, `is_new` no se actualizan |
| `ProductModifiers` | UPSERT por `loyverse_id`; actualiza `name`, `display_order`, `synced_at` | Conserva su identificador interno |
| `ProductVariants` | UPSERT por `loyverse_id`; actualiza `name`, `price`, `synced_at` | Conserva `is_available`, ID y producto padre; rechaza reasignaciones |
| `ProductComplements` | UPSERT por `loyverse_id`; actualiza `name`, `price`, `display_order`, `is_active`, `synced_at` | Conserva ID y grupo padre; rechaza reasignaciones |
| `ProductModifierAssignments` | Inserta las parejas variante–grupo con `ON CONFLICT DO NOTHING`; retira sólo asociaciones comerciales obsoletas | No elimina productos, variantes, grupos ni complementos |

Los registros nuevos de `Products` utilizan los valores predeterminados del
esquema aprobado para sus campos locales; la sincronización no inventa datos
de enriquecimiento. En registros existentes sólo cambia la lista comercial
explícita del UPSERT.

`is_available` se protege tanto en productos como en variantes existentes.
Una variante nueva toma inicialmente `available_for_sale` de la tienda; sus
UPSERT posteriores no sobrescriben una decisión de disponibilidad local, incluso
si la configuración de Loyverse cambió. El modelo sólo tiene una bandera por
variante: no se inventa otra columna para conservar simultáneamente ambos estados.
Se prioriza la protección local solicitada.

La excepción es una baja explícita mediante `deleted_at`: deshabilita la variante
para impedir que se ofrezca una presentación descontinuada. Si reaparece, la
sincronización conserva ese `is_available=false`; su habilitación local requiere
una decisión posterior. No se implementa aquí esa funcionalidad administrativa.

Los IDs internos se recuperan mediante `RETURNING`, tanto al insertar como al
actualizar. Permanecen estables. El servicio no emite operaciones sobre
fotografías o etiquetas ni elimina sus productos o variantes referenciados.
Las tablas de imágenes y etiquetas aún no están instaladas en este esquema de
cinco tablas: se comprobó la estabilidad de los IDs que utilizarán esas relaciones,
sin crear tablas auxiliares para simularlas.

### Precios, nombres y asociaciones

- Se utiliza exclusivamente la configuración de precio `FIXED` de la tienda
  seleccionada. Un precio nulo, negativo, variable, no finito o una configuración
  ausente aborta la ejecución; no se sustituye por el precio de otra tienda o cero.
- El cero explícito de una opción sigue siendo un importe válido.
- El cliente decodifica decimales del JSON como `Decimal`. La persistencia no
  acepta `float` como importe ni impone una escala que el modelo no define.
- Los nombres de variantes se derivan de `option1_value`, `option2_value` y
  `option3_value`, unidos con ` / `. Una variante sin opciones usa el nombre
  real del producto. No se supone que Loyverse devuelva un campo `name` de variante.
- Un grupo declarado por el artículo se asocia a sus variantes activas, siempre
  que el grupo exista y pertenezca a la tienda. No se inventan mínimos, máximos
  ni selecciones obligatorias. Los dos alias comprobados `modifier_ids` y
  `modifiers_ids` mantienen la validación del Bloque 1.
- Una variante u opción existente que cambie de padre produce un error y
  rollback: no se modifica retroactivamente su asociación histórica.

### Descontinuados y respuestas incompletas

Loyverse documenta las bajas mediante `deleted_at` y el filtro `show_deleted`.
Se conserva esta [referencia oficial](https://developer.loyverse.com/docs/).

| Baja explícita | Operación |
| --- | --- |
| Producto | `is_active=false`; sus variantes pasan a no disponibles y se retiran sus asignaciones actuales |
| Variante | `is_available=false`; conserva precio, nombre, ID y producto; se retiran sus asignaciones |
| Grupo | Conserva su fila; desactiva sus complementos y retira sus asignaciones. No se agrega un `is_active` al modelo |
| Complemento | `is_active=false`; conserva su precio e identidad |

No se eliminan las cuatro entidades comerciales ni se modifican pedidos.
Las únicas operaciones DELETE son sobre asociaciones comerciales actuales.
`ProductModifierAssignments` no es una tabla histórica y no contiene fechas.

Una baja nunca importada no crea una fila ficticia para llenar sus campos
obligatorios. Puede procesarse sólo con su identidad y fecha de baja; en variantes
y opciones también se verifica el padre declarado. La ausencia de un registro
en una respuesta no se interpreta por sí sola como baja. Una página incompleta,
referencia desconocida o precio incompatible aborta el proceso conservando el
catálogo anterior.

### Concurrencia y destino

Se utiliza `pg_try_advisory_xact_lock`, un bloqueo de PostgreSQL vinculado a la
transacción. Todas las instancias del servicio usan la misma clave por base:
una segunda ejecución recibe `CatalogSyncBusyError` sin esperar ni escribir.
El bloqueo se libera al commit o rollback y coordina las ejecuciones que utilizan
este servicio. [Documentación de PostgreSQL](https://www.postgresql.org/docs/18/functions-admin.html).

El servicio sólo admite `mitbol_dev` o bases de pruebas con el patrón exacto
`mitbol_test_hu008_<12 caracteres hexadecimales>`. Además verifica la base real
con `current_database()` antes de cualquier escritura. La autorización humana
para la primera ejecución real sigue pendiente: no se publicó un comando de
escritura ni se llamó al servicio contra `mitbol_dev`.

## 3. Archivos creados o modificados en este bloque

| Archivo | Responsabilidad |
| --- | --- |
| `app/services/sync_service.py` | Servicio nuevo de UPSERT, transacción, bloqueo, bajas y comprobación del destino |
| `app/services/catalog_transform.py` | Plan comercial validado y objetos de transferencia en memoria |
| `app/services/catalog_errors.py` | Errores de validación, referencias, concurrencia, destino y persistencia |
| `app/services/catalog_probe.py` | Separa descarga (`fetch_catalog`) y descripción pura (`describe_catalog`) para reutilizarlas |
| `app/services/loyverse_client.py` | Añade `include_deleted` opcional a las consultas existentes |
| `app/services/legacy_sync_service.py` | Conserva la implementación del prototipo separada del servicio vigente |
| `app/api/sync.py` | El router antiguo conserva su referencia al servicio del prototipo; sigue sin registrarse en FastAPI |
| `tests/test_sync.py` | La prueba antigua apunta al servicio conservado |
| `tests/conftest.py` | Datos simulados, base aislada y fixtures con rollback/savepoints |
| `tests/test_catalog_transform.py` | 18 pruebas puras de transformación |
| `tests/test_catalog_sync_postgresql.py` | 30 pruebas de persistencia real, rollback y concurrencia en la base aislada |
| `docs/loyverse/hu008_block2_test_database.json` | Informe de aislamiento y recuentos antes/después |
| `docs/loyverse/hu008_block2.md` | Esta guía |
| `README.MD` | Añade el estado del bloque y el comando de pruebas |

No se editaron `.env`, dependencias, modelos ni migraciones durante este bloque.
Las modificaciones de configuración del Bloque 1 siguen presentes en el workspace.

## 4. Cuatro fragmentos importantes del código real

### Transacción: `app/services/sync_service.py`

```python
with self.session_factory() as session, session.begin():
```

La fábrica crea una sesión exclusiva para la ejecución. `session.begin()` agrupa
las operaciones: confirma al terminar correctamente y revierte ante una excepción.
Los métodos por entidad no hacen commits independientes.

### Exclusión de otra sincronización: `app/services/sync_service.py`

```python
if not session.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
    raise CatalogSyncBusyError("Ya hay una sincronización comercial en ejecución.")
```

PostgreSQL devuelve `true` si obtiene el bloqueo y `false` si otra transacción
lo tiene. La segunda ejecución termina antes de los UPSERT y, en `synchronize()`,
antes de consultar Loyverse. Funciona entre conexiones y procesos distintos.

### UPSERT y conservación de identidad: `app/services/sync_service.py`

```python
options = {
    "index_elements": [table.c.loyverse_id],
    "set_": {field: statement.excluded[field] for field in fields},
}
```

`loyverse_id` identifica el conflicto. `excluded` contiene los valores entrantes.
`fields` es la lista explícita de columnas comerciales actualizables. Los campos
locales de `Products` no aparecen allí. Las líneas siguientes añaden la condición
del padre cuando corresponde y recuperan la PK con `RETURNING`.

### Dinero: `app/services/catalog_transform.py`

```python
if price is None or not price.is_finite() or price < 0:
    raise CatalogValidationError("Precio desconocido, negativo o no finito; no se inventa un importe.")
```

Después de obtener un `Decimal`, se valida que represente un importe conocido,
finito y no negativo. El error se produce antes de aplicar el plan. No se redondea
silenciosamente ni se convierte un desconocido en gratuito.

## 5. Pruebas y resultados reales

Comando completo ejecutado:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Resultado final: **123 passed in 22.54s**, sin pruebas fallidas ni omitidas.
`pip check` devolvió `No broken requirements found`. La primera consulta auxiliar
de inspección falló por un `%` SQL sin parametrizar; se corrigió usando parámetros
de SQLAlchemy. Esa consulta era de sólo lectura y no modificó PostgreSQL.

Las pruebas de persistencia verifican altas, cambios, IDs estables, ejecuciones
repetidas, campos locales, relaciones, retiro de asociaciones obsoletas, precios
inválidos de variantes y complementos, bajas explícitas, referencias a padres
incorrectos, HTTP fallido, destino incorrecto y exclusión mediante dos conexiones.
También verifican que cambios de `available_for_sale` no sobrescriban la
disponibilidad local de variantes existentes y que las nuevas usen la disponibilidad
real inicial de su tienda.

Para comprobar rollback se provoca una división por cero real de PostgreSQL
después de varios UPSERT. Se verifica tanto una inserción inicial como una
actualización de registros existentes. Todo vuelve al estado anterior.

La integración de HTTP y persistencia utiliza `MockTransport` con token ficticio.
Las pruebas comprueban que `show_deleted=true` se envíe y que el bloqueo ya exista
cuando comienza el primer GET. No se contactó la cuenta real de Loyverse.

### Cómo se aísla PostgreSQL

- Sólo se crea una base nueva de nombre aleatorio y prefijo de pruebas.
- Se aplica allí la revisión existente `20261029_0001`, sin modificarla.
- Cada prueba abre una transacción exterior. Las sesiones del servicio usan
  `join_transaction_mode="create_savepoint"`; sus commits liberan savepoints sin
  confirmar la transacción exterior de prueba.
- Al terminar cada caso se revierte esa transacción exterior. No se usa TRUNCATE,
  DROP ni borrado de datos de desarrollo para limpiar las pruebas.
- La base de pruebas se conserva con el esquema y cero filas comerciales; la
  tabla técnica `alembic_version` conserva su revisión.
- Las lecturas de control de `mitbol_dev` usan `default_transaction_read_only=on`.
  El informe registra cero filas antes y después en sus cinco tablas.

La base conserva las secuencias de prueba: los IDs pueden tener saltos porque las
secuencias PostgreSQL no se revierten junto con los datos. Esto no significa que
se hayan duplicado registros. El nombre concreto de la última base está en el
informe JSON; cada ejecución completa crea una base de pruebas independiente.

Para repetir únicamente las pruebas puras:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_catalog_transform.py -q -p no:cacheprovider
```

Para repetir la persistencia aislada:

```powershell
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_catalog_sync_postgresql.py -q -p no:cacheprovider
```

Se necesita un PostgreSQL accesible y permiso de creación de bases para la
cuenta local. Sin la variable de activación, pytest omite estas pruebas de escritura
aislada. Las comprobaciones previas de esquema en `mitbol_dev` usan su variable
independiente `MITBOL_POSTGRES_TESTS=1` y siguen siendo de sólo lectura.

### Qué queda por verificar con autorización

Todavía no se ha ejecutado la descarga con bajas ni la persistencia sobre el
catálogo real de I.C.E. En el Bloque 3 se comprobará otra vez el destino y se
descargará un catálogo nuevo. Si conserva los datos del Bloque 1, se crearán
3 productos, 5 variantes, 2 grupos, 5 complementos y 4 asociaciones variante–grupo.
Los datos se volverán a validar antes de escribir; no se importará el informe
diagnóstico guardado ni se asumirán invariables sus cantidades.

## 6. Cinco preguntas posibles del profesor

1. **¿Qué es un UPSERT?** Es una inserción que, si encuentra el mismo identificador
   único, actualiza las columnas comerciales permitidas del registro existente.
2. **¿Por qué usar una sola transacción?** Para que un fallo no deje productos
   nuevos con variantes o complementos parcialmente actualizados.
3. **¿Cómo evitamos dos sincronizaciones simultáneas?** Un bloqueo transaccional
   compartido por las instancias rechaza la segunda ejecución antes de escribir.
4. **¿Cómo conservamos el enriquecimiento local?** No incluimos esos campos en
   las actualizaciones y mantenemos los IDs internos usados por sus relaciones.
5. **¿Cómo se prueba un commit sin guardar datos de prueba permanentemente?**
   La sesión trabaja dentro de un savepoint de una transacción exterior que la
   prueba revierte al finalizar, usando PostgreSQL real en otra base.

## 7. Conceptos que debes recordar

- Identidad externa (`loyverse_id`) e identidad interna (PK) tienen funciones distintas.
- `ON CONFLICT`, `RETURNING` y las claves únicas permiten actualizar sin duplicar.
- Una lista positiva de columnas protege los datos locales frente al UPSERT.
- Las bajas comerciales no requieren eliminar registros históricos.
- Transacción, rollback, savepoint y bloqueo de concurrencia resuelven problemas distintos.
- Las pruebas de este bloque usan datos simulados y PostgreSQL aislado.
- La primera sincronización real sigue pendiente de autorización. No se avanzó
  a los Bloques 3 o 4 ni se implementaron HU-009 o HU-010.
