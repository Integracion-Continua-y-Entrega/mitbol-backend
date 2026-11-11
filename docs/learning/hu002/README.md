# HU-002 — Buscar y filtrar productos

## Estado y alcance

Backend de búsqueda por nombre y filtros por etiquetas implementado y verificado.
Última validación: 10 de noviembre de 2026, con PostgreSQL 18.6.

La integración con frontend y la aceptación del equipo siguen pendientes.
El filtrado por categorías queda fuera de esta entrega: el modelo vigente no
contiene una relación persistente entre productos y categorías. Si continúa
siendo un criterio obligatorio del backlog, HU-002 no debe cerrarse completa
hasta resolver o acordar formalmente ese alcance.

Los requisitos proceden de `Por hacer .pdf`, páginas 21–23, y del
`mitbol - Prompt Maestro v2.1.pdf`, apartados 4 y 12.1, aportados para el proyecto.

## Contrato de la API

### GET /api/menu

Endpoint público. Conserva el contrato de respuesta de HU-001 y añade dos
parámetros de consulta opcionales:

| Parámetro | Tipo | Comportamiento |
|---|---|---|
| `q` | Texto | Coincidencia parcial por nombre del producto, sin distinguir mayúsculas. Ignora espacios exteriores. |
| `tag_id` | Entero positivo repetible | Incluye productos asociados a cualquiera de las etiquetas seleccionadas. |

Ejemplos:

```http
GET /api/menu
GET /api/menu?q=doble
GET /api/menu?tag_id=7
GET /api/menu?tag_id=7&tag_id=8
GET /api/menu?q=doble&tag_id=7&tag_id=8
```

Reglas:

- `q` ausente, vacío o compuesto solo por espacios no aplica búsqueda por nombre.
- `%` y `_` se buscan como caracteres literales, no como comodines SQL.
- La búsqueda consulta `Products.name`; no incluye descripciones ni nombres de variantes.
- No se garantiza equivalencia entre texto con y sin acentos.
- Entre etiquetas se aplica OR: basta una coincidencia en alguna variante.
- Entre nombre y etiquetas se aplica AND: deben cumplirse ambos filtros.
- Sin `tag_id` no se filtra por etiquetas.
- Los IDs repetidos no duplican productos.
- Un ID positivo inexistente no genera coincidencias por sí solo. Si se combina
  con otro existente, se conservan las coincidencias de este último.
- Cero, negativos, texto y valores vacíos en `tag_id` producen HTTP 422.
- Sin coincidencias se devuelve HTTP 200 con la respuesta habitual vacía:

```json
{
  "currency": "MXN",
  "products": []
}
```

Los productos siguen ordenados por `product_id`. Coincidir en una variante
selecciona el producto completo; se conservan todas sus variantes y sus reglas
de precios, imágenes, etiquetas y disponibilidad.

Los productos comercialmente inactivos continúan excluidos. Los agotados
permanecen visibles con `can_order: false` cuando corresponda. Filtrar no cambia
los valores almacenados ni habilita la compra de productos no disponibles.

### GET /api/menu/tags

Endpoint público que devuelve todas las etiquetas registradas, incluidas las
no asignadas a variantes. El resultado se ordena por nombre y luego por ID.

```json
[
  {"tag_id": 7, "name": "sin gluten"},
  {"tag_id": 8, "name": "vegano"}
]
```

Sin etiquetas registradas devuelve HTTP 200 y `[]`. Solo expone `tag_id` y
`name`; no incluye conteos ni categorías. Como incluye etiquetas sin asignaciones,
seleccionar una etiqueta del listado puede producir cero productos.

La ruta fija `/menu/tags` se registra antes de `/menu/{product_id}` para evitar
que `tags` se interprete como un identificador de producto.

### Errores

| HTTP | Situación |
|---|---|
| `422` | Parámetros de entrada inválidos en la consulta del menú. |
| `500` | Error interno de consulta o validación de datos del catálogo. |
| `503` | PostgreSQL temporalmente no disponible o tiempo de espera agotado. |

Los errores públicos usan `{"detail": "mensaje"}` y no exponen SQL ni credenciales.
Ambos endpoints son de lectura, no requieren login y no consultan Loyverse.

## Implementación

```text
GET /api/menu
  → FastAPI valida q y la lista tag_id
  → menu_service.get_menu construye la consulta pública
  → apply_name_search añade la condición por nombre
  → apply_tag_filter añade IN (SELECT ... JOIN ProductTags ...)
  → SQLAlchemy ejecuta la consulta
  → se reutilizan el enriquecimiento y MenuResponse de HU-001
```

El filtro relaciona `ProductTags → ProductVariants → Products`. La subconsulta
puede encontrar varias variantes del mismo producto; el `IN` exterior comprueba
pertenencia sin multiplicar las filas de productos. Los valores se transmiten
como parámetros SQL y el filtrado se realiza en PostgreSQL.

| Archivo | Responsabilidad |
|---|---|
| [app/api/menu.py](../../../app/api/menu.py) | Parámetros HTTP, listado de etiquetas y traducción de errores. |
| [menu_service.py](../../../app/services/menu_service.py) | Composición de filtros y reutilización de la respuesta del menú. |
| [menu_search.py](../../../app/services/menu_search.py) | Búsqueda parcial por nombre con escape de comodines. |
| [menu_tag_filter.py](../../../app/services/menu_tag_filter.py) | Subconsulta de productos asociados a etiquetas. |
| [menu_tags.py](../../../app/services/menu_tags.py) | Lectura ordenada de etiquetas y conversión a `MenuTag`. |
| [tests/test_menu.py](../../../tests/test_menu.py) | Contratos HTTP, validación e integración con PostgreSQL aislado. |

Se reutilizan los modelos y schemas existentes. No requiere tablas nuevas,
migraciones, dependencias adicionales ni cambios de configuración para arrancar
el backend. No modifica sincronización, precios ni datos de Loyverse.

## Validación

| Criterio | Evidencia |
|---|---|
| Búsqueda parcial, mayúsculas y espacios | Casos parametrizados de búsqueda con PostgreSQL. |
| `%` y `_` literales | Casos de nombres que contienen esos caracteres. |
| Sin coincidencias y listado sin filtros | Respuestas HTTP 200 y contrato conservado. |
| Filtros OR y combinación con nombre | Doce escenarios parametrizados de etiquetas. |
| Productos sin duplicados | Producto con dos variantes que comparten etiqueta e IDs repetidos. |
| Variantes completas y precios conservados | Prueba con una variante que no tiene la etiqueta seleccionada. |
| Disponibilidad conservada | Agotados visibles y productos comercialmente inactivos excluidos. |
| Listado de etiquetas | Orden por nombre, etiquetas sin asignación y tabla vacía. |
| Entradas inválidas y OpenAPI | Lista opcional de enteros positivos; rechazo antes de ejecutar consultas. |

Resultados de la última validación:

- Menú: **117 pruebas aprobadas**, sin fallos ni omisiones.
- Suite completa: **1,426 pruebas aprobadas**, sin fallos ni omisiones, en
  657.69 segundos. Dos avisos de deprecación de dependencias existentes.
- PostgreSQL 18.6; clústeres temporales detenidos al finalizar.

El laboratorio no carga `.env` ni utiliza `mitbol_dev`, Neon o Loyverse real.
Inicia sus propios clústeres temporales con puertos aleatorios en loopback;
no necesita utilizar el servicio PostgreSQL habitual del desarrollador.

Con el entorno virtual y los binarios de PostgreSQL 18 disponibles, ejecutar
desde la raíz del repositorio:

```powershell
# Ajustar a la carpeta de binarios de la instalación local.
$env:MITBOL_TEST_PG_BIN = 'D:\PostgreSQL\18\bin'
.\venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_menu.py -q

# Suite completa, con un clúster adicional para las pruebas de permisos.
.\venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader tests -q
```

## Integración con frontend y aceptación

Frontend debe enviar `q` como query parameter y repetir `tag_id` para filtros
múltiples. Al limpiar la búsqueda o retirar filtros, debe omitir los parámetros
correspondientes; no enviar `tag_id=` para representar una lista vacía.

Debe mostrar el estado sin coincidencias, conservar la presentación de agotados
y evitar ofrecer categorías sin soporte del backend. La respuesta de productos
mantiene la estructura de HU-001; el listado de etiquetas es un array directo.

Antes de cerrar la entrega compartida, comprobar desde frontend búsqueda,
limpieza, selección y retirada de filtros, ausencia de coincidencias y
disponibilidad. Acordar explícitamente el alcance pendiente de categorías.
El código verificado localmente no acredita despliegue ni activación en Render.
