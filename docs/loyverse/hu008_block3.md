# HU-008 — Bloque 3: primera sincronización real

## 1. Qué operaciones ejecutamos

Con autorización explícita, se confirmó una única carga real del catálogo en
`mitbol_dev`. Antes se comprobó la configuración efectiva frente a `.env`, la base
real mediante `current_database()`, sus tablas y la revisión `20261029_0001`.
No se cambió `.env`, el esquema ni las migraciones. No se conectó a `mitbol_db`.

| Tabla | Antes | Después | Filas actualizadas o retiradas |
| --- | ---: | ---: | ---: |
| `Products` | 0 | 3 | 0 |
| `ProductVariants` | 0 | 5 | 0 |
| `ProductModifiers` | 0 | 2 | 0 |
| `ProductComplements` | 0 | 5 | 0 |
| `ProductModifierAssignments` | 0 | 4 | 0 |

La descarga utilizada se obtuvo el `2026-11-02T05:57:03.938026+00:00`
(`2026-11-01 23:57:03.938026-06:00`). El informe registra por separado la fecha
de confirmación: `synced_at` corresponde al momento de terminar la lectura,
no al commit. PostgreSQL puede mostrar otra zona sin cambiar el instante.

La API confirmó la tienda **I.C.E**, ID
`9e30730f-a5b4-4485-8cc4-bc499cc6e239`. Los identificadores activos y las
asociaciones coincidieron exactamente con los artículos de prueba del Bloque 1.
Ese informe anterior se utilizó únicamente para delimitar el alcance autorizado;
los nombres y precios importados provienen de una descarga nueva.

Se mostró el resumen antes de ejecutar la única llamada real a `_write()`.
No se eliminó ninguna fila. Las instrucciones de limpieza de asociaciones
no encontraron filas obsoletas. No se añadieron endpoints, ejecución periódica,
pedidos ni funcionalidades de HU-009 o HU-010.

### Dos incidencias de preparación, sin escritura

1. La consulta inicial falló por conexión dentro del entorno restringido. Se
   repitió con acceso de red autorizado; aún no se había ejecutado ningún DML.
2. `show_deleted=true` reveló dos artículos dados de baja, algo que el Bloque 1
   no había solicitado. Una guarda conservadora detuvo esa preparación. Se
   comprobó después que ambas identidades eran ajenas al catálogo activo y
   estaban ausentes del destino vacío. Sus bajas no crearon ni modificaron filas.

Las dos identidades de baja figuran en el informe. No constituyen una
incompatibilidad estructural ni provocaron pérdida de datos. Después de revisar
su alcance, se preparó la descarga definitiva y se ejecutó **una sola transacción
con escritura confirmada**. No se repitió la sincronización real.

## 2. Cómo viajaron los datos

1. El script operativo crea motores SQLAlchemy para `mitbol_dev`: uno de trabajo
   y otro configurado con `default_transaction_read_only=on` para inspección.
2. `SyncService._transaction()` abre la sesión, verifica la base y adquiere
   el bloqueo transaccional compartido por las sincronizaciones del servicio.
3. `fetch_catalog()` consulta `/stores`, selecciona el ID configurado y obtiene
   `/items` y `/modifiers` con `show_deleted=true`. La descarga utilizada tuvo
   cinco GET: una página de tiendas, tres de artículos y una de modificadores.
   El límite de dos registros por página permitió verificar la paginación real.
4. HTTPX recibe el JSON. El cliente usa `parse_float=Decimal`, de modo que un
   importe no pasa antes por `float`. Los enteros también se convierten a Decimal
   al preparar el plan.
5. `_plan()` y `transform_catalog()` validan las referencias, separan bajas y
   seleccionan `variants[].stores[].price` para la tienda I.C.E, con precio
   `FIXED`. Los complementos toman `modifier_options[].price`; sus grupos
   deben pertenecer a la tienda seleccionada. No hubo importes desconocidos.
6. `_write()` obtiene los IDs internos mediante UPSERT y `RETURNING`; inserta
   primero productos y grupos, después variantes y complementos y finalmente
   las cuatro parejas variante–grupo.
7. `verify_rows()` compara las filas y relaciones con el plan dentro de la
   transacción. Si hubiera diferencias, una excepción impediría confirmar.
8. Al salir de `session.begin()` se confirma. Otra conexión de sólo lectura
   verifica nuevamente los datos ya confirmados.

FastAPI no participa como servidor HTTP en esta operación: no se invocó ni se
publicó una ruta de escritura. Se reutilizó directamente el servicio del backend.
Alembic no volvió a migrar `mitbol_dev`; su revisión se comprobó mediante lectura.
La suite sí prepara otra base aislada con la revisión existente para sus pruebas.

### Precios y relaciones comprobados

| Producto | Variante | Precio | Grupo asignado |
| --- | --- | ---: | --- |
| Papas fritas | Papas fritas | 45.00 | Ninguno |
| Limonada natural | 355 ml | 35.00 | Extras bebida |
| Limonada natural | 500 ml | 49.00 | Extras bebida |
| Hamburguesa clásica | Sencilla | 89.00 | Extras hamburguesa |
| Hamburguesa clásica | Doble | 119.00 | Extras hamburguesa |

| Grupo | Complementos y precios |
| --- | --- |
| Extras bebida | Chía 5.00; Hierbabuena 5.00 |
| Extras hamburguesa | Queso extra 15.00; Tocino 20.00; Sin cebolla 0.00 |

`Sin cebolla` es un cero explícito de Loyverse, no un reemplazo de un precio nulo.
No se inventó una moneda: las cinco tablas no almacenan código de moneda.
Los grupos del artículo se asociaron a cada una de sus dos variantes; no se
agregaron reglas de selección. Papas fritas quedó sin asignaciones.

Los productos nuevos tienen los defaults aprobados: `description=NULL`,
`is_available=true`, `is_active=true`, `is_featured=false`, `is_new=true`.
Las cinco variantes estaban disponibles en la tienda y los complementos activos.
No había enriquecimiento local previo; su conservación en actualizaciones fue
comprobada con datos simulados en PostgreSQL aislado. En este bloque no se
crearon ni modificaron tablas de fotografías o etiquetas.

## 3. Archivos y funciones

Archivos creados o modificados en este bloque:

| Archivo | Responsabilidad |
| --- | --- |
| `docs/loyverse/hu008_block3_run.py` | Script operativo de esta ejecución única: inspección, descarga, alcance autorizado, pausa previa, una escritura y verificación |
| `docs/loyverse/hu008_block3_report.json` | Evidencia: estado inicial, plan, avisos, resultado del servicio y filas antes y después del commit |
| `docs/loyverse/hu008_block3_verify.sql` | Consultas de sólo lectura para verificar personalmente |
| `docs/loyverse/hu008_block3_sql_results.json` | Resultados reales de ejecutar esas consultas |
| `docs/loyverse/hu008_block3.md` | Esta explicación |
| `docs/loyverse/hu008_block2_test_database.json` | Actualizado por la nueva ejecución de la suite: base aislada y desarrollo sin cambios durante las pruebas |
| `app/services/sync_service.py` | Sólo actualiza una frase del docstring que antes describía el uso exclusivo con datos simulados; no cambia la persistencia |
| `README.MD` | Documenta la carga confirmada y enlaza la verificación |

Se reutilizaron sin cambiar su comportamiento `LoyverseAPIClient`,
`fetch_catalog()`, `transform_catalog()` y los cinco modelos SQLAlchemy.

El script operativo utiliza `_transaction()`, `_plan()` y `_write()`, la misma
secuencia interna de `synchronize()`, para poder publicar el plan antes del DML
sin volver a descargar datos. No duplica los UPSERT. Es un procedimiento de este
bloque, no una nueva interfaz pública del backend.

El script rechaza otra ejecución si el informe ya registra el commit o si las
tablas no están vacías. No debe utilizarse para la futura repetición del Bloque 4.

## 4. Cuatro fragmentos del código real

### a. Descargar la lectura completa: `hu008_block3_run.py`

```python
snapshot = await fetch_catalog(client, settings.loyverse_store_id, include_deleted=True)
```

`await` espera la lectura remota. La función sólo devuelve el snapshot cuando
han terminado las páginas de artículos y modificadores. La tienda se selecciona
por el ID configurado; después se comprueba además su nombre I.C.E.

### b. Verificar el destino: `app/services/sync_service.py`

```python
actual_database = session.scalar(select(func.current_database()))
if actual_database != self.expected_database:
    raise CatalogTargetError("La conexión real no coincide con la base autorizada.")
```

No basta con leer un nombre en `.env`: PostgreSQL informa la base efectiva.
`scalar()` obtiene el único valor de la consulta. Una diferencia detiene el
servicio antes de cualquier UPSERT.

### c. Recuperar la identidad interna: `app/services/sync_service.py`

```python
statement = statement.on_conflict_do_update(**options)
primary_key = next(iter(table.primary_key.columns))
identifier = session.execute(statement.returning(primary_key)).scalar_one_or_none()
```

El conflicto se resuelve por `loyverse_id` y sólo actualiza los campos permitidos.
`RETURNING` obtiene la PK interna tanto al insertar como al actualizar. Esa PK,
no el UUID externo, se utiliza en las claves foráneas del modelo actual.

### d. Una escritura y una comprobación antes del commit: `hu008_block3_run.py`

```python
report["service_result"] = service._write(session, plan)
report["verified_before_commit"] = verify_rows(session, plan)
```

Estas dos líneas están dentro de `with service._transaction()`. La primera
ejecuta el plan; la segunda consulta las filas y comprueba nombres, importes,
IDs, relaciones y duplicados. Si falla la comprobación, todo se revierte.
Sólo después de salir correctamente del contexto se guarda `real_sync_commits=1`.

## 5. Consultas SQL que puedes ejecutar

Abre pgAdmin o psql conectado a **mitbol_dev** y ejecuta
[hu008_block3_verify.sql](hu008_block3_verify.sql). El archivo abre una transacción
de sólo lectura y termina con `ROLLBACK`; eso cierra la transacción de consulta,
no deshace la carga ya confirmada en otra conexión.

Para una primera comprobación:

```sql
SELECT current_database();

SELECT p.name AS producto, v.name AS variante, v.price, v.loyverse_id
FROM public."Products" p
JOIN public."ProductVariants" v ON v.product_id = p.product_id
ORDER BY p.product_id, v.product_variant_id;
```

Los nombres con mayúsculas necesitan comillas dobles en PostgreSQL.
El archivo completo añade conteos de las cinco tablas, complementos por grupo,
asignaciones, duplicados, cuatro comprobaciones de referencias huérfanas y
precios inválidos. Se ejecutó realmente y sus resultados se guardaron en
[hu008_block3_sql_results.json](hu008_block3_sql_results.json).

Resultado: cinco filas de variantes, cinco de complementos, cuatro de
asignaciones y **cero** duplicados, referencias inválidas y precios inválidos.
El informe principal contiene además la comparación exacta con el plan: no
depende únicamente de que una FK apunte a cualquier fila existente.

## 6. Pruebas ejecutadas

Antes de la escritura real:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Resultado: **123 passed in 24.64s**, sin fallos ni omisiones. Las pruebas de
persistencia escribieron exclusivamente en una nueva base de pruebas aislada.
El informe del Bloque 2 fue actualizado por la fixture y verifica cero filas
comerciales residuales y ninguna modificación de `mitbol_dev` durante la suite.

Después de la carga real se repitió únicamente la verificación de esquema:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_postgresql_schema.py -q -p no:cacheprovider
```

Resultado: **6 passed in 0.71s**. Son consultas de sólo lectura que comparan
tablas, revisión, columnas, tipos, defaults y restricciones con los modelos.
También se ejecutaron el SQL de verificación y `git diff --check` correctamente.

No se ha probado todavía una segunda carga real ni el cambio real de un precio.
La ausencia de duplicados de esta primera carga sí se verificó por SQL. Las
repeticiones y cambios de precios de la suite utilizan sólo datos simulados.

## 7. Cinco preguntas posibles del profesor

1. **¿Cómo sabes que no escribiste en otra base?** Comparamos la URL efectiva
   con `.env`, exigimos `mitbol_dev` y consultamos `current_database()` dentro
   de la transacción antes de escribir.
2. **¿Por qué no usaste el precio del informe anterior?** Porque es una lectura
   histórica. Se descargó y validó el precio fijo actual de la tienda autorizada.
3. **¿Cómo pasaste de los UUID de Loyverse a las relaciones locales?** El UPSERT
   identifica por UUID externo y `RETURNING` devuelve las PK enteras usadas por
   las claves foráneas de variantes, complementos y asignaciones.
4. **¿Qué pasó con los artículos eliminados?** Se reconocieron dos bajas que
   nunca existieron en esta base. No se fabricaron filas ni se modificó información
   histórica; las operaciones encontraron cero registros afectados.
5. **¿Qué diferencia hay entre validar antes y verificar después?** Antes se
   comprueba que el catálogo puede representarse y que su alcance está autorizado.
   Después se consulta PostgreSQL para comprobar lo realmente guardado y confirmado.

Debes recordar la diferencia entre lectura remota, plan en memoria, DML,
transacción, commit y verificación desde otra conexión. El registro de esta carga
sirve como punto de partida del Bloque 4; en este bloque no se repitió la escritura.
