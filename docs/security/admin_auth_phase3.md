# MITBOL — Autenticación administrativa, fase 3

Fecha: 6 de noviembre de 2026, America/Mexico_City. Rama conservada: `design/admin-auth-security`.

Este documento conserva el cierre de fase 3. La revisión posterior de ACL y el ajuste de locks del repositorio están en [fase 4](admin_auth_phase4.md); la sección de pendientes describe el estado previo a esa revisión. Los contratos HTTP permanecen vigentes.

Desde [fase 5](admin_auth_phase5.md), AUTH_CATALOG_ENABLED y AUTH_IMAGES_ENABLED valen false por defecto: las pruebas locales deben habilitar explícitamente el catálogo y, si corresponde, las escrituras de imágenes. El guard usa los contextos efectivos de rutas incluidas de FastAPI para comprobar permisos antes de recibir el cuerpo.

HU-005 y HU-007 usan sesiones individuales y permisos en sus **15 operaciones HTTP existentes**, exclusivamente en el modo local aislado `AUTH_ENABLED=true`, `ADMIN_ENABLED=false`. La API pública conserva sus consultas anónimas. Cloud continúa bloqueado por configuración y sin registrar administración/autenticación. Esta fase no crea tablas, cuentas operativas, migraciones ni infraestructura nueva.

Continúa las [sesiones de fase 2](admin_auth_phase2.md) y la [persistencia de fase 1](admin_auth_phase1.md). El esquema sigue en `20261105_0004`; sólo las fixtures lo aplican en sus bases temporales propias. No se cambian `.env`, Neon, mitbol_dev, Render, el lector, Loyverse, el frontend ni develop. No se hacen commits ni push.

## 1. Revisión previa e integración

Antes de editar se revisaron `admin_catalog.py`, ambos servicios, los esquemas de enriquecimiento/disponibilidad, el almacenamiento local, el guard legado, el registro de routers, las dependencias de fase 2 y sus pruebas. No se añadieron rutas de catálogo ni se duplicó la lógica de negocio.

El problema encontrado eran los `db.commit()` internos de los servicios: confirmar allí dejaría la auditoría fuera de la modificación. Ahora, cuando la sesión SQLAlchemy pertenece al contexto autenticado, `commit_changes()` sólo hace flush; `AuthService.authenticated()` conserva la transacción y decide commit/rollback al finalizar la dependencia. El modo legado y los usos independientes de los servicios conservan su comportamiento anterior.

| Archivo | Cambio / funciones |
| --- | --- |
| [api/admin_catalog.py](../../app/api/admin_catalog.py) | `create_admin_router(authenticated=...)` conserva métodos, paths, entradas y salidas; aplica permisos y añade eventos de éxito en cada escritura. |
| [api/auth_dependencies.py](../../app/api/auth_dependencies.py) | `get_authenticated_database()` reutiliza exactamente `auth_context.db`; `require_permission()` identifica el permiso para la comprobación previa al parser. |
| [core/security.py](../../app/core/security.py) | `AdminRequestGuard.authorize_session()` comprueba sesión/CSRF y permisos de las dependencias de la ruta real antes de recibir/procesar el cuerpo. No mantiene una segunda lista de URLs. Conserva límite de bytes y barreras locales. |
| [main.py](../../app/main.py) | Registra el mismo router en el modo individual o legado excluyente; la documentación del modo individual deja de anunciar la clave compartida. |
| [services/admin_transaction.py](../../app/services/admin_transaction.py) | `commit_changes`, `rollback_changes`, `audit_success`, registro de almacenamiento y recuperación posterior a la transacción. |
| [services/auth_service.py](../../app/services/auth_service.py) | Enlaza `db.info` con el contexto autenticado y recupera diarios de imágenes después del commit/rollback. |
| [services/product_enrichment_service.py](../../app/services/product_enrichment_service.py) | Delega commit/rollback cuando hay contexto autenticado; difiere finalización/compensación de archivos al final de esa unidad. |
| [services/product_availability_service.py](../../app/services/product_availability_service.py) | Delega la confirmación, conservando el bloqueo y las reglas existentes. |
| [tests/test_auth_admin_api.py](../../tests/test_auth_admin_api.py) | Prueba las rutas reales con PostgreSQL y archivos temporales, roles, validaciones, rollback y concurrencia. |
| [tests/test_auth_api.py](../../tests/test_auth_api.py) | Actualiza la expectativa histórica de rutas cerradas en el modo individual; conserva las pruebas previas de sesiones. |

Los esquemas, modelos de catálogo, reglas HU-008, transformador/sincronizador, almacenamiento y rutas públicas no cambian.

## 2. Autorización y controles HTTP

Todas las rutas administrativas del modo individual exigen `catalog:read` mediante `require_permission`, que reutiliza `get_current_user` con `scope="function"`. La base administrativa procede de esa misma dependencia; no usa el pool público ni abre una transacción de escritura separada.

| Operación | Permiso adicional | Admin | Staff |
| --- | --- | --- | --- |
| Consultar disponibilidad de producto/variante | Ninguno | Sí | Sí |
| Consultar descripción y referencias de imágenes | Ninguno | Sí | Sí |
| Cambiar disponibilidad de producto/variante | `availability:write` | Sí | Sí |
| Cambiar/limpiar descripción | `description:write` | Sí | 403 |
| Subir, reordenar o eliminar imagen local | `images:write` | Sí | 403 |
| Consultar/crear etiquetas y consultar/asociar/retirar etiquetas de variante | `tags:write` | Sí | 403 |

La consulta de etiquetas se reserva a Admin porque la solicitud especifica «consultar y administrar etiquetas» exclusivamente para ese rol. Se reutiliza el permiso existente `tags:write` también para estas lecturas; no se cambia la matriz de permisos de fase 1. Las etiquetas que forman parte del menú público siguen siendo públicas.

Una petición con origen/localidad válidos pero sin cookie de sesión obtiene 401, incluso enviando `X-Admin-Key` o Authorization. Las escrituras requieren Origin autorizado y `X-CSRF-Token` válido. Un Origin inválido/ausente en escritura puede rechazarse con 403 antes de consultar la sesión. No se usa CORS como autorización.

Secuencia de una petición válida:

1. CORS exterior admite preflight de `http://localhost:5173` sin exigir cookie, con métodos y cabeceras explícitos.
2. El guard valida conexión directa loopback, Host, ausencia de proxies, HTTPS si corresponde, Origin, sesión, CSRF y permisos de la ruta real. Esta consulta termina antes de recibir el cuerpo; no mantiene locks durante la transferencia.
3. El cuerpo queda limitado a 5,065,536 bytes totales, incluido multipart. Después se conserva el parser/validación habitual.
4. La dependencia vuelve a comprobar estado vigente y CSRF, y mantiene locks de cuenta, empleado/roles y sesión durante la operación. No se confía en el resultado anterior al cuerpo.
5. El servicio ejecuta únicamente la operación existente; la ruta añade su evento. La dependencia revalida expiración y confirma o revierte antes de enviar la respuesta.

Esta doble comprobación es intencional: evita procesar archivos de clientes sin autorización y también evita usar una sesión revocada/expirada mientras llegaba el cuerpo. La actualización de actividad respeta el intervalo de fase 2; una comprobación previa válida puede registrar actividad aunque el payload posterior sea inválido.

Las respuestas administrativas del modo individual llevan `Cache-Control: no-store`, también en errores. Las cookies conservan HttpOnly, SameSite=Lax, Path=/ y Secure por defecto. El perfil HTTP local exige desactivar Secure explícitamente; no sirve para producción.

## 3. X-Admin-Key y cloud

| AUTH_ENABLED | ADMIN_ENABLED | Resultado |
| --- | --- | --- |
| false | false | Administración sin registrar; guard devuelve 404. Es el estado predeterminado/cloud. |
| true | false | Sesión individual, permisos y CSRF; X-Admin-Key se ignora y no aparece como alternativa en OpenAPI. Sólo base/localidad aisladas. |
| false | true | Herramienta legada de backend, exclusivamente localhost/Swagger y clave compartida; React sigue rechazado. |
| true | true | Error de configuración; no arranca. |

El tercer modo se conserva para compatibilidad/regresión de las herramientas locales existentes. **No ofrece identidad individual ni la auditoría por cuenta de esta fase y no es el modo del panel React.** Cambiar variables del servidor no es una alternativa de autenticación disponible para un cliente HTTP. En el modo individual no existe fallback a esa clave.

Al adoptar definitivamente las cuentas, se retirarán `ADMIN_API_KEY`, el esquema `LocalAdminKey`, `require_local_admin` y la rama legada del guard/router, tras migrar las herramientas y sus pruebas. No se distribuirá esa clave al frontend.

`APP_ENV=cloud` rechaza tanto AUTH_ENABLED=true como ADMIN_ENABLED=true; la fábrica revalida incluso Settings copiados. Con ambos falsos no registra ninguno de los dos routers. No se cambió configuración de Render ni del rol `mitbol_render_reader`, que sigue siendo lector. Las cargas de imágenes permanecen locales; no hay almacenamiento cloud nuevo.

## 4. Transacción, auditoría y archivos

Las nueve escrituras confirman datos y evento de éxito con **la misma sesión, conexión y transacción PostgreSQL**. Los eventos llevan `actor_account_id`, acción, entidad, identificador, `outcome="success"`, `request_id` UUID y fecha del servidor. No incluyen descripción, nombre de archivo, correo, contraseña, cookie, CSRF ni SQL.

| Escritura | action | resource_type / resource_id |
| --- | --- | --- |
| Descripción | `catalog.product.description` | `product` / product_id |
| Disponibilidad de producto | `catalog.product.availability` | `product` / product_id |
| Disponibilidad de variante | `catalog.variant.availability` | `variant` / variant_id |
| Crear etiqueta | `catalog.tag.create` | `tag` / tag_id generado |
| Asociar etiqueta | `catalog.variant.tag.attach` | `variant_tag` / `variant_id:tag_id` |
| Retirar asociación | `catalog.variant.tag.remove` | `variant_tag` / `variant_id:tag_id` |
| Crear imagen | `catalog.image.create` | `image` / image_id generado |
| Cambiar orden | `catalog.image.order` | `image` / image_id |
| Eliminar imagen | `catalog.image.delete` | `image` / image_id |

Las operaciones idempotentes aceptadas también generan éxito: refleja la solicitud autorizada confirmada, aunque ya existiera el estado deseado. Las lecturas no generan eventos de modificación.

Si falla validación, servicio, auditoría, revalidación de sesión o commit antes de confirmarse, se revierte toda la unidad. Las denegaciones de sesión/permiso/CSRF asociadas a cuenta conocida usan el registro separado `auth.access/denied` de fase 2 después del rollback. Errores de origen, cookies ausentes/mal formadas y validación no generan indiscriminadamente eventos ni obligan a consultar la base.

Se conservan los bloqueos existentes de disponibilidad y almacenamiento. Las operaciones de imagen registran el almacenamiento antes de preparar el archivo; el diario no se liquida dentro de la transacción de negocio. Tras commit/rollback, una transacción nueva valida su conexión, adquiere el mismo bloqueo de imágenes y recupera el diario consultando las referencias realmente confirmadas. Esto cubre fallo de auditoría y también un commit confirmado cuya respuesta fue incierta.

El sistema de archivos no es transaccional con PostgreSQL: una caída o un fallo de limpieza puede dejar un diario privado pendiente. Se reintenta en la siguiente carga/eliminación autorizada. Las URLs sin referencia en la base siguen sin publicarse. El borrado provisional puede hacer que una imagen responda temporalmente 404 antes de compensarse; no se promete atomicidad distribuida de archivos. No se implementó otra tecnología de almacenamiento.

Si el servidor no puede confirmar el resultado del commit, responde 503: puede haberse confirmado tanto el cambio como el evento. React debe consultar el estado antes de repetir una creación; no reintentar POST automáticamente. No se añade idempotency-key en esta fase.

## 5. Contratos reales para React

Base local: `http://localhost:8000`. Todos los paths de esta tabla comienzan por `/api/admin`. IDs enteros positivos hasta 2,147,483,647. GET no lleva cuerpo. JSON de escritura conserva tipos estrictos y rechaza campos extra. DELETE/PUT de asociaciones no requieren cuerpo.

| Método y path | Entrada | Éxito / cuerpo | Roles |
| --- | --- | --- | --- |
| GET `/products/{id}/availability` | — | 200, ProductAvailability | Admin, Staff |
| PATCH `/products/{id}/availability` | `{"is_available":false}` | 200, ProductAvailability | Admin, Staff |
| GET `/variants/{id}/availability` | — | 200, VariantAvailability | Admin, Staff |
| PATCH `/variants/{id}/availability` | `{"is_available":true}` | 200, VariantAvailability | Admin, Staff |
| GET `/products/{id}/description` | — | 200, `{"product_id":1,"description":"Texto"}` | Admin, Staff |
| PATCH `/products/{id}/description` | `{"description":"Texto"}` o null | 200, mismo formato | Admin |
| GET `/variants/{id}/images` | — | 200, `{"images":[Image]}` | Admin, Staff |
| POST `/variants/{id}/images` | multipart: `file`, `display_order` opcional | 201, Image | Admin |
| PATCH `/variants/{id}/images/{image_id}` | `{"display_order":2}` | 200, Image | Admin |
| DELETE `/variants/{id}/images/{image_id}` | — | 204 sin cuerpo | Admin |
| GET `/tags` | — | 200, `{"tags":[Tag]}` | Admin |
| POST `/tags` | `{"name":"Picante"}` | 201, Tag normalizada | Admin |
| GET `/variants/{id}/tags` | — | 200, `{"tags":[Tag]}` | Admin |
| PUT `/variants/{id}/tags/{tag_id}` | — | 204 sin cuerpo, idempotente | Admin |
| DELETE `/variants/{id}/tags/{tag_id}` | — | 204 sin cuerpo, idempotente | Admin |

Formas de respuesta (IDs y fechas ilustrativos):

```json
{
  "ProductAvailability": {
    "product_id": 1, "is_active": true, "is_available": false, "can_order": false
  },
  "VariantAvailability": {
    "product_variant_id": 2, "product_id": 1, "is_available": true,
    "is_commercially_available": true, "product_is_active": true,
    "product_is_available": false, "can_order": false
  },
  "Tag": {"tag_id": 3, "name": "picante", "created_at": "2026-11-06T12:00:00Z"},
  "Image": {
    "product_image_id": 4, "product_variant_id": 2,
    "image_url": "/media/products/1/variants/2/<32 hex>.png",
    "display_order": 0, "created_at": "2026-11-06T12:00:00Z"
  }
}
```

Descripción: texto plano, máximo 2,000 caracteres; null/vacío/blancos limpia el campo. Etiquetas: NFC, casefold, espacios normalizados, 1–150 caracteres normalizados, sin HTML; duplicado 409. No existen endpoints de renombrar/eliminar la etiqueta global en este alcance; DELETE retira su asociación.

Imágenes: contenido real JPEG/PNG/WebP estático, hasta 5,000,000 bytes y 20 megapíxeles, recodificado sin metadatos; no se confía en extensión o MIME declarados. `display_order` entre 0 y 2,147,483,647; ausente agrega al final. La imagen debe pertenecer a la variante indicada. No existe campo para enviar una URL arbitraria ni una carga cloud.

Disponibilidad: sólo cambia `Products.is_available` o `ProductVariants.is_available`. No altera precio, identidad Loyverse, activación ni `is_commercially_available`. Un producto inactivo o una variante comercialmente no disponible no se vuelve pedible por este PATCH. Se conserva `can_order` y toda la combinación de estados HU-007/HU-008.

Flujo React: login con `X-CSRF-Protection: 1`; guardar CSRF en memoria; identificar usuario con `/api/auth/me`; recuperar CSRF tras recarga mediante `/api/auth/csrf`. La cookie HttpOnly la administra el navegador; no se copia al localStorage ni a Authorization. Los contratos completos de login/logout/cambio de contraseña están en fase 2.

```js
const API = "http://localhost:8000";
let csrf; // Memoria de la aplicación; no es la cookie de sesión.

async function decode(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw Object.assign(new Error(body.detail || "No se completó la operación"),
                        { status: response.status, detail: body.detail });
  }
  return response.status === 204 ? undefined : response.json();
}

async function login(email, password) {
  const data = await decode(await fetch(`${API}/api/auth/login`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
    body: JSON.stringify({ email, password })
  }));
  csrf = data.csrf_token;
  return data.user;
}

async function restoreSession() {
  const me = await decode(await fetch(`${API}/api/auth/me`, { credentials: "include" }));
  const data = await decode(await fetch(`${API}/api/auth/csrf`, { credentials: "include" }));
  csrf = data.csrf_token;
  return me.user;
}

async function patchAdmin(path, payload) {
  return decode(await fetch(`${API}/api/admin${path}`, {
    method: "PATCH", credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(payload)
  }));
}
// Admin: patchAdmin(`/products/${productId}/description`, { description: "Texto" });
// Admin o Staff: patchAdmin(`/variants/${variantId}/availability`, { is_available: false });

async function uploadLocalImage(variantId, file) {
  const form = new FormData();
  form.append("file", file);
  return decode(await fetch(`${API}/api/admin/variants/${variantId}/images`, {
    method: "POST", credentials: "include", headers: { "X-CSRF-Token": csrf }, body: form
  })); // El navegador genera Content-Type y boundary multipart.
}

async function attachTag(variantId, tagId) {
  return decode(await fetch(`${API}/api/admin/variants/${variantId}/tags/${tagId}`, {
    method: "PUT", credentials: "include", headers: { "X-CSRF-Token": csrf }
  }));
}
```

El navegador añade Origin automáticamente; React no debe intentar escribirlo. Usar localhost en ambos extremos, sin mezclarlo con 127.0.0.1 para cookies. Para mostrar una imagen, resolver su URL relativa contra API. `permissions` de `/me` sirve para presentar controles; FastAPI decide cada acceso aunque se altere React.

| Estado | Tratamiento |
| --- | --- |
| 401 | Sesión ausente/expirada/revocada; borrar estado de UI/CSRF y solicitar login. |
| 403 | Permiso, CSRF, Origin o conexión rechazados; mostrar acceso denegado/revisar contexto, sin asumir que un nuevo login concederá permisos. |
| 404 | ID inexistente, imagen de otra variante o funcionalidad deshabilitada/cloud. |
| 422 | Entrada inválida, campos extra, HTML, orden/ID inválido, imagen vacía/dañada/animada o límites de píxeles. Los errores de esquema conservan el formato FastAPI `detail` con lista. |
| 409 | Etiqueta duplicada, relación/conflicto concurrente o lock de catálogo/imágenes ocupado; refrescar estado antes de decidir reintento. |
| 413 | Archivo/cuerpo demasiado grande. |
| 415 | Formato real de imagen no admitido. |
| 429 | Limitación de autenticación de fase 2; respetar Retry-After. |
| 500 / 503 | Error interno/base/almacenamiento; mensajes saneados. Si el commit es incierto, consultar estado antes de repetir escrituras. |

La API pública sigue exponiendo GET `/api/menu` y `/api/menu/{product_id}` sin autenticación. Las pruebas comprueban que se reflejan descripción, etiquetas, imágenes y disponibilidad desde conexiones independientes.

## 6. Pruebas y evidencia

Primera ejecución de integración y regresiones HU-005/HU-007: **308 aprobadas**, sin fallos ni omitidas, dos deprecaciones preexistentes de Starlette/AnyIO, 143.14 s. Después se añadieron casos de concurrencia/commit incierto para incluirlos en la ejecución completa.

Comando de suite completa:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra
```

El nombre del ejecutor es histórico. Arranca clústeres PostgreSQL 18 temporales propios para catálogo/auth y ACL del lector, sanea variables heredadas, evita `.env` y valida identidad, versión y data_directory. Las cuentas/contraseñas y el catálogo son sintéticos; no se ejecuta una sincronización Loyverse real. Al terminar detiene sus clústeres.

Cobertura de fase 3: todas las operaciones rechazan anónimos; todas las reservadas a Admin rechazan Staff; ambas identidades cambian disponibilidad; CSRF/Origin en cada familia de escritura; sesiones expiradas/revocadas y expiración entre recepción/ejecución o durante una mutación; normalización/duplicados/asociaciones de etiquetas; límites/formato/relaciones de imágenes; invariancia de campos comerciales; fallo de auditoría en las nueve escrituras; archivos compensados; commit incierto contrastado con estado real; dos cargas concurrentes; conflictos entre Admin/Staff; carrera con revocación; CORS/preflight/tamaño; cloud sin rutas ni conexiones administrativas. Se conservan las regresiones existentes de HU-001/HU-003/HU-005/HU-007/HU-008 y lector.

Resultado completo: **1107 pruebas aprobadas, cero fallos, cero errores y cero omitidas**, en 324.29 s, con dos avisos de deprecación preexistentes de Starlette/AnyIO. Son las 1021 pruebas previas más 86 casos de fase 3. Ambos clústeres temporales quedaron detenidos. `pip check` no detectó incompatibilidades y `git diff --check` terminó correctamente.

Evidencia completa: `TEMP/mitbol-sync-preflight-91d27b9b271a4badacad6fd7fb6a3a8e/test-run.log`. Los 11 casos adicionales seleccionados pasaron en 10.22 s; después de ajustar la prueba de concurrencia para admitir el 409 previsto y reintentar, ese caso también pasó por separado en 4.40 s (`TEMP/mitbol-sync-preflight-113bfcf862164945976d7d1e3c70ac42/test-run.log`). Todos los ejecutores finalizaron sus clústeres.

Las pruebas HTTP usan TestClient/ASGI, no un navegador real ni un despliegue de React/TLS. No se presentan como certificación de producción ni como ensayo manual de la UI.

## 7. Límites y fase 4

La autenticación MITBOL identifica a la persona; los permisos FastAPI autorizan operaciones; el rol PostgreSQL limita al proceso. Admin/Staff son códigos de aplicación, nunca nombres de usuarios PostgreSQL ni credenciales entregadas a React.

La administración usa la conexión explícita de autenticación porque sesión, catálogo y auditoría deben compartir base/transacción. El lector público mantiene su pool independiente. Para un ensayo manual local, ambas conexiones deben apuntar al mismo catálogo de la base aislada autorizada; no usar el catálogo de mitbol_dev como destino administrativo. No se crean ahora cuentas operativas ni una base manual: las fixtures son la evidencia disponible.

En ese ensayo, configurar también `IMAGE_STORAGE_ROOT` como directorio temporal exclusivo de esa base. No compartir archivos/diarios con el almacenamiento de mitbol_dev u otra base: la recuperación decide qué conservar consultando la base conectada. Las pruebas usan un directorio `tmp_path` diferente en cada caso.

Antes de preparar cualquier escritor real:

1. Diseñar y probar en PostgreSQL desechable una identidad web sin ownership/DDL/CREATEROLE ni administración de infraestructura. Mantener al lector limitado al catálogo, sin hashes, sesiones o escritura.
2. Evaluar SELECT del catálogo y UPDATE por columna de Products(description,is_available)/ProductVariants(is_available); INSERT de Tags, ProductImages y ProductTags; UPDATE de ProductImages(display_order); DELETE de imágenes/asociaciones; uso de secuencias concretas. No conceder escritura en precio/IDs/activación/campos comerciales ni permisos de sincronización.
3. Definir SELECT del núcleo de identidad, INSERT/UPDATE acotados de sesiones, UPDATE de los campos de cuenta que usa login/cambio de contraseña y sólo INSERT en AuditEvents para la identidad web. Separar alta/roles, migraciones y retención en herramientas/identidades autorizadas. Este listado es diseño pendiente, no un script GRANT aprobado.
4. **Revisar los locks de fase 2 antes de conceder permisos**: `employee_and_roles()` usa SELECT FOR UPDATE en Employees, Roles y EmployeeRoles. PostgreSQL exige UPDATE al menos en una columna de cada tabla bloqueada. No se deben conceder cambios de asignaciones/roles sólo para permitir esos locks; primero habrá que revisar el protocolo de concurrencia bajo lock de cuenta/auth_version o una alternativa controlada y probarlo con ACL mínimas. Los locks de Roles también serializan peticiones de cuentas distintas que comparten rol. No afecta a la demostración temporal como propietario, pero impide afirmar que el escritor mínimo ya está resuelto. Véase [SELECT y locking en PostgreSQL 18](https://www.postgresql.org/docs/18/sql-select.html).
5. Probar el escritor limitado con toda la suite y pruebas negativas SQL, incluidas auditoría append-only, imposibilidad de alterar comercio/roles y conservación del aislamiento del lector. No relajar permisos reales hasta aprobar el diseño.

Después, y con autorización independiente, preparar limitación compartida/persistente, aprovisionamiento/recuperación de cuentas y gestión de roles con invalidación, rehash operativo, observabilidad/retención, dominios HTTPS, proxy confiable y cookies/CORS/CSRF en navegador. Las imágenes requieren una decisión posterior de almacenamiento persistente antes de habilitar cargas cloud. No basta con cambiar AUTH_ENABLED o ADMIN_ENABLED.

Se conservan plazos de ocho horas/treinta minutos y revocación/versionado de fase 2. No hay UI, cuentas predeterminadas, endpoints de alta/roles, recuperación de contraseña ni garantía de procesamiento distribuido. La atomicidad de esta fase se verifica dentro de PostgreSQL; la recuperación de archivos sigue usando el diario local.

La dependencia finaliza antes de responder, conforme al alcance de [dependencias con yield de FastAPI](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/); el commit/rollback pertenece al contexto de [transacciones de SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html).

La fase 3 termina aquí. Crear roles/permisos en Neon o publicar administración en Render requiere una autorización posterior.
