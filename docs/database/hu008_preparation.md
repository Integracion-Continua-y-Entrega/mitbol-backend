# HU-008: preparación del esquema y Alembic

## 1. ¿Qué implementamos?

Se prepararon los modelos y la revisión inicial del catálogo de HU-008. Se retiró
la creación automática de tablas al iniciar FastAPI. No se implementó todavía
la nueva sincronización con Loyverse ni su ejecución periódica.

**Estado actual, 29 de octubre de 2026:** con autorización del usuario se creó
`mitbol_dev`, se actualizó `DATABASE_URL` en `.env` y se aplicó la revisión
`20261029_0001`. Las 26 pruebas pasan, incluidas seis verificaciones del esquema
real en modo de sólo lectura. `mitbol_db` conserva su esquema y contenido.
El informe detallado está en [hu008_postgresql_verification.json](hu008_postgresql_verification.json).

La inspección inicial del 29 de octubre de 2026 se ejecutó con
`default_transaction_read_only=on` y confirmó:

- PostgreSQL 18.2, base del prototipo `mitbol_db`, rol `postgres` con permiso CREATEDB.
- Únicamente `public.items` y `public.categories` en la base del prototipo.
- `mitbol_dev` no existía antes de la creación autorizada.

### Decisión aplicada

| Alternativa | Efecto | Recomendación |
| --- | --- | --- |
| Crear `mitbol_dev` desde `template0` | Separa el catálogo real del prototipo y evita borrar objetos | Aplicada con autorización |
| Retirar `items` y `categories` de `mitbol_db` | Elimina datos y modifica una base ya utilizada | No se preparó una migración destructiva para esta opción |

Crear una base desde `template0` obtiene una base limpia. No se clona `mitbol_db`.
Referencia: [CREATE DATABASE en PostgreSQL 18](https://www.postgresql.org/docs/18/sql-createdatabase.html).

### Cambios aplicados en PostgreSQL

1. Se creó la base vacía `mitbol_dev`.
2. Se apuntó `DATABASE_URL` a esa base conservando servidor, puerto, credenciales
   y las demás variables de `.env`. Se verificó el destino en la configuración,
   el motor SQLAlchemy y mediante `SELECT current_database()` antes de migrar.
3. Se aplicó la revisión `20261029_0001`, que creó:

| Tabla | Columnas |
| --- | --- |
| `Products` | `product_id`, `loyverse_id`, `name`, `description`, `is_available`, `is_active`, `is_featured`, `is_new`, `synced_at` |
| `ProductVariants` | `product_variant_id`, `loyverse_id`, `name`, `price`, `is_available`, `synced_at`, `product_id` |
| `ProductModifiers` | `product_modifier_id`, `loyverse_id`, `name`, `display_order`, `synced_at` |
| `ProductComplements` | `product_complement_id`, `product_modifier_id`, `loyverse_id`, `name`, `price`, `is_active`, `synced_at`, `display_order` |
| `ProductModifierAssignments` | `product_variant_id`, `product_modifier_id` |

Alembic creó además `alembic_version(version_num)`, una tabla técnica para conocer
la revisión aplicada. No es una entidad de negocio ni modifica las relaciones
del catálogo. No se crean `Categories`, descuentos, pedidos ni las otras tablas
de historias aún no abordadas. No se elimina ninguna tabla de `mitbol_db`.

Se conservan los nombres exactos del diagrama. PostgreSQL necesita comillas al
consultar nombres con mayúsculas: `SELECT * FROM "Products"`.

### Tipos y decisiones aprobadas

- Identificadores locales: `INTEGER` con generación automática `SERIAL` en
  PostgreSQL. Las relaciones usan esos identificadores internos.
- `loyverse_id`: `VARCHAR(100) NOT NULL UNIQUE` en las cuatro entidades comerciales.
- Nombres: `VARCHAR(150) NOT NULL`; descripción: `TEXT NULL`.
- Precios: `NUMERIC NOT NULL`, sin precisión ni escala impuestas. El diagrama no
  las define. En Python se representan mediante `Decimal`.
- `synced_at`: `TIMESTAMP WITH TIME ZONE`; sólo es opcional en `ProductModifiers`.
  No tiene un valor automático: el proceso comercial deberá suministrarlo.
- `display_order`: `INTEGER NOT NULL`, sin inventar un orden predeterminado.
- Defaults aceptados y aplicados: disponibilidad y activación `true`,
  destacado `false`, novedad `true`; complementos activos `true`.
- No se agregaron restricciones de precio, campos de tienda ni reglas de
  selección que no estén en el modelo. Tampoco se configuraron borrados en cascada.

El precio variable o nulo de Loyverse sigue siendo una incompatibilidad conocida
con `price NOT NULL`; no se convirtió en cero ni se resolvió cambiando el esquema.

## 2. ¿Cómo funciona?

1. `app/db/base.py` declara `Base` y sus metadatos: la descripción de las tablas.
   Importarlo no conecta con PostgreSQL.
2. `app/models/__init__.py` registra únicamente las cinco entidades vigentes.
   `LegacyBase` mantiene el prototipo fuera de esos metadatos.
3. Las claves foráneas expresan las relaciones reales. `relationship()` permite
   recorrerlas desde Python sin crear columnas adicionales.
4. `migrations/env.py` carga la URL sin importar FastAPI ni exigir un token Loyverse.
5. En modo offline Alembic escribe SQL; no abre una conexión ni ejecuta ese SQL.
6. En modo online valida primero que una base sin revisión aplicada no tenga
   tablas o vistas de aplicación. Una base del prototipo es rechazada.
7. `upgrade()` crea primero productos y modificadores, después variantes y
   complementos, y finalmente las asociaciones. Así existen los destinos de las FK.
8. Alembic registra la revisión y confirma la transacción si todo termina bien.
   FastAPI ya no ejecuta `Base.metadata.create_all()` al arrancar.

`upgrade --sql` y el modo online son capacidades distintas. La validación de la
conexión sólo protege el modo online; un SQL exportado debe revisarse y ejecutarse
en la base correcta. Referencia: [modo offline de Alembic](https://alembic.sqlalchemy.org/en/latest/offline.html).

El router antiguo `/api/v1/sync/loyverse` se retiró del registro de FastAPI porque
escribe en las tablas del prototipo. El archivo y su servicio siguen disponibles
para adaptar la integración en el siguiente bloque. Mientras tanto, la ruta
responde 404. `/api/menu` conserva los datos de demostración del repositorio.

## 3. Archivos creados o modificados

| Archivo | Cambio y responsabilidad |
| --- | --- |
| `app/db/base.py` | Nuevo: base declarativa y nombres de restricciones |
| `app/db/database.py` | Reutiliza esa base y conserva sesiones y conexión |
| `app/models/product.py` | Nuevo: `Products` y sus variantes |
| `app/models/product_variant.py` | Nuevo: `ProductVariants`, precios y asociaciones |
| `app/models/product_modifier.py` | Nuevo: `ProductModifiers`, opciones y asociaciones |
| `app/models/product_complement.py` | Nuevo: `ProductComplements` y su grupo |
| `app/models/product_modifier_assignment.py` | Nuevo: asociación con clave compuesta |
| `app/models/__init__.py` | Registro exclusivo de los modelos vigentes |
| `app/models/legacy_base.py` | Nuevo: metadatos aislados del prototipo |
| `app/models/item.py`, `app/models/category.py` | Usan `LegacyBase`; se conserva su código |
| `app/main.py` | Retira `create_all` y el registro del router del prototipo |
| `alembic.ini` | Nuevo: configuración sin credenciales |
| `migrations/env.py` | Nuevo: ejecución offline y online |
| `migrations/validation.py` | Nuevo: comprueba la base antes de la revisión inicial |
| `migrations/script.py.mako` | Nuevo: plantilla de próximas revisiones |
| `migrations/versions/20261029_0001_hu008_catalog.py` | Nuevo: creación y reversión de las cinco tablas |
| `requirements.txt` | Añade Alembic 1.20.1, Mako 1.4.3 y MarkupSafe 3.0.4; normaliza UTF-8 |
| `.env.example` | Ejemplo de conexión a `mitbol_dev`, sin credenciales reales |
| `.gitignore` | Ignora `.venv` y caché de pytest |
| `tests/test_catalog_models.py` | Nuevo: integridad y relaciones en memoria |
| `tests/test_migrations.py` | Nuevo: SQL, coherencia con modelos y protecciones |
| `tests/test_postgresql_schema.py` | Nuevo: seis verificaciones de lectura del esquema real |
| `tests/test_sync.py` | Conserva la prueba del prototipo usando su base aislada |
| `.env` | Sólo `DATABASE_URL` cambió a `mitbol_dev`; archivo privado excluido de Git |
| `docs/database/create_mitbol_dev.sql` | Comando autorizado que creó la base nueva |
| `docs/database/hu008_initial.sql` | Nuevo: SQL real exportado por Alembic |
| `docs/database/hu008_preparation.md` | Esta guía de implementación y revisión |
| `docs/database/hu008_postgresql_verification.json` | Esquema instalado y comparación del prototipo antes y después |
| `README.MD` | Enlaza la guía y explica el inicio con Alembic |

## 4. Fragmentos importantes de código

### Precio y vínculo con el producto: `app/models/product_variant.py`

```python
price: Mapped[Decimal] = mapped_column(Numeric)
```

`Mapped` declara el tipo del atributo Python; `Numeric` define el tipo SQL.
`Decimal` representa cantidades decimales y evita la aritmética binaria de `float`.
No existe un precio predeterminado: un precio desconocido no equivale a cero.

```python
product_id: Mapped[int] = mapped_column(ForeignKey("Products.product_id"))
```

La FK impide que una variante apunte a un producto inexistente.

### Clave compuesta: `app/models/product_modifier_assignment.py`

```python
product_variant_id: Mapped[int] = mapped_column(
    ForeignKey("ProductVariants.product_variant_id"), primary_key=True
)
product_modifier_id: Mapped[int] = mapped_column(
    ForeignKey("ProductModifiers.product_modifier_id"), primary_key=True
)
```

La pareja de identificadores es la clave primaria. Un grupo no puede quedar
asignado dos veces a la misma variante; sí puede estar en varias variantes.

### Transacción de migración: `migrations/env.py`

```python
with engine.begin() as connection:
    validate_initial_database(connection)
```

La validación consulta metadatos antes de las operaciones DDL. El bloque exterior
confirma la transacción al terminar correctamente y la revierte si hay una excepción.

## 5. Pruebas y verificación

Comandos de comprobación del bloque inicial de preparación:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic history
git diff --check
```

Resultado del bloque inicial: **20 passed**. Pip: **No broken requirements found**.
La revisión detectada es `20261029_0001 (head)`. La primera pasada encontró dos
fallos al compilar nombres de FK superiores al límite de PostgreSQL; se acortaron
los nombres conservando las mismas relaciones. La suite final no tiene fallos.

Las pruebas comprueban relaciones, importes Decimal, identificadores únicos,
asociaciones únicas, rechazo de precios nulos y referencias inválidas, ausencia de
borrados en cascada, igualdad entre la revisión y los modelos, generación offline
sin conexiones, rechazo de una base del prototipo e inicio de FastAPI sin DDL.
La prueba antigua de sincronización sigue siendo del prototipo.

SQLite en memoria verifica relaciones y restricciones básicas sin tocar la base
real. El SQL también se compila con el dialecto PostgreSQL. Esto no sustituye una
prueba de aplicación real de la migración. Esa comprobación ya se realizó tras
recibir autorización, con estas verificaciones adicionales:

```powershell
$env:MITBOL_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

Resultado: **26 passed in 2.54s**; revisión `20261029_0001 (head)`;
`No new upgrade operations detected.` Las seis pruebas nuevas verifican el destino,
la revisión y las cinco tablas reales: columnas, tipos, nulabilidad, defaults,
claves primarias, claves únicas y las cuatro relaciones por FK. Se conectan con
`default_transaction_read_only=on`; no insertan ni eliminan filas.
Se activan explícitamente mediante `MITBOL_POSTGRES_TESTS=1`. Sin esa variable,
pytest omite las seis verificaciones que necesitan PostgreSQL.

Las cinco tablas del catálogo están vacías; `alembic_version` contiene una fila.
La comparación de `mitbol_db` antes y después verificó el mismo esquema, índices,
restricciones y contenido: `categories` conserva dos filas e `items` dos filas.
Las huellas agregadas del contenido también coinciden. No se probó ni se inició
la nueva sincronización de Loyverse.

También se ejecutó `validate_initial_database()` contra `mitbol_db` dentro de
una conexión PostgreSQL de sólo lectura: rechazó la base del prototipo como se
esperaba. Al terminar se confirmó `transaction_read_only=on`, las tablas
`categories` e `items` y, antes de su creación autorizada, la ausencia de `mitbol_dev`.

Puedes ejecutar los mismos comandos. Para ver el SQL de revisión sin aplicarlo:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head --sql
```

### Comandos ejecutados tras recibir autorización

Se ejecutó el contenido de `create_mitbol_dev.sql` mediante una conexión SQLAlchemy
a la base de mantenimiento `postgres`, con `AUTOCOMMIT`: `CREATE DATABASE` requiere
ejecutarse fuera de una transacción. La sentencia utilizada fue:

```sql
CREATE DATABASE mitbol_dev
    WITH OWNER = postgres
         TEMPLATE = template0
         ENCODING = 'UTF8';
```

Después se cambió sólo el nombre de la base en `DATABASE_URL` de `.env` a
`mitbol_dev`, conservando servidor, puerto y credenciales. Se comprobó el destino
real y se ejecutó:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade 20261029_0001
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

`upgrade` modifica la estructura. `current` consulta la revisión y `check`
compara modelos y esquema; no crean una nueva revisión por sí mismos.
El SQL exportado es para revisar: no se debe ejecutar además del upgrade.

El método `downgrade()` existe para definir la reversión, pero elimina las cinco
tablas y sus datos. No se ejecutó y requiere autorización independiente. No se
incluyeron `DROP DATABASE`, `CASCADE` ni borrados del prototipo en la propuesta de alta.

## 6. Preparación para defender el proyecto

1. **¿Por qué una base nueva?** Evita borrar objetos del prototipo y permite
   probar el esquema real de manera independiente.
2. **¿Qué diferencia hay entre SQLAlchemy y Alembic?** SQLAlchemy describe y
   manipula entidades; Alembic aplica cambios versionados al esquema.
3. **¿Para qué sirve `alembic_version`?** Guarda la revisión aplicada para saber
   qué migraciones faltan.
4. **¿Por qué dos claves primarias en la asociación?** Juntas forman una única
   clave compuesta que impide repetir una pareja variante–modificador.
5. **¿Generar SQL significa ejecutarlo?** No. El modo offline sólo produce texto
   para revisión. La aplicación online autorizada ya instaló esta revisión.

## 7. Resumen de aprendizaje

- Modelo ORM, migración y esquema instalado son elementos distintos.
- Las FK y claves únicas protegen la integridad aun cuando el código falle.
- `relationship()` facilita navegar objetos; `ForeignKey` define el vínculo SQL.
- Una migración inicial debe crear las tablas en el orden de sus dependencias.
- Los precios se conservan como decimales y nunca se inventan valores faltantes.
- Iniciar FastAPI ya no crea tablas; la evolución del esquema corresponde a Alembic.
- La base nueva, el cambio de `.env` y la migración ya están aplicados y verificados.
  El siguiente bloque de sincronización con Loyverse todavía no se inició.
