# app/batch/worker · el parseo

Toma un job de la SQS de jobs, lee el archivo de esa cadena (o la lista de
precios que subió un comercio) y lo convierte en filas: precios y sucursales a la base, filas inservibles a `rejected/`, el
conjunto validado a `curated/` en Parquet. Cuando el job termina, lo avisa
en el topic `lgdc-cadena-procesada` (ver [El aviso de cadena procesada](#el-aviso-de-cadena-procesada)).

Corre en Fargate, en la banda batch de una VPC sin salida a internet: llega a
ECR, Logs, SQS, SNS y Secrets Manager por sus interface endpoints, a S3 por el
gateway, y a Postgres directo a la instancia (sostiene una conexión toda su
vida y carga con tablas temporales y `COPY`, que igual fijarían una conexión
del Proxy). La imagen la arma `make worker-image` desde
[`../Dockerfile`](../Dockerfile), con el código adentro: estos módulos y los
de [`app/shared`](../../shared/), planos, más [`requirements.txt`](requirements.txt).
Para correrlo desde la raíz del repo:

```bash
PYTHONPATH=app/shared JOBS_QUEUE_URL=… python app/batch/worker/main.py
```

## De dónde salen los jobs

Un job es un archivo nuevo en `raw/`. S3 avisa cada objeto creado con un
mensaje directo a la SQS de jobs (`lgdc-jobs`), así que da igual quién lo
subió: la ingesta, alguien con la CLI o el navegador de un comercio. La clave
dice qué es:

| Clave | Qué es | El job |
|---|---|---|
| `raw/feed_date=<día>/chain_id=<cadena>/<archivo>` | El ZIP de una cadena del SEPA | `kind = sepa`, uno por cadena y día |
| `raw/uploads/<upload_id>/<archivo>` | La lista de precios de un comercio, que el portal sube con un POST prefirmado | `kind = upload`, uno por carga. El día y el comercio salen de su fila en `price_upload` |

Cualquier otra clave, o la de una carga que ya no existe (se borró antes de
que llegara su archivo), se descarta con un log.

Corren varios workers a la vez y **ninguno se coordina con los demás**: SQS le
entrega cada mensaje a uno solo y lo esconde de los otros mientras dura su
*visibility timeout* (15 minutos, más de lo que tarda la cadena más grande).
Si el worker se muere a mitad de camino, el mensaje reaparece y lo toma otro.

`ingest_job` ya no es la cola, es el registro que muestra la pantalla de
Pipeline: el worker anota el job cuando recibe el mensaje (`processing`, con
el número de intento) y lo cierra al terminar. Una fila por cadena y día; un
reproceso reusa la suya. Para una carga, el worker además vincula el job a su
fila de `price_upload` (`job_id`, y el estado `queued`) en la misma
transacción que lo anota, y la cierra con el job (`done` o `failed`): es lo
que ve el comercio en Cargar precios. Un comercio puede subir dos listas del
mismo día, así que el job de una carga es uno por carga, no por cadena y día;
volver a subir el mismo archivo (otra versión) reusa el suyo.

## Un mensaje dos veces no carga dos veces

SQS entrega *al menos una vez*: el mismo mensaje puede llegar dos veces. El
bucket está versionado, así que cada versión de un archivo es un mensaje
distinto, con clave `<key>#<versionId>`. Esa clave va a `processed_message`
(el inbox) **en la misma transacción que la carga**, con
`ON CONFLICT DO NOTHING`: o quedan registrados los precios y el mensaje, o
ninguno de los dos. Un mensaje que ya está en el inbox se descarta sin tocar
nada, ni siquiera `ingest_job`.

Reprocesar un día es subir el archivo de nuevo: es otra versión, otro mensaje.

## Cuando algo falla

Dos tipos de error, con dos caminos:

- **El archivo está roto** (no abre, le falta una columna del formato, o se
  rechaza más del 5 % de las filas; ver abajo). Reintentar no lo arregla: el
  worker copia el archivo a `rejected/<partición>/`, deja el job en `failed`
  con el motivo, anota el mensaje en el inbox y lo borra de la cola.
- **Cualquier otra cosa** (S3 o la base que no responden, un error
  inesperado) se supone transitoria. El mensaje queda en la cola y vuelve a
  los 30 segundos (`RETRY_DELAY_SECONDS`), con el job en `pending` y el error a
  la vista. Al tercer intento (`maxReceiveCount` de la cola) el job queda en
  `failed` y SQS pasa el mensaje a la DLQ, `lgdc-jobs-dlq`, donde espera 14
  días a que alguien lo mire.

Un mensaje que no es un archivo nuevo (el `s3:TestEvent` que S3 manda al crear
la notificación) o una clave que no es de una cadena se descartan con un log.

## De 0 a N

El servicio de Fargate arranca en 0 tareas y lo escala Application Auto
Scaling, mirando la cola:

- **Para arriba**: mientras haya mensajes visibles (esperando que alguien los
  tome), una tarea más por minuto (dos con más de diez), hasta
  `worker_max_capacity` (4).
- **Para abajo**: solo a 0, y solo después de 5 minutos sin mensajes visibles
  **ni en vuelo**. Un mensaje que un worker está procesando está en vuelo, así
  que nunca se apaga un worker en medio de un job. Bajar de a una tarea podría
  hacerlo: ECS elige cuál detener.

El worker no se apaga solo: espera mensajes con long polling hasta que ECS le
manda `SIGTERM`, y termina después del receive en curso (20 segundos, menos
que los 30 que da ECS antes del `SIGKILL`).

Las métricas de SQS llegan a CloudWatch con unos minutos de demora, así que
el primer worker tarda de 3 a 5 minutos en aparecer.

## Qué hace con el archivo

[`sepa.py`](sepa.py) es el parseo puro: no toca red, ni base, ni S3. Ahí viven
las reglas de qué fila sirve, y se pueden leer solas.

Un archivo de cadena trae tres CSV separados por `|`:

| Archivo | Qué aporta |
|---|---|
| `comercio.csv` | La empresa y sus banderas. Cencosud publica Disco, Jumbo y Vea en un mismo archivo, y al comprador le importa en cuál está parado |
| `sucursales.csv` | Las sucursales, con provincia, localidad y coordenadas |
| `productos.csv` | Un precio por producto y sucursal. Es el grueso del archivo |

Las columnas se leen **por nombre, no por posición**: así una cadena puede
agregar columnas sin rompernos, y si falta una nos enteramos.

### Qué se descarta

Una fila se rechaza si el código de barras no tiene 13 dígitos o es todo ceros,
o si el precio no es un número, es cero o negativo, o pasa los diez millones.
Cada rechazo se guarda con **el número de línea, el motivo y la línea original**,
en `rejected/…/rejected.jsonl`. Eso es lo que se le devuelve a la cadena.

### Cuando el archivo entero está mal

Dos casos terminan en `failed`, no en filas cargadas:

- **El archivo no abre, o le falta una columna del formato.**
- **Más del 5% de las filas se rechazó** (`REJECT_THRESHOLD`). Eso no es una
  cadena publicando datos sucios: es una cadena que cambió el formato. Cargar el
  resto como si nada sería peor que fallar.

El job falla solo, sin reintento (ver arriba); las otras quince cadenas
siguen su curso.

## Dónde deja las cosas

```
base de datos   branch, product, price   ← lo que consulta el portal
curated/…/prices.parquet                 ← columnar, para reportes largos
rejected/…/rejected.jsonl                ← qué se descartó y por qué
rejected/…/<archivo>                     ← la copia de un archivo que no se pudo cargar
```

Reprocesar una cadena **reemplaza** los precios de ese día en vez de sumarlos, así
que correr un job dos veces deja las mismas filas. Es lo que hace seguro
reintentar después de una caída.

## El catálogo unificado, sin que gane el último que cargó

Cada cadena describe el mismo código de barras con su propio texto. La
versión vieja de este archivo hacía `ON CONFLICT (ean) DO UPDATE SET
description = …`: la que quedaba era la de la última cadena en re-ingestar,
sin ningún criterio detrás. Ahora `load()` guarda la descripción de **cada**
cadena en `product_name` (`ean, company_id`) y arma la fila canónica de
`product` recalculando, solo para los códigos que el job tocó, cuál
descripción es la que más cadenas coinciden en usar. Reprocesar un día
vuelve a hacer la misma cuenta con los mismos datos, así que el resultado no
depende del orden en que las cadenas se cargaron.

## Detección de cambios

Adentro de la misma transacción que carga los precios, **`detect_changes()`**
compara el precio de cada producto y sucursal de este job contra el
`feed_date` anterior *de esa misma cadena* (lo busca en `ingest_job`, no con
`MAX(feed_date)` sobre `price`, que no tiene índice que lo sostenga), guarda
una fila en `price_change` por cada uno que se movió y dice cuántos fueron:
ese número viaja en el aviso (`price_changes`). Un cambio por debajo de
`CHANGE_FLOOR_PCT` (0.5% por default) ni se guarda: es ruido de redondeo, no
una variación real. El primer día de una cadena no genera nada — no hay
contra qué comparar.

Las alertas **ya no se generan acá**: las genera la Lambda alert-matcher
([`app/batch/alert_matcher`](../alert_matcher/README.md)) cuando le llega el
aviso de una cadena con cambios. El worker sí borra, en la misma garantía de
reproceso que el resto de `load()`, todo lo derivado de
`(feed_date, company_id)` — alertas, cambios y precios, en ese orden — antes
de volver a insertar: reprocesar un día no deja alertas de precios que ya no
están, y el alert-matcher genera las de los cambios nuevos con el aviso
nuevo.

## El aviso de cadena procesada

Cada job que termina se avisa **una vez** en el topic SNS
`lgdc-cadena-procesada`, con [`outbox.py`](outbox.py):

```json
{"feed_date": "2026-10-12", "company_id": 10, "kind": "sepa", "status": "done", "price_changes": 4213}
```

`kind` es `sepa` o `upload` (`company_id` es entonces el del comercio);
`status` es `done` (con cuántos precios cambiaron) o `failed` (con
`price_changes = 0`). No lleva precios: es un aviso por cadena y día.

**El outbox.** El aviso se guarda en la tabla `outbox` en **la misma
transacción** que cierra el job: la de `load()` cuando el job termina en
`done`, la de `reject()` para un archivo roto, y la del último intento de
un error transitorio, cuando el job queda en `failed` y el mensaje se va a
la DLQ. Un intento que todavía se va a reintentar no avisa nada. Así, el
aviso existe exactamente cuando existe el resultado del job: si la
transacción no llega al commit, no hay ni precios ni aviso.

**La publicación.** Después de cada receive, el worker publica todo lo que
el outbox tiene sin enviar, del más viejo al más nuevo, y marca cada uno con
`sent_at` apenas SNS lo acepta (`FOR UPDATE SKIP LOCKED`: dos workers no
publican el mismo). Si SNS no responde, el error queda en el log y el aviso
sigue en el outbox: lo publica el próximo receive de ese worker, o el
próximo worker que arranque. Es *al menos una vez*: si el worker muere entre
el publish y la marca, el aviso sale dos veces, y sus consumidores lo
toleran (las alertas son únicas por regla, día, producto y sucursal; el
cierre del día ya tiene marcada la cadena y no hace nada). Un mensaje del job que llega dos veces no
genera otro aviso: el inbox lo descarta antes.

**Los atributos.** Los mismos cinco campos van como atributos del mensaje
(`company_id` y `price_changes` como `Number`), porque SNS filtra cada
suscripción por ellos: la SQS de alertas recibe solo `done` con
`price_changes > 0`, la del cierre del día todos, y el email del admin solo
`failed`. Para ese email el mensaje lleva un asunto (`LGDC: error al
procesar la cadena 10 del 2026-10-12`); a las colas les llega el aviso solo.

## Carga propia: una cadena más, para el resto del código

[`upload.py`](upload.py) lee el CSV que sube un comercio que no está en el
SEPA (`ean,price[,description,brand,presentation]`, separado por comas) y
devuelve el mismo `sepa.Contents` que arma `sepa.read()`. Es lo que permite
que `process()` no tenga que saber, más allá de un `if`, si el archivo vino
del SEPA o de una carga propia: `store_rejects`, `store_curated` y `load` no
cambian una línea.

Lo que hace que esto funcione sin romper nada existente es el `company_id`
sintético que el backend le asigna a cada comercio (arrancando en
900.000.000, bien afuera del rango del SEPA). El `chain_id` de una carga
propia es ese número como texto: `int(job.chain_id)` sigue andando, el
`DELETE` de reproceso por `(feed_date, company_id)` sigue andando, y para
el resto de la base una carga propia es, literalmente, una cadena más.

`ingest_job.kind` (`'sepa'` por default, o `'upload'`) es lo único que decide
qué lector usar — ver `process()` en `main.py`. Un archivo que no es texto
UTF-8 (un ZIP, una planilla) falla como un archivo roto, sin reintento.

Lo único que cambia es dónde quedan sus rechazos: en
`rejected/uploads/<upload_id>/` (`rejected.jsonl`, o la copia del archivo si
está roto) y no en la partición del día, para que cada carga conserve los
suyos aunque haya otra del mismo día. La API los lee de ahí
(`GET /uploads/{id}/rejected`). El Parquet sí va a la partición del día en
`curated/`: como en Postgres, la última lista del día reemplaza a la
anterior.

## El mantenimiento del catálogo

El worker ya no lo corre. `normalize_catalog` y `refresh_neighbors`
([`app/shared/maintenance.py`](../../shared/maintenance.py)) son
reconstrucciones completas, y las corre una sola vez por día la Lambda de
cierre del día, cuando termina la última cadena
([`app/batch/day_close`](../day_close/README.md)), que se entera por el aviso
de cadena procesada. Antes las corría cada worker cuando la cola se vaciaba.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `JOBS_QUEUE_URL` | — | La SQS de jobs. Sin ella el worker no arranca |
| `MAX_RECEIVE_COUNT` | `3` | El `maxReceiveCount` de la cola: en ese intento un error transitorio deja el job en `failed` |
| `RETRY_DELAY_SECONDS` | `30` | Cuánto espera un job que falló por un error transitorio antes del próximo intento |
| `REJECT_THRESHOLD` | `0.05` | Sobre esa proporción de rechazo, el archivo entero falla |
| `MAX_ROWS_PER_JOB` | `0` | Tope de filas por job. `0` lee todas. Útil en máquinas con poco disco |
| `CHANGE_FLOOR_PCT` | `0.5` | Debajo de ese porcentaje, un cambio de precio es ruido y no se guarda |
| `CHAIN_PROCESSED_TOPIC_ARN` | — | El topic `lgdc-cadena-procesada`, donde avisa cada job que termina. Sin él el worker no arranca |
| `WORKER_ID` | el hostname | Quién quedó registrado en el job |

Terraform le pasa las primeras dos, el topic, el bucket y la conexión a la
base (`infra/main.tf`, módulo `batch`).

## Los chequeos

La suite `pipeline` de `make verify` ([`tools/verify`](../../../tools/verify/README.md)),
contra el stack desplegado:

```bash
make verify SUITES=pipeline
```

Entra por las mismas puertas que un usuario o la cátedra: sube archivos a
`raw/` y manda mensajes a la cola como lo haría la CLI, lee los jobs y los
precios por la API pública (con un owner de prueba) y las tareas por la API de
ECS. Las cadenas son sintéticas (899000001 a 899000003, sin coordenadas, así
que nunca entran en una comparación) y con la fecha de hoy: correrlo de
nuevo reemplaza las mismas filas, y sus archivos en el bucket se borran al
terminar. Tarda unos 15 minutos, casi todos esperando que el servicio arranque
desde 0 y vuelva a 0. La suite `competitors` también carga sus sucursales por
el worker (tres cadenas con coordenadas, para el radio) y chequea que
`branch_neighbor` se reconstruye al cerrarse el día, y la suite `uploads`
sube la lista de un comercio como lo hace el portal (ver
[`tools/verify`](../../../tools/verify/README.md)).

| Grupo | Cómo se prueba |
|---|---|
| Sin mensajes | Con la cola vacía, el servicio tiene 0 tareas |
| Carga | Un ZIP con 49 precios y una fila rota en `raw/` deja el job en `done` con esos conteos; la API sirve los 49 precios con el valor del archivo; `curated/` tiene el Parquet con 49 filas y `rejected/` la fila rota con su motivo |
| Reentrega | El mismo mensaje (misma clave y versión) mandado otra vez a la cola se consume sin volver a correr el job ni cambiar los precios |
| Archivo roto | Un archivo que no es un ZIP deja el job en `failed` con el motivo, su copia en `rejected/` y un solo intento |
| Error transitorio | Un mensaje por una versión que no existe falla tres veces, el job queda en `failed` con `attempts = 3` y el mensaje aparece en la DLQ (el chequeo lo borra de ahí) |
| Escalado | Hubo workers corriendo mientras había mensajes, y el servicio vuelve a 0 tareas |

El aviso de cadena procesada lo chequea la suite `events`: a qué suscripción
le llega el aviso de una cadena con cambios, de una sin cambios y de un
archivo roto, que llega una sola vez aunque el mensaje del job llegue dos, y
que el cambio de precio termina en una alerta en el portal (ver
[`tools/verify`](../../../tools/verify/README.md)).

## Lo que falta

- **Las alertas dependen de que `branch_neighbor` esté al día.** Lo recalcula
  el cierre del día, cuando termina la última cadena; la primera carga de una
  cadena no tiene vecinos todavía, y el alert-matcher, que corre apenas llega
  el aviso, no genera nada para ella.
- **Un aviso que no se pudo publicar espera al próximo worker.** Si SNS
  falla y el servicio vuelve a 0 tareas, el aviso queda en el outbox hasta
  que llegue otro job. Y se publican en orden: si SNS rechazara siempre un
  aviso en particular, los siguientes esperarían detrás de él (con el error
  en el log de cada pasada). Con avisos de cinco campos fijos no hay hoy
  nada que SNS pueda rechazar de uno y no de los demás. El cierre del día
  tiene su cron de respaldo para no depender de eso.
- **Un job que falla porque la base no responde, en su último intento, no
  avisa.** El aviso `failed` se guarda en la base: si es la base la que no
  anda (o la tarea muere en ese intento), el mensaje se va a la DLQ sin
  aviso, así que no hay email. El cron de respaldo del cierre cierra el día
  igual.
- **Reprocesar fuera de orden cronológico deja desactualizado el día
  siguiente.** `detect_changes()` de un día ya cargado no se recalcula solo
  si se reprocesa un día anterior después. Ver [`app/api/README.md`](../../api/README.md).
- **Una carga cuyo archivo nunca llegó queda en `awaiting_file`**: si el
  POST del navegador falla, no hay archivo ni mensaje, y nada la cierra. El
  comercio la ve pendiente y la puede borrar.
