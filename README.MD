# ⚙️ Backend - API Menú Digital mitbol

Este repositorio contiene la capa de lógica de negocio del sistema de menú digital para el restaurante **mitbol**. Expone una API RESTful que alimenta la interfaz de los comensales y el panel de administración.

## 🚀 Tecnologías Utilizadas

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
- **Servidor ASGI:** Uvicorn
- **Base de Datos:** PostgreSQL
- **ORM:** SQLAlchemy
- **Integración:** API de Loyverse POS

## 📁 Estructura del Proyecto

El proyecto sigue una arquitectura por capas dentro del directorio `/app`:

- `api/`: Controladores y definición de endpoints.
- `schemas/`: Validación de datos para peticiones y respuestas.
- `models/`: Definición de tablas relacionales.
- `services/`: Lógica de integración externa.

## 🛠️ Instalación y Uso Local

Sigue estos pasos para correr el proyecto en tu entorno local de Windows:

1. **Clona el repositorio y entra a la carpeta:**

```bash
   git clone https://github.com/Integracion-Continua-y-Entrega/mitbol-backend.git
   cd mitbol-backend
```

2. **Crea y activa un entorno virtual:**

```
   python -m venv venv
   venv\Scripts\activate
```

3. **Instala las dependencias:**

```bash
   pip install -r requirements.txt
```

4. **Configura las variables de entorno:**
   - En la raíz del proyecto, haz una copia del archivo de ejemplo para crear tu propio archivo de configuración local ejecutando este comando:

```
     copy .env.example .env
```

- Abre el nuevo archivo `.env` y reemplaza los valores de ejemplo con tus credenciales reales.

5. **Revisa las migraciones antes de aplicarlas:**

   Los modelos vigentes corresponden a las cinco tablas del catálogo de HU-008.
   Alembic administra el esquema; iniciar FastAPI ya no crea tablas.
   En el entorno de desarrollo verificado se creó `mitbol_dev` y se aplicó la
   revisión `20261029_0001` con autorización. La conexión local apunta a esa base;
   `mitbol_db` conserva íntegramente las tablas y datos del prototipo.

   Consulta la [guía del esquema, pruebas y comandos](docs/database/hu008_preparation.md)
   y el [SQL de la migración inicial](docs/database/hu008_initial.sql).

   Para ver el SQL sin ejecutarlo:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic upgrade head --sql
   ```

   Para comprobar la revisión y la correspondencia con los modelos:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic current
   .\.venv\Scripts\python.exe -m alembic check
   ```

   Las pruebas de PostgreSQL son optativas y sólo consultan `mitbol_dev`:

   ```powershell
   $env:MITBOL_POSTGRES_TESTS = '1'
   .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
   ```

6. **Levanta el servidor de desarrollo:**

```bash
   uvicorn app.main:app --reload
```

La documentación automática de la API estará disponible en: `http://localhost:8000/docs`.

## Consulta de Loyverse — HU-008, Bloque 1

El diagnóstico local consulta tiendas, artículos con variantes y grupos con sus
opciones mediante GET. No escribe en PostgreSQL ni utiliza el servicio de
sincronización del prototipo. Configura `LOYVERSE_ACCESS_TOKEN` y el identificador
de la tienda en `LOYVERSE_STORE_ID` dentro de `.env`.

```powershell
.\.venv\Scripts\python.exe -m app.cli.probe_loyverse --page-size 2
```

La [guía del Bloque 1](docs/loyverse/hu008_block1.md) explica el código, las pruebas,
la procedencia de los precios y la diferencia observada entre `modifier_ids` y
`modifiers_ids`. El [informe de lectura real](docs/loyverse/hu008_block1_report.json)
contiene el catálogo observado, sin credenciales ni cabeceras.

## Persistencia del catálogo — HU-008, Bloque 2

La transformación y los UPSERT están implementados y verificados con PostgreSQL
aislado y datos simulados. El Bloque 3 aplicó después una primera carga real
autorizada, documentada en el apartado siguiente. No hay un endpoint de escritura.

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
$env:MITBOL_SYNC_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Las pruebas de persistencia crean otra base `mitbol_test_hu008_<id>`, aplican la
migración existente y revierten sus datos mediante transacciones y savepoints.
No escriben en el catálogo de desarrollo. La base de pruebas se conserva.

Consulta la [guía del Bloque 2](docs/loyverse/hu008_block2.md) y el
[informe de aislamiento](docs/loyverse/hu008_block2_test_database.json).

## Primera carga real — HU-008, Bloque 3

Se realizó una sola sincronización autorizada en `mitbol_dev`, desde la tienda
I.C.E: 3 productos, 5 variantes, 2 grupos, 5 complementos y 4 asociaciones.
La verificación posterior fue de sólo lectura: no encontró duplicados,
referencias huérfanas ni precios inválidos. La revisión sigue siendo `20261029_0001`.

La [guía del Bloque 3](docs/loyverse/hu008_block3.md) explica la ejecución y los
resultados. El [informe](docs/loyverse/hu008_block3_report.json) conserva el plan
previo y las filas confirmadas. Puedes ejecutar las
[consultas SQL de verificación](docs/loyverse/hu008_block3_verify.sql) en pgAdmin
conectado a `mitbol_dev`; son de sólo lectura.

La repetición real y la actualización de precios quedan para el Bloque 4.

## Idempotencia real — HU-008, Bloque 4, primera parte

Se repitió una sola sincronización real con el catálogo de I.C.E sin cambios.
Se conservaron los conteos 3/5/2/5/4, todos los IDs internos y externos,
precios, relaciones y campos locales. Sólo cambió `synced_at` en las filas
comerciales; las secuencias avanzaron por los UPSERT sin reasignar los IDs.
La suite completa dio 124 pruebas aprobadas.

Consulta la [explicación de idempotencia](docs/loyverse/hu008_block4_idempotency.md)
y el [informe antes/después](docs/loyverse/hu008_block4_idempotency_report.json).
Después de esta prueba, el usuario confirmó el cambio manual de Doble de 119
a 129 en Loyverse. Su importación se verificó en la segunda parte siguiente.

## Actualización comercial real — HU-008, Bloque 4, segunda parte

Se confirmó que I.C.E devuelve Doble a 129.00 y se aplicó una sincronización
controlada en `mitbol_dev`. La variante conservó el ID 5, su UUID de Loyverse,
el producto padre 3 y sus asociaciones. Los demás precios y campos locales
permanecieron iguales, sin duplicados y con los conteos 3/5/2/5/4.

La suite dio 125 pruebas aprobadas y las siete comprobaciones posteriores
también pasaron. Consulta la [explicación de actualización de precios](docs/loyverse/hu008_block4_price_update.md),
el [informe antes/después](docs/loyverse/hu008_block4_price_update_report.json)
y las [consultas para pgAdmin](docs/loyverse/hu008_block4_price_update_verify.sql).
El esquema y las migraciones se conservaron; no hay sincronización periódica.

## 👥 Equipo de Desarrollo

- José Fernando Enríquez Maldonado
- Pedro Alonso Montes Fabian
