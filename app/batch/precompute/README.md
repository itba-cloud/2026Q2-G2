# app/batch/precompute · el precálculo

Calcula **una vez** la comparación de una sucursal seguida a un radio para un
día, y la guarda en la tabla DynamoDB `lgdc-comparison`, que es de donde leen
el tablero y Mis Precios. Es la Lambda `lgdc-precompute` y consume la SQS
`lgdc-precompute`, donde dejan una entrada por sucursal seguida y radio:

- **El cierre del día** ([`app/batch/day_close`](../day_close/README.md)),
  cuando cierra un día (todas las sucursales seguidas) o cuando carga la
  lista de un comercio (solo la suya).
- **La API**, cuando responde el tablero o Mis Precios de una sucursal que
  todavía no tiene precálculo (con el día que respondió), y cuando se borra
  una carga (el día de esa carga, que ya no tiene esos precios).

```json
{"feed_date": "2026-10-21", "company_id": 10, "banner_id": 1, "branch_id": "523", "radius_km": 5.0}
```

## Qué hace con cada entrada

1. **Lee `META`** de la partición. Si ya nombra un día **más nuevo** que el
   de la entrada, la entrada es vieja y no calcula nada (`outcome: stale`):
   `META` nunca vuelve a un día anterior. Pasa seguido: cada cierre de un día
   viejo encola todas las sucursales seguidas, y `make verify` cierra días de
   hace 100 a 160 días.
2. **Corre la comparación** de siempre, `comparison.dashboard`
   ([`app/shared/comparison.py`](../../shared/comparison.py)), la misma que la
   API corre en vivo, **sin `LIMIT`**: el resumen de posición y todas las
   filas, la peor brecha primero. Contra la réplica, o contra la primaria si
   `read_replica_enabled = false` (ver abajo).
3. **Escribe el día como una generación nueva**
   ([`app/shared/precomputed.py`](../../shared/precomputed.py),
   [ADR-0005](../../../docs/adr/0005-precalculo-por-generaciones.md)), en este
   orden: las filas con `BatchWriteItem`, después el resumen y al final
   `META` con el día, la generación y el resumen. Hasta que `META` cambia, la
   API sigue leyendo la escritura anterior, que está entera: también cuando
   es el mismo día escrito otra vez (una carga, un día reabierto). `META` se
   escribe con una condición (`no existe` o `feed_date <= nueva`), así que si
   dos entradas corren juntas, la de un día más viejo no pisa a la más
   nueva.

Es repetible: la misma entrada otra vez reescribe las mismas claves. Una
sucursal sin precios ese día, o sin competidores en el radio, escribe su
resumen (todo en 0, o `compared = 0`) y ninguna fila.

## La tabla `lgdc-comparison`

Una partición por sucursal seguida y radio
(`CMP#<company>:<banner>:<branch>#<radio con un decimal>`):

| `SK` | Qué es | TTL (`expires_at`) |
|---|---|---|
| `META` | `feed_date` y `generation`: el día y la escritura completos que se muestran, con su resumen, `rows` y `rows_expire_at` (hasta cuándo duran sus filas) | Ninguno: nunca se borra, y así siempre sabe cuál es el día más nuevo |
| `SUM#<día>` | El resumen de posición (`compared`, `cheaper`, `matched_min`, `dearer`, `own_assortment`) y `rows`, de la última escritura del día | 400 días (alimenta Tendencias, TP4) |
| `ROW#<día>#<generación>#R00001` … | Las filas de una escritura, en orden de peor brecha; además, `rank` y `desc_norm` (la descripción en minúsculas, para el filtro) | 7 días |
| `QUEUED` | La API encoló el precálculo de esta partición (para no encolarlo en cada recarga) | 5 minutos |

Los precios se guardan como el texto del `NUMERIC` de Postgres (`"100.00"`),
igual que los devuelve la consulta en vivo: un número de DynamoDB perdería
los ceros del final. Si la ingesta para y pasan 7 días sin un día nuevo,
las filas de `META` vencen, y la API vuelve a la consulta en vivo (y encola
el precálculo).

## La réplica

Con réplica, antes de leer espera a que la réplica haya aplicado todo lo que
la primaria tenía confirmado al llegar la entrada
(`pg_last_wal_replay_lsn() >= pg_current_wal_lsn()` de la primaria): los
precios de los que habla la entrada se confirmaron antes de encolarla. Si en
60 segundos no llegó, la entrada falla y vuelve a la cola. Se conecta
**directo** a la instancia, sin el Proxy: son a lo sumo dos invocaciones a la
vez, con una conexión a cada instancia.

## Dónde corre

Dentro de la VPC, en la banda app: llega a la base por su security group,
a DynamoDB por el gateway endpoint y al secreto por el interface endpoint de
Secrets Manager. **De a una entrada por invocación** (`batch_size = 1`) y **a
lo sumo 2 invocaciones a la vez** (`maximum_concurrency` del event source
mapping: el Learner Lab no deja reservar concurrencia), para cuidar la base.
Tiene 300 segundos (`local.precompute_timeout`) y la cola un *visibility
timeout* de 6 veces eso. Al tercer intento fallido, la entrada pasa a
`lgdc-precompute-dlq`.

El paquete lo arma `make lambdas` en `build/precompute.zip`. El handler es
`main.handler`.

## Configuración

Terraform le pasa la conexión a la base (`DB_HOST`, la primaria, `DB_HOST_RO`,
la réplica o vacío, `DB_PORT`, `DB_NAME`, `DB_USER` y `DB_SECRET_ARN`) y
`COMPARISON_TABLE` (`infra/main.tf`, módulo `precompute_function`).

## Lo que deja en el log

Una línea por entrada, `precompute {...}`, que es lo que lee `make verify`:

```json
{"feed_date": "2026-10-21", "company_id": 10, "banner_id": 1, "branch_id": "523",
 "radius_km": 5.0, "outcome": "published", "rows": 7954, "compared": 7954,
 "query_s": 1.52, "write_s": 9.8}
```

`outcome` es `published` (escribió el día y movió `META`) o `stale` (había un
día más nuevo; con `meta`, cuál).

## Los chequeos

La suite `precompute` de `make verify` ([`tools/verify`](../../../tools/verify/README.md)),
contra el stack desplegado.

## Lo que falta

- **Reescribir un día deja la generación anterior** hasta que vence (7
  días): más almacenamiento por cada carga o día reabierto.
- **Una entrada en la DLQ queda ahí**, y sin el módulo `observability` (TP4)
  solo se ve en los logs.
