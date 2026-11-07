# HU-004 — Generar código QR del menú

Fuente: backlog adjunto `Por hacer  (1).pdf`, páginas 20–21; dependencia de
HU-001 en página 31. Prioridad Must Have, FE-2 / BE-1, coordinación FE-1.
El backend proporciona una URL pública estable. Frontend genera, muestra,
descarga e imprime **un QR general**, sin códigos por mesa ni login para abrir
el menú. El backlog permite generarlo directamente en frontend.

## Implementación

Ya existían `GET /api/menu` y `GET /api/menu/{product_id}`, públicos, con
catálogo, fotografías, variantes y disponibilidad. Faltaba publicar la dirección
del **frontend**, independiente del contenido del catálogo.

Se añade `PUBLIC_MENU_URL` opcional en Settings y `GET /api/menu/config`:

```json
{"menu_url":"https://mitbol-frontend.onrender.com/"}
```

El ejemplo sólo corresponde si FE-1 confirma que el menú está en esa ruta.
Si todavía no se configura, devuelve HTTP 200 con `{"menu_url":null}` para que
frontend informe que falta la dirección y deshabilite generar/descargar/imprimir.
La variable ausente no impide arrancar el backend público existente.

La URL se valida y normaliza: HTTP(S), sin credenciales, query ni fragmento;
HTTP sólo para loopback local. Cloud requiere HTTPS remoto si se configura.
Se permiten rutas, por ejemplo `/menu`. Se rechazan espacios literales,
controles y barras inversas. No se consulta DNS ni se afirma comprobar que
la página exista; eso se verifica con FE-1 al integrar.

La ruta es pública y sólo GET, se registra antes de `/api/menu/{product_id}` y
no consulta PostgreSQL, Loyverse, cookies ni tablas privadas. Sólo expone
`menu_url`; no serializa Settings. No acepta URL por petición ni deriva su
destino de Host/Forwarded. Responde `Cache-Control: no-store` para evitar
conservar configuración antigua en caché.

Se reutilizan FastAPI, Pydantic y SQLAlchemy, que es el ORM real del proyecto.
No se incorpora SQLModel, librería QR, servicio externo, tabla RestaurantRules,
migración ni escritura. Los cambios de productos, variantes o precios no
intervienen en esta configuración. Cambiar el dominio/ruta configurados sí
exige actualizar y volver a imprimir el QR; conservarlos estables.

## Integración pendiente con frontend

1. FE-1 confirma la URL final que abre el menú sin autenticación. La variable
   no se aplica a Render en esta intervención; su configuración/despliegue
   requiere autorización posterior. No sustituirla por la URL JSON de la API.
2. FE-2 consulta `/api/menu/config` con `credentials: "omit"`, muestra esa URL
   y genera el QR con ella. Si recibe null, informa que falta configurar la
   dirección. Implementa vista previa, descarga como imagen e impresión.
3. Probar el QR con un teléfono y sesión cerrada, confirmar la ruta con FE-1
   y volver a abrir el mismo QR después de un cambio normal de catálogo.

La autenticación no es una dependencia para abrir el menú ni consultar su URL.
Esta entrega completa **el alcance backend** de HU-004. La historia completa
requiere los controles QR del frontend y la prueba física de escaneo.
AUTH_ENABLED/ADMIN_ENABLED siguen false y CORS no se modifica aquí.

## Archivos cambiados en esta intervención

| Archivo | Cambio |
| --- | --- |
| `app/core/config.py` | Variable opcional y validación de URL pública. |
| `app/api/menu.py` | GET público de configuración, sin DB. |
| `app/schemas/menu.py` | Contrato con `menu_url` nullable. |
| `tests/test_public_menu_config.py` | Casos de URL, endpoint, CORS y aislamiento de DB/auth. |
| `tests/test_account_provisioning.py` | Desactivación por CLI de Admin/Staff y conservación de auditoría en temporal. |
| `scripts/test_auth_phase1.py` | Excluir PUBLIC_MENU_URL heredada del laboratorio de pruebas. |
| `.env.example` | Ejemplo opcional, sin secretos. |
| `README.MD` | Enlace al contrato HU-004. |
| `docs/security/admin_auth_phase5.md` | Evidencia de cuenta 1 y procedimiento de desactivación pendiente de autorización. |
| `docs/learning/hu004/README.md` | Alcance, contrato, integración y comandos de prueba. |

Se conservaron los demás cambios locales anteriores, sin incluirlos como trabajo
de HU-004. Rama `feature/hu-004-menu-qr-url`, creada desde develop, mismo HEAD
388556f. Sin commits ni push.

## Probar localmente

Desde `C:\Proyectos\mitbol\mitbol-backend`, con dependencias ya instaladas y
PostgreSQL 18 disponible, ejecutar el laboratorio existente:

```powershell
& .\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py tests/test_public_menu_config.py tests/test_menu.py tests/test_cloud_configuration.py tests/test_account_provisioning.py -q --tb=short
```

Usa PostgreSQL temporal, sin .env, Neon ni mitbol_dev; crea allí únicamente
datos sintéticos. Admin/Staff, desactivación y auditoría se validan localmente,
sin crear Staff en production.

Para probar **sólo la configuración del QR por HTTP**, en una PowerShell nueva:

```powershell
Set-Location 'C:\Proyectos\mitbol\mitbol-backend'
$env:APP_ENV = 'test'
$env:DATABASE_URL = 'postgresql+psycopg2://mitbol_ci@127.0.0.1:1/mitbol_unused'
$env:AUTH_ENABLED = 'false'
$env:ADMIN_ENABLED = 'false'
$env:PUBLIC_MENU_URL = 'http://localhost:5173/'
$env:CORS_ORIGINS = '["http://localhost:5173"]'
& .\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Desde otra consola:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/menu/config'
```

Esperado: `menu_url = http://localhost:5173/`. Este ejemplo usa una conexión
inexistente intencionadamente para demostrar que HU-004 no depende de DB;
consultar el catálogo daría 503. No sirve para comprobar el contenido del menú.
Detener Uvicorn con Ctrl+C y cerrar esa consola para retirar sus variables.

## Cuenta ficticia de autenticación

El usuario ya verificó en Neon la cuenta 1: activa, empleado activo, Argon2id,
rol `{admin}` y auditoría de creación presente. No repetir la verificación
previa ni crear Staff ficticio. La desactivación por CLI `access 1`, con roles
`admin` y estados `no`/`no`, está preparada en la
[sección 6.5 de seguridad](../../security/admin_auth_phase5.md#65-cerrar-la-prueba-de-la-cuenta-ficticia-1--preparado-no-ejecutado).
**No ejecutada: requiere autorización explícita antes de escribir en Neon.**

## Resultado de las pruebas

6 de noviembre de 2026, America/Mexico_City:

- **164 aprobadas**, 14.73 s: el comando focalizado anterior. Incluye regresión
  de menú/detalle, configuración cloud, contrato nuevo y aprovisionamiento;
  Admin/Staff se desactivan y pierden acceso conservando su auditoría.
- **44 aprobadas**, 1.23 s: `tests/test_public_menu_config.py` y
  `tests/test_ci_configuration.py` mediante el mismo ejecutor, después de añadir
  PUBLIC_MENU_URL a su limpieza de entorno. Se heredó a propósito un valor
  ficticio inválido y se restauró al salir; el laboratorio lo excluyó. Esta
  ejecución repite los casos del endpoint, no son 44 casos nuevos adicionales.
- Dos deprecaciones existentes de Starlette/AnyIO por ejecución, sin fallos.
  Ambos clústeres temporales detenidos; no se repitió la suite completa.
- Función PowerShell del operador y bloques de esta guía validados por el
  parser, sin invocar operaciones del CLI contra Neon. `git diff --check` correcto.

Evidencias `test-run.log` en TEMP:
`mitbol-sync-preflight-3fa4358dd48f46868443754ba307c241` y
`mitbol-sync-preflight-3b09f4d87dc147bda498b7a1f58f24e1`.
