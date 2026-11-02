# HU-008 — Bloque 4, segunda parte: actualización comercial

## 1. Qué hicimos y qué resultado obtuvimos

El usuario confirmó que cambió manualmente en Loyverse el precio de
**Hamburguesa clásica → Doble**, de **119.00 a 129.00 MXN** para **I.C.E**.
Se descargó el catálogo actualizado, se comprobó ese precio y se ejecutó una
única sincronización controlada en **mitbol_dev**.

| Dato de Doble | Antes | Después |
| --- | --- | --- |
| ID interno de variante | 5 | 5 |
| ID externo Loyverse | `246aad80-4540-4c1a-8877-2e31bc03d668` | El mismo |
| ID interno del producto | 3 | 3 |
| Precio | 119.00 | 129.00 |
| Grupo asignado | Extras hamburguesa | El mismo |

Los conteos continuaron en **3 productos, 5 variantes, 2 grupos, 5 complementos
y 4 asociaciones**. No se insertó ni eliminó ninguna fila de negocio. Los
productos no se recrearon y la variante existente recibió el importe nuevo.

El único campo de negocio que cambió fue `ProductVariants[5].price`. Los
metadatos `synced_at` de las 15 entidades se actualizaron a la nueva lectura.
Se conservaron todos los identificadores, precios restantes, campos locales y
relaciones. La verificación SQL encontró cero duplicados, referencias huérfanas
y precios inválidos.

La lectura terminó el **2 de noviembre de 2026 a las 08:31:17.250312**, hora de
Ciudad de México (`2026-11-02T14:31:17.250312+00:00`). La transacción se confirmó
a las **08:32:22.542095** en la misma zona. `synced_at` guarda el momento de
lectura, no el de confirmación.

No se modificaron Loyverse, `.env`, los modelos o las migraciones. No se conectó
a `mitbol_db`. No se implementaron otras historias, endpoints ni ejecución periódica.
MXN es el contexto indicado por el usuario; no se inventó un campo de moneda en
la respuesta remota ni se agregó una columna al modelo.

## 2. Cómo funciona y qué validamos

1. Se comparó la URL efectiva con `.env`, exigiendo PostgreSQL y `mitbol_dev`.
   Una conexión de sólo lectura comprobó `current_database()` y guardó las filas
   iniciales completas. Coincidían con el cierre de la primera parte y Doble
   aún costaba 119.00 en PostgreSQL.
2. El servicio abrió su transacción y adquirió el bloqueo de catálogo. Se
   comprobaron nuevamente la revisión `20261029_0001`, las cinco tablas y el
   estado inicial antes de consultar Loyverse.
3. `fetch_catalog()` consultó la tienda configurada y comprobó I.C.E. La lectura
   tuvo cinco GET: una página de tiendas, tres de artículos y una de grupos.
   Los dos artículos dados de baja de las etapas anteriores continuaban ausentes
   de PostgreSQL y no afectaron filas.
4. `transform_catalog()` obtuvo los precios fijos de la tienda y preparó el plan.
   Se comparó con el plan de idempotencia permitiendo sólo Doble a 129.00.
   Se ignoraron el momento de lectura y el orden de las colecciones; nombres,
   UUID, precios restantes, posiciones y asociaciones debían coincidir.
5. Se mostró el resumen antes de escribir: cero filas nuevas, cero eliminadas
   y un precio cambiado. Doble devolvía realmente 129.00 desde Loyverse.
6. `_write()` aplicó el **plan remoto validado** mediante los UPSERT existentes.
   Las constantes 119 y 129 del procedimiento son guardas de esta prueba; no
   sustituyen la respuesta remota ni se ejecutó un UPDATE manual del precio.
7. Antes del commit se compararon todas las columnas de negocio, permitiendo
   exclusivamente el nuevo importe de Doble. Una diferencia habría provocado
   rollback. Después se consultaron las filas desde otra conexión de sólo
   lectura y se repitió la comparación.
8. Se ejecutaron consultas SQL para verificar identidad, relaciones, duplicados
   y todos los precios de variantes y complementos, además de pruebas pytest.

FastAPI no recibió una ruta nueva ni participó como servidor en esta prueba.
El procedimiento operativo reutiliza el servicio del backend. Alembic sólo se
utilizó para preparar bases de pruebas aisladas con la revisión existente;
`mitbol_dev` no recibió DDL.

### Precios confirmados

| ID variante | Producto | Variante | Antes | Después |
| ---: | --- | --- | ---: | ---: |
| 1 | Papas fritas | Papas fritas | 45.00 | 45.00 |
| 2 | Limonada natural | 355 ml | 35.00 | 35.00 |
| 3 | Limonada natural | 500 ml | 49.00 | 49.00 |
| 4 | Hamburguesa clásica | Sencilla | 89.00 | 89.00 |
| 5 | Hamburguesa clásica | Doble | 119.00 | 129.00 |

Los complementos conservaron Chía **5.00**, Hierbabuena **5.00**, Queso extra
**15.00**, Tocino **20.00** y Sin cebolla **0.00**. Las parejas variante–grupo
siguieron siendo `(2,1)`, `(3,1)`, `(4,2)` y `(5,2)`.

## 3. Archivos creados o modificados

| Archivo | Responsabilidad |
| --- | --- |
| `docs/loyverse/hu008_block4_price_update_run.py` | Ejecución excepcional de esta prueba, guardas del cambio autorizado y verificación transaccional |
| `docs/loyverse/hu008_block4_price_update_before.json` | Captura inicial de las cinco tablas |
| `docs/loyverse/hu008_block4_price_update_report.json` | Plan remoto, resultados del servicio, filas y comparaciones antes/después |
| `docs/loyverse/hu008_block4_price_update_verify.sql` | Consultas de sólo lectura para pgAdmin, incluyendo identidades y precios esperados |
| `docs/loyverse/hu008_block4_price_update_sql_results.json` | Resultados reales del SQL general y del específico de esta prueba |
| `docs/loyverse/hu008_block4_price_update.md` | Esta guía |
| `tests/test_catalog_sync_postgresql.py` | Nueva prueba aislada del cambio exclusivo de Doble de 119 a 129 |
| `docs/loyverse/hu008_block2_test_database.json` | Evidencia actualizada automáticamente al ejecutar las pruebas aisladas |
| `docs/loyverse/hu008_block4_idempotency.md` | Enlace al resultado posterior, conservando el estado histórico de la primera parte |
| `README.MD` | Estado del bloque completado y enlaces |

Se reutilizaron sin modificar `SyncService`, `LoyverseAPIClient`,
`fetch_catalog()`, los modelos y los auxiliares de comparación del procedimiento
de idempotencia. El script no publica una interfaz nueva y bloquea otra ejecución
si el informe indica que su escritura ya se inició o confirmó.

## 4. Cuatro fragmentos importantes del código real

### a. Confirmar el precio remoto: `hu008_block4_price_update_run.py`

```python
remote_target = next(row for row in plan.variants if row.loyverse_id == TARGET_ID)
require(remote_target.price == NEW_PRICE, "La tienda no devuelve 129.00 para Doble.")
```

Se localiza la variante por identidad externa, no únicamente por un nombre que
podría repetirse. El precio viene de la lectura real de la tienda seleccionada;
la escritura no continúa si falta o es distinto de 129.00.

### b. Cambiar la fila existente: `app/services/sync_service.py`

```python
options = {
    "index_elements": [table.c.loyverse_id],
    "set_": {field: statement.excluded[field] for field in fields},
}
```

El UPSERT identifica el conflicto por `loyverse_id`. En variantes, los campos
actualizables son nombre, precio y fecha. La PK no se reemplaza y el padre no
se reasigna. Los campos locales quedan fuera de esa lista de actualización.

### c. Detectar efectos adicionales: `hu008_block4_price_update_run.py`

```python
require(expected == business_rows(after),
        "Se alteró otro precio, identidad, relación o campo local.")
```

`expected` es una copia de las filas previas con un solo cambio autorizado en
Doble. `business_rows()` excluye únicamente `synced_at`. Por ello cambiar cualquier
otra columna de negocio hace fallar la comparación. Se comprueba también que
la fecha guardada corresponda a la nueva lectura.

### d. Verificar la identidad en la prueba: `tests/test_catalog_sync_postgresql.py`

```python
assert actual_double["price"] == Decimal("129.00")
assert actual_double["product_variant_id"] == old_double["product_variant_id"]
assert actual_double["is_available"] is False
```

La prueba establece previamente la disponibilidad local en falso. Después
comprueba el precio nuevo, la misma PK y la conservación de esa decisión local.
Los IDs de las pruebas aisladas se obtienen de sus registros; el ID real 5 se
comprueba explícitamente en el procedimiento y en el SQL real.

## 5. Cómo comprobarlo en pgAdmin

Conéctate a **mitbol_dev**, abre Query Tool y ejecuta
[hu008_block4_price_update_verify.sql](hu008_block4_price_update_verify.sql).
Todo el archivo es de sólo lectura. La primera consulta verifica la base y
después muestra Doble, compara las cinco variantes y los cinco complementos
con sus identidades, padres y precios esperados, y busca variantes duplicadas.

Una comprobación breve:

```sql
SELECT current_database();

SELECT v.product_variant_id, v.loyverse_id, v.name AS variante,
       v.price, p.product_id, p.name AS producto
FROM public."ProductVariants" v
JOIN public."Products" p ON p.product_id = v.product_id
WHERE v.loyverse_id = '246aad80-4540-4c1a-8877-2e31bc03d668';
```

Debe devolver exactamente una fila: variante **5**, producto **3**, nombre
**Doble**, producto **Hamburguesa clásica** y precio **129.00**.

En las matrices del archivo, `correcto` debe ser `true` en las cinco variantes
y los cinco complementos. El `FULL JOIN` permite detectar también registros
faltantes o adicionales. La consulta de duplicados debe devolver cero filas.

También se ejecutó el SQL general del Bloque 3 para comprobar duplicados de
las cinco tablas, cuatro clases de referencias huérfanas y precios inválidos.
Los resultados están en el informe SQL de esta segunda parte. La evidencia
histórica de qué había antes se conserva en los JSON, sin depender de una
consulta al estado actual.

## 6. Pruebas ejecutadas y resultados

Antes de la escritura real:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Resultado: **125 passed in 19.16s**, sin fallos ni omisiones.

La nueva prueba `test_only_double_price_changes_from_119_to_129`:

- Inserta el catálogo simulado en otra base PostgreSQL.
- Establece descripción, disponibilidad, destacado y novedad locales con
  valores personalizados, además de deshabilitar localmente Doble.
- Cambia únicamente el precio de tienda de Doble a `Decimal("129.00")`.
  Su precio predeterminado permanece en 119, comprobando la procedencia del precio.
- Verifica misma PK, disponibilidad local preservada y comparación completa de
  todas las filas, admitiendo sólo ese cambio de precio y las fechas nuevas.

Después de la carga real:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_catalog_sync_postgresql.py::test_only_double_price_changes_from_119_to_129 tests/test_postgresql_schema.py -q -p no:cacheprovider
```

Resultado: **7 passed in 1.99s**: el caso nuevo aislado y seis comprobaciones
de lectura del esquema real. Las pruebas de persistencia no escriben en
`mitbol_dev` ni contactan a la cuenta real de Loyverse. Las bases aisladas se
conservaron con cero filas comerciales residuales.

El SQL directo confirmó cero duplicados, referencias huérfanas y precios
inválidos. Las diez comparaciones de variantes y complementos dieron `correcto=true`.
También se comprobó sintaxis Python y `git diff --check` sin errores.

## 7. Cinco preguntas posibles del profesor

1. **¿Cómo sabes que actualizaste Doble y no creaste otra variante?** El UUID
   externo coincidió en el UPSERT, la PK siguió siendo 5 y las cinco filas se
   compararon completas antes y después.
2. **¿Por qué usaste el precio de tienda?** La operación está autorizada para
   I.C.E y el modelo guarda un único precio por variante. No se usa el precio
   de otra tienda ni un fallback que oculte una configuración desconocida.
3. **¿Qué garantiza que no cambiaron los demás precios?** Se compararon todas
   las columnas de negocio permitiendo sólo el importe de Doble, y después se
   verificaron los diez precios e identidades mediante SQL.
4. **¿Para qué sirven Decimal y NUMERIC?** Conservan importes decimales sin
   introducir la representación binaria de `float` en el recorrido del dinero.
5. **¿Qué habría pasado si la verificación previa al commit fallaba?** Una
   excepción habría revertido toda la transacción, conservando el catálogo previo.

Recuerda: el precio viene del catálogo remoto validado, la identidad externa
resuelve el UPSERT, las FK internas conservan relaciones y la transacción hace
atómica la escritura. El Bloque 4 queda verificado dentro de este alcance;
no se avanzó a otras historias ni se activó ejecución periódica.
