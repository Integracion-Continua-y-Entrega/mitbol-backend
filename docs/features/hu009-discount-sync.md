# HU-009 — Descuentos comerciales de Loyverse

El backend sincroniza descuentos existentes, conserva su identidad local y
expone su consulta administrativa. La revisión Alembic `20261111_0006` añade
`Discounts` y sucede a `20261106_0005`. FastAPI no crea ni migra tablas al iniciar.

No crea descuentos en Loyverse, calcula beneficios o modifica pedidos. La
administración de ejecuciones y su historial corresponden a HU-010. Esta
entrega no incluye pantalla de frontend ni activa servicios externos.

## Reglas de importación

- `loyverse_id` es la identidad única. Repetir una importación no duplica filas
  ni reemplaza `discount_id` o la fecha de creación local.
- Los tipos fijos conservan su importe o porcentaje exacto como Decimal.
  Variables y `DISCOUNT_BY_POINTS` conservan `value=null`. Los puntos se
  almacenan solo para consulta; no se convierten a dinero ni se aplican.
- Regla acordada para tiendas: una lista `stores` vacía aplica a todas; una
  lista con IDs aplica solo a esas tiendas. Fuera de la tienda elegida se
  almacena el registro como inactivo. Una baja explícita siempre lo desactiva.
- `restricted_access` no determina activación. `is_active` tampoco indica
  elegibilidad para aplicar promociones.
- Si no hay evidencia del alcance producto/ticket, se guarda `scope="unknown"`.
  Las tiendas no permiten deducir ese alcance. Una actualización desconocida
  conserva un alcance conocido que ya estuviera almacenado.
- El lote completo se valida antes de escribir. Identificadores duplicados,
  fechas sin zona o importes inválidos rechazan el lote; no se sustituyen por
  cero. Una colección vacía o una ausencia no autoriza eliminar registros.
- Los cambios se confirman en una sola transacción. Se comparte el bloqueo
  comercial con el catálogo para evitar importaciones concurrentes en otro
  orden. Un error previo al commit revierte el lote completo.

La lectura usa `GET /discounts` de la [API de Loyverse](https://developer.loyverse.com/docs/),
incluye bajas con `show_deleted=true` y no inventa paginación para ese recurso.
Una respuesta con cursor inesperado se rechaza para no importar un lote truncado.
No se ha contrastado esta entrega con una cuenta real del proveedor.

## Consulta administrativa

`GET /api/admin/discounts?limit=50&offset=0`

Se publica junto con el catálogo administrativo: `ADMIN_ENABLED` para el modo
local legado, o `AUTH_ENABLED` y `AUTH_CATALOG_ENABLED` para sesiones
individuales. Este último exige `catalog:read`, disponible para Admin y Staff
activos. No hay un endpoint público de descuentos ni endpoint web de sincronización.

`limit` admite 1–100, por defecto 50; `offset` es un entero no negativo.
La respuesta es una lista ordenada por `discount_id` ascendente e incluye
inactivos. Una tabla o página vacía devuelve `[]`.

```json
[
  {
    "discount_id": 1,
    "loyverse_id": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
    "name": "Descuento comercial",
    "discount_type": "FIXED_AMOUNT",
    "value": "35.25",
    "scope": "unknown",
    "is_active": true,
    "synced_at": "2026-11-12T12:00:00Z",
    "created_at": "2026-11-12T12:00:00Z"
  }
]
```

Los valores decimales se serializan como cadenas exactas. `value` y `synced_at`
son obligatorios en el contrato aunque admiten null. Errores: 401 sin sesión,
403 sin permiso, 422 por paginación inválida, 503 si falta la tabla o no está
disponible la base, y 500 seguro ante fallos inesperados. No se exponen SQL o secretos.

## Operación manual

`python -m app.cli.sync_discounts` usa exclusivamente variables del proceso;
no carga `.env`. Requiere `DATABASE_URL` del operador, `APP_ENV` y
`LOYVERSE_STORE_ID`. El UUID pasado en `--store-id` debe coincidir con esa
variable. La base debe estar en `20261111_0006` y ejecutar PostgreSQL 18.

Con las variables configuradas, sustituir los valores de ejemplo:

```powershell
.\venv\Scripts\python.exe -B -m app.cli.sync_discounts --expected-database mitbol_dev --store-id UUID_DE_LA_TIENDA --preflight
```

El preflight es el modo predeterminado. Solo lee revisión, identidad, permisos
y cantidad de descuentos; no llama a Loyverse ni consume secuencias. No necesita
token. Si falla, no se inicia la descarga.

La ejecución requiere además `LOYVERSE_ACCESS_TOKEN` en el entorno y cambiar
`--preflight` por `--execute`. El resultado informa registros procesados, no
cuántos fueron nuevas inserciones. Ante un error de conexión o commit se debe
comprobar el estado antes de repetir; no se promete ausencia de commit si la
conexión se pierde al confirmarlo.

Cloud requiere `APP_ENV=cloud`, `EXPECTED_DATABASE`, `EXPECTED_DATABASE_HOST`
y `--expected-host` coincidentes, endpoint directo sin pooler y TLS
verify-full/channel-binding. La terminal del operador tiene su propia
configuración: no se cambian las credenciales del Web Service público.
El token Loyverse nunca debe ponerse en React o en Render para servir consultas.

El operador necesita SELECT de `public.alembic_version` y de las columnas de
Discounts, INSERT/UPDATE de las columnas comerciales y USAGE en la secuencia;
no necesita DELETE. El rol web
`mitbol_admin_writer` solo recibe SELECT por columna de Discounts. El lector
público no recibe acceso a esta tabla. Los perfiles históricos del catálogo
continúan limitados a sus ocho tablas; el registro ORM actual tiene nueve.

## Validación e integración

Las pruebas cubren validación/HTTP del proveedor simulado, importación repetida,
bajas, puntos, conservación de datos ante fallos, bloqueo, revisión/destino,
consulta paginada, login real y ACL de PostgreSQL con roles independientes.
El laboratorio usa bases y clústeres temporales; no consulta `.env`, Loyverse
real, `mitbol_dev` ni Neon.

Verificación final del 12 de noviembre de 2026: **1,662 pruebas aprobadas**,
sin fallos, errores u omisiones, en 592.41 segundos sobre Windows, Python 3.13
y PostgreSQL 18. Se observaron dos avisos de deprecación de Starlette/TestClient.
Comando equivalente al de CI:

```text
python -B scripts/test_auth_phase1.py --with-reader -ra --junitxml=test-results/pytest.xml
```

El laboratorio requiere los binarios PostgreSQL 18 accesibles mediante
`MITBOL_TEST_PG_BIN`. En Windows se puede dirigir TEMP/TMP a un directorio
local con espacio suficiente: los clústeres se detienen y sus archivos se
conservan para diagnóstico. Los informes temporales no se versionan.

La publicación y actualización del rol existente se describen en
[procedimiento HU-009](../deployment/hu009_rollout.md). La activación externa,
la revisión de frontend y el check remoto de CI deben confirmarse por separado.
