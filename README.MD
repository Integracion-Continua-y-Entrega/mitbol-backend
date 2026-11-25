# 🍔 MITBOL — Backend

**API REST para un menú digital con código QR, conectada con Loyverse.**

MITBOL es un proyecto web para modernizar la atención de un restaurante que utiliza **Loyverse** como punto de venta. Su API permite consultar un menú actualizado desde el celular, con productos, precios, variantes y complementos, sin necesidad de iniciar sesión.

> 📌 **Este repositorio contiene el backend.** La interfaz para clientes y personal se desarrolla por separado con React. La API permite registrar solicitudes, gestionar su atención por modalidad y verificar manualmente su pago. La venta y conciliación en Loyverse y la fidelización completa corresponden a etapas posteriores.

## ✨ Funcionalidades principales

- 🍽️ **Menú público:** consulta de productos, categorías, precios en MXN, variantes, modificadores, complementos, imágenes y disponibilidad. Incluye búsqueda y filtros por categoría o etiquetas.
- 🛠️ **Administración del catálogo:** edición autorizada de descripciones, fotografías, etiquetas, disponibilidad, destacados y novedades.
- 🔄 **Integración con Loyverse:** sincronización de productos, variantes, categorías, modificadores y complementos, con actualizaciones que evitan duplicados y conservan los datos locales.
- 🏷️ **Descuentos comerciales:** importación y consulta de descuentos compatibles; los registros desconocidos se tratan de forma controlada, sin inventar valores.
- 🖼️ **Imágenes persistentes:** fotografías almacenadas en **Cloudflare R2** y consultadas a través de la API.
- 🔐 **Acceso administrativo:** autenticación por sesiones, roles y permisos para proteger las operaciones internas.
- 👤 **Cuentas personales:** registro, inicio/cierre de sesión, perfil y recuperación de contraseña mediante SMTP o Brevo cuando se configura el proveedor.
- 🛒 **Carrito y solicitudes:** validación de selecciones, modalidades local/recogida, cálculo de importes y descuentos compatibles, registro idempotente e información histórica.
- 🎟️ **Cupones personales:** selección y cálculo de beneficios elegibles; no se reservan ni canjean al registrar la solicitud.
- 📋 **Atención de solicitudes:** seguimiento privado de invitados, cancelación pendiente, aceptación/rechazo, preparación, servido/entrega y finalización por modalidad, con historial de estados.
- 🧾 **Mis pedidos:** historial paginado y detalle privado de solicitudes asociadas a la cuenta autenticada, con importes históricos y estado actual.
- **Pago verificado:** confirmación manual del total histórico por personal autorizado, consulta separada del estado operativo y protección contra duplicados.

**Validación independiente:** integración del frontend, configuración y funcionamiento de los servicios desplegados y ejecuciones programadas. La implementación de endpoints y sus pruebas de backend no acredita esos entornos.

## Corte académico y backlog vigente

La planificación vigente es **Por hacer FINAL.pdf**, páginas 1–10. Parte de
HU-001 a HU-023 ya trabajadas. Por acuerdo de trabajo, el alcance de implementación
comprende **13 historias**: las siete del flujo de pedidos y las seis de
fidelización. Se desarrollan por bloques, priorizando primero el flujo de pedidos.
Las tablas describen el backend disponible; no acreditan la integración del
frontend ni el cierre completo de las historias.

| Historia | Base disponible | Pendiente del backend según el plan final |
| --- | --- | --- |
| HU-024 · Consultar solicitudes | Backend implementado: listado protegido con prioridad de pendientes, detalle, tiempo de espera y aviso configurable. | Sin pendiente identificado en este bloque de backend. |
| HU-025 · Aceptar solicitudes | Backend implementado: revalidación local, estado `Confirmado`, responsable e historial transaccional. | Sin pendiente identificado en este bloque de backend; reserva y canje de beneficios quedan fuera de este corte. |
| HU-026 · Rechazar solicitudes | Backend implementado: rechazo de pendientes con motivo obligatorio, fecha, responsable e historial transaccional. | Sin pendiente identificado en este bloque de backend. |
| HU-027 · Actualizar preparación | Acciones `prepare` y `ready`, con validación de modalidad e historial; acepta `Confirmado` y `Aceptado` legado. | Sin pendiente identificado en este bloque de backend. |
| HU-028 · Servicio o entrega | Acciones `serve`, `deliver` y `complete`; el cobro se consulta y confirma por separado mediante HU-031. | Sin pendiente identificado en este bloque de backend. |
| HU-041 · Estados desde mi cuenta | Listado propio, detalle histórico y estado actual, incluido `Confirmado`; acceso restringido por propietario. | Sin pendiente identificado en este bloque de backend. |
| HU-031 · Pago verificado | Backend implementado: confirmación manual protegida del total exacto, evidencia persistente y consulta independiente; duplicados y concurrencia controlados. | Sin pendiente identificado en este bloque de backend. Uso académico con datos sintéticos; ventas y conciliación en Loyverse quedan fuera. |

Orden de continuidad del backend: comenzar HU-046, configuración de niveles y
recompensas, y continuar con las seis historias de fidelización indicadas abajo.

La rama académica reutiliza los bloques de historial personal, preparación y
servicio/entrega. Excluye el bloque de pausa y reactivación de recepción
(HU-038), que permanece conservado en `feature/order-reception-settings` para
una integración posterior. Este corte conserva los permisos de recepción
anteriores; HU-031 añade únicamente consulta e inserción de pagos al escritor web.

Las seis historias de fidelización también forman parte del alcance acordado y
se implementarán después del flujo de pedidos, en este orden:

| Historia | Estado del backend |
| --- | --- |
| HU-046 · Configurar niveles y recompensas | Persistencia y contrato implementados: migración 0016, meta, nivel, recompensa y condiciones. Pendiente administración protegida y validación contra el catálogo. |
| HU-042 · Identificación QR/PIN | Pendiente. |
| HU-043 · Registrar visita presencial | Pendiente. |
| HU-045 · Acumular visitas y otorgar recompensas | Pendiente. |
| HU-047 · Consultar progreso de lealtad | Pendiente. |
| HU-049 · Consultar billetera digital | Existe consulta de cupones; falta integrar las recompensas de fidelización y la presentación de estados de billetera. |

Este bloque se limita a configuración, visitas, generación y consulta de
recompensas; no incluye reserva ni canje. Ventas en Loyverse, vinculación,
conciliación y correcciones de pagos permanecen fuera del alcance actual.

Los cupones y cobros se demuestran únicamente con datos de prueba. No habilitar
su uso comercial antes de completar los mecanismos posteriores de reserva,
canje y conciliación. El registro de un pago simulado no acredita una venta
real en Loyverse.

## 🧭 Arquitectura

El backend es un **monolito modular organizado por capas**: una aplicación FastAPI cuyos módulos separan rutas HTTP, validación, reglas de negocio y persistencia.

| Flujo                        | ¿Cómo funciona?                                                             |
| ---------------------------- | --------------------------------------------------------------------------- |
| **Consulta del menú**        | `React → FastAPI (Render) → PostgreSQL (Neon) → FastAPI → React`            |
| **Sincronización comercial** | `GitHub Actions → API de Loyverse → PostgreSQL (Neon)`                      |
| **Fotografías**              | `FastAPI ↔ Cloudflare R2` (objetos privados y URLs servidas por el backend) |

**Loyverse** es la fuente de productos, precios y descuentos. **MITBOL** administra información propia, como descripciones, fotos, etiquetas y disponibilidad local. Los sincronizadores actualizan los datos comerciales **sin sobrescribir esas configuraciones**.

Los procesos programados se ejecutan mediante **GitHub Actions** y no dentro del servidor web: el workflow se administra desde `main` y ejecuta los comandos Python de `develop`. El catálogo tiene sincronización recurrente; la automatización de descuentos está preparada y su ejecución programada debe verificarse por separado.

## ⚙️ Tecnologías

| Tecnología                         | Uso principal                                         |
| ---------------------------------- | ----------------------------------------------------- |
| **Python · FastAPI · Uvicorn**     | API REST y servidor web.                              |
| **PostgreSQL · Neon**              | Base de datos relacional y alojamiento en la nube.    |
| **SQLAlchemy · psycopg2**          | Modelos, consultas y transacciones.                   |
| **Pydantic · Alembic**             | Validación de datos y migraciones del esquema.        |
| **HTTPX · Tenacity**               | Comunicación con Loyverse y reintentos controlados.   |
| **Cloudflare R2 · boto3 · Pillow** | Almacenamiento y validación de imágenes.              |
| **GitHub Actions · pytest**        | Sincronizaciones programadas y pruebas automatizadas. |
| **Render**                         | Despliegue del servicio FastAPI.                      |

Las dependencias se encuentran en [`requirements.txt`](requirements.txt).

## 🗂️ Organización del proyecto

| Ruta                                            | Responsabilidad                                                            |
| ----------------------------------------------- | -------------------------------------------------------------------------- |
| `app/api/`                                      | Endpoints públicos, autenticación y administración.                        |
| `app/services/`                                 | Lógica del menú, sincronizaciones, imágenes y operaciones administrativas. |
| `app/models/`                                   | Modelos y relaciones de PostgreSQL.                                        |
| `app/schemas/`                                  | Validación y contratos de la API.                                          |
| `app/db/`                                       | Conexiones, sesiones y políticas de acceso a datos.                        |
| `app/core/`                                     | Configuración, autenticación y seguridad transversal.                      |
| `app/cli/`                                      | Comandos operativos y sincronizadores ejecutables.                         |
| `app/repositories/`                             | Consultas especializadas, principalmente de autenticación.                 |
| `migrations/` · `tests/` · `.github/workflows/` | Migraciones, pruebas y automatización.                                     |

## 🌐 Endpoints destacados

| Método       | Ruta                     | Función                                                              |
| ------------ | ------------------------ | -------------------------------------------------------------------- |
| `GET`        | `/api/menu`              | Menú público, búsqueda y filtros.                                    |
| `GET`        | `/api/categories`        | Categorías del catálogo.                                             |
| `GET`        | `/api/menu/{product_id}` | Detalle, variantes y complementos.                                   |
| `GET`        | `/api/menu/config`       | Dirección pública del menú para el QR.                               |
| `GET`        | `/api/menu/tags`         | Etiquetas disponibles.                                               |
| `GET`        | `/api/admin/discounts`   | Descuentos sincronizados; requiere autorización.                     |
| `GET / POST` | `/api/admin/sync/...`    | Estado, solicitudes e historial de sincronización; acceso protegido. |

La administración de productos y la autenticación se encuentran bajo `/api/admin/` y `/api/auth/`. Consulta la documentación OpenAPI (`/docs`) del entorno correspondiente para ver sus contratos completos. **El QR se genera desde el frontend; el backend proporciona la URL del menú.**

## HU-024: recepción y consulta operativa

`GET /api/admin/orders` y `GET /api/admin/orders/{order_id}` conservan el acceso
protegido por `orders:read`, disponible para Admin y Staff. El UUID `order_id`
identifica la solicitud; se devuelven modalidad, fecha, estado, importes y los
datos necesarios para atenderla. El detalle incluye productos, variantes,
complementos, cantidades e historial, sin publicar tokens o claves de idempotencia.

El listado coloca primero `Solicitud recibida`. Dentro del grupo pendiente y
del resto, ordena por fecha ascendente y UUID ascendente como desempate. La
prioridad se aplica antes de paginar y se mantienen los filtros `status`,
`limit` y `offset`. Las páginas no congelan la cola entre consultas: nuevos
pedidos o cambios de estado pueden mover posiciones.

Lista y detalle añaden dos campos informativos:

| Campo | Significado |
| --- | --- |
| `pending_wait_seconds` | Segundos enteros desde la creación de una solicitud pendiente, con mínimo cero; `null` para otros estados. |
| `needs_attention` | `true` cuando la solicitud sigue pendiente y alcanzó el umbral de espera configurado. |

`ORDER_PENDING_WARNING_SECONDS` configura el umbral del aviso, entre 1 y 86400
segundos. El valor inicial es 600 segundos (10 minutos); puede ajustarse para
la demostración sin cambiar datos. El reloj del backend calcula la espera y
usa una misma referencia temporal para todos los elementos de una página.

Consultar repetidamente no atiende, rechaza ni cancela pedidos, aunque lleven
mucho tiempo pendientes. Al salir de `Solicitud recibida`, el aviso desaparece
y la espera pendiente se devuelve como `null`; no representa el tiempo total
de preparación o entrega. Las respuestas mantienen `Cache-Control: no-store`.
No se requieren migraciones, permisos nuevos, Web Push ni avisos externos.

## HU-025: confirmación con revalidación local

`POST /api/admin/orders/{order_id}/accept` recibe `{}` y requiere Admin o Staff
con `orders:write`, sesión, Origin autorizado y CSRF. Solo una solicitud en
`Solicitud recibida` puede pasar a `Confirmado`. Devuelve el detalle administrativo
y su historial, con `Cache-Control: no-store`.

Antes de confirmar, el backend reconstruye la selección original y comprueba
disponibilidad local, compatibilidad de complementos, precios y beneficios
aplicados. Un cupón se valida contra la cuenta propietaria del pedido, su vigencia
y sus condiciones actuales. No se consulta Loyverse durante esta acción.

Si cambia la selección, un precio o un beneficio aplicado, devuelve 409 y conserva
la solicitud pendiente con sus importes históricos. No permite forzar la
aceptación ni recalcular el precio del pedido. Cambiar nombres o metadatos de
descuentos que nunca se aplicaron no invalida la cotización. El personal puede
resolver el conflicto y reintentar, o rechazar con motivo. Ante una respuesta
incierta, consultar el detalle antes de repetir.

La operación bloquea la fila del pedido y mantiene el bloqueo transaccional de
catálogo compartido con sincronización y disponibilidad hasta guardar estado e
historial. Si el catálogo está ocupado devuelve 409 para reintentar. Decisiones
concurrentes no sobrescriben un estado ya atendido. Un fallo al guardar el
historial revierte la confirmación.

Las nuevas confirmaciones usan `Confirmado`; los registros y eventos antiguos
con `Aceptado` se conservan y también permiten iniciar preparación. No se
requieren migraciones ni permisos nuevos. Confirmar no reserva ni canjea cupones,
no asegura exclusividad de un beneficio y no registra pagos o ventas.

## HU-026: rechazo con motivo obligatorio

`POST /api/admin/orders/{order_id}/reject` requiere Admin o Staff con
`orders:write`, sesión, Origin autorizado y CSRF. El cuerpo es:

```json
{"reason":"No contamos con disponibilidad para atender la solicitud"}
```

El motivo es obligatorio, tiene de 1 a 500 caracteres después de recortar los
espacios exteriores y no admite controles internos. Se rechazan con 422 los
motivos ausentes, nulos, vacíos, de otro tipo o demasiado largos, y los campos
administrativos adicionales. La misma validación se aplica en el servicio para
evitar rechazos sin motivo en llamadas internas. Clientes que antes enviaban
`{}` o `{"reason":null}` deben actualizar su solicitud.

Solo `Solicitud recibida` puede pasar a `Rechazado`. Pedidos ya aceptados,
cancelados, rechazados o en atención devuelven 409; un UUID inexistente con
cuerpo válido devuelve 404. La comprobación de estado forma parte del UPDATE:
una aceptación, cancelación o segundo rechazo concurrente no puede sobrescribir
una decisión ya confirmada.

Motivo normalizado, fecha y responsable se guardan en el historial dentro de la
misma transacción que el estado. Si falla el historial, se revierte el rechazo.
Ante una respuesta perdida o incierta, consultar el detalle antes de repetir.
No se cambian productos, importes históricos ni beneficios. La respuesta contiene
el detalle administrativo y `Cache-Control: no-store`. El personal puede leer
el motivo en el historial; el seguimiento de invitados y el historial personal
conservan el estado `Rechazado` sin publicar notas internas ni identidades del
empleado.

No se requieren migraciones ni permisos nuevos. Los eventos antiguos con motivo
nulo se conservan; no se inventan motivos ni se modifican registros históricos.

## Historial personal de pedidos

El seguimiento desde la cuenta implementa el alcance del Prompt Maestro v2.1,
sección 6.8. No requiere roles administrativos ni permisos nuevos de PostgreSQL.

| Método | Ruta | Resultado |
| --- | --- | --- |
| `GET` | `/api/auth/orders?limit=25&offset=0` | `items`, `total`, `limit` y `offset`, solo de la cuenta autenticada. |
| `GET` | `/api/auth/orders/{order_id}` | Estado actual, modalidad, productos e importes históricos y `can_cancel`. |

El listado admite `status` opcional, `limit` de 1 a 100 y `offset` desde cero.
Ordena por fecha descendente y UUID descendente como desempate. Un historial
vacío devuelve 200 con `items: []` y `total: 0`. La paginación no congela el
conjunto de pedidos entre consultas.

La cookie de sesión determina la cuenta. No se acepta un token de invitado como
sustituto de la sesión ni se vinculan pedidos anteriores de invitado por nombre
o teléfono. Admin y Staff consultan aquí únicamente sus pedidos propios; la
consulta operativa del restaurante conserva su endpoint y permiso separados.
Pedidos inexistentes, ajenos o de invitado devuelven el mismo 404. Sesiones
ausentes, expiradas o revocadas devuelven 401; parámetros inválidos, 422, y
errores de base de datos, 503 sin datos internos.

Las respuestas incluyen `Cache-Control: no-store`; el detalle no expone contacto,
tokens, claves de idempotencia ni identidades internas del personal. Las lecturas
no alteran pedidos, beneficios ni precios; la sesión conserva su renovación normal.
El detalle recupera los descuentos y cupones históricos cuando existe su snapshot,
sin recalcularlos con el catálogo actual ni reservar o canjear beneficios.

`can_cancel` es true solo mientras el estado sea `Solicitud recibida`. Es
informativo: `POST /api/orders/{order_id}/cancel` sigue exigiendo propiedad,
sesión, Origin y CSRF, y revalida el estado al ejecutar. Después de cancelar,
el pedido permanece en el historial con su información original.

## Preparación de pedidos

Este bloque aplica los estados de preparación del Prompt Maestro v2.1, sección
6.4 y el contrato de HU-025: las nuevas aceptaciones usan `Confirmado` y se
conserva compatibilidad con `Aceptado` sin renombrar registros históricos.

| Método | Ruta | Transición permitida |
| --- | --- | --- |
| `POST` | `/api/admin/orders/{order_id}/prepare` | `Confirmado` (o `Aceptado` legado) → `En preparación`, en ambas modalidades. |
| `POST` | `/api/admin/orders/{order_id}/ready` | `En preparación` → `Listo para recoger`, únicamente para `pickup`. |

Ambas acciones reciben JSON vacío `{}` y requieren sesión de Admin o Staff con
`orders:write`, Origin autorizado y CSRF. Devuelven el detalle administrativo con
el historial actualizado y `Cache-Control: no-store`. Requieren el historial
instalado por la migración existente `20261121_0014`; no necesitan una migración
ni permisos adicionales. Un pedido inexistente devuelve 404; un estado o una
modalidad incompatibles, 409; campos como `status` o `actor_account_id`, 422.

El estado anterior y, al marcar listo, la modalidad se comprueban en el mismo
UPDATE. Intentos duplicados concurrentes producen una sola transición: el otro
recibe 409. Estado, fecha y responsable del historial se confirman en la misma
transacción; si falla el historial, se revierte el cambio. Ante una respuesta
perdida o incierta, consultar el detalle antes de repetir.

La preparación conserva productos, precios y descuentos históricos. Cerrar la
recepción no impide atender pedidos ya aceptados. El seguimiento de invitados
y el historial personal muestran el nuevo estado; la cancelación directa del
cliente sigue limitada a `Solicitud recibida`. Marcar listo no confirma entrega,
pago, venta, reserva ni canje de cupones. La atención continúa mediante las
acciones explícitas de servido o entrega descritas a continuación.

## Servido, entrega y finalización

Las acciones operativas de esta sección completan el recorrido de atención del
Prompt Maestro v2.1, sección 6.4. Cada confirmación requiere Admin o Staff con
`orders:write`, sesión, Origin y CSRF, y recibe únicamente JSON vacío `{}`.

| Método | Ruta | Modalidad y transición |
| --- | --- | --- |
| `POST` | `/api/admin/orders/{order_id}/serve` | Consumo local: `En preparación` → `Servido`. |
| `POST` | `/api/admin/orders/{order_id}/deliver` | Recogida: `Listo para recoger` → `Entregado`. |
| `POST` | `/api/admin/orders/{order_id}/complete` | Local: `Servido` → `Completado`; recogida: `Entregado` → `Completado`. |

Servir y entregar son confirmaciones manuales de recepción de los productos.
No completan automáticamente el pedido: finalizar la atención requiere su propia
acción. No se permite completar desde aceptado, preparación o listo para recoger,
ni intercambiar los estados de entrega entre modalidades. Pedidos desconocidos
devuelven 404; estados, modalidades o repeticiones incompatibles devuelven 409;
campos adicionales como `paid`, `status` o `actor_account_id` devuelven 422.

Estado y modalidad se comprueban en el UPDATE. Estado, fecha y responsable del
historial se confirman en la misma transacción. Un fallo del historial revierte
la transición y dos confirmaciones simultáneas de la misma acción producen una
sola transición. La respuesta incluye el detalle administrativo con
`Cache-Control: no-store`; ante resultado incierto, consultar el estado antes
de repetir. Se reutilizan la migración `0014` y los permisos existentes.

Los importes, productos, complementos y descuentos originales se conservan.
El seguimiento privado y el historial personal muestran el estado actual, incluso
después de completar. Se puede atender un pedido ya aceptado con la recepción
cerrada. Servir, entregar o completar no verifica pagos, registra ventas, genera
visitas ni reserva o canjea beneficios; esos acontecimientos permanecen separados.

## HU-031: confirmación manual de pago verificado

La migración aditiva `20261124_0015` incorpora `OrderPayments`, separada del
estado operativo. Guarda un identificador, pedido, importe decimal positivo y
finito, método (`cash`, `card`, `transfer`, `other`), fecha del servidor y cuenta
responsable. Una restricción única impide dos confirmaciones para el mismo pedido.
Las referencias protegidas impiden borrar el pedido o la cuenta mientras exista
su registro de pago. Cambiar el estado de atención no modifica ese registro.

| Método | Ruta | Resultado |
| --- | --- | --- |
| `GET` | `/api/admin/orders/{order_id}/payment` | Consulta `total`, `currency`, `payment_status` y `payment`; no registra cobros. |
| `POST` | `/api/admin/orders/{order_id}/payment` | Confirma manualmente el pago completo; devuelve 201 con la evidencia guardada. |

Ambas rutas requieren sesión de Admin o Staff activo con los permisos específicos
`payments:read` y `payments:write`, respectivamente. El POST exige Origin autorizado
y CSRF. El cuerpo recibe únicamente importe y método:

```json
{"amount":"170.00","method":"cash"}
```

El importe se envía como cadena decimal positiva, sin exponentes ni redondeo.
Debe coincidir exactamente con el total histórico, incluidos sus descuentos;
no se recalcula con precios actuales. Se rechazan números JSON, booleanos,
importes no finitos y campos administrativos con 422. El método puede ser
`cash`, `card`, `transfer` u `other`; todos representan comprobaciones manuales.
La fecha la genera PostgreSQL y el responsable se toma de la sesión autenticada.

Solo se cobra desde `Confirmado` (o `Aceptado` legado), preparación, listo para
recoger, servido, entregado o completado. Una solicitud aún no confirmada,
cancelada o rechazada devuelve 409. Un importe distinto del total, un pedido de
total cero o una segunda confirmación también devuelven 409. No se admiten pagos
parciales, sobrepagos ni correcciones en este bloque. Un pedido inexistente da
404; sin migración 0015 o ante un fallo de base de datos, 503.

La consulta devuelve `payment_status: unverified` y `payment: null` mientras no
exista confirmación; `verified` incluye `payment_id`, `amount`, `method`,
`verified_at` y `verified_by_account_id`. Para un total cero sin pago devuelve
`not_required`, que no acredita un cobro. El seguimiento público y el historial
personal mantienen sus contratos actuales sin exponer la identidad del empleado.
Las respuestas de las rutas de pagos incluyen `Cache-Control: no-store`.

Confirmar bloquea el pedido hasta el commit y revalida su estado después de
esperar. Intentos simultáneos generan una única evidencia; un fallo del commit
no responde con éxito. Ante una respuesta incierta, consultar el GET antes de
repetir. Confirmar el cobro no cambia estados ni crea eventos de preparación,
visitas, ventas o canjes; servir, entregar o completar tampoco crea un pago.

La migración no genera cobros históricos ni concede privilegios. Tras aplicar
0015, un operador debe actualizar las ACL con los scripts revisados de
`docs/database`: `update_admin_writer_accounts_orders.sql` para un escritor
existente o el perfil de preparación para uno nuevo. El escritor tiene SELECT e
INSERT por columna sobre pagos, sin UPDATE, DELETE ni inserción de `verified_at`.
Lectores públicos y workers continúan sin acceso. No hay otra migración para
este bloque del endpoint; estos scripts no se ejecutan automáticamente.

El registro se demostrará con datos sintéticos y no sustituye una venta o recibo
de Loyverse; HU-032, HU-033 y HU-035 siguen pendientes.

## HU-046: configuración de metas de fidelización

El primer bloque incorpora `LoyaltyGoals` mediante la migración aditiva
`20261124_0016`. Cada meta guarda su identificador estable, visitas requeridas,
nombre de nivel y recompensa, descripción opcional, descuento de referencia,
días de vigencia del futuro cupón y condiciones compatibles con `CouponConditions`.
Incluye activación explícita, versión y fecha y cuenta de la última modificación.

Las visitas requeridas son enteros positivos y no se repiten entre metas, para
evitar dos niveles en el mismo umbral. Los nombres deben ser visibles y las
condiciones declarar un alcance coherente; para productos se requiere indicar
sus identificadores y el tratamiento de complementos. Los días de vigencia
deben estar entre 1 y 32767, rango positivo del tipo de almacenamiento. Los
importes mínimos de las condiciones conservan su precisión decimal.

No se insertan reglas predeterminadas ni valores reales del restaurante. Una
meta nueva queda inactiva por defecto. Configurar una meta no registra visitas,
no reinicia progreso ni genera, reserva o canjea cupones. La identidad de la meta
servirá para asociar posteriormente las recompensas de cada cliente en HU-045.

HU-046 todavía está en desarrollo: faltan los endpoints de administración,
sus permisos y las comprobaciones del descuento y de los productos referidos.
La persistencia por sí sola no acredita una regla lista para emitir beneficios.
Los perfiles SQL reconocen 0016 conservando los permisos anteriores, incluidos
los pagos; no se concede acceso a `LoyaltyGoals` en este bloque. La migración
no modifica pedidos, pagos ni cupones existentes.

## 🚀 Inicio rápido (Windows)

**Requisitos:** Git, Python 3.13, PostgreSQL local y PowerShell.

```powershell
git clone https://github.com/Integracion-Continua-y-Entrega/mitbol-backend.git
cd mitbol-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configura `.env` para una **base PostgreSQL local nueva** (consulta `.env.example`). Después:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

📍 API local: `http://127.0.0.1:8000/api/menu` · Documentación: `http://127.0.0.1:8000/docs`

> ⚠️ **Nunca ejecutes migraciones de prueba contra Neon.** Verifica primero el valor de `DATABASE_URL`. No subas archivos `.env`, contraseñas ni tokens al repositorio.

## 🛡️ Seguridad y calidad

- Sesiones almacenadas en PostgreSQL, cookies `HttpOnly`, contraseñas protegidas con **Argon2id** y protección **CSRF** para operaciones sensibles.
- Roles de aplicación (Admin/Staff) separados de los usuarios PostgreSQL con permisos específicos de lectura, administración y sincronización.
- Validaciones, bloqueos y **transacciones** para proteger la integridad de los datos durante las sincronizaciones.
- Pruebas con **pytest** y PostgreSQL temporal; los workflows de CI no equivalen a un despliegue automático en Render.

---

**👥 MITBOL** · Proyecto integrador de Ingeniería de Software, **Universidad Veracruzana**. Desarrollado por el equipo MITBOL.
