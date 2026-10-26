# app/db_migrator · el esquema de la base

La Lambda migradora: aplica todo el esquema de Postgres durante el apply de
Terraform, sin pasos manuales. `make lambdas` la empaqueta en
`build/db_migrator.zip`, plana con [`app/shared`](../shared/) y
[`requirements.txt`](requirements.txt); el handler es `main.handler`.

| Archivo | Qué es |
|---|---|
| [`schema.py`](schema.py) | Todo el DDL, en el orden en que corre. No habla con nada |
| [`main.py`](main.py) | El handler: se conecta, aplica `schema.py`, asegura las particiones de `price`, agenda `pg_cron` y devuelve las tablas que quedaron |

## Cómo corre en AWS

[`infra/migration.tf`](../../infra/migration.tf) la invoca con un
`aws_lambda_invocation` en cuanto existen la base y la función. Si la
migración falla, falla el apply. Vuelve a invocarse solo si cambia el código
(el hash del zip) o se reemplaza la instancia; un Deploy repetido no la toca.

Corre en la banda app de la VPC y se conecta **directo a la instancia**, no
por el RDS Proxy: el DDL no necesita pooling y así no depende de que el
target del Proxy ya esté disponible. La contraseña la lee de Secrets Manager
(`DB_SECRET_ARN`), por el interface endpoint.

Devuelve `{"tables": [...]}`: las tablas de `public` sin las particiones de
`price`. Terraform lo expone como el output `db_schema_tables`.

Es **idempotente**: todo es `CREATE ... IF NOT EXISTS`, `CREATE OR REPLACE`
o un bloque que ignora `duplicate_object`, así que aplicarla dos veces no
cambia nada ni pisa datos.

Desde el repo, contra una base a mano:

```bash
PYTHONPATH=app/shared DB_HOST=localhost DB_PORT=5432 python app/db_migrator/main.py
```

## Qué crea

| | Qué | Por qué |
|---|---|---|
| **Extensiones** | `cube`, `earthdistance`, `pg_trgm`, `unaccent` | El radio sin PostGIS y la búsqueda por trigramas (ver [`app/api/README.md`](../api/README.md)) |
| **`CORE`** | `ingest_job`, `product`, `branch`, `price` | El catálogo y el registro de los jobs |
| **`PORTAL`** | tenencia, reglas, alertas, cargas y las tablas derivadas | Lo que suma la API |
| **`PIPELINE`** | `processed_message`, `outbox`, `feed_day`, `feed_day_chain` | Lo que suma el pipeline S3 → SQS del TP3 (abajo) |
| **Índices** | al final, algunos caros | Ver `schema.py`, que lleva el comentario de por qué existe cada uno |
| **Particiones** | las de `price` para los 6 meses de retención, el mes actual y tres hacia adelante | `price` está particionada por mes y **no tiene partición `DEFAULT`**, así que todo mes que se vaya a escribir tiene que existir antes del `INSERT`, incluidos los de atrás: el archivo stageado, una carga propia y cualquier reproceso traen un `feed_date` que no es hoy |
| **`pg_cron`** | el job `price-partitions`, 03:00 UTC | Rueda las particiones desde adentro de la base. El parameter group de la instancia precarga `pg_cron` desde el primer arranque. Best effort: sin la extensión (un Postgres local) avisa con un warning y sigue |

## Las tablas del pipeline

| Tabla | Para qué |
|---|---|
| `processed_message` | El inbox de idempotencia. Una fila por mensaje de SQS cargado, con clave `<key de S3>#<versionId>`. El worker la inserta con `ON CONFLICT DO NOTHING` en la misma transacción que los precios: si ya estaba, descarta el mensaje |
| `outbox` | El aviso de cadena procesada, escrito en la misma transacción que la carga y publicado en SNS después del commit. `sent_at` queda en `null` hasta que se publica, así que nunca se pierde un aviso ni se avisa algo que no se guardó |
| `feed_day` | Una fila por día. La Lambda de cierre gana el cierre con `UPDATE ... SET closed_at = now() WHERE closed_at IS NULL`: de varias invocaciones que terminan juntas, una sola corre el mantenimiento y el precálculo ([`app/batch/day_close`](../batch/day_close/README.md)) |
| `feed_day_chain` | Las cadenas que entraron en un día: las marca el aviso de cada una, y el cierre a todas las que encontró terminadas. El aviso de una cadena que el día no tenía (una que el cron de respaldo no esperó) reabre el día y lo vuelve a cerrar |

## La tabla `ingest_job`

Deja de ser la cola (la cola es la SQS de jobs) y queda como el registro que
muestra la pantalla de Pipeline.

| Columna | Para qué |
|---|---|
| `feed_date`, `chain_id` | Qué día y qué cadena. Juntas son **únicas** para el SEPA |
| `s3_key` | Dónde quedó el archivo |
| `kind` | `sepa` o `upload`: una carga propia recorre el mismo camino |
| `status` | `pending` → `processing` → `done` \| `failed` |
| `worker_id`, `claimed_at`, `attempts` | Quién lo tomó, cuándo, cuántas veces |
| `rows_ok`, `rows_rejected`, `last_error` | Cómo terminó |

## Las tablas del catálogo

| Tabla | Qué guarda |
|---|---|
| `product` | Un producto por código de barras, **compartido entre cadenas**. Es lo que hace comparables los precios entre sucursales |
| `branch` | Las sucursales, con su bandera, localidad y coordenadas |
| `price` | El precio de un producto en una sucursal un día dado |

`price` está **particionada por rango sobre `feed_date`**, un mes por
partición: a 14,5 millones de filas por día, tirar un mes tiene que ser un
`DROP TABLE` y no un `DELETE`. El índice que importa es `idx_price_branch
(feed_date, company_id, banner_id, branch_id, ean) INCLUDE (price)`: la
consulta caliente del portal es el listado de precios de una sucursal para
una fecha, y con el `INCLUDE` sale por index-only scan.

## Los chequeos

```bash
make verify SUITES=schema
```

La suite `schema` de `make verify` ([`tools/verify`](../../tools/verify/README.md)),
contra el stack desplegado: lee `terraform output`, invoca la migradora dos
veces y pide `/api/ready`. Establece que el apply dejó las tablas del TP3, que
migrar otra vez deja exactamente las mismas, y que la API llega a la base por
el Proxy.
