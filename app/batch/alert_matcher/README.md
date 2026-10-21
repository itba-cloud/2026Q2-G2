# app/batch/alert_matcher · las alertas de los comercios

Convierte los cambios de precio de una cadena en las **alertas** de los
comercios cuyas reglas los miran. Es la Lambda `lgdc-alert-matcher`, y la
invoca la SQS `lgdc-alerts`, que recibe del topic `lgdc-cadena-procesada`
solo los avisos de las cadenas que terminaron en `done` con
`price_changes > 0` (ver [El aviso de cadena procesada](../worker/README.md#el-aviso-de-cadena-procesada)).

Antes las generaba el worker en la misma transacción que la carga. Ahora el
worker solo guarda los cambios (`price_change`) y avisa; sumar o cambiar
cómo se generan las alertas no toca la carga.

## Qué hace con cada aviso

Lee `feed_date` y `company_id` del aviso y corre un solo `INSERT … SELECT`
([`main.py`](main.py)) que cruza los cambios de ese día de esa cadena con las
reglas de todas las organizaciones. Un cambio llega a una regla si:

- la sucursal que cambió está dentro del radio de la regla (o, si la regla no
  tiene, del de su sucursal seguida), según `branch_neighbor`;
- el cambio supera el umbral de la regla (`threshold_pct`, en valor
  absoluto);
- la regla no nombra competidor, o nombra esa cadena (y, si las nombra, esa
  bandera y esa sucursal);
- la regla mira todos los productos, o ese código de barras está en su lista
  de productos críticos (`watch_product`).

Cada coincidencia es una fila en `alert`, que el portal lista en Alertas
(`GET /alerts`, `GET /alerts/unread-count`).

## Reintentos e idempotencia

- **Un aviso dos veces no duplica nada**: `alert` es única por regla, día,
  producto y sucursal, y el `INSERT` hace `ON CONFLICT DO NOTHING`. Una
  alerta ya leída no vuelve a aparecer sin leer.
- **Lotes de hasta 10 avisos**, y la función devuelve cuáles fallaron
  (`ReportBatchItemFailures`): vuelve a la cola solo ese, no el lote entero.
  Al tercer intento pasa a la DLQ `lgdc-alerts-dlq`.
- **Reprocesar un día** lo resuelve el worker: borra las alertas de esa
  cadena y ese día junto con sus precios, y el aviso nuevo las vuelve a
  generar con los cambios nuevos. Entre el commit del worker y el
  alert-matcher (segundos) esas alertas no están, y vuelven sin leer.

## Dónde corre

Dentro de la VPC, en la banda app, como la API: llega a Postgres por el RDS
Proxy y a Secrets Manager (la contraseña) por su interface endpoint. La
conexión se abre en la primera invocación y queda abierta para las
siguientes del mismo entorno. Lambda lee la cola por su cuenta, sin pasar
por la VPC, y corre a lo sumo **2 invocaciones a la vez**
(`maximum_concurrency` del event source mapping), para no ocupar conexiones
del Proxy: son 15 a 20 avisos por día. Tiene 60 segundos por lote, y la
cola un *visibility timeout* de 6 veces eso.

El paquete lo arma `make lambdas` en `build/alert_matcher.zip`: este
módulo y los de [`app/shared`](../../shared/), planos, más las dependencias
de [`requirements.txt`](requirements.txt). El handler es `main.handler`.

## Configuración

Solo la conexión a la base, las
[variables comunes](../../../docs/desarrollo.md#configuración): Terraform le pasa `DB_HOST` (el
Proxy), `DB_PORT`, `DB_NAME`, `DB_USER` y `DB_SECRET_ARN`
(`infra/main.tf`, módulo `alert_matcher_function`).

## Los chequeos

La suite `events` de `make verify` ([`tools/verify`](../../../tools/verify/README.md)),
contra el stack desplegado: chequea que la función consume la SQS de
alertas, y que un competidor que sube 20 % dos productos, uno de ellos en la
lista de una regla, termina en una sola alerta en el portal, la del producto
de la regla, con su precio anterior, el nuevo y la variación.

## Lo que falta

- **Depende de que `branch_neighbor` esté al día.** Si la sucursal que
  cambió se cargó por primera vez y el mantenimiento todavía no recalculó
  los vecinos, el cambio no llega a ninguna regla, y el aviso no se vuelve a
  procesar cuando sí están. En el día normal no pasa: el mantenimiento corre
  al cerrarse cada día ([`app/batch/day_close`](../day_close/README.md)) y
  una sucursal nueva no tiene cambios contra un día anterior.
- **Un aviso en la DLQ queda ahí**: nadie lo reprocesa solo, y sin el módulo
  `observability` (TP4) solo se ve en los logs.
