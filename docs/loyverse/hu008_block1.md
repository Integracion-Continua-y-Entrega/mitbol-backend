# HU-008 — Bloque 1: lectura real de Loyverse

## 1. Qué implementamos y qué problema resuelve

El backend puede consultar el catálogo de la tienda de pruebas **I.C.E**, cuyo
identificador confirmó el usuario. El comando es local: no se publicó una ruta
administrativa ni se habilitó el endpoint del prototipo.

La ejecución final consultó exclusivamente `GET /stores`, `GET /items` y
`GET /modifiers`. No utilizó `SyncService`, sesiones SQLAlchemy ni PostgreSQL.
Las variantes proceden del arreglo `variants` de cada artículo; los complementos,
de `modifier_options` de cada grupo.

Resultado real en [hu008_block1_report.json](hu008_block1_report.json):

| Dato observado | Cantidad |
| --- | --- |
| Artículos | 3 |
| Variantes | 5 |
| Grupos de modificadores | 2 |
| Opciones de modificador | 5 |
| Relaciones artículo–grupo declaradas por Loyverse | 2 |

| Artículo | Variante observada | Precio de la tienda | Grupo declarado |
| --- | --- | --- | --- |
| Papas fritas | Sin opciones de variante | 45.00 | Ninguno |
| Limonada natural | 355 ml | 35.00 | Extras bebida |
| Limonada natural | 500 ml | 49.00 | Extras bebida |
| Hamburguesa clásica | Sencilla | 89.00 | Extras hamburguesa |
| Hamburguesa clásica | Doble | 119.00 | Extras hamburguesa |

Las opciones observadas son Chía (5.00), Hierbabuena (5.00), Queso extra (15.00),
Tocino (20.00) y Sin cebolla (0.00). Este último cero fue devuelto por Loyverse;
no sustituye un valor desconocido. No se consultó la moneda del comerciante.

La ejecución final necesitó cuatro GET: una página de tiendas, dos de artículos
y una de modificadores. Se usó `--page-size 2` para ejercitar la paginación real.
El informe conserva su fecha en UTC, igual que las fechas de la API.

## 2. Cómo funciona internamente

1. `Settings` lee las credenciales locales. El token usa `SecretStr` para ocultarlo
   en representaciones habituales; `get_secret_value()` sólo lo extrae al construir
   la cabecera de autenticación. Nunca se incluye en el informe.
2. `LOYVERSE_STORE_ID` queda fijado en `.env` al ID de I.C.E. El diagnóstico lo
   comprueba contra las tiendas activas accesibles. Si no se configura un ID,
   sólo puede seleccionarse automáticamente una única tienda activa.
3. `LoyverseAPIClient` conserva la responsabilidad HTTP del cliente anterior.
   Reutiliza un `AsyncClient` y cierra sus conexiones al salir de `async with`.
4. El cliente recorre los cursores de artículos y modificadores. Una colección
   ausente no equivale a una lista vacía. Un cursor repetido o una página inválida
   aborta la consulta completa: no se devuelven resultados parciales.
5. Se reintentan hasta tres veces los fallos de conexión, timeout, HTTP 408, 429
   y 5xx. La espera exponencial es 1, 2... segundos, con máximo de 10 por espera.
   Si existe `Retry-After`, se respeta; si supera el máximo configurado de 300
   segundos, se pospone la consulta en vez de reintentar anticipadamente.
6. Los errores permanentes 4xx no se reintentan. Los mensajes omiten cuerpos HTTP
   y cabeceras, para no copiar información sensible del servidor.
7. `catalog_probe.py` inspecciona identificadores, relaciones y precios. Selecciona
   la entrada de `variants[].stores[]` cuyo `store_id` coincide con la tienda.
   Informa el precio y su procedencia; no genera entidades ORM ni operaciones UPSERT.
8. Los precios fijos conocidos se distinguen de valores variables o nulos. Si
   falta la configuración de tienda o su precio, se informa la incompatibilidad
   sin usar otro establecimiento, el primer precio ni cero como sustituto.
9. El comando puede guardar un informe local con campos seleccionados. Omite
   token, cabeceras, dirección, teléfono, costos y otros campos ajenos al diagnóstico.

### Diferencia comprobada entre documentación y respuesta

La [referencia oficial de Loyverse](https://developer.loyverse.com/docs/) publica
`modifiers_ids` para los grupos del artículo. La respuesta real de esta cuenta usa
`modifier_ids`. La primera inspección con `.get('modifiers_ids')` había ocultado
esa diferencia al representar un campo ausente como `null`.

El primer diagnóstico estricto falló con `Falta el campo 'modifiers_ids'`. Se
inspeccionaron las claves reales y se corrigió el adaptador para admitir ambas
formas comprobadas. Si ninguna existe, o ambas aparecen con valores diferentes,
la respuesta se rechaza. Cada artículo registra `modifier_ids_field` para mostrar
qué forma devolvió la API. No se inventan nombres adicionales ni asociaciones.

En los datos actuales todas las referencias resuelven a grupos de la tienda y
los precios de variante son `FIXED`, definidos y coincidentes con sus valores
predeterminados. `issues` y `unused_modifier_ids` están vacíos.

La ausencia de incidencias en esta lectura no implementa todavía las reglas de
selección ni asigna los grupos a filas de `ProductModifierAssignments`. Esa
adaptación y la persistencia corresponden al Bloque 2.

## 3. Archivos creados o modificados

| Archivo | Responsabilidad |
| --- | --- |
| `app/services/loyverse_client.py` | Cliente existente mejorado: GET, conexiones, timeout, reintentos, JSON y paginación |
| `app/services/catalog_probe.py` | Nuevo: inspección en memoria de tienda, relaciones y precios |
| `app/cli/__init__.py` | Nuevo: paquete para comandos locales |
| `app/cli/probe_loyverse.py` | Nuevo: comando ejecutable y salida del informe |
| `app/core/config.py` | Token como `SecretStr` y configuración opcional de tienda |
| `.env` | ID de I.C.E confirmado; las otras variables se conservaron; excluido de Git |
| `.env.example` | Documenta `LOYVERSE_STORE_ID` sin credenciales |
| `tests/test_loyverse_client.py` | Nuevo: pruebas HTTP con RESPX y token ficticio |
| `tests/test_catalog_probe.py` | Nuevo: relaciones, selección de tienda, precios y acceso a BD prohibido |
| `docs/loyverse/hu008_block1_report.json` | Informe filtrado de la consulta real final |
| `docs/loyverse/hu008_block1.md` | Esta explicación educativa |
| `README.MD` | Añade el comando y el enlace a esta guía |

Se revisaron `sync_service.py` y los cinco modelos, sin modificarlos. No hubo
migraciones, modificaciones del esquema ni escrituras del catálogo en PostgreSQL.
No se añadieron dependencias, categorías, descuentos, pedidos ni ejecución periódica.

La comprobación final de PostgreSQL se hizo con `transaction_read_only=on` y
confirmó cero filas en cada una de las cinco tablas del catálogo de `mitbol_dev`.

## 4. Fragmentos importantes del código real

### Cliente: reintentos limitados (`app/services/loyverse_client.py`)

```python
async for attempt in AsyncRetrying(
    stop=stop_after_attempt(self.max_attempts),
    wait=self._retry_wait,
    retry=retry_if_exception_type(LoyverseTransientError),
    sleep=self._sleep,
    reraise=True,
):
    with attempt:
        return await self._get_once(endpoint, params)
```

Cada intento llama a la misma operación GET. Tenacity sólo repite errores
clasificados como transitorios; al agotarse los intentos conserva el error. En
las pruebas se sustituye la espera por `AsyncMock`, sin esperar segundos reales.

### Cliente: precisión monetaria (`app/services/loyverse_client.py`)

```python
data = json.loads(
    response.content, parse_float=Decimal, parse_constant=_reject_json_constant,
)
```

Los decimales del JSON se leen directamente como `Decimal`, sin pasar primero
por `float`. `null` continúa siendo `None`. Las constantes no válidas se rechazan.
El informe serializa los importes como texto para conservar su representación.

### Diagnóstico: compatibilidad explícita (`app/services/catalog_probe.py`)

```python
fields = [field for field in ("modifier_ids", "modifiers_ids") if field in item]
```

Se comprueba la presencia de los campos realmente observados o documentados.
Las líneas siguientes rechazan ausencia o contradicción y validan la lista.
Esto evita confundir un nombre incorrecto con un artículo sin modificadores.

### Diagnóstico: tienda exacta (`app/services/catalog_probe.py`)

```python
stores = _identities(_list(variant, "stores"), "store_id")
selected = stores.get(store_id)
```

Primero se comprueba que no haya entradas duplicadas por tienda. Después se
busca el identificador exacto. Si no existe, se registra una incidencia y el
precio observado sigue siendo desconocido.

## 5. Pruebas ejecutadas y cómo repetirlas

Se ejecutó primero el grupo nuevo: **46 passed**. Después de comprobar la
diferencia `modifier_ids/modifiers_ids` se añadieron tres casos de regresión.
La ejecución completa final dio **75 passed in 15.41s**: 49 pruebas nuevas y
las 26 anteriores, incluidas las seis verificaciones PostgreSQL de sólo lectura.

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Las pruebas nuevas cubren paginación realista, respuestas incompletas, cursores
inválidos, errores HTTP, conexión y timeout, agotamiento de reintentos,
`Retry-After`, números decimales, cero válido, nulos, precios por tienda,
referencias inválidas y la diferencia entre los dos nombres del campo de grupos.
También prohíben conexiones SQLAlchemy durante el diagnóstico simulado.

`pip check` devolvió `No broken requirements found`. El primer acceso desde el
entorno restringido falló con `ConnectError`; las consultas reales se ejecutaron
con acceso de red autorizado. El diagnóstico completo final terminó con código 0.

Para repetir sólo las pruebas nuevas, sin API real ni PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_loyverse_client.py tests/test_catalog_probe.py -q -p no:cacheprovider
```

Para repetir la lectura real con las credenciales locales:

```powershell
.\.venv\Scripts\python.exe -m app.cli.probe_loyverse --page-size 2
```

Para guardar además el informe filtrado:

```powershell
.\.venv\Scripts\python.exe -m app.cli.probe_loyverse --page-size 2 --output docs/loyverse/hu008_block1_report.json
```

Código 0 significa que la lectura y la inspección terminaron; también hay que
revisar `issues`. Código 1 indica un error de API o validación. Si falla, no se
sobrescribe un informe anterior: su fecha permite identificar la última lectura
correcta. El diagnóstico actual consulta los recursos sin `show_deleted=true`;
las bajas comerciales se tratarán en el bloque de sincronización.

## 6. Cinco preguntas posibles del profesor

1. **¿Por qué separar cliente HTTP y diagnóstico?** El cliente resuelve transporte
   y paginación; el diagnóstico interpreta los datos recibidos. La persistencia
   podrá reutilizar el mismo cliente sin duplicar llamadas ni mezclar responsabilidades.
2. **¿Por qué verificar el ID de tienda?** Una variante puede tener precios
   distintos por establecimiento. El primero del arreglo no identifica nuestra tienda.
3. **¿Por qué un precio cero puede ser válido y un nulo no se convierte en cero?**
   Cero es un importe conocido; `null` indica que no tenemos ese importe definido.
4. **¿Cómo evitamos un ciclo infinito o un catálogo parcial?** Detectamos cursores
   repetidos, imponemos un máximo de páginas y abortamos cuando una página es inválida.
5. **¿Qué aprendimos de `modifier_ids`?** La documentación y el servicio real
   pueden diferir. Debemos inspeccionar la respuesta, documentar la diferencia y
   probar una compatibilidad explícita, sin deducir relaciones ausentes.

## 7. Conceptos que debes dominar

- GET autenticado y diferencia entre consulta remota y escritura local.
- Programación asíncrona y cierre de conexiones mediante `async with`.
- Paginación por cursor, timeout y reintentos limitados.
- Precio decimal, precio desconocido y procedencia por tienda.
- Validación de contratos externos y referencias entre entidades.
- Separación entre transporte HTTP, diagnóstico y futura persistencia.
- Este bloque verifica datos. Los UPSERT, transacciones PostgreSQL y asignaciones
  por variante todavía no están implementados.
