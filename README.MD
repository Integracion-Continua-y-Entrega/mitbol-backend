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

## 👥 Equipo de Desarrollo

- José Fernando Enríquez Maldonado
- Pedro Alonso Montes Fabian
