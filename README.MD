# 🍔 MITBOL — Backend

**API REST para un menú digital con código QR, conectada con Loyverse.**

MITBOL es un proyecto web para modernizar la atención de un restaurante que utiliza **Loyverse** como punto de venta. Su API permite consultar un menú actualizado desde el celular, con productos, precios, variantes y complementos, sin necesidad de iniciar sesión.

> 📌 **Este repositorio contiene el backend.** La interfaz para clientes y personal se desarrolla por separado con React. Las funcionalidades de pedidos, pagos, promociones y fidelización forman parte de la visión del proyecto, pero **aún no están implementadas como flujos completos en este backend**.

## ✨ Funcionalidades principales

- 🍽️ **Menú público:** consulta de productos, categorías, precios en MXN, variantes, modificadores, complementos, imágenes y disponibilidad. Incluye búsqueda y filtros por categoría o etiquetas.
- 🛠️ **Administración del catálogo:** edición autorizada de descripciones, fotografías, etiquetas, disponibilidad, destacados y novedades.
- 🔄 **Integración con Loyverse:** sincronización de productos, variantes, categorías, modificadores y complementos, con actualizaciones que evitan duplicados y conservan los datos locales.
- 🏷️ **Descuentos comerciales:** importación y consulta de descuentos compatibles; los registros desconocidos se tratan de forma controlada, sin inventar valores.
- 🖼️ **Imágenes persistentes:** fotografías almacenadas en **Cloudflare R2** y consultadas a través de la API.
- 🔐 **Acceso administrativo:** autenticación por sesiones, roles y permisos para proteger las operaciones internas.

**En desarrollo o por validar:** integración completa del frontend, flujo administrativo de sincronización manual y comprobación de las ejecuciones programadas de descuentos. El carrito, los pedidos, el cobro y la fidelización corresponden a etapas posteriores.

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
