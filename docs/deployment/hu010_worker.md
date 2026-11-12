# HU-010 — Worker privado y ejecución periódica

Procedimiento preparado, no ejecutado en infraestructura real. No crea servicios
o timers automáticamente y no agrega persistencia para un historial completo.
Primero integrar HU-009 y el incremento de lectura de estado de HU-010, según
la [documentación funcional](../features/hu010-sync-management.md).

## Topología disponible

El backend público conserva su pool lector y el pool limitado de autenticación.
Para una solicitud manual solo conoce una dirección loopback y un secreto de
delegación. Un proceso worker independiente posee la URL del operador y el token
Loyverse. No colocar esas credenciales operativas en React o en el entorno del
Web Service público para intentar habilitar la operación.

El transporte implementado requiere ambos procesos en el mismo host o un canal
loopback aprobado por infraestructura. El worker rechaza clientes remotos,
Origin y cabeceras de proxy, incluso con token. No exponerlo como un servicio
público, desactivar sus comprobaciones o apuntar a una URL remota: si la
infraestructura no admite esta topología, mantener deshabilitado el POST hasta
que se apruebe otro canal interno. No se ha instalado un túnel o sidecar.

## Configuración del backend público

| Variable | Uso |
|---|---|
| `SYNC_WORKER_URL` | Origen loopback explícito, por ejemplo `http://127.0.0.1:8766`, sin rutas, credenciales, query o fragmento. |
| `SYNC_WORKER_TOKEN` | Secreto de delegación independiente, URL-safe de 32–128 caracteres. No es el token Loyverse. |
| `SYNC_WORKER_TIMEOUT_SECONDS` | Espera de respuesta, 1–300 segundos; por defecto 120. No es garantía de duración máxima del trabajo. |

URL y token se configuran juntos; ausentes dejan la ejecución deshabilitada con
503. Se conservan AUTH_ENABLED, AUTH_CATALOG_ENABLED y las políticas de sesión,
Origin y CSRF existentes. Staff puede consultar estado pero no solicitar trabajo.
El JSON no permite sustituir tienda, base, actor o credenciales del servidor.

## Configuración del proceso worker

Solo variables del proceso, sin carga de .env:

| Variable | Uso |
|---|---|
| `APP_ENV` | local, test o cloud, según el destino autorizado. |
| `SYNC_DATABASE_URL` | URL del operador, distinta de los roles web. No se usa DATABASE_URL como fallback. |
| `SYNC_EXPECTED_DATABASE` | Base exacta esperada: local mitbol_auth_local/mitbol_dev; cloud el destino autorizado. |
| `SYNC_EXPECTED_HOST` | Host PostgreSQL directo autorizado en cloud; no pooler. |
| `LOYVERSE_STORE_ID` | UUID de la tienda seleccionada. |
| `LOYVERSE_ACCESS_TOKEN` | Secreto del proveedor, solo en el entorno operativo. |
| `SYNC_WORKER_TOKEN` | El secreto de delegación que usa el backend, independiente del proveedor. |

PostgreSQL debe ser 18 y estar en 20261111_0006. El operador necesita las
capacidades de sincronización del catálogo/descuentos; no usar
mitbol_admin_writer o mitbol_render_reader. El runtime comprueba revisión,
identidad, permisos y modo de lectura antes de contactar al proveedor.
En cloud se exige TLS verify-full, CA y channel binding con el destino existente.
No cambia las variables EXPECTED_DATABASE/HOST de otro proceso.

Con secretos configurados por un medio aprobado, iniciar explícitamente:

```text
python -m uvicorn app.sync_worker:create_worker --factory --host 127.0.0.1 --port 8766 --no-access-log
```

La fábrica no lee .env y el worker no publica Swagger/OpenAPI. El endpoint
interno POST /internal/sync/execute exige Bearer de delegación, cuerpo acotado
y un target conocido. Los errores nunca devuelven el cuerpo del proveedor o SQL.

## Programación externa

La misma configuración operativa permite inspección sin escribir o contactar
Loyverse, aunque requiere las credenciales del runtime configuradas:

```text
python -m app.cli.run_sync --target catalog --preflight
python -m app.cli.run_sync --target discounts --preflight
```

Un programador aprobado del sistema puede ejecutar, por ejemplo:

```text
python -m app.cli.run_sync --target catalog --execute --skip-if-busy
```

Configurar la frecuencia y el entorno en Task Scheduler/cron/plataforma queda
a cargo de infraestructura. Se recomienda separar los disparos de catálogo y
descuentos. Esta entrega no registra un timer ni hace una carga inicial real.

Código 0: completado, preflight correcto u omitido por ocupado con ese flag;
examinar status para distinguirlos. Código 2: ocupado sin skip; código 1: fallo;
130: interrumpido. No se reintenta automáticamente un error o resultado incierto.
La exclusión mutua se decide en PostgreSQL y funciona entre procesos manuales,
periódicos y comandos anteriores actualizados.

## Resultado y límites

El POST web espera la respuesta del worker y confirma completed con conteos
validados. No es una cola durable. Una pérdida de conexión puede dejar un
resultado desconocido, incluso si el worker confirmó la transacción. Consultar
el estado y los datos antes de repetir; no interpretar 503 como garantía de que
no hubo commit.

El GET informa actividad de esta base, no un job individual. AuditEvents registra
la autorización, no todo el inicio/fin/error de cada ejecución. Reinicios no
conservan historial por un mecanismo nuevo: ese criterio sigue pendiente.

Para deshabilitar nuevas solicitudes, retirar conjuntamente URL y token de
delegación del backend. Eso no cancela un trabajo ya iniciado en el worker.
Una reversión del código/ACL de lectura se coordina con la ventana administrativa
descrita en la documentación de estado; no eliminar tablas o datos para resolver
un error de comunicación.
