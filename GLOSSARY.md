# LGDC

Plataforma que compara los precios de un comercio contra los de sus competidores cercanos, a partir de los datos diarios del SEPA.

## Language

### Avisos

**Alerta**:
Aviso de negocio a un comercio sobre un cambio de precio de un competidor que le importa según sus reglas.
_En código_: `alert`
_Avoid_: Alarma, notificación

**Alarma**:
Aviso operativo al equipo de que algo falló en la plataforma, como un job que terminó en la DLQ o una Lambda con errores.
_En código_: `alarm`
_Avoid_: Alerta

### Precios

**Conjunto competitivo**:
Las sucursales de otras cadenas dentro del radio de una sucursal seguida, la más cercana primero: contra sus precios se compara la sucursal.
_En código_: `competitors` (los pares hasta 10 km, precalculados: `branch_neighbor`)
_Avoid_: Competencia

**Radio**:
La distancia, hasta 10 km, que define el conjunto competitivo de una sucursal seguida. Cada organización guarda el suyo por sucursal, y cada pantalla puede pedir otro.
_En código_: `radius_km`
_Avoid_: Rango, zona

**Comparación**:
Los precios de una sucursal seguida contra el mínimo, el promedio y el máximo de su conjunto competitivo, producto por producto y la peor brecha primero, con el resumen de posición (más barata, iguala el mínimo, más cara). Es lo que muestran el tablero y Mis Precios.
_En código_: `comparison` (`app/shared/comparison.py`)
_Avoid_: Benchmark

**Precálculo**:
La comparación de una sucursal seguida a su radio configurado, calculada una vez por día y guardada en DynamoDB, de donde leen el tablero y Mis Precios. Muestra siempre el último día escrito entero (`META`); sin precálculo, la API responde con la consulta en vivo y lo encola.
_En código_: `precompute` (la Lambda `lgdc-precompute`, la tabla `lgdc-comparison`, `ComparisonTable`)
_Avoid_: Caché, snapshot

**Sucursal seguida**:
Una sucursal que una organización sigue, con el radio con que la sigue: la unidad que el cierre del día encola y el precálculo calcula, una partición de `lgdc-comparison`. La propia de un comercio que carga su lista la sigue su organización desde que la da de alta.
_En código_: `FollowedBranch` (`org_branch`)
_Avoid_: Suscripción, partición

### Pipeline

**Job**:
El procesamiento de un archivo que llegó a `raw/`: el de una cadena del SEPA para un día, o el de una carga. Lo crea el worker cuando toma el aviso de S3, nunca la API, y termina en `done` o `failed`.
_En código_: `ingest_job` (`kind`: `sepa` o `upload`)
_Avoid_: Tarea, proceso

**Ingesta**:
La corrida diaria que baja el archivo del día del SEPA por el proxy SEPA y deja el archivo de cada cadena en `raw/`, y nada más: cada archivo nuevo es un job. La dispara un cron, o el workflow run-ingest a pedido.
_En código_: `ingest` (la Lambda `lgdc-ingest`, `make ingest`)
_Avoid_: Importación, scraping

**Proxy SEPA**:
El servicio que baja el ZIP del SEPA y lo sirve por HTTP detrás de un túnel, fuera de AWS, porque el SEPA bloquea las IPs de AWS. Lo levanta quien despliega, en una máquina propia. La URL del túnel es lo único que la ingesta necesita saber de él.
_En código_: `tools/sepa-proxy`, `SEPA_API_URL`
_Avoid_: Espejo, mirror

**Aviso de cadena procesada**:
El aviso, uno por job que termina en `done` o en `failed`, de que una cadena (o una carga) de un día quedó procesada, con cuántos precios cambiaron. Se guarda en el outbox junto con el resultado del job y se publica en el topic `cadena-procesada`, que lo reparte a las alertas, al cierre del día y al email del admin. No lleva precios.
_En código_: `ChainProcessed` (`outbox`, el topic `lgdc-cadena-procesada`)
_Avoid_: Notificación, alerta

**Cierre del día**:
El paso que cierra un día una sola vez, cuando todas sus cadenas en `raw/` terminaron (`done` o `failed`): reconstruye los vecinos y el catálogo y encola el precálculo de cada sucursal seguida y radio. Lo dispara el aviso de cadena procesada; un cron de respaldo cierra el día con lo que haya, y una cadena que llega después lo reabre y lo vuelve a cerrar.
_En código_: `day_close` (la Lambda `lgdc-day-close`, `feed_day`, `feed_day_chain`)
_Avoid_: Fin de día, consolidación

**Carga**:
La lista de precios propia que sube una organización desde el portal, directo a S3, para su sucursal propia y un día. Sigue el mismo camino que el archivo de una cadena: su llegada crea su job, y la carga muestra su estado y sus filas rechazadas.
_En código_: `upload` (`price_upload`)
_Avoid_: Importación, archivo

### Cuentas

**Organización**:
El comercio que usa el portal: dueña de sus sucursales seguidas, reglas, alertas y cargas, que ninguna otra organización ve.
_En código_: `org`
_Avoid_: Tenant, cuenta, empresa

**Dueño**:
El rol del usuario que creó la organización en su primer login: el único que puede invitar miembros y ver la Administración.
_En código_: `owner`
_Avoid_: Admin

**Miembro**:
El rol de un usuario invitado por el dueño a su organización.
_En código_: `member`
_Avoid_: Invitado, usuario común

**Invitación**:
El alta de un miembro por el dueño de su organización, para alguien que todavía no está en ninguna: si no tiene usuario en Cognito se lo crea, y Cognito le manda por email una contraseña temporal; si ya lo tiene, entra con su contraseña. Queda con rol `member`.
_En código_: `invite`
_Avoid_: Registro, alta

**Admin de plataforma**:
Quien opera LGDC entero, por encima de las organizaciones: es un grupo de Cognito, no un rol de una organización.
_En código_: `platform-admin`
_Avoid_: Admin, súper usuario, dueño
