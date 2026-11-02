# HU-003 — Consultar detalles de productos

Fecha de verificación: **2 de noviembre de 2026**.

HU-003 incorpora `GET /api/menu/{product_id}`, un endpoint público que consulta
PostgreSQL y devuelve un producto con sus variantes, grupos de modificadores y
complementos. Conserva las reglas comerciales de HU-001 y utiliza las relaciones
persistidas por HU-008.

La suite completa terminó con **223 pruebas aprobadas, sin fallos ni omisiones**:
las 189 anteriores y 34 casos nuevos de HU-003. Las lecturas reales comprobaron
los productos 1, 2 y 3 y la conservación de datos, esquema y migraciones.

## 1. Qué implementamos

- Consulta pública por el identificador interno `product_id`, entero mayor que cero.
- Nombre, descripción local y disponibilidad del producto; `image_url` siempre `null`.
- Variantes con su identificador, nombre, precio y disponibilidad efectiva.
- Grupos dentro de cada variante, exclusivamente mediante sus asignaciones reales.
- Complementos con identificador, nombre, precio adicional, orden y activación.
- Complementos inactivos visibles con `is_active: false` y `can_order: false`.
- Precios exactos representados como cadenas decimales, con al menos dos decimales.
- Orden estable y carga anticipada por lotes para evitar N+1.
- Errores HTTP documentados y mensajes sin SQL ni credenciales.

El alcance es consultar información comercial. No se agregaron tablas, columnas,
migraciones, fotografías, frontend, carrito ni cálculo de totales de pedidos.
Consultar el endpoint no inicia una sincronización con Loyverse.

## 2. Arquitectura y recorrido de la consulta

```text
GET /api/menu/{product_id}
  → app/api/menu.py: valida el ID y obtiene la sesión mediante get_db
  → app/services/menu_service.py: consulta el producto activo y carga sus relaciones
  → _build_product: aplica las reglas existentes de HU-001
  → _build_product_detail: incorpora los grupos de cada variante
  → _build_modifier: incorpora opciones y calcula su can_order contextual
  → app/schemas/menu.py: valida y serializa la respuesta pública
```

El recorrido relacional es:

```text
Products.product_id
  → ProductVariants.product_id
    → ProductModifierAssignments.product_variant_id
      → ProductModifiers.product_modifier_id
        → ProductComplements.product_modifier_id
```

Paso a paso:

1. FastAPI valida `product_id` como entero positivo. Un ID inválido produce `422`
   antes de consultar el catálogo.
2. La dependencia existente `get_db` entrega una sesión SQLAlchemy síncrona y la
   cierra al finalizar la petición.
3. El servicio busca directamente por `product_id` e `is_active=true`. No obtiene
   todo el menú para filtrarlo en Python.
4. `selectinload` carga variantes y asignaciones por lotes. `joinedload` incorpora
   el grupo de cada asignación en la misma consulta de asignaciones. Otro
   `selectinload` obtiene los complementos de esos grupos.
5. Si no encuentra un producto publicable, lanza `MenuProductNotFoundError` y el
   router responde `404`.
6. `_build_product` construye los campos comerciales comunes usando exactamente
   las reglas de HU-001. El detalle los amplía sin recalcular esas reglas.
7. Cada variante recorre sus propias asignaciones. Un grupo compartido conserva
   su identidad, pero su `can_order` de complementos se calcula para cada variante.
8. Los esquemas públicos conservan `Decimal` hasta la serialización JSON y sólo
   exponen los campos del contrato.

Para los tamaños verificados, un árbol con grupos requiere cuatro `SELECT`:
producto, variantes, asignaciones con grupos y complementos. Un producto sin
grupos requiere tres; uno sin variantes, dos; uno inexistente o inactivo, uno.
Con conjuntos grandes, SQLAlchemy puede dividir las consultas en más lotes. La
garantía es evitar consultas por cada entidad, no prometer cuatro consultas para
cualquier tamaño posible.

## 3. Archivos creados o modificados

Las rutas siguientes son relativas a este README.

| Archivo | Responsabilidad y cambio |
| --- | --- |
| [app/api/menu.py](../../../app/api/menu.py) | Ruta de detalle, ID positivo, respuestas y documentación Swagger. |
| [app/services/menu_service.py](../../../app/services/menu_service.py) | Consulta de detalle y construcción del árbol; reutiliza `_build_product`. |
| [app/schemas/menu.py](../../../app/schemas/menu.py) | `MenuComplement`, `MenuModifier`, `MenuVariantDetail`, `MenuProductDetail` y `MenuProductDetailResponse`. |
| [app/main.py](../../../app/main.py) | Extiende el ajuste de ejemplos OpenAPI para conservar `null` también en el detalle. |
| [tests/test_menu.py](../../../tests/test_menu.py) | Añade 34 casos parametrizados de HU-003 utilizando las fixtures existentes. |
| [Este README](README.md) | Único documento de aprendizaje, contrato y resultados de HU-003. |

Los modelos y la sincronización de HU-008 no se modificaron. Las pruebas
anteriores de HU-001 conservan su contrato y comportamiento.

## 4. Contrato para frontend

**Método y ruta:** `GET /api/menu/{product_id}`, sin autenticación ni cuerpo.

El equipo React obtiene el ID en `GET /api/menu`, dentro de
`products[].product_id`, y lo utiliza para consultar el detalle. Por ejemplo,
`GET /api/menu/3` consulta Hamburguesa clásica en la base verificada.

La respuesta tiene `currency: "MXN"` y un objeto singular `product`. El ejemplo
siguiente es ilustrativo del contrato; muestra también una opción inactiva y
no representa una modificación ni una lectura del catálogo real:

```json
{
  "currency": "MXN",
  "product": {
    "product_id": 42,
    "name": "Hamburguesa clásica",
    "description": null,
    "image_url": null,
    "is_active": true,
    "is_available": true,
    "can_order": true,
    "price_min": "89.00",
    "price_max": "89.00",
    "has_price_range": false,
    "variants": [
      {
        "product_variant_id": 101,
        "name": "Sencilla",
        "price": "89.00",
        "is_available": true,
        "can_order": true,
        "modifiers": [
          {
            "product_modifier_id": 7,
            "name": "Extras",
            "display_order": 1,
            "complements": [
              {
                "product_complement_id": 20,
                "name": "Sin cebolla",
                "price": "0.00",
                "display_order": 1,
                "is_active": true,
                "can_order": true
              },
              {
                "product_complement_id": 21,
                "name": "Queso",
                "price": "15.00",
                "display_order": 2,
                "is_active": true,
                "can_order": true
              },
              {
                "product_complement_id": 22,
                "name": "Tocino",
                "price": "20.00",
                "display_order": 3,
                "is_active": false,
                "can_order": false
              }
            ]
          }
        ]
      }
    ]
  }
}
```

### Recorrido de los datos

| Ruta JSON | Uso |
| --- | --- |
| `product` | Información general y disponibilidad del producto. |
| `product.variants[]` | Presentaciones y sus precios base. |
| `variant.modifiers[]` | Grupos asignados a esa variante concreta. |
| `modifier.complements[]` | Opciones del grupo y sus importes adicionales. |
| `product_id`, `product_variant_id`, `product_modifier_id`, `product_complement_id` | Identificadores internos; no usar el nombre ni el índice como identidad. |

Aunque dos variantes contengan el mismo grupo, frontend debe leerlo dentro de
la variante seleccionada. El estado efectivo de sus opciones puede ser distinto.
No se debe construir una unión de todos los grupos del producto.

### Precios, disponibilidad y orden

| Campo o situación | Interpretación |
| --- | --- |
| `currency` | `MXN`; no hay conversiones. |
| `variant.price` | Precio base exacto de esa presentación. |
| `complement.price` | Importe adicional de esa opción; `"0.00"` es válido. |
| `price_min`, `price_max` | Extremos de variantes disponibles; si ninguna lo está, referencia de todas. No incluyen complementos. |
| `has_price_range` | Indica si los extremos difieren. |
| `product.is_active` | Activación comercial. Los productos inactivos no se publican. |
| `product.is_available`, `variant.is_available` | Estado almacenado, que se conserva aunque el padre impida pedir. |
| `product.can_order` | Producto activo y disponible con al menos una variante disponible. |
| `variant.can_order` | Producto activo y disponible y variante disponible. |
| `complement.is_active` | Activación comercial de la opción; las inactivas siguen visibles. |
| `complement.can_order` | Variante con `can_order=true` y complemento activo. |
| Sin variantes | `variants: []`, extremos `null`, `has_price_range=false`, `can_order=false`. |
| Sin grupos u opciones | `modifiers: []` o `complements: []`; un grupo asignado vacío se conserva. |
| Orden | Variantes por ID; grupos y opciones por `display_order` e ID como desempate. |

Los importes llegan como cadenas, sin símbolo monetario y sin exponentes. Se
conservan decimales adicionales si existen: por ejemplo,
`"90.1234567890123456789"`. No debe asumirse que siempre hay exactamente dos
decimales ni convertirlos a `Number` para cálculos monetarios exactos. Esta
historia no calcula totales. `can_order` comunica el estado comercial observado;
no reserva existencias ni representa una operación de selección o de pedido.

### Errores que debe manejar React

| HTTP | Significado | Respuesta |
| --- | --- | --- |
| `200` | Producto encontrado, incluso agotado o sin variantes. | `currency` y `product`. |
| `404` | Inexistente o comercialmente inactivo. | `{"detail": "Producto no encontrado."}` |
| `422` | ID no entero o menor que uno. | Error estándar de FastAPI, con `detail` como lista. |
| `500` | Error interno de consulta o integridad del catálogo. | `{"detail": "No fue posible consultar el detalle del producto."}` |
| `503` | Fallo de conexión o tiempo de espera agotado. | `{"detail": "El detalle del producto no está disponible temporalmente."}` |

Frontend debe contemplar que `detail` es una cadena en los errores propios del
endpoint y una lista en los errores de validación. No se publican UUID de
Loyverse, fechas de sincronización ni detalles de infraestructura.

## 5. Ocho fragmentos reales explicados línea por línea

Los números de las tablas cuentan las líneas dentro de cada fragmento. Todos
proceden de los archivos de implementación o pruebas; algunos son extractos
de funciones mayores y no programas independientes.

### Fragmento 1: parámetro de ruta y dependencia

Origen: `app/api/menu.py`, función `get_product_detail`.

```python
def get_product_detail(
    product_id: Annotated[int, Path(ge=1, description="ID interno del producto, entero mayor que cero.")],
    db: Session = Depends(get_db),
) -> MenuProductDetailResponse:
```

| Línea | Explicación |
| --- | --- |
| 1 | Declara el controlador síncrono de la ruta. |
| 2 | Exige un entero mayor o igual que uno y documenta el parámetro. FastAPI genera `422` si no cumple. |
| 3 | Obtiene la sesión mediante la dependencia existente, sustituible en pruebas. |
| 4 | Declara el esquema de respuesta del detalle. El decorador también lo registra en OpenAPI. |

### Fragmento 2: consulta y carga anticipada del árbol

Origen: `app/services/menu_service.py`, función `get_product_detail`.

```python
    statement = (
        select(Product)
        .where(Product.product_id == product_id, Product.is_active.is_(True))
        .options(
            selectinload(Product.variants)
            .selectinload(ProductVariant.modifier_assignments)
            .joinedload(ProductModifierAssignment.modifier)
            .selectinload(ProductModifier.complements)
        )
    )
```

| Línea | Explicación |
| --- | --- |
| 1 | Empieza la construcción de una sentencia; aún no la ejecuta. |
| 2 | Selecciona el modelo de producto. |
| 3 | Acota por ID y activación comercial en la propia base. |
| 4 | Configura cómo cargar sus relaciones. |
| 5 | Obtiene sus variantes por lotes. |
| 6 | Obtiene las asignaciones de esas variantes por lotes. |
| 7 | Une el grupo en cada asignación; es una relación muchos a uno, no una colección adicional. |
| 8 | Carga las opciones de los grupos obtenidos. |
| 9 | Cierra las opciones de carga. |
| 10 | Termina la sentencia, que después ejecuta `db.scalars(statement).one_or_none()`. |

### Fragmento 3: reutilizar HU-001 sin copiar sus reglas

Origen: `app/services/menu_service.py`, inicio de `_build_product_detail`.

```python
def _build_product_detail(product: Product) -> MenuProductDetail:
    # HU-001 sigue siendo la única fuente de reglas para precios y disponibilidad.
    base_product = _build_product(product)
    variants_by_id = {variant.product_variant_id: variant for variant in product.variants}
    variants = []
```

| Línea | Explicación |
| --- | --- |
| 1 | Recibe el modelo ORM cargado y devolverá el esquema público de detalle. |
| 2 | Explica por qué se conserva la función existente como fuente común. |
| 3 | Obtiene precios extremos, estados y variantes mediante las reglas ya probadas de HU-001. |
| 4 | Crea un mapa por ID para recuperar las asignaciones ORM de cada variante pública sin buscar por nombre. |
| 5 | Prepara la colección de variantes enriquecidas. |

El resto de la función añade los grupos a cada variante. `MenuVariantDetail`
hereda de `MenuVariant` y `MenuProductDetail` de `MenuProduct`; el esquema de
listado sigue utilizando sus clases originales.

### Fragmento 4: conservar Decimal al ampliar los esquemas

Origen: `app/services/menu_service.py`, final de `_build_product_detail`.

```python
        variants.append(MenuVariantDetail(
            **base_variant.model_dump(mode="python"), modifiers=modifiers,
        ))
    # El modo Python conserva Decimal hasta la serialización final de la respuesta.
    return MenuProductDetail(
        **base_product.model_dump(mode="python", exclude={"variants"}), variants=variants,
    )
```

| Línea | Explicación |
| --- | --- |
| 1 | Agrega el esquema ampliado de la variante que se está recorriendo. |
| 2 | Conserva los campos de HU-001 como objetos Python y añade sólo sus grupos. |
| 3 | Cierra la construcción y la inserción en la lista. |
| 4 | Documenta que los importes aún no se convierten a cadenas JSON. |
| 5 | Construye el detalle final del producto. |
| 6 | Conserva sus campos comunes, excluye la lista base y la sustituye por las variantes ampliadas. |
| 7 | Termina el retorno del esquema validado. |

### Fragmento 5: disponibilidad contextual de complementos

Origen: `app/services/menu_service.py`, función `_build_modifier`.

```python
            MenuComplement(
                product_complement_id=complement.product_complement_id,
                name=complement.name,
                price=complement.price,
                display_order=complement.display_order,
                is_active=complement.is_active,
                can_order=variant_can_order and complement.is_active,
            )
```

| Línea | Explicación |
| --- | --- |
| 1 | Construye el contrato público de una opción del grupo. |
| 2 | Conserva el identificador interno de la opción. |
| 3 | Conserva el nombre almacenado. |
| 4 | Usa el precio adicional como `Decimal`, sin sumarlo ni redondearlo. |
| 5 | Expone su orden comercial. |
| 6 | Conserva la activación comercial, incluso si es falsa. |
| 7 | Sólo habilita la opción si su variante puede pedirse y la opción está activa. |
| 8 | Cierra el esquema. |

No se filtran las opciones inactivas. Tampoco se inventa `is_available` para
complementos ni un estado de activación para grupos.

### Fragmento 6: serialización monetaria compartida con HU-001

Origen: `app/schemas/menu.py`, función existente `_serialize_amount`.

```python
def _serialize_amount(value: Decimal) -> str:
    """Sin exponentes ni redondeo, con al menos dos decimales."""
    if value.is_zero():
        return "0.00"
    whole, _, fraction = format(value, "f").partition(".")
    return f"{whole}.{fraction.rstrip('0').ljust(2, '0')}"
```

| Línea | Explicación |
| --- | --- |
| 1 | Recibe un importe decimal y devuelve su representación textual. |
| 2 | Declara la garantía de formato y conservación del valor. |
| 3 | Reconoce cero, incluido el cero negativo. |
| 4 | Publica una única representación de cero. |
| 5 | Evita notación exponencial y separa parte entera y fraccionaria. |
| 6 | Quita ceros finales innecesarios y garantiza al menos dos cifras sin redondear. |

`MenuAmount` vincula este serializador a `Decimal` mediante `PlainSerializer`.
Su validación exige `Decimal`, valor no negativo y finito; `WithJsonSchema`
documenta el resultado como cadena. Reutilizar este tipo evita una política
monetaria distinta para los complementos.

### Fragmento 7: distinguir 404 de los errores internos

Origen: `app/api/menu.py`, función `get_product_detail`.

```python
    except menu_service.MenuProductNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado.",
        ) from None
```

| Línea | Explicación |
| --- | --- |
| 1 | Captura la ausencia comercial de un producto antes de la captura general. |
| 2 | Traduce el resultado del servicio a una excepción HTTP. |
| 3 | Usa `404` y el mismo mensaje para inexistentes e inactivos. |
| 4 | Evita encadenar la excepción de dominio en esta respuesta HTTP. |

`MenuUnavailableError` se traduce a `503`; el resto de fallos internos, a `500`.
El servicio distingue errores SQLAlchemy de conexión y errores de consulta o
validación. Los mensajes públicos son fijos y los registros sólo incluyen el
nombre del tipo de error, sin su texto ni trazas con SQL o credenciales.

### Fragmento 8: comprobar consultas reales y ausencia de escrituras

Origen: `tests/test_menu.py`, `test_detail_loads_full_tree_without_n_plus_one`.

```python
    # Los SAVEPOINT de la fixture aíslan las pruebas; no son consultas de catálogo.
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 4
    assert '"Products"' in selects[0] and '"ProductVariants"' in selects[1]
    assert '"ProductModifierAssignments"' in selects[2] and 'JOIN "ProductModifiers"' in selects[2]
    assert '"ProductComplements"' in selects[3]
    assert not any(sql.lstrip().upper().startswith(
        ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"),
    ) for sql in statements)
```

| Línea | Explicación |
| --- | --- |
| 1 | Distingue el aislamiento transaccional de las lecturas del catálogo. |
| 2 | Selecciona únicamente las sentencias de lectura capturadas por el listener SQLAlchemy. |
| 3 | Exige cuatro lecturas tanto con una como con 25 variantes y grupos distintos. |
| 4 | Comprueba las dos primeras etapas: producto y variantes. |
| 5 | Verifica que grupos y asignaciones llegan juntos. |
| 6 | Comprueba la carga final de opciones. |
| 7 | Inicia la comprobación de ausencia de sentencias de escritura. |
| 8 | Enumera operaciones que la consulta HTTP no debe emitir. |
| 9 | Aplica la comprobación a todas las sentencias capturadas durante la petición. |

## 6. Conceptos técnicos y decisiones

| Concepto o decisión | Motivo |
| --- | --- |
| Separación router, servicio y esquemas | HTTP, consulta comercial y contrato tienen responsabilidades distintas. |
| Endpoint síncrono | Reutiliza las sesiones síncronas existentes; no añade otra arquitectura de acceso. |
| Herencia de esquemas de HU-001 | Conserva campos y validaciones, ampliando sólo el detalle. |
| Reutilización de `_build_product` | Evita fórmulas duplicadas de precios y disponibilidad. |
| `Numeric` → `Decimal` → cadena JSON | Mantiene exactitud desde PostgreSQL hasta el consumidor. |
| `selectinload` en colecciones | Obtiene relaciones por lotes sin una consulta por entidad. |
| `joinedload` en asignación → grupo | Es una relación muchos a uno y evita una etapa separada de lectura. |
| Grupos anidados por variante | Respeta las asociaciones existentes en lugar de reconstruir las de Loyverse. |
| Complementos inactivos visibles | Permite representar su estado sin habilitarlos; decisión aprobada del contrato. |
| `can_order` contextual | El mismo complemento puede estar habilitado en una variante y deshabilitado en otra. |
| Orden con desempate por ID | Estabilidad incluso si dos registros comparten `display_order`. |
| Mensajes y registros limitados | Evita filtrar infraestructura al cliente o a los registros de la ruta. |
| Ejemplos OpenAPI con `null` | Amplía el ajuste ya existente de HU-001 para que los ejemplos sean válidos. |

## 7. Pruebas automatizadas y resultados

### Aislamiento de las escrituras

Todas las pruebas nuevas de HU-003 que escriben registros utilizan PostgreSQL
aislado. Se parametriza `menu_db` con `postgresql`, reutilizando la fixture de
HU-008 que crea `mitbol_test_hu008_<id>` y aplica la migración existente.
Las pruebas sin escrituras simulan fallos, validan esquemas o consultan OpenAPI.
La suite anterior conserva sus pruebas SQLite y PostgreSQL.

Cada prueba escribe dentro de una transacción exterior con sesiones que usan
`join_transaction_mode="create_savepoint"`. Los commits de preparación quedan
dentro de esa transacción y su rollback final elimina los datos simulados.
No se ejecutó `DROP DATABASE`.

La base de la ejecución final fue **`mitbol_test_hu008_5991e709bf78`**. Terminó con
cero filas en las cinco tablas comerciales y se conservó, con la revisión
`20261029_0001`. La ejecución preliminar también dejó su base aislada vacía.

La fixture histórica escribe `docs/loyverse/hu008_block2_test_database.json`.
Durante estas ejecuciones se conservó su contenido original en memoria y se
restauró al terminar pytest. No se cambió la fixture ni la evidencia histórica.

### Cobertura de los 34 casos nuevos

| Prueba | Casos | Qué demuestra |
| --- | --- | --- |
| `test_detail_is_public_and_extends_hu001` | 3 | Consulta pública, una o varias variantes, precio cero, metadatos y campos comunes idénticos al listado. |
| `test_detail_keeps_assignments_and_shared_group_context` | 1 | Grupos compartidos, exclusivos, ajenos y vacíos; opciones gratuitas, adicionales e inactivas; orden y estado por variante. |
| `test_detail_availability_matches_hu001` | 5 | Agotamiento del padre y variantes, extremos de precios y propagación a complementos. |
| `test_detail_without_variants` | 1 | Listas vacías, extremos nulos y producto no seleccionable. |
| `test_detail_missing_and_inactive_share_404` | 1 | `404` idéntico para inexistentes e inactivos, sin cargar relaciones. |
| `test_detail_invalid_identifier_never_queries_database` | 4 | `422` para texto, cero, negativo y decimal sin consultar el catálogo. |
| `test_detail_postgresql_preserves_variant_and_complement_precision` | 1 | Precisión extensa real de PostgreSQL hasta JSON para base y adicional. |
| `test_detail_invalid_complement_prices_are_rejected` | 7 | Rechazo de nulo, negativos, no finitos, float y texto en el esquema Python estricto. |
| `test_detail_loads_full_tree_without_n_plus_one` | 2 | Cuatro SELECT con una y 25 variantes y grupos distintos; ninguna escritura de la petición. |
| `test_detail_repeated_reads_preserve_all_catalog_rows` | 1 | Respuestas estables y todas las filas del catálogo de prueba sin cambios. |
| `test_detail_database_errors_are_sanitized` | 5 | `500` y `503` sin SQL ni credenciales en respuesta o registros. |
| `test_detail_complement_query_error_is_sanitized` | 1 | Error SQL real al cargar complementos, tratado como `500` sin filtraciones. |
| `test_detail_openapi_and_swagger_describe_contract` | 1 | Swagger, estados HTTP, tipos, ID positivo, ejemplos válidos y campos internos ausentes. |
| `test_detail_reads_catalog_written_by_hu008` | 1 | HU-008 persiste un catálogo simulado y HU-003 recupera productos, grupos y opciones correctos. |

### Ejecución completa

Desde la raíz del backend y con la configuración local autorizada:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Resultado final: **`223 passed, 2 warnings in 18.48s`**. No hubo fallos ni pruebas
omitidas. La ejecución incluye HU-008, HU-001, HU-003, modelos, cliente de
Loyverse y migraciones. Las llamadas a Loyverse de las pruebas se simulan;
esta ejecución no sincroniza el catálogo comercial real.

Las dos advertencias corresponden a dependencias existentes: la compatibilidad
de Starlette TestClient con HTTPX y el alias `BlockingPortal` de AnyIO. No se
modificaron dependencias para ocultarlas.

La primera ejecución detectó tres fallos en las nuevas aserciones de conteo:
incluían los `SAVEPOINT` y su rollback como si fueran consultas del catálogo.
Se corrigieron las pruebas para contar SELECT y comprobar por separado la
ausencia de escrituras. No fue necesario modificar la implementación del
Bloque 2, HU-001 ni la sincronización de HU-008.

## 8. Verificación con el catálogo real y Swagger

Se utilizó la aplicación real y su dependencia `get_db` contra `mitbol_dev`.
Un listener exclusivo del proceso de verificación configuró cada conexión
psycopg2 con `set_session(readonly=True)`. Se comprobaron
`current_database() = mitbol_dev` y `SHOW transaction_read_only = on`.
Esta protección no modifica la configuración permanente de la aplicación.

### Resultados reales por HTTP

| Ruta | Producto | Variantes y precios MXN | Modificadores | SELECT |
| --- | --- | --- | --- | --- |
| `/api/menu` | Listado completo | 3 productos, 5 variantes | Conserva el contrato de HU-001. | 2 |
| `/api/menu/1` | Papas fritas | Variante 1: Papas fritas, `45.00`. | `modifiers: []`. | 3 |
| `/api/menu/2` | Limonada natural | Variante 2: 355 ml, `35.00`; variante 3: 500 ml, `49.00`. | Ambas contienen Extras bebida. | 4 |
| `/api/menu/3` | Hamburguesa clásica | Variante 4: Sencilla, `89.00`; variante 5: Doble, `129.00`. | Ambas contienen Extras hamburguesa. | 4 |

Las dos variantes de Limonada natural contienen Chía y Hierbabuena, a `5.00`
cada una. Las dos variantes de Hamburguesa clásica contienen Queso extra
(`15.00`), Tocino (`20.00`) y Sin cebolla (`0.00`). Papas fritas no contiene
grupos. Todas esas peticiones devolvieron `200` sin autenticación.

Se compararon los campos comunes de cada detalle con su producto en
`GET /api/menu`: coincidieron exactamente al excluir sólo los nuevos grupos
anidados. También se comprobaron `/docs` y `/openapi.json`, ambos con `200`.

### Comprobación interactiva de Swagger

Se abrió Swagger UI en Microsoft Edge sin ventana, conectado a un servidor
Uvicorn temporal de la aplicación. Se desplegó la operación de detalle, se
activó **Try it out**, se introdujeron sucesivamente los IDs `1`, `2` y `3` y se
pulsó **Execute** mediante eventos de entrada del navegador. Los tres casos
mostraron **HTTP 200** y los precios reales `45.00`, `35.00`/`49.00` y
`89.00`/`129.00`, respectivamente. Se capturó temporalmente la pantalla de la
respuesta de Hamburguesa clásica para inspección visual.

La automatización se ejecutó fuera del sandbox porque éste interrumpía la
conexión local de control del navegador. El servidor de verificación mantuvo
las conexiones PostgreSQL en modo de sólo lectura. El navegador y el servidor
eran exclusivos de esta comprobación; no se añadieron dependencias ni archivos
de automatización al repositorio.

Para repetirla personalmente:

1. Inicia el backend desde su raíz:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

2. Abre `http://localhost:8000/docs`.
3. En **Menú → GET /api/menu**, pulsa **Try it out → Execute** y revisa los IDs.
4. Abre **GET /api/menu/{product_id}**, pulsa **Try it out**, introduce `1` y
   pulsa **Execute**. Debe mostrar Papas fritas sin modificadores.
5. Repite con `2`: revisa las variantes de Limonada y Extras bebida.
6. Repite con `3`: revisa `89.00`, `129.00`, Extras hamburguesa y sus tres opciones.
7. Comprueba precios entre comillas, `image_url: null`, estados y orden.
8. Prueba `0` para obtener `422`. Para `404`, usa un entero positivo que no exista
   en tu catálogo; no es necesario desactivar productos reales.
9. Revisa los esquemas y los ejemplos de errores `404`, `500` y `503` sin provocar
   fallos ni modificar la base comercial.

### Integridad del catálogo y migraciones

Se comparó el estado antes de la suite y después de las verificaciones. Se
incluyeron todas las filas y columnas de las cinco tablas, ordenadas por clave
primaria, serializadas a JSON canónico y resumidas mediante SHA-256. Se
incluyen descripciones, estados, asociaciones y fechas, no sólo conteos.

| Tabla | Filas antes y después |
| --- | --- |
| Products | 3 |
| ProductVariants | 5 |
| ProductModifiers | 2 |
| ProductComplements | 5 |
| ProductModifierAssignments | 4 |

Huella de datos, idéntica antes y después:

```text
2b2b2d74fc11f1875d61dcb2ac9caefb38d15224a13ab3230899dd66108267ea
```

Huella del esquema reflejado, idéntica antes y después:

```text
7d4abc68311cde356e74a36cb2ccfc70c25fab9bf182bd511b16b3937475d145
```

La revisión instalada sigue siendo **`20261029_0001`**. También se compararon
las huellas de los archivos Python de `migrations/`; permanecieron iguales.
Las pruebas de PostgreSQL verificaron columnas, tipos, valores predeterminados,
claves y restricciones frente al ORM. No se aplicaron migraciones a `mitbol_dev`.
Aplicar la migración existente en la base aislada forma parte de la preparación
de pruebas y no modifica el esquema real.

La representación canónica de esta comprobación usa JSON con claves ordenadas,
UTF-8, `ensure_ascii=False`, `default=str` y separadores compactos. Su huella no
debe compararse con informes que utilicen otra representación del mismo catálogo.

## 9. Limitaciones y pendientes

- Las variantes sólo tienen `is_available`: no se distingue agotamiento de baja
  comercial con el modelo actual.
- Los complementos tienen `is_active`, sin stock ni disponibilidad local
  independiente. No se inventan esos campos.
- Los grupos no tienen un estado de activación. Sólo aparecen cuando existen
  asignaciones a las variantes consultadas. HU-008 retira asociaciones al dar
  de baja grupos.
- No existen cantidades mínimas o máximas, obligatoriedad, selección única o
  múltiple ni restricciones de combinación representadas en estas tablas.
- Los precios y opciones de un grupo son compartidos por todas sus variantes
  asignadas; no hay sobreprecios distintos por variante para el mismo complemento.
- No hay soporte real para fotografías: `image_url` permanece en `null`.
- El endpoint refleja el último catálogo almacenado; no confirma en tiempo real
  existencias ni cambios recientes de Loyverse.
- Las pruebas de N+1 cubren una y 25 variantes con grupos distintos; no son una
  prueba de carga ni una garantía de un número fijo de consultas a cualquier escala.
- No hay carrito, reservas, pedidos ni cálculo de totales. Una futura operación
  de pedido tendrá que validar de nuevo precios, estados y asociaciones.
- La prueba del backend y Swagger no sustituye la integración completa con la
  aplicación React del equipo. Se conserva el CORS existente para
  `http://localhost:5173`.
- Las bases aisladas de prueba se conservaron vacías. No se añadieron tareas de
  eliminación ni se ejecutaron operaciones destructivas.

Estas limitaciones corresponden al alcance y al modelo aprobado, no a criterios
de aceptación pendientes de HU-003.

## 10. Diez preguntas del profesor con respuestas

1. **¿Qué diferencia hay entre HU-001 y HU-003?**
   HU-001 lista el catálogo público con variantes y precios. HU-003 consulta un
   producto y añade los grupos y complementos de cada variante, conservando los
   mismos campos y reglas comerciales.

2. **¿Por qué los grupos están dentro de las variantes?**
   Porque `ProductModifierAssignments` relaciona variantes con grupos. Aunque
   Loyverse los asocie a artículos, el endpoint debe respetar las relaciones que
   HU-008 ya almacenó, sin inventar asociaciones.

3. **¿Cómo evitas N+1?**
   Cargando colecciones por lotes con `selectinload` y el grupo de cada
   asignación con `joinedload`. Las pruebas cuentan cuatro SELECT con una y
   25 variantes, cada una con su propio grupo y opción.

4. **¿Por qué no usar float para precios?**
   Puede introducir errores de representación binaria. PostgreSQL `Numeric`
   entrega `Decimal`; Pydantic lo conserva en Python y JSON publica una cadena
   exacta mediante el mismo tipo `MenuAmount` que HU-001.

5. **¿Qué significa can_order?**
   Es disponibilidad efectiva. El producto necesita estar activo, disponible y
   tener alguna variante disponible. La variante necesita además su propio
   estado disponible. El complemento necesita una variante habilitada y estar
   activo. No implica que se haya creado un pedido.

6. **¿Por qué el mismo complemento puede tener can_order distinto?**
   Porque se devuelve en el contexto de una variante. Si una presentación está
   agotada y otra disponible, un grupo compartido conserva los mismos IDs y
   precios, pero sus opciones quedan deshabilitadas sólo en la agotada.

7. **¿Por qué un agotado devuelve 200 y un inactivo 404?**
   Un agotado sigue siendo un producto publicado y puede consultarse con
   `can_order=false`. Un producto comercialmente inactivo no debe publicarse,
   por lo que comparte el `404` de los inexistentes.

8. **¿Qué sucede con productos sin variantes o sin modificadores?**
   Un producto activo sin variantes devuelve `200`, `variants: []`, extremos
   nulos y `can_order=false`. Una variante sin grupos devuelve `modifiers: []`;
   un grupo asignado sin opciones conserva `complements: []`.

9. **¿Cómo demuestras que no cambiaste mitbol_dev ni rompiste HU-008?**
   Las escrituras nuevas se ejecutaron en PostgreSQL aislado con rollback y la
   suite completa pasó 223 pruebas. Las peticiones reales usaron conexiones de
   sólo lectura. Las huellas de todas las filas, el esquema y los archivos de
   migración se conservaron, igual que la revisión instalada.

10. **¿Qué puede ver un cliente cuando falla la base?**
    Un `503` de indisponibilidad o un `500` interno con mensajes fijos. Los
    registros de la ruta sólo incluyen el tipo de excepción. Las pruebas
    comprueban que ni las respuestas ni los registros contienen SQL,
    credenciales o nombres internos sensibles.

## 11. Resumen para explicar al equipo

HU-003 añade `GET /api/menu/{product_id}`. React toma el ID del listado, consulta
el detalle y recorre `product.variants`, luego `modifiers` y finalmente
`complements`. Los precios se entregan como cadenas exactas en MXN. Frontend usa
`can_order` para representar si una variante u opción está habilitada; las
opciones inactivas siguen visibles, pero deshabilitadas.

El backend reutiliza las reglas de HU-001 y las asociaciones persistidas por
HU-008. Recupera el árbol por lotes, conserva el orden y maneja casos vacíos y
errores. La suite completa pasó 223 pruebas, y los productos reales 1, 2 y 3
devolvieron sus variantes, precios y complementos correctos sin modificar datos
ni migraciones. El contrato está listo para su integración con frontend dentro
del alcance de consulta de información comercial.
