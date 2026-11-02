# HU-008 — Bloque 4, primera parte: idempotencia

## 1. Qué hicimos y cómo funciona

Se realizó una única repetición real autorizada de la sincronización del
catálogo de prueba, desde **I.C.E** hacia **mitbol_dev**. No se modificó Loyverse.
No se inició la prueba de cambio de precio ni la sincronización periódica.

| Tabla | Antes | Después | Filas nuevas o eliminadas |
| --- | ---: | ---: | ---: |
| Products | 3 | 3 | 0 |
| ProductVariants | 5 | 5 | 0 |
| ProductModifiers | 2 | 2 | 0 |
| ProductComplements | 5 | 5 | 0 |
| ProductModifierAssignments | 4 | 4 | 0 |

La verificación SQL encontró cero grupos duplicados, referencias huérfanas y
precios inválidos. La comparación completa conservó todos los identificadores,
precios, relaciones y campos locales. Las cuatro parejas de asignación siguieron
siendo `(2, 1)`, `(3, 1)`, `(4, 2)` y `(5, 2)`.

### Pasos de la ejecución

1. Se comparó la configuración efectiva con `.env` y se exigió `mitbol_dev`.
   La inspección inicial fue de sólo lectura y guardó las cinco tablas completas
   en `hu008_block4_idempotency_before.json`.
2. El servicio volvió a comprobar `current_database()` dentro de su transacción
   y adquirió su bloqueo de sincronización antes de consultar Loyverse.
3. Se verificaron las tablas y la revisión `20261029_0001`. Se comprobó que las
   filas locales aún coincidieran con la captura inicial.
4. La API confirmó el ID de tienda `9e30730f-a5b4-4485-8cc4-bc499cc6e239` y el
   nombre I.C.E. La descarga final tuvo cinco GET: `/stores` una página,
   `/items` tres y `/modifiers` una. La paginación incluyó dos bajas conocidas
   que siguen sin corresponder a registros locales.
5. `transform_catalog()` validó precios y referencias. El plan comercial se
   comparó con el del Bloque 3, ignorando el momento de consulta y el orden de
   las colecciones. Los importes se compararon numéricamente como `Decimal`.
   No hubo diferencias remotas inesperadas.
6. Se publicaron los resultados antes del DML. Luego se ejecutó una sola llamada
   a `_write()`, reutilizando los UPSERT existentes.
7. Dentro de la misma transacción se compararon las filas completas antes y
   después, excluyendo únicamente `synced_at`. Cualquier diferencia habría
   provocado rollback. Después del commit se hizo otra comparación desde una
   conexión de sólo lectura y se ejecutaron las consultas SQL de integridad.

La lectura utilizada terminó el **2 de noviembre de 2026 a las 00:18:24.592391**,
hora de Ciudad de México (`2026-11-02T06:18:24.592391+00:00`). La confirmación fue
a las **00:18:41.432847**, en la misma zona.

### Qué significa idempotencia en esta prueba

Repetir el mismo catálogo conserva su estado comercial y las identidades.
Los UPSERT sí ejecutan SQL y actualizan `synced_at`: cambiaron tres fechas de
productos, cinco de variantes, dos de grupos y cinco de complementos. Las
asociaciones no tienen esa columna y permanecieron exactamente iguales.

Las secuencias también avanzaron, aunque no se insertaron entidades nuevas:

| Secuencia | Antes | Después |
| --- | ---: | ---: |
| Products | 3 | 6 |
| ProductVariants | 5 | 10 |
| ProductModifiers | 2 | 4 |
| ProductComplements | 5 | 10 |

PostgreSQL puede obtener el siguiente valor de una secuencia al intentar una
inserción que termina en conflicto. El UPSERT conserva la PK de la fila existente.
Una secuencia con saltos no demuestra que haya duplicados; las PK no son un
contador continuo de registros.

### Precios e identificadores de variantes conservados

| ID interno | Producto | Variante | Precio |
| ---: | --- | --- | ---: |
| 1 | Papas fritas | Papas fritas | 45.00 |
| 2 | Limonada natural | 355 ml | 35.00 |
| 3 | Limonada natural | 500 ml | 49.00 |
| 4 | Hamburguesa clásica | Sencilla | 89.00 |
| 5 | Hamburguesa clásica | Doble | 119.00 |

También permanecieron iguales los cinco precios de complementos: 5.00, 5.00,
15.00, 20.00 y 0.00. Los UUID externos completos están en el informe.

Se conservaron `description`, `is_available`, `is_featured` e `is_new` de
productos y `is_available` de variantes. Fotografías y etiquetas no forman
parte de estas cinco tablas instaladas; no se emitieron operaciones sobre ellas.
No se conectó a `mitbol_db` ni se cambiaron modelos, columnas o migraciones.

## 2. Archivos creados o modificados

| Archivo | Responsabilidad |
| --- | --- |
| `docs/loyverse/hu008_block4_idempotency_before.json` | Captura inicial de sólo lectura con IDs, precios, relaciones y campos locales |
| `docs/loyverse/hu008_block4_idempotency_run.py` | Procedimiento excepcional de esta prueba: compara el catálogo, ejecuta una vez y verifica |
| `docs/loyverse/hu008_block4_idempotency_report.json` | Plan revisado, filas completas antes/después, comparaciones y secuencias |
| `docs/loyverse/hu008_block4_idempotency_sql_results.json` | Resultados reales de reutilizar el SQL del Bloque 3 |
| `docs/loyverse/hu008_block4_idempotency.md` | Esta explicación |
| `tests/test_catalog_sync_postgresql.py` | Añade una prueba con fecha de descarga distinta y campos locales deliberadamente personalizados |
| `docs/loyverse/hu008_block2_test_database.json` | Actualizado automáticamente por las fixtures al ejecutar las pruebas aisladas |
| `README.MD` | Estado de la primera parte y enlace a la evidencia |

No se cambió el código del servicio ni del cliente de Loyverse. El procedimiento
reutiliza `_transaction()`, `_plan()` y `_write()` de `SyncService`, y `encode()`,
`read_rows()` y `require()` del procedimiento anterior. No vuelve a ejecutar el
script del Bloque 3 ni su comprobación de tablas vacías.

El procedimiento rechaza otra ejecución si su informe indica que la escritura
ya se inició o confirmó. Así una interrupción requiere inspeccionar el resultado
antes de decidir repetirla. No incluye un modo de actualización de precios.

## 3. Fragmentos reales importantes

### UPSERT: `app/services/sync_service.py`

```python
options = {
    "index_elements": [table.c.loyverse_id],
    "set_": {field: statement.excluded[field] for field in fields},
}
```

La clave única externa identifica el conflicto. `fields` contiene únicamente
las columnas comerciales permitidas. `excluded` representa los valores entrantes;
los campos locales no incluidos permanecen como estaban. El `RETURNING`
posterior obtiene la misma clave interna de la fila existente.

### Asociación sin duplicados: `app/services/sync_service.py`

```python
).on_conflict_do_nothing(
    index_elements=["product_variant_id", "product_modifier_id"],
)
```

Es el final de la sentencia de inserción de asociaciones. La clave primaria
compuesta impide repetir la misma pareja variante–grupo. El servicio informó
`assignments_inserted=0` y `assignments_removed=0` en esta repetición.

### Comparación del estado de negocio: `hu008_block4_idempotency_run.py`

```python
return {table: [{key: value for key, value in row.items() if key != "synced_at"}
                for row in rows] for table, rows in data.items()}
```

`business_rows()` produce una copia de comparación sin la fecha de consulta.
No excluye PK, UUID externos, precios, FK ni campos locales. La captura original
con todas las columnas se conserva en el informe.

### Comprobación antes de confirmar: `hu008_block4_idempotency_run.py`

```python
require(business_rows(before) == business_rows(after),
        "La repetición alteró identificadores, precios, relaciones o campos locales.")
```

No basta con contar filas: borrar y recrear registros podría mantener un conteo
igual con IDs distintos. Esta comparación obliga a conservar todas las columnas
de negocio. Se ejecuta dentro de la transacción y también después del commit.

## 4. Consultas SQL para verificar personalmente

Conéctate a `mitbol_dev` en pgAdmin y ejecuta:

```sql
SELECT current_database();

SELECT p.product_id, p.loyverse_id AS product_loyverse_id,
       p.name AS producto, v.product_variant_id,
       v.loyverse_id AS variant_loyverse_id, v.name AS variante,
       v.price, v.synced_at
FROM public."Products" p
JOIN public."ProductVariants" v ON v.product_id = p.product_id
ORDER BY v.product_variant_id;
```

Para Doble debe aparecer el ID **5**, UUID
`246aad80-4540-4c1a-8877-2e31bc03d668`, producto padre **3** y precio **119.00**.

Para buscar duplicados de variantes:

```sql
SELECT loyverse_id, count(*)
FROM public."ProductVariants"
GROUP BY loyverse_id
HAVING count(*) > 1;
```

Debe devolver **cero filas**. Para inspeccionar las cuatro relaciones:

```sql
SELECT a.product_variant_id, v.name AS variante,
       a.product_modifier_id, m.name AS grupo
FROM public."ProductModifierAssignments" a
JOIN public."ProductVariants" v ON v.product_variant_id = a.product_variant_id
JOIN public."ProductModifiers" m ON m.product_modifier_id = a.product_modifier_id
ORDER BY a.product_variant_id;
```

Se reutilizó [hu008_block3_verify.sql](hu008_block3_verify.sql), que incluye
conteos, todos los precios, claves externas, duplicados de las cinco tablas,
referencias huérfanas y precios inválidos. Es de sólo lectura y sus resultados
de esta ejecución están en el nuevo informe SQL, sin sobrescribir los del Bloque 3.
La comparación histórica antes/después está en los JSON: una consulta actual
por sí sola no demuestra qué valores había antes de sincronizar.

## 5. Pruebas y resultados

Antes de la repetición real:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Resultado: **124 passed in 22.12s**, sin fallos ni omisiones. La nueva prueba
`test_fresh_snapshot_changes_only_sync_timestamp` usa datos simulados, establece
campos locales distintos de sus defaults, avanza un minuto la fecha del snapshot
y verifica que todo se conserve salvo `synced_at`. La prueba anterior de
repetición usaba exactamente la misma fecha; esta amplía la comprobación.

Después de la repetición real se ejecutó el caso nuevo y las seis verificaciones
de esquema:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_catalog_sync_postgresql.py::test_fresh_snapshot_changes_only_sync_timestamp tests/test_postgresql_schema.py -q -p no:cacheprovider
```

Resultado: **7 passed in 2.92s**. La prueba de persistencia utiliza otra base
PostgreSQL aislada y rollback; las seis de esquema sólo leen `mitbol_dev`.
Se conservaron las bases de prueba con cero filas comerciales residuales.
`git diff --check` tampoco encontró errores.

Las comprobaciones reales del catálogo se hicieron además dentro de la
transacción, desde otra conexión después del commit y con el SQL de integridad.
Ninguna prueba automatizada modificó el precio real de Loyverse.

## 6. Cinco preguntas posibles del profesor

1. **¿Qué demuestra esta idempotencia?** Que repetir un catálogo comercial
   idéntico conserva las filas de negocio, sus identificadores y relaciones.
2. **¿Por qué cambió `synced_at`?** Registra una nueva consulta completada; es
   metadato de sincronización que se verifica por separado del estado comercial.
3. **¿Alcanzaría con comprobar los conteos?** No. Los mismos conteos pueden
   ocultar identidades o relaciones distintas; por eso comparamos todas sus columnas.
4. **¿Por qué avanzaron las secuencias sin filas nuevas?** Un intento de INSERT
   puede consumir un valor antes de resolver su conflicto con la clave externa.
   El UPDATE conserva la PK existente; no se requiere numeración sin saltos.
5. **¿Cómo evitamos sobrescribir campos locales?** Los UPSERT utilizan una lista
   explícita de columnas actualizables y las comparaciones antes/después verifican
   que los campos protegidos permanezcan iguales.

Los conceptos centrales son estado comercial, metadatos de consulta, identidad,
UPSERT, clave única, clave compuesta, transacción y comparación histórica.

## Siguiente paso, detenido

Doble conserva **119.00**. La segunda parte espera que el usuario cambie
manualmente esa variante a **129 MXN** en Loyverse para I.C.E y confirme que guardó
el cambio. No se modificó el precio remoto ni se ejecutó su actualización local.

Ese era el estado al cerrar la primera parte. Posteriormente el usuario confirmó
el cambio y se completó la [segunda parte](hu008_block4_price_update.md), conservando
este informe histórico de la prueba de idempotencia.
