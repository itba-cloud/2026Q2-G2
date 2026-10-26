# app/batch/day_close · el cierre del día

Cierra cada día una sola vez, cuando termina su última cadena: corre el
**mantenimiento del catálogo** (`refresh_neighbors` y `normalize_catalog`, de
[`app/shared/maintenance.py`](../../shared/maintenance.py)) y encola en la
SQS `lgdc-precompute` una entrada por cada sucursal seguida y radio, para
que el [precálculo](../precompute/README.md) recalcule su comparación. Es la Lambda
`lgdc-day-close`, y la invocan tres cosas:

- **La SQS `lgdc-day-close`**, que recibe del topic `lgdc-cadena-procesada`
  todos los avisos, `done` y `failed` (ver
  [El aviso de cadena procesada](../worker/README.md#el-aviso-de-cadena-procesada)).
- **Un cron de EventBridge**, el respaldo, todos los días a las 10:00 de
  Buenos Aires (`cron(0 13 * * ? *)`, la variable
  `day_close_schedule_expression`).
- **La API**, para `POST /admin/neighbors/refresh`, de forma asíncrona con
  `{"action": "refresh_neighbors"}`: reconstruye los vecinos y vuelve a
  encolar el precálculo de cada sucursal seguida para el último día
  cerrado. Es trabajo que no entra en los 30 s de API Gateway.

Al cerrar un día, antes de encolar el precálculo, corre `ANALYZE price`: el
autovacuum analiza una partición recién cuando cambia ~10 % de ella, y un día
de un mes nunca llega. Sin estadísticas nuevas el planner toma el día nuevo
por una fila y la comparación pasa de segundos a superar el timeout del
precálculo.

## Cuándo termina la última cadena

La decisión está en [ADR-0004](../../../docs/adr/0004-cierre-del-dia.md). Con
el aviso de una cadena del SEPA:

1. **Marca la cadena en su día**: crea la fila de `feed_day` si no está y
   agrega la cadena a `feed_day_chain`, con la fila del día bloqueada
   (`FOR UPDATE`). Si el día ya estaba cerrado y la cadena es nueva para él,
   lo **reabre** (ver abajo).
2. **Lista las cadenas del día en `raw/`** (`raw/feed_date=<día>/chain_id=<n>/`):
   cada archivo que dejó la ingesta es un job, aunque ningún worker lo haya
   tomado todavía. `ingest_job` no alcanza: el worker crea el job recién
   cuando recibe el mensaje, así que con un worker y veinte cadenas, al
   terminar la primera las otras diecinueve todavía no tienen fila.
3. **Intenta cerrar el día**, en una transacción:
   `UPDATE feed_day SET closed_at = now() WHERE closed_at IS NULL` y cada
   cadena de `raw/` tiene su job en `done` o `failed`. Si varias
   invocaciones llegan juntas, el `UPDATE` bloquea la fila: gana una, y las
   demás encuentran `closed_at` puesto cuando la primera hace commit.
4. **La que gana**, en la misma transacción: marca todas las cadenas que
   encontró terminadas, corre `normalize_catalog`, lee `org_branch`, corre
   `refresh_neighbors` y encola un mensaje por cada combinación de sucursal
   seguida y radio. Si algo falla, el rollback deja el día abierto y el aviso
   vuelve a la cola. El `TRUNCATE` de `refresh_neighbors` bloquea
   `branch_neighbor` hasta el commit (las lecturas del conjunto competitivo
   esperan), por eso va último, antes de encolar: unos segundos.

Un día sin nada en `raw/` no se cierra: no hubo ingesta, o el job es de un
archivo que ya no está.

Las cadenas terminan de cargarse antes de que lleguen sus avisos (los avisos
pasan por SNS y SQS después del commit), así que el primer aviso puede
encontrar todas las cadenas terminadas y cerrar el día; los demás lo
encuentran cerrado, con sus cadenas ya marcadas, y no hacen nada.

### Una carga de un comercio

El aviso de una carga (`kind = upload`) no marca nada ni cierra el día: si
terminó en `done`, encola solo las sucursales seguidas de ese comercio (la
suya, que su organización sigue desde que la dio de alta), con la fecha de la
carga. Un aviso `failed` de una carga no encola nada.

### El cron de respaldo

Si una cadena termina en la DLQ sin aviso (un último intento que falla porque
la base no responde), su día nunca se completaría. El cron invoca la función
con el evento de EventBridge; la función toma la fecha de su `time` en
Buenos Aires y **cierra ese día con lo que haya**: el mismo paso 3 sin
esperar a ninguna cadena, si el día tiene algo en `raw/` y no estaba cerrado.
Marca solo las cadenas que encontró terminadas. Responde con lo que decidió.

### Una cadena que llega tarde reabre el día

Si después del cierre llega el aviso de una cadena que el día no tenía
marcada (una que el cron no esperó, o un día cuyo cierre ya había corrido y
recibe una cadena nueva), el aviso reabre el día (`closed_at = NULL`) y lo
vuelve a cerrar en el acto **con lo que haya**, como el cron: el
mantenimiento y el precálculo vuelven a correr, ahora con esa cadena. No
espera a las que siguen cargando, porque una que nunca termina dejaría el
día abierto para siempre (el cron solo mira el día de su fecha): cada una
que llegue después lo reabre y lo cierra otra vez. Un aviso repetido de una
cadena ya marcada no reabre nada.

## El mensaje de precálculo

Uno por sucursal seguida y radio, en lotes de 10 (`SendMessageBatch`). Qué
sucursales hay y cómo es el mensaje lo define
[`app/shared/precomputed.py`](../../shared/precomputed.py), que comparten el
cierre, la API y el precálculo:

```json
{"feed_date": "2026-10-21", "company_id": 10, "banner_id": 1, "branch_id": "523", "radius_km": 5.0}
```

## Lo que deja en el log

Una línea por aviso y por invocación del cron, `day-close {...}`, con lo que
decidió. Es lo que lee `make verify`, porque la cola de precálculo es del
precálculo y sus mensajes no se pueden leer sin tomarlos:

```json
{"trigger": "notice", "feed_date": "2026-10-21", "kind": "sepa", "company_id": 10,
 "status": "done", "reopened": false, "stored": 16, "settled": 16, "closed": true,
 "precompute": [{"company_id": 10, "banner_id": 1, "branch_id": "523", "radius_km": 5.0}, ...]}
```

`stored` son las cadenas del día en `raw/`, `settled` las que tienen el job
terminado, y `precompute` las entradas que encoló. La invocación del cron
responde con la misma línea (`trigger: backup`).

## Reintentos

- **Lotes de hasta 10 avisos**, y la función devuelve cuáles fallaron
  (`ReportBatchItemFailures`). Al tercer intento el aviso pasa a la DLQ
  `lgdc-day-close-dlq`.
- **Todo es repetible**: marcar una cadena ya marcada no hace nada, cerrar
  un día cerrado tampoco, y el precálculo reescribe las mismas claves. Si
  falla un lote de `SendMessageBatch` después de otros, el reintento vuelve
  a mandar esos: el precálculo los recibe dos veces y escribe lo mismo.
- **El cron**, si falla, se reintenta dos veces (la invocación asíncrona de
  Lambda); el día queda abierto hasta entonces.

## Dónde corre

Dentro de la VPC, en la banda app, como el alert-matcher: llega a Postgres
por el RDS Proxy, a `raw/` por el gateway endpoint de S3 y a la cola de
precálculo por el interface endpoint de SQS. A lo sumo **2 invocaciones a la
vez** (`maximum_concurrency` del event source mapping). Tiene 300 segundos
(`local.day_close_timeout`), por el mantenimiento, y la cola un *visibility
timeout* de 6 veces eso.

El paquete lo arma `make lambdas` en `build/day_close.zip`. El handler es
`main.handler`.

## Configuración

Terraform le pasa la conexión a la base (`DB_HOST`, el Proxy, `DB_PORT`,
`DB_NAME`, `DB_USER` y `DB_SECRET_ARN`), `S3_DATA_BUCKET` (de dónde lista
`raw/`) y `PRECOMPUTE_QUEUE_URL` (`infra/main.tf`, módulo
`day_close_function`).

## Los chequeos

La suite `close` de `make verify` ([`tools/verify`](../../../tools/verify/README.md)),
contra el stack desplegado, en cuatro días propios de cada corrida: tres
cadenas de un día cierran el día una sola vez y el portal muestra a la
vecina en el conjunto competitivo; el cierre encola cada sucursal seguida y
radio una vez; una carga encola solo la sucursal de su comercio sin cerrar
nada; y el cron cierra un día con sus cadenas todavía cargando, y el día se
vuelve a cerrar cuando llegan.

## Lo que falta

- **Asume que la ingesta subió todas las cadenas antes de que termine la
  primera.** Si un worker terminara una cadena mientras la ingesta todavía
  sube las demás, el día se cerraría con las que estaban en `raw/`, y cada
  cadena que llegue después lo reabriría y lo volvería a cerrar. La ingesta sube
  las cadenas en segundos, y un worker tarda minutos en arrancar y cargar.
- **Reprocesar una cadena de un día ya cerrado no lo vuelve a cerrar**: la
  cadena ya está marcada. El admin de plataforma puede forzar el
  mantenimiento (`POST /admin/*`).
- **El cron mira la fecha del día en Buenos Aires.** Si el SEPA fechara sus
  archivos el día anterior, el cron no encontraría nada que cerrar ese día.
- **Un aviso en la DLQ queda ahí**, y sin el módulo `observability` (TP4)
  solo se ve en los logs. **(TP4)** El cierre también va a arrancar el job
  de Glue.
