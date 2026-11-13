# MITBOL — Backend

**API para el menú digital y la administración del catálogo de MITBOL.**

MITBOL busca que los clientes de un restaurante puedan consultar su menú desde el teléfono mediante un código QR, con información clara sobre productos, precios y opciones disponibles. Este repositorio contiene el **backend** de la plataforma: el servicio que obtiene información comercial de Loyverse, la almacena en PostgreSQL y la pone a disposición del frontend a través de una API REST.

El proyecto está desarrollado con **Python y FastAPI**. Además de las consultas públicas del menú, incorpora herramientas para administrar contenido del catálogo, controlar su disponibilidad y proteger las operaciones del personal del restaurante.

> **Alcance:** este repositorio contiene únicamente el backend. La interfaz web se desarrolla por separado. Las funciones descritas a continuación reflejan el código revisado en la rama `develop`; no constituyen una verificación del despliegue actual en Render o Neon.

## Contenido

- [¿Qué permite hacer MITBOL?](#qué-permite-hacer-mitbol)
- [Tecnologías](#tecnologías)
- [Cómo está organizado](#cómo-está-organizado)
- [Primeros pasos](#primeros-pasos)
- [Configuración](#configuración)
- [API y documentación interactiva](#api-y-documentación-interactiva)
- [Autenticación y permisos](#autenticación-y-permisos)
- [Base de datos y migraciones](#base-de-datos-y-migraciones)
- [Integración con Loyverse](#integración-con-loyverse)
- [Pruebas automatizadas](#pruebas-automatizadas)
- [Integración continua con GitHub Actions](#integración-continua-con-github-actions)
- [Despliegue](#despliegue)
- [Colaborar en el proyecto](#colaborar-en-el-proyecto)
- [Documentación complementaria](#documentación-complementaria)
- [Equipo y licencia](#equipo-y-licencia)

## ¿Qué permite hacer MITBOL?

### Consultar el menú digital

La API proporciona un catálogo público con productos, precios, variantes, fotografías y disponibilidad. Cada producto puede consultarse individualmente para conocer sus opciones y complementos.

Los visitantes pueden **buscar productos por nombre** y **filtrarlos mediante etiquetas**. También se identifican productos destacados y novedades para que el frontend pueda presentarlos de forma especial. Los productos comercialmente activos pueden seguir apareciendo cuando están agotados, pero se informa que no están disponibles para pedir; los productos inactivos en el catálogo comercial se excluyen de las consultas públicas.

El backend también entrega una **URL configurable del menú** para facilitar su acceso mediante QR. La generación y presentación del código QR corresponden a la aplicación frontend.

### Administrar la información del catálogo

El personal autorizado dispone de operaciones para:

- Editar descripciones de productos.
- Agregar, ordenar y retirar fotografías asociadas a variantes.
- Crear etiquetas y asociarlas a variantes.
- Marcar productos o variantes como disponibles o no disponibles.
- Marcar productos como destacados o novedades.
- Consultar los descuentos comerciales que se hayan importado.

Los cambios locales —como fotografías, descripciones, etiquetas, destacados y disponibilidad— se mantienen separados de los datos comerciales que llegan desde Loyverse, de modo que una sincronización no los sobrescriba indiscriminadamente.

### Importar información de Loyverse

MITBOL incorpora un cliente para consultar la API de Loyverse y servicios para procesar productos, variantes, modificadores y descuentos. La información se guarda en PostgreSQL para que **cada visita al menú no dependa de una consulta directa a Loyverse**.

La importación cuenta con validaciones, control de duplicados y transacciones. Actualmente existen herramientas de ejecución controlada para la **carga inicial del catálogo en un destino vacío** y la **importación manual de descuentos**. Todavía no hay sincronización periódica ni una interfaz administrativa para iniciar y consultar ejecuciones.

### Funciones aún no disponibles

MITBOL continúa en desarrollo. En el backend revisado todavía no están integrados el carrito de compras, la creación y seguimiento de pedidos de clientes ni el canje de cupones. Los descuentos importados pueden consultarse administrativamente, pero **no se aplican todavía a pedidos**.

La búsqueda no incluye filtrado por categorías. Asimismo, las imágenes se almacenan en el sistema de archivos local: para un despliegue permanente se necesita almacenamiento duradero y una estrategia de respaldo.

## Tecnologías

| Tecnología                         | Uso dentro del proyecto                                                                                |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| **Python**                         | Lenguaje principal del backend. La integración continua utiliza Python 3.13.                           |
| **FastAPI**                        | Define la API HTTP, las rutas, las dependencias y la documentación OpenAPI.                            |
| **Uvicorn**                        | Servidor ASGI que ejecuta la aplicación.                                                               |
| **PostgreSQL**                     | Almacena el catálogo, sus relaciones y los datos de autenticación. Las pruebas utilizan PostgreSQL 18. |
| **SQLAlchemy 2**                   | Modelos ORM, consultas, sesiones y transacciones con la base de datos.                                 |
| **psycopg2**                       | Controlador que permite la comunicación entre SQLAlchemy y PostgreSQL.                                 |
| **Pydantic y pydantic-settings**   | Validación de los datos de entrada y salida, y gestión de la configuración.                            |
| **Alembic**                        | Mantiene el historial de cambios estructurales de la base de datos.                                    |
| **HTTPX y Tenacity**               | Consultas HTTP a Loyverse, con tiempos límite y reintentos controlados.                                |
| **argon2-cffi**                    | Protección de contraseñas mediante hashes Argon2id.                                                    |
| **Pillow y python-multipart**      | Recepción, validación y procesamiento de imágenes.                                                     |
| **pytest, pytest-asyncio y RESPX** | Pruebas automatizadas y simulación de respuestas HTTP externas.                                        |
| **GitHub Actions**                 | Ejecuta comprobaciones y pruebas cuando se proponen o integran cambios.                                |
| **Render y Neon**                  | Servicios contemplados para alojar la API y PostgreSQL, respectivamente.                               |

Las versiones concretas de las dependencias de Python están declaradas en [`requirements.txt`](requirements.txt).

## Cómo está organizado

MITBOL utiliza una **arquitectura cliente-servidor**. Su backend es una aplicación monolítica organizada por capas: cada módulo tiene una responsabilidad específica, sin necesidad de desplegar varios microservicios.

Una consulta típica sigue este recorrido:

```text
React (frontend)
      │  Solicitud HTTP
      ▼
Router de FastAPI
      │  Valida datos y comprueba permisos, si corresponde
      ▼
Servicio
      │  Aplica las reglas de negocio
      ▼
SQLAlchemy + psycopg2
      │  Ejecuta consultas
      ▼
PostgreSQL
```

Los **routers** reciben las solicitudes, los **schemas** de Pydantic describen y validan los datos, los **servicios** aplican las reglas de negocio y los **modelos** de SQLAlchemy representan la información persistida. Las **dependencias** de FastAPI suministran recursos, como una sesión de base de datos, o comprueban permisos antes de ejecutar una operación.

### Estructura principal del repositorio

```text
mitbol-backend/
├── app/
│   ├── main.py         # Creación de la aplicación y registro de rutas
│   ├── api/            # Endpoints públicos, administrativos y de autenticación
│   ├── core/           # Configuración y mecanismos de seguridad
│   ├── schemas/        # Contratos de entrada y salida
│   ├── models/         # Modelos de datos con SQLAlchemy
│   ├── services/       # Reglas de negocio e integración con Loyverse
│   ├── repositories/   # Acceso a datos de autenticación
│   ├── db/             # Conexiones y sesiones de PostgreSQL
│   └── cli/            # Herramientas operativas
├── migrations/         # Versiones de migraciones Alembic
├── tests/              # Pruebas automatizadas
├── scripts/            # Ejecución de pruebas y utilidades
├── docs/               # Documentación técnica y operativa
└── .github/workflows/  # Integración continua
```

Algunas rutas administrativas solo se registran cuando se habilita la configuración correspondiente. Por eso, **la API pública puede ejecutarse sin activar la administración**.

## Primeros pasos

Esta guía utiliza **Windows y PowerShell** y prepara una instancia local del backend con una base de datos propia. No modifica Neon ni necesita credenciales de Loyverse para consultar un catálogo ya almacenado.

### 1. Requisitos

- Git.
- Python **3.13**, con `pip` y `venv`.
- PostgreSQL; se recomienda la versión **18** para coincidir con las pruebas del proyecto.
- Acceso a Internet para instalar las dependencias.

Para utilizar los comandos de PostgreSQL, asegúrate de que su carpeta `bin` esté disponible en el `PATH`.

### 2. Descargar el repositorio e instalar las dependencias

```powershell
git clone https://github.com/Integracion-Continua-y-Entrega/mitbol-backend.git
cd mitbol-backend
git switch develop

python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

El ejemplo utiliza `develop`, la rama examinada para esta documentación. Si vas a trabajar en otra rama, acuerda con el equipo cuál corresponde. Los comandos llaman directamente al intérprete del entorno virtual, por lo que no necesitas activar el entorno desde PowerShell.

### 3. Crear una base de datos local

Con PostgreSQL iniciado, crea una base vacía para desarrollo. Sustituye `USUARIO_LOCAL` por tu usuario de PostgreSQL:

```powershell
createdb -h localhost -p 5432 -U USUARIO_LOCAL -W -T template0 -E UTF8 mitbol_dev
```

Ejecuta este comando **solo si la base aún no existe**. La contraseña se solicita de manera interactiva. No utilices una base compartida ni credenciales de Neon para estos pasos.

### 4. Configurar las variables de entorno

Crea tu archivo `.env` a partir de la plantilla, sin sobrescribir uno existente:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

Edita `.env` para apuntar a tu PostgreSQL local. Estos valores son únicamente ilustrativos:

```dotenv
APP_ENV=local
DATABASE_URL=postgresql+psycopg2://USUARIO:CONTRASENA@localhost:5432/mitbol_dev
CORS_ORIGINS=["http://localhost:5173"]
PUBLIC_MENU_URL=http://localhost:5173/
AUTH_ENABLED=false
AUTH_CATALOG_ENABLED=false
AUTH_IMAGES_ENABLED=false
```

Reemplaza los marcadores de usuario y contraseña por tus credenciales **solo en tu archivo privado**. Si la contraseña contiene caracteres reservados de una URL, deberán codificarse. Nunca subas `.env` al repositorio.

### 5. Crear las tablas mediante Alembic

Antes de ejecutar el siguiente comando, comprueba que `DATABASE_URL` apunte exclusivamente a tu base local vacía:

```powershell
.\.venv\Scripts\python.exe -B -m alembic upgrade head
```

Este comando crea la estructura de tablas correspondiente a la revisión más reciente. **No importa productos ni crea usuarios automáticamente.** Si trabajas con una base que ya contiene información, revisa primero su historial de migraciones; no la elimines ni fuerces su versión.

### 6. Iniciar FastAPI

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Con el servidor encendido, abre:

- **Documentación interactiva:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Menú público:** [http://127.0.0.1:8000/api/menu](http://127.0.0.1:8000/api/menu)
- **Configuración del menú:** [http://127.0.0.1:8000/api/menu/config](http://127.0.0.1:8000/api/menu/config)

También puedes comprobar las respuestas desde PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/
Invoke-RestMethod http://127.0.0.1:8000/api/menu
```

Es normal que el menú esté **vacío** en una instalación nueva: las migraciones crean las tablas, no cargan el catálogo de Loyverse. La ruta `GET /` solo verifica que la aplicación responde; para comprobar el acceso a PostgreSQL, consulta `/api/menu`.

Detén el servidor con `Ctrl+C`.

### Opcional: entorno de demostración administrativa

El repositorio incluye un laboratorio aislado con cuentas y datos sintéticos, útil para probar endpoints administrativos sin utilizar las cuentas ni la base de datos del restaurante.

```powershell
$env:MITBOL_TEST_PG_BIN = 'C:\Program Files\PostgreSQL\18\bin'
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --serve-admin
```

Ajusta la ruta a tu instalación de PostgreSQL. El laboratorio utiliza el puerto `8000`, así que debes detener antes cualquier otro servidor que lo ocupe. Sigue las indicaciones que aparezcan en la terminal y **no expongas el laboratorio a Internet ni a la red local**.

## Configuración

La configuración se centraliza en [`app/core/config.py`](app/core/config.py) y su plantilla está en [`.env.example`](.env.example). En el entorno local se puede leer `.env`; en pruebas y cloud se utilizan variables del proceso, sin cargar ese archivo. Las variables definidas por el proceso tienen prioridad.

| Variable                | Para qué sirve                                                                                        |
| ----------------------- | ----------------------------------------------------------------------------------------------------- |
| `APP_ENV`               | Selecciona el entorno: `local`, `test` o `cloud`.                                                     |
| `DATABASE_URL`          | Conexión principal a PostgreSQL; es obligatoria.                                                      |
| `CORS_ORIGINS`          | Orígenes web autorizados para consultar la API desde un navegador.                                    |
| `PUBLIC_MENU_URL`       | Dirección del frontend que se expone para el acceso mediante QR.                                      |
| `LOYVERSE_ACCESS_TOKEN` | Credencial privada para consultar la API de Loyverse.                                                 |
| `LOYVERSE_STORE_ID`     | Identificador de la tienda de Loyverse.                                                               |
| `IMAGE_STORAGE_ROOT`    | Carpeta local donde se almacenan fotografías.                                                         |
| `AUTH_ENABLED`          | Habilita los endpoints de autenticación individual.                                                   |
| `AUTH_CATALOG_ENABLED`  | Habilita la administración autenticada del catálogo.                                                  |
| `AUTH_IMAGES_ENABLED`   | Habilita escrituras de imágenes cuando están permitidas.                                              |
| `AUTH_DATABASE_URL`     | Conexión independiente para operaciones autenticadas que requieren escritura, especialmente en cloud. |

Para sesiones, cookies seguras, límites de acceso y perfiles de permisos hay variables adicionales. Están descritas en la [guía de integración administrativa](docs/security/admin_frontend_integration.md).

> **Seguridad:** el token de Loyverse y las cadenas de conexión pertenecen al backend. No deben incluirse en el código frontend, capturas públicas ni commits.

## API y documentación interactiva

FastAPI genera documentación basada en OpenAPI. Con el servidor local en funcionamiento puedes utilizar:

- [Swagger UI](http://127.0.0.1:8000/docs): permite explorar y probar las operaciones disponibles.
- [ReDoc](http://127.0.0.1:8000/redoc): presenta la documentación de la API.
- [OpenAPI JSON](http://127.0.0.1:8000/openapi.json): describe formalmente los endpoints y sus contratos.

### Endpoints públicos

| Método | Ruta                                                            | Descripción                                             |
| ------ | --------------------------------------------------------------- | ------------------------------------------------------- |
| `GET`  | `/`                                                             | Comprueba que el proceso HTTP responde.                 |
| `GET`  | `/api/menu`                                                     | Lista los productos publicados, con búsqueda y filtros. |
| `GET`  | `/api/menu/{product_id}`                                        | Devuelve el detalle de un producto activo.              |
| `GET`  | `/api/menu/tags`                                                | Obtiene las etiquetas del catálogo.                     |
| `GET`  | `/api/menu/config`                                              | Devuelve la URL configurada del menú, si existe.        |
| `GET`  | `/media/products/{product_id}/variants/{variant_id}/{filename}` | Entrega una fotografía pública de una variante válida.  |

Por ejemplo:

```http
GET /api/menu?q=hamburguesa&tag_id=1&tag_id=2
```

`q` busca coincidencias en el nombre del producto. Si se indican varias etiquetas, se aceptan productos que coincidan con **cualquiera** de ellas; la búsqueda por nombre también debe cumplirse. Los precios se devuelven como cadenas decimales para evitar errores de precisión y la moneda indicada por el contrato es **MXN**.

### Operaciones administrativas

Cuando la administración está habilitada, las rutas bajo `/api/admin` permiten consultar o modificar información del catálogo conforme al rol y los permisos de cada usuario.

| Recurso                | Operaciones disponibles                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| Descripciones          | Consultar y actualizar la descripción de un producto.                   |
| Disponibilidad         | Consultar y modificar la disponibilidad local de productos y variantes. |
| Fotografías            | Consultar, subir, ordenar y eliminar fotografías de variantes.          |
| Etiquetas              | Consultar, crear, asociar y retirar etiquetas.                          |
| Destacados y novedades | Consultar y actualizar los indicadores de productos.                    |
| Descuentos             | Consultar descuentos importados con paginación.                         |

Los métodos, cuerpos de solicitud y respuestas exactas se pueden consultar en Swagger cuando las rutas están activadas. **No existe actualmente una ruta administrativa registrada para iniciar sincronizaciones**.

## Autenticación y permisos

La administración utiliza un sistema de autenticación con **sesiones individuales**. Las contraseñas se verifican mediante Argon2id; la sesión se identifica con una cookie `HttpOnly` que puede caducar o revocarse. Las operaciones que modifican información están protegidas por permisos y controles CSRF.

El proyecto distingue dos conceptos:

- **Autenticación:** verificar quién intenta utilizar la aplicación.
- **Autorización:** verificar qué operaciones tiene permitido realizar.

En la configuración administrativa actual, **Admin** puede gestionar descripciones, imágenes, etiquetas, destacados y disponibilidad. **Staff** puede consultar información autorizada y modificar la disponibilidad, pero no dispone de todos los permisos de edición de Admin.

Estos roles pertenecen a la aplicación. Son diferentes de los **usuarios técnicos de PostgreSQL**, que controlan qué conexiones pueden leer o escribir información en la base de datos.

La autenticación está **deshabilitada por defecto** y necesita configuración adicional para ponerse en marcha. No se utilizan JWT: el sistema implementa sesiones opacas persistidas en PostgreSQL. Para integrar un frontend con estas sesiones, consulta la [guía de autenticación y permisos](docs/security/admin_frontend_integration.md).

## Base de datos y migraciones

El catálogo y la información de autenticación se almacenan en PostgreSQL. SQLAlchemy representa las tablas mediante modelos y gestiona las consultas; **Alembic versiona los cambios en la estructura de la base de datos**.

Entre las entidades del catálogo se encuentran productos, variantes, modificadores, complementos, fotografías, etiquetas y descuentos. El esquema de identidad incorpora las estructuras necesarias para cuentas, roles, sesiones, auditoría y controles de acceso.

El historial de migraciones vigente en el código revisado incluye:

| Revisión        | Cambio principal                                     |
| --------------- | ---------------------------------------------------- |
| `20261029_0001` | Estructura inicial del catálogo comercial.           |
| `20261102_0002` | Fotografías y etiquetas locales.                     |
| `20261102_0003` | Separación de disponibilidad comercial y local.      |
| `20261105_0004` | Identidad, roles, sesiones y auditoría.              |
| `20261106_0005` | Controles persistentes de intentos de autenticación. |
| `20261111_0006` | Descuentos comerciales.                              |

Para **consultar el historial sin modificar una base de datos**:

```powershell
.\.venv\Scripts\python.exe -B -m alembic history
.\.venv\Scripts\python.exe -B -m alembic heads
```

La aplicación **no crea tablas automáticamente al iniciarse**: los cambios del esquema se administran mediante migraciones. Las operaciones sobre una base existente requieren revisar su versión y coordinarse con el responsable de datos.

## Integración con Loyverse

Loyverse suministra información comercial; MITBOL la transforma y mantiene una copia local para servir el menú con rapidez y controlar su presentación.

```text
Loyverse API
     │  HTTPX: consulta de productos, variantes, modificadores y descuentos
     ▼
Validación y transformación
     │  Comprobación de relaciones, datos y precios
     ▼
Sincronización con SQLAlchemy
     │  UPSERT y transacciones
     ▼
PostgreSQL
     │
     ▼
API pública de MITBOL → Frontend
```

La sincronización utiliza el identificador externo de Loyverse para **insertar productos nuevos o actualizar los existentes sin duplicarlos**. Los campos locales, como descripciones personalizadas, etiquetas, fotografías, disponibilidad y destacados, se conservan al actualizar los datos comerciales.

Las operaciones de escritura se realizan mediante transacciones; si ocurre un error, pueden revertirse para no dejar una importación a medias. Para ciertos errores temporales de red, el cliente aplica reintentos acotados. Una respuesta incompleta del proveedor no se interpreta automáticamente como una orden para borrar productos.

### Herramientas disponibles

| Herramienta              | Propósito                                                                                           |
| ------------------------ | --------------------------------------------------------------------------------------------------- |
| `app.cli.probe_loyverse` | Consultar e inspeccionar información de Loyverse sin importarla a PostgreSQL.                       |
| `app.cli.sync_catalog`   | Ejecutar una carga inicial controlada en un catálogo vacío, con comprobaciones y aprobación previa. |
| `app.cli.sync_discounts` | Revisar y ejecutar manualmente la importación de descuentos.                                        |

Puedes ver las opciones de las herramientas de importación **sin ejecutarlas**:

```powershell
.\.venv\Scripts\python.exe -B -m app.cli.sync_catalog --help
.\.venv\Scripts\python.exe -B -m app.cli.sync_discounts --help
```

> **Atención:** las importaciones reales son tareas de operación, no pasos de instalación. Requieren validar tienda, credenciales, permisos, migraciones y destino. No deben ejecutarse contra Neon ni otros servicios compartidos sin autorización.

## Pruebas automatizadas

El proyecto utiliza **pytest** para comprobar endpoints, validaciones, permisos, sincronización, migraciones y comportamiento de la base de datos. Las respuestas externas de Loyverse se simulan para que las pruebas no dependan de su disponibilidad.

Las pruebas de base de datos no usan únicamente objetos simulados: crean **instancias temporales de PostgreSQL 18**. Así se pueden comprobar restricciones, transacciones y operaciones SQL reales sin utilizar Neon ni la base del equipo.

### Ejecutar toda la colección de pruebas

En Windows, desde la raíz del repositorio y con las dependencias instaladas:

```powershell
$env:MITBOL_TEST_PG_BIN = 'C:\Program Files\PostgreSQL\18\bin'
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader tests -q
```

Ajusta la ruta a la ubicación de PostgreSQL. El script de pruebas prepara un entorno aislado y evita reutilizar la configuración sensible del desarrollo local. **No es necesario proporcionar credenciales reales de Loyverse o Neon.**

El comando anterior solicita toda la colección `tests`. Ejecutar el script sin ese argumento puede seleccionar un conjunto más reducido de pruebas, por lo que conviene distinguir ambos modos.

## Integración continua con GitHub Actions

Las pruebas también se ejecutan automáticamente mediante **GitHub Actions**, la herramienta de automatización integrada en GitHub.

El repositorio dispone del workflow [`Backend CI`](.github/workflows/backend-ci.yml), que se activa cuando:

- Se publica un cambio en `develop`.
- Se abre o actualiza un Pull Request dirigido a `develop` o `main`.

En cada ejecución, GitHub prepara un entorno Ubuntu con Python 3.13 y PostgreSQL 18, instala las dependencias y lanza el ejecutor de pruebas. Al terminar, genera un reporte JUnit que facilita revisar los resultados.

Si las comprobaciones fallan, el job aparece como **fallido**. Para impedir automáticamente que se fusione un Pull Request, GitHub también debe tener configurada una regla de protección de rama que exija aprobar ese check.

El comando configurado en el workflow es:

```text
python -B scripts/test_auth_phase1.py --with-reader -ra --junitxml=test-results/pytest.xml
```

**Este workflow implementa integración continua (CI), no despliegue continuo.** No publica versiones en Render. El conjunto exacto de pruebas que ejecuta se determina por los argumentos y valores predeterminados del ejecutor; no debe confundirse automáticamente con la invocación local que incluye `tests -q`.

## Despliegue

El backend está preparado para ejecutarse como servicio web en **Render** y conectarse a una base de datos **PostgreSQL de Neon**. La configuración remota debe revisarse de forma independiente de la instalación local.

Como referencia, el servicio utiliza estos comandos:

**Instalación:**

```bash
python -m pip install -r requirements.txt
```

**Inicio:**

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

En cloud, la configuración se proporciona mediante variables del entorno del servicio; **no se utiliza el archivo `.env` local**. Las conexiones a la base de datos deben validar su destino, utilizar TLS y contar con permisos mínimos según las operaciones que necesiten realizar.

El acceso público puede funcionar con una conexión de lectura, mientras que las operaciones administrativas autenticadas requieren una conexión y permisos de escritura configurados por separado. Las migraciones, la preparación de permisos y las sincronizaciones se ejecutan como tareas controladas, **no durante el arranque habitual del servidor**.

Antes de un despliegue permanente deben revisarse especialmente el almacenamiento duradero de imágenes, la configuración de sesiones y cookies, los orígenes autorizados y la actualización coordinada del esquema.

Para los detalles de infraestructura, consulta las guías de [Render](docs/deployment/render_stage3f.md), [Neon](docs/database/neon_stage3c.md) y [seguridad administrativa](docs/security/admin_frontend_integration.md).

## Colaborar en el proyecto

Para mantener un repositorio entendible y fácil de mantener, proponemos este flujo de trabajo:

1. Crea una rama de trabajo a partir de la rama acordada por el equipo.
2. Realiza cambios pequeños y con un propósito claro.
3. Añade o actualiza pruebas cuando cambie el comportamiento.
4. Ejecuta las comprobaciones pertinentes antes de compartir los cambios.
5. Abre un Pull Request con una explicación de lo implementado y cualquier requisito de migración o configuración.
6. Revisa el resultado de GitHub Actions antes de integrar los cambios.

Se recomiendan mensajes de commit descriptivos, por ejemplo, con prefijos `feat`, `fix`, `docs` o `test`. Estos prefijos son una convención sugerida, no una regla automática del repositorio.

Nunca incluyas contraseñas, tokens, archivos `.env` ni datos privados en los commits.

## Documentación complementaria

El README es el punto de entrada al proyecto. Los procedimientos operativos y las decisiones más detalladas se conservan en `docs/`.

| Tema                                                   | Documento                                                                          |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| Integración del frontend con sesiones administrativas  | [Guía de autenticación e integración](docs/security/admin_frontend_integration.md) |
| Desarrollo del frontend local contra un backend remoto | [Guía de desarrollo remoto](docs/security/frontend_remote_dev.md)                  |
| Configuración de PostgreSQL en Neon                    | [Guía de base de datos](docs/database/neon_stage3c.md)                             |
| Preparación de la importación de catálogo              | [Guía de sincronización](docs/loyverse/neon_stage3e.md)                            |
| Despliegue inicial en Render                           | [Guía de despliegue](docs/deployment/render_stage3f.md)                            |
| Importación de descuentos                              | [Guía de descuentos](docs/deployment/hu009_rollout.md)                             |

Algunas guías documentan decisiones y configuraciones de fases anteriores. Si existe una diferencia, consulta primero el código, las migraciones y los workflows vigentes antes de ejecutar un procedimiento.

## Equipo y licencia

**Colaboradores identificados en el repositorio del backend:**

- José Fernando Enríquez Maldonado.
- Pedro Alonso Montes Fabian.
