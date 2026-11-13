# MITBOL — Backend

**API REST para el menú digital de MITBOL, desarrollada con Python, FastAPI y PostgreSQL.**

MITBOL permite que los clientes consulten desde su teléfono los productos de un restaurante, sus precios, presentaciones y complementos. El backend conserva la información comercial en PostgreSQL y la ofrece al frontend sin consultar Loyverse en cada visita.

Este repositorio contiene **únicamente el backend**; la interfaz web se desarrolla por separado con React.

> **Sobre las versiones:** las funciones descritas como _publicadas_ se basan en el documento OpenAPI compartido el **12 de noviembre de 2026**. El código de `develop` puede contener cambios más recientes que todavía no se han desplegado en Render. La presencia de una ruta en OpenAPI no confirma, por sí sola, que su base de datos o sus permisos funcionen correctamente.

## Funcionalidades

### Menú digital

La API publicada permite:

- Consultar productos, precios y presentaciones sin iniciar sesión.
- Ver los detalles de un producto, incluidos sus grupos de modificadores y complementos.
- Consultar fotografías y descripciones cuando estén disponibles.
- Distinguir productos disponibles y agotados mediante el campo `can_order`. Los agotados pueden continuar visibles; los productos comercialmente inactivos no se muestran.
- Obtener la dirección pública configurada del menú para que el frontend genere un código QR.

Los precios se entregan como cadenas decimales y la moneda del menú es **MXN**. La consulta pública lee PostgreSQL, no Loyverse directamente.

### Administración del catálogo

Las rutas administrativas publicadas permiten al personal autorizado:

- Consultar y cambiar la disponibilidad local de productos y variantes.
- Consultar, actualizar o borrar descripciones de productos.
- Agregar, consultar, ordenar y retirar fotografías de variantes.
- Crear etiquetas y asociarlas o retirarlas de variantes.

El acceso se protege mediante **sesiones individuales, permisos y tokens CSRF**. Las operaciones de escritura requieren las condiciones de seguridad correspondientes; aparecer en OpenAPI no significa que cualquier usuario pueda ejecutarlas.

### Integración con Loyverse

El repositorio incorpora herramientas para importar información comercial de Loyverse y almacenarla en PostgreSQL. La importación utiliza identificadores externos, validaciones y transacciones para evitar duplicados y preservar la información local del catálogo.

La carga inicial y otras sincronizaciones se administran mediante **herramientas operativas del backend**, no mediante una ruta de sincronización publicada en esta versión de la API. Tampoco existe una sincronización periódica documentada como activa.

### Funciones que todavía no anunciamos como publicadas

El OpenAPI compartido **no documenta** endpoints para buscar productos por nombre o filtrar el menú, consultar destacados y novedades, consultar descuentos, gestionar sincronizaciones, registrar pedidos o administrar carritos. Algunas de estas capacidades pueden existir en `develop` o como herramientas internas, pero no deben confundirse con las rutas del despliegue actual.

## Tecnologías

| Tecnología                    | Uso en MITBOL                                          |
| ----------------------------- | ------------------------------------------------------ |
| **Python, FastAPI y Uvicorn** | Implementación de la API y ejecución del servidor.     |
| **PostgreSQL y Neon**         | Base de datos relacional y alojamiento administrado.   |
| **SQLAlchemy y psycopg2**     | Modelos, consultas y conexión con PostgreSQL.          |
| **Pydantic**                  | Validación de solicitudes, respuestas y configuración. |
| **Alembic**                   | Migraciones versionadas de la base de datos.           |
| **HTTPX y Tenacity**          | Comunicación con Loyverse y reintentos controlados.    |
| **Argon2id**                  | Almacenamiento seguro de hashes de contraseñas.        |
| **Pillow**                    | Validación y procesamiento de fotografías.             |
| **pytest y GitHub Actions**   | Pruebas automatizadas e integración continua.          |
| **Render**                    | Alojamiento del servicio FastAPI.                      |

Las dependencias utilizadas por el repositorio se encuentran en [`requirements.txt`](requirements.txt).

## Arquitectura

El backend es una aplicación organizada por capas:

```text
Frontend React
    ↓ HTTP
FastAPI (routers y schemas)
    ↓
Servicios (reglas de negocio)
    ↓
SQLAlchemy + psycopg2
    ↓
PostgreSQL (local o Neon)
```

Las carpetas principales son `app/api/` (rutas), `app/schemas/` (validación), `app/services/` (lógica), `app/models/` (tablas), `app/db/` (conexiones), `migrations/` (Alembic) y `tests/` (pruebas).

## Instalación local en Windows

Estos pasos preparan una **instalación nueva con una base local**. No modifican Neon ni Render.

**Requisitos:** Git, Python 3.13, PostgreSQL local y PowerShell. Las pruebas automatizadas del proyecto utilizan PostgreSQL 18.

### 1. Preparar el proyecto

```powershell
git clone https://github.com/Integracion-Continua-y-Entrega/mitbol-backend.git
cd mitbol-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Selecciona la rama que haya acordado el equipo antes de comenzar a trabajar.

### 2. Preparar PostgreSQL

Con PostgreSQL iniciado, crea una base local nueva (sustituye `USUARIO_LOCAL` por tu usuario):

```powershell
createdb -h localhost -p 5432 -U USUARIO_LOCAL -W -T template0 -E UTF8 mitbol_dev
```

Crea la configuración local sin sobrescribir un `.env` existente:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Ajusta en `.env` los valores necesarios, especialmente:

```dotenv
APP_ENV=local
DATABASE_URL=postgresql+psycopg2://USUARIO:CONTRASENA@localhost:5432/mitbol_dev
CORS_ORIGINS=["http://localhost:5173"]
PUBLIC_MENU_URL=http://localhost:5173/
AUTH_ENABLED=false
```

No subas `.env` al repositorio. El menú público local puede iniciarse sin credenciales de Loyverse; una base nueva aparecerá vacía hasta que se incorporen productos.

### 3. Aplicar migraciones e iniciar

**Verifica que `DATABASE_URL` apunta a tu base local nueva, nunca a Neon, antes de ejecutar Alembic.**

```powershell
.\.venv\Scripts\python.exe -B -m alembic upgrade head
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Prueba [`http://127.0.0.1:8000/api/menu`](http://127.0.0.1:8000/api/menu). La documentación interactiva de FastAPI suele estar disponible en [`/docs`](http://127.0.0.1:8000/docs).

La configuración de usuarios administrativos requiere pasos adicionales: consulta la [guía de integración administrativa](docs/security/admin_frontend_integration.md).

## API documentada en el despliegue compartido

### Consultas públicas

| Método | Ruta                                                            | Descripción                                                |
| ------ | --------------------------------------------------------------- | ---------------------------------------------------------- |
| `GET`  | `/`                                                             | Respuesta básica del proceso HTTP; no verifica PostgreSQL. |
| `GET`  | `/api/menu`                                                     | Productos, variantes, precios y disponibilidad.            |
| `GET`  | `/api/menu/{product_id}`                                        | Detalles, modificadores y complementos del producto.       |
| `GET`  | `/api/menu/config`                                              | Dirección pública del menú configurada para el QR.         |
| `GET`  | `/media/products/{product_id}/variants/{variant_id}/{filename}` | Fotografía publicada de una variante.                      |

### Autenticación y administración

La API expone rutas de inicio y cierre de sesión, consulta de usuario, obtención de token CSRF y cambio de contraseña bajo `/api/auth/`.

La administración se encuentra bajo `/api/admin/`:

| Recurso                                 | Operaciones documentadas                                                   |
| --------------------------------------- | -------------------------------------------------------------------------- |
| Disponibilidad de productos y variantes | Consultar y modificar (`GET`, `PATCH`).                                    |
| Descripciones de productos              | Consultar y modificar (`GET`, `PATCH`).                                    |
| Fotografías de variantes                | Consultar, subir, reordenar y eliminar (`GET`, `POST`, `PATCH`, `DELETE`). |
| Etiquetas                               | Consultar, crear, asociar y retirar (`GET`, `POST`, `PUT`, `DELETE`).      |

Los permisos y formatos exactos de cada solicitud pueden consultarse en el OpenAPI del entorno correspondiente. La documentación de las rutas **no sustituye las pruebas funcionales de ese entorno**.

## Configuración y seguridad

La referencia de configuración es [`.env.example`](.env.example). Entre las variables principales están `APP_ENV`, `DATABASE_URL`, `CORS_ORIGINS`, `PUBLIC_MENU_URL`, `LOYVERSE_ACCESS_TOKEN` y `LOYVERSE_STORE_ID`.

Para utilizar sesiones administrativas se necesitan ajustes adicionales, como `AUTH_ENABLED`, orígenes autorizados y una conexión de escritura restringida. Los roles **Admin y Staff** determinan permisos dentro de MITBOL; los roles PostgreSQL controlan qué operaciones puede ejecutar cada conexión a la base de datos.

Las contraseñas se protegen con Argon2id y las sesiones utilizan cookies `HttpOnly`. Las escrituras autenticadas requieren protección CSRF. Consulta la [documentación de seguridad](docs/security/admin_frontend_integration.md) antes de integrar estas operaciones con React.

## Base de datos y migraciones

**Render ejecuta la aplicación; Neon almacena PostgreSQL.** Publicar una nueva versión en Render **no actualiza automáticamente** la estructura de Neon.

Los archivos de `migrations/versions/` describen cambios versionados gestionados por **Alembic**. Para una base local nueva se utiliza `alembic upgrade head`; para una base existente deben revisarse primero el esquema, las revisiones aplicadas y una copia recuperable.

**No borres tablas ni ejecutes migraciones contra Neon solo para actualizar el README.** Las migraciones, la sincronización y el despliegue son operaciones independientes.

## Pruebas y GitHub Actions

El repositorio utiliza **pytest** y PostgreSQL temporal para validar la API, la seguridad y la persistencia sin depender de la base Neon. En Windows, con PostgreSQL 18 instalado:

```powershell
$env:MITBOL_TEST_PG_BIN = 'C:\Program Files\PostgreSQL\18\bin'
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader tests -q
```

El workflow [`Backend CI`](.github/workflows/backend-ci.yml) ejecuta comprobaciones automáticas en cambios enviados a `develop` y Pull Requests dirigidos a `develop` o `main`. **Este workflow verifica código; no despliega automáticamente la API en Render.**

## Despliegue y documentación

El backend se aloja en **Render** y utiliza **Neon** como base de datos administrada. Las credenciales deben configurarse como variables privadas del servicio, nunca guardarse en Git.

Antes de actualizar Render, verifica qué commit está publicado y si el esquema de Neon es compatible con la nueva versión. Las fotografías almacenadas en el sistema de archivos local necesitan una solución duradera para evitar pérdidas entre despliegues.

Guías útiles:

- [Integración administrativa](docs/security/admin_frontend_integration.md)
- [Frontend local con backend remoto](docs/security/frontend_remote_dev.md)
- [Configuración de Neon](docs/database/neon_stage3c.md)
- [Despliegue en Render](docs/deployment/render_stage3f.md)

## Equipo y licencia

El proyecto es desarrollado por el equipo MITBOL. El repositorio no declara una licencia de software.
