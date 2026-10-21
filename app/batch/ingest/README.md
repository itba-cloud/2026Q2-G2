# app/batch/ingest · la ingesta diaria del SEPA

Baja el archivo que el SEPA publica cada día, por el proxy SEPA, y deja
el archivo de cada cadena de supermercados en `raw/` del bucket de datos.
**Nada más**: no parsea ni toca la base. Cada archivo nuevo en `raw/` le
llega a la SQS de jobs por S3 Event Notifications, y los workers hacen el
resto ([`app/batch/worker`](../worker/README.md)).

Es la Lambda `lgdc-ingest`, **fuera de la VPC**: necesita internet para
llegar al proxy, y la VPC no tiene salida. La invocan:

- **El cron**: una regla de EventBridge, todos los días a las 04:00 UTC
  (01:00 en Buenos Aires; `ingest_schedule_expression` en
  [`infra/variables.tf`](../../../infra/variables.tf)). Usa el `SEPA_API_URL`
  que le puso Deploy.
- **El workflow run-ingest**, que corre `make ingest`
  ([`scripts/run-ingest.sh`](../../../scripts/run-ingest.sh)): invoca la
  función, espera su resultado y lo muestra. Le pasa el `SEPA_API_URL` del
  secret, si está, que reemplaza al del deploy: un túnel nuevo no pide otro
  Deploy.

Las dos la invocan de forma asíncrona, y una corrida que falla no se
reintenta (`maximum_retry_attempts = 0`): reintentar no levanta un túnel
caído, y la próxima corrida guarda solo lo que falte. run-ingest no deja una
conexión abierta minutos esperando la respuesta (la red de un runner de CI la
puede cortar): le pone a la corrida un `run_id`, y la función escribe al
final una línea con él en su log, `ingestion <run_id> finished: {…}` o
`ingestion <run_id> failed: <motivo>`, que el script lee de CloudWatch.

El paquete lo arma `make lambdas` en `build/ingest.zip`:
[`main.py`](main.py) y los módulos de [`app/shared`](../../shared/), planos,
más [`requirements.txt`](requirements.txt). Tiene 15 minutos, 1 GB de memoria
y 2 GB de `/tmp`: bajar 330 MB por el túnel tarda de 2 a 5 minutos.

## El proxy y `SEPA_API_URL`

El SEPA bloquea las IPs de AWS, así que la ingesta no le pide el archivo a
él: se lo pide al proxy de [`tools/sepa-proxy`](../../../tools/sepa-proxy/README.md),
que quien despliega corre fuera de AWS detrás de un túnel (un Quick Tunnel de
Cloudflare). `SEPA_API_URL` es la URL del túnel, sin `/v1/sepa/precios`; en
GitHub, un secret **opcional**. No hay ninguna URL en el código.

Que el evento pueda traer otra URL quiere decir que quien puede invocar la
función puede hacerla bajar de cualquier lado y escribir en `raw/`. No abre
nada nuevo: invocarla pide las mismas credenciales de AWS que escribir en
`raw/` directo.

Deploy no depende del proxy: sin `SEPA_API_URL`, o con el proxy caído, el
apply termina igual y solo la ingesta falla, con un mensaje que lo dice.
run-ingest lo muestra tal cual, en una línea:

```
The ingestion failed: IngestError: SEPA_API_URL is not set. The ingestion downloads the SEPA archive through the group's proxy (tools/sepa-proxy), which runs outside AWS behind a tunnel: start the proxy and its tunnel, and load the tunnel's URL as the SEPA_API_URL secret.
```

Con el proxy caído dice `the SEPA proxy at <url> did not answer (…)`, y si el
proxy responde con un error (por ejemplo, el SEPA tampoco le contesta y no
tiene respaldo), `the SEPA proxy at <url> answered 503: …` con lo que dijo.

## Qué hace, en orden

Leer [`main.py`](main.py) de arriba abajo alcanza; las funciones están en el
orden en que se usan.

1. **`archive_url()`** — arma la URL del proxy, agregando `?dia=` si `SEPA_DIA`
   pide un día puntual. El SEPA no publica uno por fecha: mantiene **siete
   recursos fijos, uno por día de la semana**, y los va pisando; el proxy
   replica ese mismo esquema. Sin URL, falla acá.
2. **`download()`** — lo baja a `/tmp` en streaming. Cualquier error al
   bajarlo es del proxy, y el mensaje lo dice.
3. **`chains_in()`** — adentro del zip diario hay un zip por cadena. Los lista,
   saltea los que vienen vacíos o con nombres que no reconoce, y los ordena.
4. **`store()`** — sube cada uno a `raw/` y comprueba contra S3 que el tamaño
   coincida. Que el `put` no falle no garantiza que lo guardado sea lo enviado.
5. **`handler()`** — escribe en el log la línea con el `run_id` y devuelve
   el resumen, que es lo que muestra run-ingest:

```json
{"feed_dates": ["2026-10-21"], "chains": 16, "stored": 16, "skipped": 0}
```

## Qué día se ingiere

`SEPA_DIA` y `FEED_DATE` son dos palancas distintas, para dos pasos distintos:

- **`SEPA_DIA`** decide **qué archivo pedirle al proxy** (`?dia=lunes`, tal
  cual lo espera el proxy). Vacío pide el más reciente.
- **`FEED_DATE`** decide **qué hacer con lo que vino adentro**: es una guarda
  que corre después de la descarga, sobre la fecha real que traen los nombres
  de los archivos internos.

**La fecha de los datos no sale de la respuesta del proxy.** Sale de los
nombres de los archivos internos, que la traen:
`sepa_1_comercio-sepa-9_2026-08-30_09-05-11.zip`. Es por cadena, así que si una
publicara desfasada quedaría registrada con su fecha real.

Y por eso pedir `FEED_DATE` sirve de guarda: si el archivo que llegó no es del
día pedido, esas cadenas se saltean en vez de guardarse bajo la fecha equivocada.

## Dónde queda cada cosa

```
raw/feed_date=2026-08-30/chain_id=10/sepa_2_comercio-sepa-10_….zip
    └── fecha ──┘        └─ cadena ─┘
```

La clave se deriva de la fecha y la cadena, sin nada aleatorio. Eso es lo que
hace la ingesta repetible: la misma cadena del mismo día siempre apunta a la
misma clave. El worker saca de la clave la fecha y la cadena del job.

## Idempotencia

Correr la ingesta dos veces el mismo día **no** resube archivos ni crea jobs:
si el objeto ya está en `raw/` con el mismo tamaño, se saltea. Como un job
nace de un objeto nuevo (o de una versión nueva), sin subida no hay mensaje
en la cola, y el job que un worker ya terminó queda como estaba.

Reprocesar es una decisión explícita, no un efecto de volver a correr: para
eso está `REPROCESS=1`, que vuelve a subir cada archivo. La versión nueva del
objeto genera otro mensaje, y el worker vuelve a correr el job de esa cadena.

## Configuración

La función recibe de Terraform `S3_DATA_BUCKET` y `SEPA_API_URL`; las demás
son para una corrida a mano. Además de las
[variables comunes](../../../docs/desarrollo.md#configuración):

| Variable | Default | Para qué |
|---|---|---|
| `SEPA_API_URL` | vacío | La URL del túnel al proxy, sin `/v1/sepa/precios`. Vacío, la ingesta falla diciendo que falta. La reemplaza el `sepa_api_url` del evento, que manda run-ingest |
| `SEPA_DIA` | vacío | Qué día pedirle al proxy (`?dia=lunes`). Vacío pide el más reciente |
| `FEED_DATE` | vacío | Guarda posterior a la descarga: solo guarda cadenas fechadas este día |
| `CACHE_ZIP` | vacío | Una ruta acá reusa ese archivo en vez de bajarlo. Solo para pruebas |
| `MAX_CHAINS` | `0` | Cuántas cadenas procesar. `0` son todas |
| `REPROCESS` | vacío | `1` vuelve a subir cada archivo, y el worker vuelve a correr sus jobs |

Desde el repo, con credenciales de AWS en el entorno:

```bash
SEPA_API_URL=https://<id>.trycloudflare.com S3_DATA_BUCKET=lgdc-data-<account_id> \
  PYTHONPATH=app/shared python app/batch/ingest/main.py
```

## Los chequeos

La suite `ingest` de `make verify` ([`tools/verify`](../../../tools/verify/README.md))
la prueba por la misma puerta que run-ingest, `make ingest`, contra el stack
desplegado: sin `SEPA_API_URL` y con un proxy que no responde falla
nombrándolo; con un proxy de prueba detrás de un Quick Tunnel deja un archivo
por cadena en `raw/` y cada job termina en `done`; y la segunda corrida del
mismo día no sube nada ni vuelve a correr ningún job.
