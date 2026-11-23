# 🍔 MITBOL — Backend

**API REST para un menú digital con código QR, conectada con Loyverse.**

MITBOL es un proyecto web para modernizar la atención de un restaurante que utiliza **Loyverse** como punto de venta. Su API permite consultar un menú actualizado desde el celular, con productos, precios, variantes y complementos, sin necesidad de iniciar sesión.

> 📌 **Este repositorio contiene el backend.** La interfaz para clientes y personal se desarrolla por separado con React. La API ya permite preparar, registrar, consultar y decidir solicitudes de pedido, iniciar su preparación y marcar pedidos de recogida como listos. Entrega, finalización, pagos, conciliación de ventas y fidelización completa corresponden a etapas posteriores.

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
- 📋 **Atención de solicitudes:** seguimiento privado de invitados, cancelación pendiente, aceptación/rechazo, preparación, pedidos listos para recoger e historial de estados.
- 🧾 **Mis pedidos:** historial paginado y detalle privado de solicitudes asociadas a la cuenta autenticada, con importes históricos y estado actual.

**Validación independiente:** integración del frontend, configuración y funcionamiento de los servicios desplegados y ejecuciones programadas. La implementación de endpoints y sus pruebas de backend no acredita esos entornos.

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
6.4, manteniendo `Aceptado` como nombre del estado confirmado que ya utiliza la
API. No renombra registros históricos ni cambia el contrato de aceptación.

| Método | Ruta | Transición permitida |
| --- | --- | --- |
| `POST` | `/api/admin/orders/{order_id}/prepare` | `Aceptado` → `En preparación`, en ambas modalidades. |
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
pago, venta, reserva ni canje de cupones. Consumo local permanece en preparación
hasta la futura acción de servido; este bloque no implementa entrega o finalización.

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
