# HU-007 — Gestión de disponibilidad local de productos

Proyecto universitario **MITBOL v2.1**. Cierre de backend para desarrollo local: **2 de noviembre de 2026**, zona `America/Mexico_City`.

**Resultado final: 452 pruebas aprobadas, 0 fallos, 0 errores, 0 omitidas y 2 advertencias de dependencias.** Alembic está en `20261102_0003`. La administración real permanece deshabilitada: `ADMIN_ENABLED=false`.

Este documento reúne la explicación técnica, los contratos, la evidencia, la guía de Swagger y la preparación de la exposición. Las acciones de escritura sobre desarrollo incluidas en la guía son instrucciones para el usuario: el cierre automatizado no cambió la disponibilidad de `mitbol_dev` ni ejecutó otra sincronización real.

## 1. ¿Qué implementamos?

### 1.1. El problema original

Loyverse administra el catálogo comercial: artículos, presentaciones, precios y configuraciones de venta de la tienda. MITBOL necesita indicar que, temporalmente, una presentación está agotada en el establecimiento sin modificar ese catálogo comercial.

Antes de HU-007, `ProductVariants.is_available` tenía responsabilidades mezcladas. Al insertar una variante tomaba el estado comercial de Loyverse; después se conservaba como preferencia local. Una baja comercial lo forzaba a `false`. Esto producía dos problemas: los cambios posteriores de `available_for_sale` podían no reflejarse y una baja podía borrar la preferencia local anterior.

Un booleano no permite guardar independientemente «Loyverse permite vender» y «MITBOL tiene disponible». Por ejemplo, una variante puede estar disponible localmente y deshabilitada comercialmente. Si ambos estados se comprimen en un único `false`, ya no se conoce cuál de ellos impide solicitarla.

### 1.2. La solución

Se incorporó `ProductVariants.is_commercially_available` y se mantuvo `is_available` para la preferencia local. Los cuatro estados posibles quedan representados:

| Local: `is_available` | Comercial: `is_commercially_available` | ¿Puede solicitarse si el padre también lo permite? |
|---|---|---|
| `true` | `true` | Sí. |
| `false` | `true` | No: agotamiento local. |
| `true` | `false` | No: restricción comercial. |
| `false` | `false` | No: ambas restricciones. |

MITBOL puede cambiar únicamente la disponibilidad local del producto o de una variante mediante los nuevos PATCH. Loyverse controla la activación comercial del producto y la disponibilidad comercial de la variante. La API administrativa no permite escribir esos campos comerciales.

### 1.3. `can_order` es el resultado, no otra columna

`can_order` es un booleano calculado para la respuesta; no se almacena en otra tabla. La variante necesita simultáneamente:

1. Producto comercialmente activo: `product.is_active`.
2. Producto disponible localmente: `product.is_available`.
3. Variante disponible localmente: `variant.is_available`.
4. Variante habilitada comercialmente: `variant.is_commercially_available`.

Las cuatro condiciones se unen con AND. Si cualquiera es falsa, la variante tiene `can_order=false`.

El producto tiene `can_order=true` cuando alguna de sus variantes cumple la regla completa. Sin variantes, el resultado es falso. El complemento requiere que su variante pueda solicitarse y que el propio complemento esté comercialmente activo.

En el frontend futuro debe utilizarse **`can_order`** para habilitar la selección. El campo público `is_available` conserva la preferencia local y no basta para decidir si algo puede venderse.

## 2. Arquitectura y recorrido de las solicitudes

### 2.1. Archivos principales

Las rutas de esta tabla son relativas a este README y apuntan al código real:

| Archivo | Responsabilidad |
|---|---|
| [app/api/admin_catalog.py](../../../app/api/admin_catalog.py) | Router `/api/admin`, cuatro operaciones de disponibilidad y seguridad compartida con HU-005. |
| [app/schemas/availability.py](../../../app/schemas/availability.py) | Entrada `AvailabilityUpdate` y respuestas de producto y variante. |
| [app/schemas/enrichment.py](../../../app/schemas/enrichment.py) | Base `AdminInput`: validación estricta y rechazo de campos adicionales. |
| [app/services/product_availability_service.py](../../../app/services/product_availability_service.py) | Lecturas, cambios locales, transacciones y bloqueo del catálogo. |
| [app/services/availability_rules.py](../../../app/services/availability_rules.py) | Regla efectiva compartida de variantes y productos. |
| [app/services/menu_service.py](../../../app/services/menu_service.py) | Listado, detalle, complementos, rangos y enriquecimiento por lotes. |
| [app/schemas/menu.py](../../../app/schemas/menu.py) | Contratos públicos y serialización exacta de importes `Decimal`. |
| [app/api/menu.py](../../../app/api/menu.py) | GET públicos y respuestas de error sanitizadas. |
| [app/services/catalog_probe.py](../../../app/services/catalog_probe.py) | Descarga y diagnóstico del catálogo remoto. |
| [app/services/catalog_transform.py](../../../app/services/catalog_transform.py) | Convierte el catálogo validado en un plan comercial; no escribe en PostgreSQL. |
| [app/services/sync_service.py](../../../app/services/sync_service.py) | UPSERT, transacción, control de destino, bloqueo y bajas comerciales. |
| [app/services/product_enrichment_service.py](../../../app/services/product_enrichment_service.py) | Proporciona el manejo transaccional de errores ya utilizado por HU-005. |
| [app/core/security.py](../../../app/core/security.py) | `require_local_admin`, `AdminRequestGuard` y clave temporal local. |
| [app/core/config.py](../../../app/core/config.py) | Configuración; administración deshabilitada por defecto. |
| [app/main.py](../../../app/main.py) | Fábrica de FastAPI, middleware, errores y registro condicional del router administrativo. |
| [app/db/database.py](../../../app/db/database.py) | Sesión SQLAlchemy y dependencia `get_db`. |
| [app/models/product.py](../../../app/models/product.py) | Producto: estado comercial y disponibilidad local. |
| [app/models/product_variant.py](../../../app/models/product_variant.py) | Variante: precio, identidad y disponibilidades separadas. |
| [Migración 20261102_0003](../../../migrations/versions/20261102_0003_hu007_availability.py) | Añade únicamente la columna comercial aprobada. |
| [tests/test_availability_api.py](../../../tests/test_availability_api.py) | Administración, reglas, Swagger/OpenAPI, rollback y concurrencia en PostgreSQL aislado. |
| [tests/test_availability_migration.py](../../../tests/test_availability_migration.py) | Migración real con cinco variantes históricas simuladas. |
| [tests/test_catalog_sync_postgresql.py](../../../tests/test_catalog_sync_postgresql.py) | Regresiones de sincronización, bajas, reapariciones y preservación local. |
| [tests/test_menu.py](../../../tests/test_menu.py), [tests/test_enrichment_api.py](../../../tests/test_enrichment_api.py) | Regresiones públicas, HU-005, seguridad y consultas por lotes. |

### 2.2. Swagger/React → FastAPI → servicio → SQLAlchemy → PostgreSQL

El recorrido arquitectónico previsto es cliente → FastAPI → servicio → ORM → PostgreSQL. En HU-007 la administración se prueba desde Swagger local. **React todavía no tiene una integración administrativa autorizada** y su origen es rechazado por la protección temporal.

Una solicitud PATCH sigue estos pasos:

1. El cliente envía el ID interno, el JSON y el encabezado `X-Admin-Key`.
2. `AdminRequestGuard` verifica que la administración esté habilitada, que el acceso sea local y que la credencial sea válida, antes de procesar el cuerpo.
3. El router reutiliza `require_local_admin`. FastAPI valida el identificador y Pydantic valida el JSON.
4. `get_db` proporciona una sesión SQLAlchemy a la operación.
5. El servicio obtiene el bloqueo transaccional compartido con HU-008.
6. Carga el recurso; un ID válido pero inexistente produce `404`.
7. Asigna solamente `is_available`, ejecuta `flush`, calcula la respuesta y confirma con `commit`.
8. Ante errores, el manejador hace rollback y devuelve un error controlado. La sesión se cierra al terminar la petición.

Las operaciones GET no solicitan el bloqueo de mutación ni hacen commit. Leen la configuración y calculan `can_order` usando las mismas reglas del menú.

### 2.3. Loyverse → cliente → plan → `SyncService` → PostgreSQL

`LoyverseAPIClient` consulta `/stores`, `/items` y `/modifiers`, completando la paginación. `fetch_catalog` selecciona la tienda configurada y solicita registros eliminados cuando corresponde. La transformación valida identidades, relaciones, precios y disponibilidad de la tienda.

El estado comercial de una variante proviene del campo real `available_for_sale` dentro de `variants[].stores[]`, para `LOYVERSE_STORE_ID`. Las bajas explícitas se identifican mediante `deleted_at`. No se inventa un campo `is_active` en las variantes de la API remota.

`SyncService.synchronize` mantiene su transacción y bloqueo durante la descarga y persistencia. El servicio también ofrece `persist_snapshot` para un catálogo previamente descargado y validado. La sincronización controlada autorizada utilizó ese catálogo validado y el servicio existente, sin duplicar los UPSERT ni realizar una segunda descarga durante la escritura.

## 3. Base de datos, migración y persistencia comercial

### 3.1. Responsabilidad de cada campo

| Campo | Quién lo administra | Significado |
|---|---|---|
| `Products.is_active` | HU-008/Loyverse. | Vigencia comercial del artículo. Una baja explícita lo desactiva. |
| `Products.is_available` | MITBOL. | Preferencia local del producto completo. |
| `ProductVariants.is_available` | MITBOL. | Preferencia local de una presentación. |
| `ProductVariants.is_commercially_available` | HU-008/Loyverse. | Habilitación comercial de esa presentación para la tienda. |
| `ProductComplements.is_active` | HU-008/Loyverse. | Vigencia comercial del complemento. |

Los complementos no tienen una disponibilidad local independiente. Los grupos de modificadores tampoco tienen `is_active`. HU-007 no agregó esos conceptos.

Cambiar el producto a no disponible no escribe en las variantes: solo cambia el resultado efectivo. Así, restaurar el producto no borra una preferencia de agotamiento que ya tuviera una variante.

### 3.2. Migración `20261102_0003`

Esta revisión sucede a `20261102_0002`. Añade exclusivamente `ProductVariants.is_commercially_available BOOLEAN NOT NULL DEFAULT false`. No crea tablas ni modifica las migraciones anteriores.

- `BOOLEAN` admite los estados verdadero y falso.
- `NOT NULL` impide que la columna quede sin valor.
- `DEFAULT false` se utiliza cuando una inserción omite el campo. Al añadir la columna, las filas existentes también adquieren el valor conservador falso.
- El valor local existente de `is_available` no se transforma ni se interpreta como prueba de habilitación comercial.

La migración no puede saber por qué una variante histórica estaba en `false`; ese campo antes mezclaba causas. Conservarlo evita perder información adicional. Si un valor histórico local falso requiere restauración, debe hacerlo el administrador después de verificar el caso.

Después de instalar la columna, las cinco variantes quedaron temporalmente deshabilitadas para solicitarse. Fue necesario consultar Loyverse y ejecutar una sincronización autorizada para obtener la habilitación comercial verdadera. La migración nunca llamó por sí misma a Loyverse.

La revisión ya está instalada y comprobada. Los comandos de verificación son:

```powershell
.\.venv\Scripts\python.exe -B -m alembic current
.\.venv\Scripts\python.exe -B -m alembic check
```

El resultado del cierre es `20261102_0003 (head)` y `No new upgrade operations detected`. El [SQL revisado de la migración](../../database/hu007_pending.sql) conserva su nombre histórico de preparación; **ya fue aplicado** durante la etapa expresamente autorizada. No se vuelve a ejecutar como parte de esta guía.

### 3.3. Qué conserva y qué actualiza una sincronización

En variantes nuevas, el servicio establece `is_available=true` como preferencia local inicial y escribe la disponibilidad comercial realmente verificada. Si el estado comercial es falso, no puede solicitarse aunque la preferencia local sea verdadera. El valor predeterminado comercial de la base sigue siendo falso para otras inserciones que omitan el campo.

En variantes existentes, el UPSERT actualiza nombre, precio, estado comercial y fecha. **No incluye `is_available` en los campos del UPDATE**. Productos, imágenes, etiquetas y descripciones mantienen su configuración local conforme a HU-005.

Una baja de producto marca `is_active=false` y deshabilita comercialmente sus variantes. Una baja de variante marca su disponibilidad comercial en falso y retira sus asignaciones de modificadores. Ambas conservan la preferencia local. Una reaparición con el mismo UUID conserva el ID interno y recibe la configuración comercial y las asignaciones válidas del nuevo catálogo.

La ausencia de una entidad en una respuesta no se interpreta automáticamente como baja. Si faltan datos necesarios para determinar la disponibilidad o el precio, la transformación rechaza el catálogo completo. El servicio conserva el último estado confirmado; no inventa valores ni aplica un catálogo parcial.

## 4. Diez fragmentos reales explicados línea por línea

Los bloques siguientes fueron extraídos del código del repositorio. En los fragmentos tomados del interior de una función se quitó únicamente la sangría exterior. La numeración de la explicación se refiere a las líneas del bloque mostrado.

### 4.1. Regla compartida y disponibilidad efectiva del producto

Archivo: [availability_rules.py](../../../app/services/availability_rules.py). Funciones: `variant_can_order` y `product_can_order`.

```python
def variant_can_order(product: Product, variant: ProductVariant) -> bool:
    return bool(
        product.is_active and product.is_available
        and variant.is_available and variant.is_commercially_available
    )


def product_can_order(product: Product) -> bool:
    return any(variant_can_order(product, variant) for variant in product.variants)
```

- **Línea 1:** declara la función para un producto y una variante ORM; devuelve un booleano.
- **Línea 2:** `bool` garantiza un resultado booleano de la expresión.
- **Línea 3:** exige que el producto esté comercialmente activo y localmente disponible.
- **Línea 4:** añade las dos condiciones de la variante. AND necesita que todas sean verdaderas.
- **Línea 5:** cierra la expresión y termina el retorno.
- **Líneas 6–7:** separan las dos funciones para facilitar la lectura.
- **Línea 8:** declara la regla efectiva del producto completo.
- **Línea 9:** `any` busca alguna variante que cumpla la regla anterior. Reutiliza la misma función; con una colección vacía devuelve falso. Las variantes ya se cargaron por lotes antes de llamar a esta regla.

### 4.2. Entrada Pydantic estricta

Archivo: [schemas/availability.py](../../../app/schemas/availability.py). Clase: `AvailabilityUpdate`.

```python
class AvailabilityUpdate(AdminInput):
    is_available: bool
```

- **Línea 1:** hereda `AdminInput`, cuya configuración existente es `ConfigDict(extra="forbid", strict=True)` en `schemas/enrichment.py`. Esto rechaza campos adicionales y conversiones automáticas de cadenas o números a booleanos.
- **Línea 2:** declara el único campo que puede modificarse. Como no tiene valor predeterminado, es obligatorio. Su tipo `bool` no admite `null` en este contrato.

El cliente envía `true` o `false` JSON. No envía el UUID de Loyverse, un precio ni el estado comercial.

### 4.3. GET administrativo

Archivo: [api/admin_catalog.py](../../../app/api/admin_catalog.py). Función: `get_product_availability`.

```python
@router.get("/products/{product_id}/availability", response_model=ProductAvailabilityResponse, summary="Consultar disponibilidad del producto")
def get_product_availability(product_id: Identifier, db: Database):
    return availability.get_product_availability(db, product_id)
```

- **Línea 1:** registra GET y su modelo de respuesta. El prefijo del router agrega `/api/admin` a la ruta mostrada. La seguridad está definida en el router compartido.
- **Línea 2:** `Identifier` valida un entero entre 1 y 2,147,483,647; `Database` obtiene la sesión mediante `Depends(get_db)`.
- **Línea 3:** delega la consulta al servicio. El router no realiza SQL ni duplica las reglas de negocio.

El servicio carga el producto y sus variantes mediante `selectinload`, construye la respuesta y utiliza `product_can_order`. Una entidad inexistente genera `404`.

### 4.4. PATCH local de una variante y transacción

Archivo: [product_availability_service.py](../../../app/services/product_availability_service.py). Función: `update_variant_availability`.

```python
def update_variant_availability(db, variant_id, is_available):
    with database_errors(db):
        _mutation_lock(db)
        variant = _variant(db, variant_id)
        variant.is_available = is_available
        db.flush()
        result = _variant_response(variant)
        db.commit()
        return result
```

- **Línea 1:** recibe la sesión, el ID interno y el booleano que FastAPI ya validó.
- **Línea 2:** entra en el manejador de errores reutilizado de HU-005. Ante errores hace rollback y devuelve mensajes sanitizados.
- **Línea 3:** obtiene el bloqueo común de las mutaciones de catálogo.
- **Línea 4:** carga la variante junto con su producto mediante `joinedload`; si no existe, detiene la operación.
- **Línea 5:** modifica exclusivamente la preferencia local. No asigna el campo comercial, el precio ni los datos de enriquecimiento.
- **Línea 6:** `flush` envía los cambios a PostgreSQL dentro de la transacción; todavía no los confirma definitivamente.
- **Línea 7:** construye la respuesta con el estado comercial y el padre actuales, aplicando la regla compartida.
- **Línea 8:** confirma la transacción con `commit`; si falla, el manejador intenta rollback y devuelve un error controlado.
- **Línea 9:** devuelve el modelo ya construido, únicamente después del commit satisfactorio.

El PATCH de producto sigue el mismo patrón sobre `Product.is_available`, sin asignar campos de las variantes.

### 4.5. Bloqueo transaccional compartido

Archivo: [product_availability_service.py](../../../app/services/product_availability_service.py). Función: `_mutation_lock`.

```python
def _mutation_lock(db):
    # La misma clave que HU-008 evita sobrescrituras y respuestas inconsistentes
    # durante una baja/reactivación. Se libera al commit o rollback.
    if db.get_bind().dialect.name != "postgresql":
        raise EnrichmentError(500, "La administración de disponibilidad requiere PostgreSQL.")
    if not db.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
        raise EnrichmentError(409, "Hay otra operación de catálogo en curso; intenta nuevamente.")
```

- **Línea 1:** define el control previo a una escritura de disponibilidad.
- **Líneas 2–3:** documentan por qué comparte la clave de HU-008 y cuándo se libera el bloqueo.
- **Línea 4:** comprueba el dialecto real de la sesión.
- **Línea 5:** rechaza un motor que no sea PostgreSQL con un error interno controlado.
- **Línea 6:** construye un SELECT a `pg_try_advisory_xact_lock` con `CATALOG_SYNC_LOCK_KEY`. `db.scalar` obtiene el booleano; `try` significa que no espera indefinidamente si otro proceso tiene la clave.
- **Línea 7:** devuelve `409` cuando otra operación de catálogo ocupa el bloqueo. No llega a asignar el campo local.

El sincronizador solicita la misma clave. El bloqueo dura hasta commit o rollback y coordina a quienes lo utilizan; no sustituye la autenticación ni impide por sí mismo un UPDATE manual que ignore el protocolo.

### 4.6. UPSERT comercial que preserva la preferencia local

Archivo: [sync_service.py](../../../app/services/sync_service.py). Fragmento de `SyncService._write`.

```python
for variant in plan.variants:
    # Nuevas: preferencia local disponible y estado comercial verificado.
    # Existentes: Loyverse nunca modifica la preferencia local.
    variants[variant.loyverse_id] = self._upsert(session, ProductVariant, {
        "loyverse_id": variant.loyverse_id, "name": variant.name, "price": variant.price,
        "product_id": products[variant.product_loyverse_id],
        "is_available": True,
        "is_commercially_available": variant.is_commercially_available,
        "synced_at": plan.synced_at,
    }, ("name", "price", "is_commercially_available", "synced_at"), parent="product_id")
```

- **Línea 1:** recorre las variantes del plan previamente validado.
- **Líneas 2–3:** distinguen la inicialización de una fila nueva de la conservación de una existente.
- **Línea 4:** llama al UPSERT y guarda el ID interno devuelto en el mapa de UUID externos.
- **Línea 5:** proporciona la identidad, el nombre y el precio comercial; el precio continúa siendo `Decimal`.
- **Línea 6:** resuelve el padre con el mapa de productos, conservando la relación correcta.
- **Línea 7:** inicializa la preferencia local de una nueva variante en verdadero.
- **Línea 8:** aporta el estado comercial obtenido de `available_for_sale` para la tienda.
- **Línea 9:** guarda la fecha del catálogo validado.
- **Línea 10:** especifica los únicos campos que se actualizan ante un conflicto por `loyverse_id`. La ausencia de `is_available` en esta tupla conserva la preferencia local de las filas existentes. `parent="product_id"` impide reasignar una identidad histórica a otro producto.

La función `_upsert` utiliza `INSERT ... ON CONFLICT DO UPDATE` y `RETURNING` para recuperar la PK. Las secuencias pueden avanzar por intentos de INSERT aunque la fila finalmente se actualice; eso no cambia la PK existente.

### 4.7. Baja comercial sin sobrescribir el estado local

Archivo: [sync_service.py](../../../app/services/sync_service.py). Fragmento de `SyncService._retire`.

```python
for external_id, parent_id in plan.deleted_variants:
    variant_id = self._retire_child(
        session, ProductVariant, Product, "product_variant_id", "product_id", external_id, parent_id,
        {"is_commercially_available": False, "synced_at": plan.synced_at},
    )
    if variant_id is not None:
        retired["variants"] += 1
        removed += session.execute(delete(ProductModifierAssignment).where(
            ProductModifierAssignment.product_variant_id == variant_id,
        )).rowcount
```

- **Línea 1:** recorre las bajas explícitas de variantes con su identidad y la de su padre.
- **Línea 2:** delega la búsqueda y la actualización en `_retire_child`.
- **Línea 3:** proporciona los modelos, las claves y las identidades para verificar que la variante siga perteneciendo al mismo producto.
- **Línea 4:** cambia exclusivamente el estado comercial y la fecha de sincronización. La preferencia local no aparece en la asignación.
- **Línea 5:** cierra la llamada que devuelve el ID interno, o `None` si esa baja nunca se importó.
- **Línea 6:** distingue una variante histórica encontrada de una entidad inexistente.
- **Línea 7:** aumenta el contador informativo de variantes retiradas.
- **Línea 8:** prepara y ejecuta la eliminación de sus asignaciones comerciales de modificadores.
- **Línea 9:** limita esa eliminación a la variante dada de baja.
- **Línea 10:** añade el número de asociaciones retiradas al informe.

No se elimina la variante, su precio, sus fotografías ni sus etiquetas. La baja del producto aplica la misma separación comercial a sus variantes. Una reaparición reconstruye las asignaciones recibidas y respeta la preferencia local guardada.

### 4.8. Rango de precios y referencia cuando no puede pedirse nada

Archivo: [menu_service.py](../../../app/services/menu_service.py). Fragmento de `_build_product`.

```python
available_variants = [variant for variant in variants if variant.can_order]
# Si todas están agotadas, los importes se conservan únicamente como referencia.
prices = [variant.price for variant in (available_variants or variants)]
price_min = min(prices, default=None)
price_max = max(prices, default=None)
```

- **Línea 1:** selecciona las respuestas de variantes que tienen `can_order=true`, con todas las condiciones comerciales y locales ya evaluadas.
- **Línea 2:** explica el uso de importes de referencia cuando ninguna es solicitable.
- **Línea 3:** usa los precios de las solicitables; si la lista está vacía, usa todas las variantes. No modifica ninguno de esos precios.
- **Línea 4:** calcula el mínimo; sin variantes devuelve `None`, serializado como `null`.
- **Línea 5:** calcula el máximo con el mismo conjunto. El modelo del producto utiliza `price_min != price_max` para `has_price_range`.

Si solo la Sencilla puede pedirse, el rango actual es 89.00–89.00 y `has_price_range=false`. Si ninguna puede pedirse, se conserva 89.00–129.00 como referencia. El precio propio de la Doble sigue siendo 129.00 en ambos casos.

### 4.9. Complementos y disponibilidad contextual

Archivo: [menu_service.py](../../../app/services/menu_service.py). Función: `_build_modifier`.

```python
def _build_modifier(modifier: ProductModifier, *, variant_can_order: bool) -> MenuModifier:
    return MenuModifier(
        product_modifier_id=modifier.product_modifier_id,
        name=modifier.name,
        display_order=modifier.display_order,
        complements=[
            MenuComplement(
                product_complement_id=complement.product_complement_id,
                name=complement.name,
                price=complement.price,
                display_order=complement.display_order,
                is_active=complement.is_active,
                can_order=variant_can_order and complement.is_active,
            )
            for complement in sorted(
                modifier.complements,
                key=lambda value: (value.display_order, value.product_complement_id),
            )
        ],
    )
```

- **Línea 1:** recibe el grupo y, como argumento nombrado, la disponibilidad efectiva de la variante que lo está mostrando.
- **Línea 2:** construye la respuesta del grupo.
- **Línea 3:** conserva el ID del grupo.
- **Línea 4:** conserva su nombre comercial.
- **Línea 5:** conserva el orden de presentación.
- **Línea 6:** inicia la lista de complementos del grupo.
- **Línea 7:** construye una respuesta por cada complemento.
- **Línea 8:** incluye su ID interno.
- **Línea 9:** incluye su nombre.
- **Línea 10:** incluye su precio adicional como `Decimal`, sin sumarlo al rango del producto.
- **Línea 11:** conserva el orden de la opción.
- **Línea 12:** informa si el complemento está comercialmente activo.
- **Línea 13:** permite seleccionarlo únicamente cuando la variante puede pedirse y la opción está activa.
- **Línea 14:** cierra la respuesta individual.
- **Línea 15:** comienza el recorrido de las opciones ordenadas.
- **Línea 16:** utiliza solamente las opciones que pertenecen a ese grupo.
- **Línea 17:** ordena por `display_order` y usa el ID para desempatar.
- **Línea 18:** cierra la operación de ordenación.
- **Línea 19:** cierra la lista construida.
- **Línea 20:** termina la respuesta del grupo.

El detalle llama a esta función con el `can_order` ya calculado por la regla compartida. Un grupo compartido puede tener opciones seleccionables en la Sencilla y deshabilitadas en la Doble, sin cambiar sus IDs ni precios.

### 4.10. Migración Alembic conservadora

Archivo: [20261102_0003_hu007_availability.py](../../../migrations/versions/20261102_0003_hu007_availability.py). Función: `upgrade`.

```python
def upgrade() -> None:
    op.add_column("ProductVariants", sa.Column(
        "is_commercially_available", sa.Boolean(), server_default=sa.false(), nullable=False,
    ))
```

- **Línea 1:** define la operación que Alembic ejecuta al avanzar a esta revisión.
- **Línea 2:** solicita añadir una columna únicamente a `ProductVariants`.
- **Línea 3:** establece el nombre, el tipo booleano, el valor predeterminado del servidor en falso y la prohibición de `NULL`.
- **Línea 4:** cierra la definición de la columna y su incorporación a la tabla.

Los metadatos del mismo archivo declaran `revision="20261102_0003"` y `down_revision="20261102_0002"`. No hay transformación de `is_available`, creación de tablas ni llamadas a la API externa.

## 5. Conceptos importantes para estudiar

| Concepto | Aplicación concreta en HU-007 |
|---|---|
| Disponibilidad local | Decisión temporal del establecimiento; puede deshabilitar una variante sin cambiar Loyverse. |
| Disponibilidad comercial | Restricción recibida de Loyverse; MITBOL no puede saltársela mediante PATCH. |
| Booleano y AND | Valores verdadero/falso; cuatro condiciones verdaderas permiten solicitar una variante. |
| Regla de negocio | Restricción del dominio centralizada en `availability_rules.py`, compartida entre consumidores. |
| API REST | Recursos identificados por URL e ID interno, con contratos JSON y métodos HTTP definidos. |
| GET | Consulta; no cambia la configuración local ni solicita una sincronización. |
| PATCH | Cambio parcial del recurso; en estas rutas solo permite asignar `is_available`. |
| Modelo ORM | Clase SQLAlchemy que representa una tabla y sus relaciones, por ejemplo `ProductVariant`. |
| Migración | Cambio versionado del esquema. Alembic sabe qué revisión está instalada y qué revisión la precede. |
| UPSERT | Inserta una entidad nueva o actualiza la existente cuando coincide su identidad comercial única. |
| Idempotencia | Repetir la asignación del mismo estado produce el mismo resultado funcional. El mismo snapshot tampoco duplica filas ni asociaciones. No promete que los contadores de secuencia no avancen. |
| Transacción | Agrupa escrituras que deben confirmarse juntas; la sincronización no confirma cada entidad por separado. |
| `flush` | Envía los cambios pendientes sin cerrar la transacción. |
| `commit` | Confirma los cambios de la transacción. |
| Rollback | Revierte cambios de filas no confirmados ante errores. PostgreSQL no revierte los incrementos de secuencias. |
| Bloqueo y concurrencia | Una clave consultiva común coordina sincronizaciones y PATCH, evitando que dos operaciones autorizadas se pisen. |
| Validación | Comprueba tipos, campos, IDs y estructura antes de ejecutar las reglas y escrituras. |
| Seguridad administrativa | Clave compartida temporal, acceso local, rechazo de proxies y de orígenes no permitidos. |
| Prueba unitaria | Comprueba una regla o transformación con entradas controladas y sin necesitar el sistema completo. |
| Prueba de integración | Comprueba varios componentes reales juntos: FastAPI, SQLAlchemy y PostgreSQL aislado. `TestClient` ejecuta ASGI sin abrir un puerto TCP. |
| N+1 | Una consulta inicial seguida de una consulta por cada hijo. Se evita cargando relaciones y enriquecimiento por lotes. |

## 6. Contratos de API

### 6.1. Cuatro operaciones administrativas

| Método | Ruta | Entrada | Respuesta satisfactoria |
|---|---|---|---|
| GET | `/api/admin/products/{product_id}/availability` | ID interno en la ruta, sin cuerpo. | `200`, estado local, comercial y efectivo del producto. |
| PATCH | `/api/admin/products/{product_id}/availability` | ID y JSON con `is_available`. | `200`, mismo contrato del GET de producto. |
| GET | `/api/admin/variants/{variant_id}/availability` | ID interno en la ruta, sin cuerpo. | `200`, estados de variante y restricciones del padre. |
| PATCH | `/api/admin/variants/{variant_id}/availability` | ID y JSON con `is_available`. | `200`, mismo contrato del GET de variante. |

Los IDs son enteros positivos, hasta 2,147,483,647, y no son los UUID de Loyverse. Las cuatro operaciones utilizan `LocalAdminKey`, enviado en el encabezado `X-Admin-Key`. Solo están registradas cuando se habilita temporalmente la administración.

Los PATCH admiten exclusivamente uno de estos dos cuerpos:

```json
{"is_available": false}
```

```json
{"is_available": true}
```

No son válidos `{}`, `null`, `{"is_available": null}`, `{"is_available": "false"}`, `{"is_available": 0}` ni un cuerpo que agregue `is_commercially_available`, `is_active`, `price` u otro campo.

### 6.2. Respuesta del producto

Ejemplo del producto 3 agotado localmente, pero vigente en el catálogo comercial:

```json
{
  "product_id": 3,
  "is_active": true,
  "is_available": false,
  "can_order": false
}
```

No incluye una lista de variantes: para consultar sus restricciones individuales se utilizan los GET de variantes o el detalle público. Un producto activo, disponible y sin variantes solicitables también tiene `can_order=false`.

### 6.3. Respuesta de una variante

Ejemplo de la Doble agotada localmente, con autorización comercial intacta:

```json
{
  "product_variant_id": 5,
  "product_id": 3,
  "is_available": false,
  "is_commercially_available": true,
  "product_is_active": true,
  "product_is_available": true,
  "can_order": false
}
```

Si se restaura la preferencia local pero Loyverse la mantiene deshabilitada, la respuesta puede tener `is_available=true`, `is_commercially_available=false` y `can_order=false`. No es una contradicción: el primer valor expresa una preferencia, el último expresa el permiso efectivo.

### 6.4. Errores y operaciones repetidas

| Código | Situación |
|---|---|
| `401` | Falta la clave o no coincide. El guard la comprueba antes de acceder a PostgreSQL. |
| `403` | Cliente remoto, cabeceras de proxy, host u origen no permitido. |
| `404` | Administración deshabilitada, producto inexistente o variante inexistente. |
| `409` | Otro cambio de catálogo ocupa el bloqueo, o existe un conflicto de integridad controlado. |
| `422` | ID inválido, campo obligatorio ausente, tipo incorrecto, JSON inválido o campos adicionales. |
| `500` | Error interno sanitizado, incluido un error SQL inesperado después del flush. |
| `503` | Problema de conexión, interfaz o tiempo de espera de PostgreSQL. |

Los errores controlados usan `{"detail": "mensaje"}`. Los errores de validación de FastAPI usan una lista estructurada en `detail`; no hay que asumir que todos los errores tienen exactamente el mismo cuerpo.

El router compartido con HU-005 también anuncia errores de archivos como `415`, que no son una regla nueva de estos PATCH JSON. El guard global puede rechazar un cuerpo excesivo con `413`.

Repetir un PATCH con el mismo valor devuelve `200` y conserva el mismo estado. No es una operación de alternancia: enviar `false` dos veces no vuelve a activar el producto.

### 6.5. Efectos públicos y precios

- `GET /api/menu` sigue siendo público y devuelve `currency` y `products`.
- `GET /api/menu/{product_id}` devuelve `currency` y `product`, añadiendo grupos, complementos y galerías.
- Un producto comercialmente inactivo no aparece en el listado y su detalle responde `404`.
- Un producto agotado localmente sigue visible con `can_order=false`.
- Las variantes agotadas siguen visibles. Sus precios no se cambian ni se reemplazan por cero.
- Los precios son `Decimal` en Python y cadenas decimales exactas en JSON.
- El rango considera variantes con `can_order=true`; cuando ninguna es solicitable utiliza todas como referencia. Sin variantes, ambos extremos son `null` y `has_price_range=false`.
- Descripciones, `image_url`, galerías `images` y etiquetas `tags` se conservan.
- No se agregó el nuevo booleano comercial a la respuesta pública. La decisión efectiva ya está expresada por `can_order`.

## 7. Pruebas, revisión y evidencia final

### 7.1. Ejecución de este Bloque 3

Se ejecutó nuevamente la suite completa, sin reutilizar el resultado histórico de 446 casos:

```powershell
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
$env:MITBOL_POSTGRES_TESTS = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q -ra --junitxml="$env:TEMP\mitbol_hu007_block3_tests.xml"
```

| Resultado | Cantidad |
|---|---:|
| Aprobadas | **452** |
| Fallos | **0** |
| Errores | **0** |
| Omitidas | **0** |
| Advertencias | **2** |

La ejecución tardó aproximadamente 49.5 segundos. Las advertencias existentes son la deprecación del uso de `httpx` por `starlette.testclient` y la del alias `anyio.abc.BlockingPortal`. No se actualizaron dependencias fuera del alcance de HU-007.

Las escrituras de pruebas utilizan bases PostgreSQL independientes con nombres `mitbol_test_hu008_<identificador>`. El prefijo es histórico de la infraestructura reutilizada de HU-008. Las peticiones de pruebas reciben sesiones aisladas mediante `dependency_overrides[get_db]`; sus commits liberan SAVEPOINT dentro de una transacción exterior que la fixture revierte.

Las verificaciones de `mitbol_dev` utilizan conexiones configuradas con `default_transaction_read_only=on`. Las fixtures comparan esquema, huellas de filas y secuencias antes y después. No se cambiaron estados reales para demostrar agotamiento. Una prueba negativa de dialecto todavía abre SQLite en memoria, sin escribir un catálogo: comprueba que las migraciones requieren PostgreSQL.

### 7.2. Casos importantes y seis comprobaciones añadidas

| Área | Casos y evidencia |
|---|---|
| Regla principal | Las 16 combinaciones de los cuatro booleanos producen el mismo resultado en administración, listado, detalle y complementos. |
| Producto y variantes | Agotamiento/restauración, producto inactivo, ausencia de variantes, variantes hermanas independientes y preferencia local preservada. |
| Validación | Campo obligatorio, rechazo de `null`, números, cadenas, campos comerciales y adicionales, IDs inválidos e inexistentes. |
| Idempotencia | PATCH repetidos y UPSERT repetidos conservan el resultado funcional y no duplican entidades o asociaciones. |
| Sincronización | Cambios comerciales en ambos sentidos, nuevas variantes, bajas/reapariciones con la misma identidad, rechazo de datos incompletos y preservación de enriquecimiento. |
| Errores | Rollback ante fallos del servicio y de PostgreSQL, mensajes sin SQL ni secretos. |
| Concurrencia | El bloqueo del sincronizador impide un PATCH; el de un PATCH impide otra sincronización. |
| Seguridad | Administración apagada, falta de clave, clave incorrecta, cliente remoto, origen React, host y cabeceras de proxy no permitidos. |
| Swagger/OpenAPI | `/docs` y `/openapi.json` responden; las cuatro rutas declaran la clave y los contratos correctos cuando la instancia aislada habilita administración. Con ella apagada, no aparecen. |
| Compatibilidad | Se ejecutan conjuntamente las pruebas de HU-008, HU-001, HU-003 y HU-005. |

En `tests/test_availability_api.py` se añadieron cuatro funciones que producen **seis casos nuevos**:

1. `test_double_stockout_and_parent_restore_preserve_individual_preferences`: agota la Doble, deshabilita/restaura el padre y demuestra que la Doble conserva su preferencia mientras la Sencilla puede recuperarse. Después restaura la Doble y compara todas las filas con el estado inicial.
2. `test_commercial_sync_changes_reach_admin_and_public_api_without_overwriting_local`, con dos preferencias locales: enlaza la sincronización simulada con la administración, listado y detalle, y verifica ambos sentidos del cambio comercial.
3. `test_real_postgresql_failure_rolls_back_patch_and_releases_catalog_lock`, para producto y variante: provoca una división por cero real después del flush, comprueba rollback, sanitización y liberación del bloqueo, y verifica una operación posterior satisfactoria.
4. `test_availability_openapi_documents_strict_input_and_matching_get_patch_responses`: verifica el cuerpo booleano obligatorio, campos adicionales prohibidos, seguridad y modelos coincidentes entre GET/PATCH.

Los datos de hamburguesa de la fixture tienen precios 89.00 y 119.00; las pruebas resuelven sus IDs dinámicamente. Los datos reales verificados de desarrollo tienen precios 89.00 y 129.00 e IDs 4 y 5. Esta diferencia es deliberada: las pruebas no dependen de IDs o precios de la base real.

### 7.3. Consultas sin N+1

`selectinload` carga las variantes del producto por lotes. El detalle carga las asignaciones junto con sus grupos y después los complementos por lotes. Imágenes y etiquetas se consultan en lotes de hasta 500 variantes.

Las pruebas miden SQL real: para las escalas comprobadas de 1 y 25 se mantienen 4 SELECT en el listado y 6 en el detalle. Al superar un lote, el caso de 501 variantes añadidas comprueba 6 y 9 respectivamente. Esto es crecimiento por lotes, no una consulta extra por cada variante. Los GET administrativos utilizan 2 SELECT para producto y 1 para variante, sin cargar imágenes ni modificadores.

### 7.4. PostgreSQL, API y ausencia de modificaciones indebidas

Se tomó una instantánea de solo lectura antes del Bloque 3 y se comparó al finalizar: **esquema, filas y secuencias de desarrollo permanecieron idénticos**. La huella de esa instantánea fue:

`1faafab82f6acdd95d1574806ab7961ad888ef6aa0e15eeec343dd8e8badc3a8`

La verificación final de claves encontró 0 grupos duplicados y 0 relaciones huérfanas. Se mantienen 3 productos, 5 variantes, 2 grupos, 5 complementos y 4 asignaciones comerciales; también 1 fotografía, 1 etiqueta y 1 asociación variante–etiqueta preexistentes.

En este Bloque 3 se enviaron solicitudes TCP HTTP reales a un Uvicorn temporal en `127.0.0.1`, con sesiones de PostgreSQL forzadas a solo lectura. El servidor temporal se detuvo al finalizar.

| Ruta consultada en este cierre | HTTP | Resultado observado |
|---|---|---|
| `/api/menu` | `200` | 3 productos, 5 variantes, las 5 solicitables. |
| `/api/menu/1` | `200` | Producto solicitable; rango 45.00-45.00. |
| `/api/menu/2` | `200` | Producto solicitable; rango 35.00-49.00. |
| `/api/menu/3` | `200` | Producto solicitable; rango 89.00-129.00. |
| `/docs` | `200` | HTML de Swagger UI disponible; sin interacción manual en navegador. |
| `/openapi.json` | `200` | Contrato público disponible; rutas administrativas ausentes con administración apagada. |

Se comprobó que listado y detalle coinciden en estados, precios, descripción, imagen representativa y etiquetas, y que las galerías y complementos mantienen sus contratos. Los GET administrativos de producto 3 y variante 5 devolvieron `404` con la configuración real deshabilitada. Los GET/PATCH administrativos habilitados se verificaron mediante `TestClient` y PostgreSQL aislado, no escribiendo sobre desarrollo.

Se comprobaron las respuestas y los campos públicos mediante HTTP/ASGI automatizado, según se indica arriba. **No se abrió un navegador ni se pulsó manualmente Authorize o Try it out.** La guía de la sección 8 describe esas acciones pendientes para el usuario.

### 7.5. Sincronización real previamente confirmada

La sincronización real autorizada ya había confirmado la tienda **I.C.E**, sin altas ni bajas. Las cinco variantes recibieron `is_commercially_available=true` y conservaron `is_available=true`. Los precios no cambiaron:

| ID | Variante | Precio anterior y posterior | Local conservada | Comercial antes → después | `can_order` tras sincronizar |
|---|---|---|---|---|---|
| 1 | Papas fritas | 45.00 | `true` | `false → true` | `true` |
| 2 | 355 ml | 35.00 | `true` | `false → true` | `true` |
| 3 | 500 ml | 49.00 | `true` | `false → true` | `true` |
| 4 | Sencilla | 89.00 | `true` | `false → true` | `true` |
| 5 | Doble | 129.00 | `true` | `false → true` | `true` |

La [evidencia del segundo intento autorizado](../../loyverse/hu007_sync_second_attempt_report.json) conserva el resultado confirmado. El [primer intento](../../loyverse/hu007_sync_report.json) se revirtió porque una comprobación operativa comparó como texto dos fechas equivalentes con distinto desplazamiento horario. La corrección comparó objetos `datetime` con zona horaria; se validaron 15 instantes de sincronización. Los contadores de secuencia pudieron avanzar por los UPSERT incluso durante el intento revertido; los IDs existentes no cambiaron.

Estos son hechos de una etapa anterior autorizada, no nuevas sincronizaciones realizadas durante el Bloque 3. En este cierre no se volvió a consultar ni sincronizar Loyverse.

### 7.6. Defectos y correcciones

La revisión final no encontró nuevos defectos de implementación de HU-007. Se completó la cobertura con los seis casos anteriores y se redactó este documento, sin refactorizaciones ni cambios del esquema.

Los defectos resueltos durante la implementación de la historia fueron la mezcla del estado comercial y local, la falta de actualización de `available_for_sale` en variantes existentes y la pérdida de preferencia local durante bajas. La corrección operativa de fechas ocurrió antes de este cierre y no cambió la regla comercial.

## 8. Guía manual de Swagger en Windows

Estas instrucciones están preparadas para una sesión manual local. **El asistente no ejecutó los PATCH siguientes sobre `mitbol_dev`.** Los ejemplos de respuestas son contratos y resultados esperados bajo las condiciones indicadas, no afirmaciones de haber realizado clics en el navegador.

### 8.1. Abrir una consola y generar una clave temporal

Detén cualquier instancia anterior de Uvicorn. En una consola PowerShell de desarrollo, sitúate en el backend:

```powershell
Set-Location 'C:\Proyectos\mitbol\mitbol-backend'
$env:ADMIN_API_KEY = (& .\.venv\Scripts\python.exe -B -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
Set-Clipboard -Value $env:ADMIN_API_KEY
```

La salida de Python queda capturada en una variable de proceso y no se imprime. Se genera una clave URL-safe de longitud válida, independiente de Loyverse. El portapapeles permite pegarla en Swagger sin escribirla en el comando ni en la documentación. No la copies a React, URLs, repositorios, capturas o mensajes.

### 8.2. Habilitar temporalmente la administración en `.env`

Edita únicamente el indicador local en `.env`:

```dotenv
ADMIN_ENABLED=true
```

Conserva los valores de PostgreSQL y Loyverse. La clave generada permanece en `$env:ADMIN_API_KEY` y prevalece sobre cualquier clave anterior de `.env`; no es necesario guardarla en el archivo. `.env` está ignorado por Git.

Si la consola tiene un indicador `ADMIN_ENABLED` heredado, retíralo para que se lea el archivo:

```powershell
Remove-Item Env:ADMIN_ENABLED -ErrorAction SilentlyContinue
```

El proceso que inicia Uvicorn debe ser el de esta consola, para que herede la clave temporal. Los cambios de configuración requieren reiniciar el servidor: no basta con editar `.env` mientras sigue funcionando. Habilitar administración sin una clave válida impide arrancar la aplicación.

### 8.3. Iniciar Uvicorn directamente en loopback

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

No coloques un proxy delante de esta sesión ni publiques el puerto. Usa una consola normal de desarrollo, no una consola de auditoría configurada para forzar PostgreSQL a solo lectura, ya que los PATCH manuales necesitan una transacción de escritura autorizada.

### 8.4. Authorize y comprobaciones iniciales

1. Abre `http://127.0.0.1:8000/docs`.
2. Pulsa **Authorize** y selecciona `LocalAdminKey`.
3. Pega la clave del portapapeles y confirma. Swagger envía `X-Admin-Key`; no va en el JSON ni en la URL.
4. Desde otra consola, después de pegarla, puedes limpiar el portapapeles con `Set-Clipboard -Value ''`.
5. Ejecuta `GET /api/admin/products/3/availability` y conserva su `is_available` original.
6. Ejecuta `GET /api/admin/variants/4/availability` y `GET /api/admin/variants/5/availability`; conserva ambos estados locales y comerciales.
7. Consulta `GET /api/menu` y `GET /api/menu/3` como referencia de precios, fotografías, etiquetas y complementos.

La relación verificada al cerrar esta historia es producto 3, Sencilla 4 y Doble 5. Confírmala en el menú antes de escribir: los IDs podrían ser diferentes en otra base. No sustituyas un UUID de Loyverse en estos parámetros.

Si las condiciones comerciales o locales actuales difieren de las descritas, interpreta `can_order` con la regla de cuatro condiciones. No cambies estados comerciales manualmente para forzar el ejemplo.

### 8.5. Agotar temporalmente el producto

Selecciona **Try it out** en `PATCH /api/admin/products/3/availability`, introduce `product_id=3` y ejecuta:

```json
{"is_available": false}
```

Debe responder `200`, con `product_id=3`, `is_available=false` y `can_order=false`. Consulta de nuevo las variantes 4 y 5: sus valores locales individuales deben seguir siendo los guardados, y sus campos comerciales no deben cambiar.

Ejecuta `GET /api/menu` y `GET /api/menu/3`. El producto y las dos variantes continúan visibles, pero sus `can_order` son falsos. Los complementos tampoco pueden seleccionarse. Los precios individuales siguen en 89.00 y 129.00; el rango de referencia es 89.00–129.00.

### 8.6. Restaurar el producto

Si su estado original era verdadero, ejecuta en el mismo PATCH:

```json
{"is_available": true}
```

Si originalmente era falso, restaura ese valor y no lo actives solo para reproducir el ejemplo. Comprueba GET administrativo y menú. Restaurar el producto habilita únicamente las variantes cuyas condiciones propias lo permitan; una variante previamente agotada conserva su estado local falso.

### 8.7. Agotar solo la Doble

Para el escenario verificado de ambas variantes habilitadas y producto disponible, ejecuta `PATCH /api/admin/variants/5/availability`:

```json
{"is_available": false}
```

La respuesta debe conservar `product_variant_id=5`, `product_id=3` e `is_commercially_available=true`, y mostrar `is_available=false`, `can_order=false`. Comprueba:

1. `GET /api/admin/variants/5/availability`: la Doble quedó agotada localmente.
2. `GET /api/admin/variants/4/availability`: la Sencilla no cambió y sigue solicitable si todas sus condiciones siguen siendo verdaderas.
3. `GET /api/admin/products/3/availability`: el producto aún puede pedirse mediante la Sencilla.
4. `GET /api/menu` y `GET /api/menu/3`: ambas variantes siguen visibles; la Sencilla tiene `can_order=true` y la Doble `false`.
5. En el detalle, los complementos de la Doble tienen `can_order=false`; los de la Sencilla dependen de su propia activación comercial.
6. El precio de la Doble continúa en 129.00. El rango del producto pasa a 89.00–89.00 y `has_price_range=false` porque solo la Sencilla es solicitable.

El valor `is_available=true` del producto no reemplaza las condiciones de sus variantes.

### 8.8. Restaurar la Doble y el estado inicial

Si la Doble tenía disponibilidad local verdadera al comenzar, ejecuta en el PATCH de variante 5:

```json
{"is_available": true}
```

Restaura el valor realmente guardado si era diferente. Después consulta las tres disponibilidades administrativas y el menú. Con las condiciones comerciales verificadas de cierre, ambas variantes vuelven a tener `can_order=true` y el rango vuelve a 89.00–129.00.

No hace falta cambiar la Sencilla para probar la Doble. No ejecutes sincronizaciones ni cambies fotos, etiquetas, descripciones o precios como parte de esta demostración.

### 8.9. Comprobar validación y repetición

Repetir el PATCH con el mismo booleano debe devolver `200` y mantener el resultado. Un cuerpo como el siguiente debe devolver `422`, sin cambiar el estado:

```json
{"is_available": "false"}
```

También son rechazados un campo adicional o `null`. Un ID inexistente pero válido responde `404`; `0`, un negativo o un texto responden `422`. Revisa el código HTTP y el campo `detail` en Swagger.

### 8.10. Desactivar la administración al terminar

1. Restaura primero todos los valores locales originales y compruébalos mediante GET.
2. Detén Uvicorn con `Ctrl+C`.
3. Deja nuevamente en `.env`:

```dotenv
ADMIN_ENABLED=false
```

4. Retira la clave temporal y limpia el portapapeles:

```powershell
Remove-Item Env:ADMIN_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ADMIN_ENABLED -ErrorAction SilentlyContinue
Set-Clipboard -Value ''
```

5. Si necesitas dejar la API pública funcionando, vuelve a iniciarla con el comando de loopback anterior. Comprueba que las rutas administrativas respondan `404`, que no aparezcan en OpenAPI y que el menú siga accesible sin credenciales.

Una respuesta `401` suele indicar clave ausente o incorrecta; `403` requiere revisar cliente, host, origen y cabeceras de proxy; `409` indica otra operación de catálogo en curso. No se soluciona una restricción comercial enviando más campos en el PATCH.

## 9. Quince preguntas técnicas del profesor

1. **¿Por qué separaron disponibilidad local y comercial?** Porque son decisiones independientes. Un único booleano perdía la causa del bloqueo y permitía que la sincronización sobrescribiera preferencias locales o ignorara cambios comerciales.

2. **¿Qué significa `can_order`?** Es el permiso efectivo calculado. En una variante exige producto activo y disponible, variante disponible localmente y variante habilitada comercialmente. No es una columna adicional.

3. **¿Qué pasa si MITBOL permite vender pero Loyverse no?** La preferencia local puede ser verdadera, pero el estado comercial falso hace que `can_order=false`. El PATCH no puede modificar ese estado comercial.

4. **¿Qué ocurre si se agota solamente la Doble?** Se cambia su `is_available` a falso. La Sencilla mantiene su estado y el producto puede seguir siendo solicitable a través de ella. Los complementos de la Doble quedan deshabilitados en ese contexto.

5. **¿Cómo conservan los cambios locales al sincronizar?** El UPSERT de variantes existentes excluye `is_available` del UPDATE. Las bajas cambian el campo comercial. El resto del enriquecimiento sigue las reglas de preservación de HU-005.

6. **¿Para qué sirve Alembic?** Versiona y aplica cambios controlados del esquema. Permite saber qué revisión está instalada y comprobar si los modelos ORM requieren operaciones adicionales. Aquí la revisión es `20261102_0003`.

7. **¿Por qué `DEFAULT false`?** Porque la migración no conocía el estado comercial real. El valor conservador bloquea solicitudes hasta verificar Loyverse, sin inferir disponibilidad comercial a partir de la preferencia local histórica.

8. **¿Qué significa UPSERT?** Insertar o actualizar según una identidad única. El UUID de Loyverse decide si una entidad ya existe; `RETURNING` recupera el ID interno y se rechaza que cambie de padre.

9. **¿Por qué un PATCH puede ser idempotente?** Porque asigna un valor concreto. Repetir `is_available=false` deja el mismo estado; no alterna la bandera ni crea registros.

10. **¿Por qué necesitan un bloqueo transaccional?** Para coordinar sincronizaciones y cambios administrativos que afectan el mismo catálogo. Ambos solicitan la misma clave; quien no la obtiene se detiene antes de escribir. Se libera al finalizar la transacción.

11. **¿Cómo validan la entrada?** Pydantic exige un booleano JSON obligatorio y rechaza campos adicionales. FastAPI valida los IDs positivos dentro del rango admitido. Los campos comerciales no forman parte del cuerpo permitido.

12. **¿Qué diferencia hay entre `is_active` e `is_available`?** En productos, `is_active` representa vigencia comercial sincronizada y `is_available` una preferencia local. Un producto inactivo no se publica; uno agotado localmente permanece visible. Las variantes utilizan una columna comercial separada y no tienen `is_active`.

13. **¿Cómo se comportan los complementos?** Su `can_order` requiere el permiso efectivo de la variante y su propio `is_active`. La misma opción compartida puede estar habilitada desde una variante y deshabilitada desde otra, sin modificar su precio o identidad.

14. **¿Cómo demuestran la disponibilidad en Swagger?** Habilitan temporalmente la administración local con una clave independiente, consultan el estado original, ejecutan el PATCH y comparan GET administrativo y menú público. Después restauran el valor inicial y desactivan la administración.

15. **¿Por qué no habilitar esta administración en producción?** La clave es compartida y no identifica empleados ni roles. La protección fue diseñada para desarrollo local directo. Faltan autenticación individual, permisos definitivos y las decisiones de despliegue e integración correspondientes.

## 10. Resumen oral para una exposición de aproximadamente tres minutos

«HU-007 permite al establecimiento controlar temporalmente la disponibilidad de sus productos sin cambiar el catálogo comercial de Loyverse. Por ejemplo, si se agota la hamburguesa Doble, podemos deshabilitar esa presentación y conservar disponible la Sencilla.

El problema inicial era que una sola bandera mezclaba dos responsabilidades: la decisión comercial de Loyverse y la disponibilidad local. Eso podía borrar una preferencia local al procesar una baja o impedir que un cambio comercial se reflejara correctamente.

Separamos ambas responsabilidades con una nueva columna en variantes: is_commercially_available. MITBOL conserva is_available para la preferencia local. En productos mantenemos is_active como estado comercial y is_available como disponibilidad local.

La selección se decide con can_order. Una variante necesita que las cuatro condiciones sean verdaderas: producto activo, producto disponible, variante disponible y variante comercialmente habilitada. El producto puede solicitarse si alguna variante cumple esa regla. Los complementos requieren, además, su propia activación comercial.

Creamos cuatro operaciones administrativas: GET y PATCH de disponibilidad para productos y variantes. Los PATCH reciben únicamente un booleano is_available. Validamos estrictamente el JSON y los IDs. Reutilizamos la protección local de HU-005 y dejamos la administración apagada por defecto.

La lógica de disponibilidad vive en un módulo compartido. El menú y la administración utilizan las mismas reglas. Los agotados siguen visibles, con can_order falso. Los precios individuales no cambian; el rango considera las variantes solicitables y mantiene precios de referencia si ninguna puede pedirse.

La sincronización actualiza solo los campos comerciales de variantes existentes y conserva sus preferencias locales. El UPSERT mantiene las identidades. Una baja comercial no borra la variante ni sus fotos o etiquetas. Para evitar carreras, sincronización y PATCH comparten un bloqueo transaccional. Si algo falla, se revierte la transacción.

La migración inició la nueva columna en falso para no habilitar variantes cuyo estado comercial era desconocido. Después se realizó una sincronización real autorizada con la tienda I.C.E. y las cinco variantes recibieron su habilitación comercial, manteniendo sus precios y datos locales.

En el cierre ejecutamos 452 pruebas, sin fallos, errores ni omisiones. Todas las escrituras de prueba se hicieron en PostgreSQL aislado. También comprobamos Alembic, claves, consultas por lotes y que desarrollo permaneciera intacto. La historia queda completa como backend de desarrollo local; la autenticación individual, los roles y la integración administrativa con React corresponden a etapas posteriores».

## 11. Limitaciones y pendientes antes de producción

- **Autenticación definitiva:** la clave compartida no representa usuarios, empleados, sesiones ni roles. No existe atribución individual de cambios en HU-007.
- **Entorno local:** el acceso administrativo rechaza clientes remotos, proxies y orígenes ajenos al Swagger local. No constituye un despliegue administrativo de producción.
- **React pendiente:** el menú público mantiene su contrato, pero no se implementó un panel administrativo ni se distribuyó una clave al frontend.
- **Estado comercial observado:** el menú lee PostgreSQL; no consulta Loyverse en cada GET. Refleja el último catálogo válido sincronizado y la configuración local actual, no inventario en tiempo real.
- **Sin inventario automático:** agotamiento, reposición y restauración local son manuales. No se añadieron notificaciones, horarios ni programación.
- **Estado comercial no verificado:** el booleano falso no distingue entre «todavía no verificado» y «Loyverse lo deshabilitó». Ambos bloquean solicitudes. No se agregó un tercer estado ni otra columna.
- **Histórico ambiguo:** no se reconstruye automáticamente la causa de los antiguos valores locales falsos. Se preservan para revisión administrativa.
- **Lectura y pedidos futuros:** `can_order` comunica la disponibilidad al consultar. No reserva existencias. Un futuro módulo de pedidos deberá volver a validar las condiciones al aceptar una solicitud.
- **Bloqueo cooperativo:** coordina a los servicios que utilizan la clave común; no bloquea cualquier edición SQL manual ajena a ese protocolo. Mientras una sincronización ocupa la clave, un PATCH puede recibir `409`.
- **Fallos de transporte al confirmar:** ante una pérdida de conexión durante un commit, el cliente puede no conocer el resultado definitivo. Debe consultar el estado mediante GET antes de decidir un nuevo envío; la asignación idempotente facilita esa comprobación.
- **Opciones limitadas al modelo aprobado:** no existe disponibilidad local individual de complementos ni activación propia del grupo. No se implementaron esas funcionalidades fuera del alcance.
- **Verificación manual:** no se realizaron clics en un navegador ni demostraciones de agotamiento sobre los datos reales. Quedan disponibles las instrucciones manuales y la evidencia automatizada; no son una validación de la futura interfaz React.

**Conclusión de alcance:** HU-007 quedó completa a nivel de backend para desarrollo local. Las limitaciones anteriores delimitan la administración definitiva y funcionalidades de otras historias; no se implementaron como parte de este cierre.
