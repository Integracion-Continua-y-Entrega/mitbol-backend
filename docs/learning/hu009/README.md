# HU-009 — Sincronizar descuentos comerciales

Estado: tipos, lectura HTTP, validación y preparación por tienda implementados
y probados con datos sintéticos. Sin persistencia, endpoints
o sincronización operativa conectados. El modelo de Discount está definido y
su mapeo fue comprobado sin conexión; todavía no está registrado ni migrado.
Su revisión preparada está en `migrations/pending` y se probó en una cadena
temporal de Alembic, sin modificar el head habitual `20261106_0005`.
No se ha consultado Loyverse real ni
modificado una base de datos para esta historia.

## Objetivo y alcance

Fuente: `Por hacer .pdf`, páginas 26–28. Incorporar descuentos de Loyverse a
MITBOL para su consulta administrativa y uso posterior por promociones.

Incluye lectura de identificador, nombre, tipo y valor cuando exista;
persistencia sin duplicados, actualización transaccional y conservación de
datos válidos si una consulta falla. Los tipos variables deben admitir valor
nulo, sin confundirlo con un descuento de valor cero.

No incluye crear descuentos en Loyverse, campañas, aplicación de beneficios a
pedidos ni acumulación de promociones. La administración de ejecuciones de
sincronización corresponde a HU-010, no a esta entrega.

## Contrato del proveedor y decisiones pendientes

Referencia: [API oficial de Loyverse](https://developer.loyverse.com/docs/),
apartado Discounts. El cliente restringe las rutas permitidas e incorpora
`get_discounts(include_deleted=...)`. Ese recurso no documenta `limit/cursor`:
se realiza una lectura sin esos parámetros y se rechaza paginación inesperada,
en lugar de entregar silenciosamente una colección incompleta. Se reutilizan
reintentos, control de errores y decodificación Decimal existentes.

Hay tipos de porcentaje/importe fijos y variables, además de descuentos por
puntos. Reconocer un identificador no significa que esté autorizado para
aplicarse o importarse como un descuento compatible del MVP. El tratamiento de
puntos queda pendiente de una decisión explícita; no se convertirá a dinero.
La capa en memoria los reconoce con valor fijo nulo, conservando su tipo;
eso no declara elegibilidad para promociones o persistencia definitiva.

Al diseñar la transformación se debe distinguir:

- Campo monetario y campo porcentual según el tipo, usando Decimal.
- Valor ausente de variable frente a cero; nunca inventar un valor.
- Baja comercial mediante datos del proveedor y selección de tienda.
- Restricción de acceso frente a activación comercial.
- Tiendas donde aplica frente al alcance producto/ticket: no son equivalentes.

Decisión acordada: guardar `scope="unknown"` cuando no exista evidencia del
alcance. No inferirlo a partir de `stores` ni usarlo para calcular promociones.
La columna sigue siendo obligatoria y sin default: el proceso de importación
asignará ese valor explícitamente. Un alcance conocido solo se guardará con
evidencia y un mapeo validado, no por suposición.

## Persistencia y dependencias

La tabla `Discounts` aparece en el diagrama aprobado. Su modelo y migración
están preparados, pero pendientes de integración global. Esto no acredita qué tablas
existan en una base externa: antes de aplicar una migración se verificará su
esquema en el destino autorizado.

Se prevé incorporar al código esa tabla del diseño, sin ampliar el diagrama
con tablas de historial o campañas. Las migraciones se probarán primero en
PostgreSQL temporal, sin ejecutarlas en `mitbol_dev` o Neon. También habrá que
revisar qué verificadores, pruebas y permisos esperan revisiones/tablas exactas.

La sincronización conservará identidades locales y `loyverse_id` único. Se
validará la descarga completa antes de confirmar cambios; una respuesta inválida
o fallida no debe borrar descuentos almacenados ni producir una importación
parcial. Las bajas deberán apoyarse en evidencia explícita del proveedor.

## Plan de implementación

| Bloque | Resultado |
|---|---|
| 1 | Tipos de descuento reconocidos y clasificación de variables. |
| 2 | Validación y transformación de respuestas sintéticas a datos internos. |
| 3 | Modelo Discounts y migración del diseño, probados en base temporal. |
| 4 | Lectura del proveedor con HTTP simulado y paginación/error verificados. |
| 5 | Persistencia transaccional e idempotente; bajas y errores sin pérdida de datos. |
| 6 | Consulta administrativa protegida y contrato para frontend. |
| 7 | Regresiones, documentación final, PR dependiente e integración. |

El orden puede ajustarse cuando se cierre el contrato de tipos/alcance y la
revisión de esquema. Los bloques de seguridad SQL y migraciones requieren
pruebas específicas; no se resuelven abriendo permisos generales.

## Git y publicación

Rama: `feature/hu-009-discount-sync`, basada en HU-006 (`33b132e`). HU-006 sigue
pendiente de integración/publicación; esta rama no cambia ese estado.

El PR inicial de HU-009 debe tener como base `feature/hu-006-product-highlights`
para revisar únicamente su diferencia. Tras integrar HU-006 se actualizará la
rama y se cambiará la base del PR a `develop`, comprobando el diff y CI.
No fusionar HU-009 dentro de HU-006 para publicar ambas sin revisión separada.

La aceptación completa requiere validar la consulta administrativa desde
frontend. No se ejecutará una sincronización real ni un despliegue como parte
de los ejercicios iniciales.

## Piezas implementadas y límites de validación

| Archivo | Función |
|---|---|
| [discount_rules.py](../../../app/services/discount_rules.py) | Enum y clasificación exacta de variables. |
| [loyverse_client.py](../../../app/services/loyverse_client.py) | Lectura de descuentos y manejo de errores HTTP. |
| [discount_transform.py](../../../app/services/discount_transform.py) | Validación completa de registros y lotes, sin E/S. |
| [discount_probe.py](../../../app/services/discount_probe.py) | Snapshot inmutable, entregado solo después de validar la colección. |

El registro intermedio conserva ID y tiendas normalizados como UUID, nombre,
tipo, valor, restricción de acceso, fechas del proveedor y baja explícita.
No transforma `restricted_access` en estado inactivo ni deriva scope de tiendas.
Regla acordada: `stores=[]` aplica a todas las tiendas; una lista con IDs limita
la pertenencia. Se valida el ID seleccionado incluso con lista vacía. El mapeo
guarda `scope="unknown"` explícitamente y solo activa un registro si pertenece
a la tienda y no tiene baja explícita. Conserva Decimal/None y normaliza la
fecha de sincronización a UTC, sin incluir IDs locales ni created_at.

Los valores fijos usan el campo correspondiente a su tipo, con los límites
documentados por el proveedor y sin redondeo. La validación requiere números
exactos del cliente (Decimal o enteros), rechaza booleanos/strings/floats y
no sustituye importes inválidos por cero. Variables y puntos no adquieren un
valor fijo. Un registro no fijo con valor fijo inesperado se rechaza.

Se requieren IDs, nombres, listas de tiendas, restricciones booleanas y fechas
con zona horaria válidos. Fechas se normalizan a UTC. Un ID repetido o un registro
inválido rechaza el lote completo; no hay inserciones ni borrados parciales.
Los mensajes no incluyen payload, token o datos HTTP del proveedor.

Los campos obligatorios y la validación son deliberadamente conservadores:
si una respuesta omite metadatos o cambia su contrato, se detiene su preparación
en lugar de inventar datos. Los ejemplos documentados se contrastaron con mocks,
no con una cuenta real. Un snapshot vacío no autoriza eliminar descuentos.

Pruebas principales: [tipos](../../../tests/test_discount_rules.py),
[transformación](../../../tests/test_discount_transform.py) y
[HTTP](../../../tests/test_discounts_http.py). El cliente y las transformaciones
anteriores del catálogo se incluyen en la comprobación de regresiones.

```powershell
$env:APP_ENV = 'test'
$env:DATABASE_URL = 'postgresql+psycopg2://mitbol_ci@127.0.0.1:1/mitbol_unused'
$env:AUTH_ENABLED = 'false'
$env:ADMIN_ENABLED = 'false'
.\venv\Scripts\python.exe -B -m pytest tests/test_discount_rules.py tests/test_discount_transform.py tests/test_discounts_http.py tests/test_loyverse_client.py tests/test_catalog_probe.py tests/test_catalog_transform.py -q -p no:cacheprovider
```

Esta selección no abre PostgreSQL. Los transportes HTTP se simulan con RESPX.
El modelo y la migración están preparados y probados de forma aislada; su
integración global, la persistencia y la consulta administrativa siguen
pendientes. Estas pruebas no acreditan HU-009 completa.

Última verificación, 11 de noviembre de 2026: **191 pruebas aprobadas** en
18.13 segundos, sin fallos ni omisiones. Incluye las suites de descuentos
y regresiones del cliente, diagnóstico y transformación del catálogo existente.
Incluye la preparación de lotes, el mapeo a columnas y la pertenencia por tienda con
lista vacía, IDs explícitos y baja del proveedor. No se ejecutó la suite
PostgreSQL completa ni se usaron credenciales reales en esta verificación.

Validación posterior de esquema: **16 pruebas aprobadas** en PostgreSQL 18
temporal para `test_discount_migration.py` y `test_migrations.py`. La revisión
`20261111_0006` se probó copiándola a una cadena temporal: crea solo Discounts,
conserva datos anteriores, respeta unicidad/nulos y revierte solo esa tabla.
El modelo se contrastó en un proceso separado para no alterar los perfiles
históricos durante la ejecución de pruebas.

La revisión preparada no aparece en `alembic upgrade head` normal. Antes de
activarla y registrar Discount habrá que adaptar coherentemente fixtures,
compatibilidad de revisiones, perfiles SQL y verificadores de tablas exactas.
La generación/prueba del cambio no acredita esa integración global ni permite
aplicarlo en una base real. No se ejecutó en `mitbol_dev` o Neon.

Validación del UPSERT: **17 pruebas aprobadas** en 7.48 segundos para las
suites de migraciones, sobre PostgreSQL 18 temporal. La escritura por
`loyverse_id` conserva identidad y creación local al repetir registros, permite
pasar un valor fijo a NULL, actualiza estado y fecha, y preserva un alcance
conocido si llega `unknown`. Un alcance explícito nuevo sí reemplaza el anterior.
La construcción SQL está implementada en `discount_statements.py`; aún no está
conectada a una sincronización real. La preparación de lotes por tienda en
`discount_batch.py` devuelve una tupla de filas con una fecha común; conserva
registros excluidos o borrados como inactivos y admite colecciones vacías.
