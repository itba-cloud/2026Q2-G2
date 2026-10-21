# app/api · la API

Consultas de precios, conjunto competitivo, alertas y POST prefirmados para
la carga propia. **No parsea nada, no encola nada y no toca S3 salvo para
firmar cargas y leer sus filas rechazadas**: el catálogo lo escribe el
worker, esto solo lo sirve.

En el TP3 corre como Lambda detrás de API Gateway. El paquete lo arma
`make lambdas` en `build/api.zip`: estos módulos y los de
[`app/shared`](../shared/), planos, más las dependencias de
[`requirements.txt`](requirements.txt). El handler es
`main.handler`: [Mangum](https://mangum.fastapiexpert.com/) traduce el evento
de API Gateway (payload 2.0) a ASGI. Corre con `lifespan="off"`, así que un
arranque en frío no abre los pools: los abre el primer request que pide una
conexión, y `/api/health` responde aunque la base no exista.

Para correrla en tu máquina, desde la raíz del repo y con una base a la que
apuntar (las [variables comunes](../../docs/desarrollo.md#configuración)):

```bash
pip install -r app/api/requirements-dev.txt
PYTHONPATH=app/shared uvicorn main:app --app-dir app/api
```

Queda en `http://localhost:8000`, con Swagger en `/docs`. Todo lo demás vive
bajo `/api/*`. Sin API Gateway delante no hay claims de un token, así que
toda ruta salvo `/api/health` y `/api/ready` responde 401.

## Quién pide: el token de Cognito

El login es de Cognito ([`app/auth_callback`](../auth_callback/README.md)):
acá no hay registro, ni login, ni contraseñas, ni emisión de tokens. El
portal manda el **ID token** en `Authorization: Bearer`, y el authorizer JWT
de API Gateway lo valida (firma, issuer, audiencia, vencimiento) antes de
invocar la Lambda. Las únicas rutas sin token son `GET /api/health`,
`GET /api/ready` (sondas: no devuelven datos) y el callback, que es otra
Lambda.

[`auth.py`](auth.py) no verifica firmas: lee los claims que API Gateway dejó
en el evento (Mangum lo guarda en el scope ASGI como `aws.event`). El
usuario es el `sub` del token; exige además `token_use = id` y `email`,
porque un access token del mismo client también pasa el authorizer pero no
trae el email.

| Endpoint | Qué hace |
|---|---|
| `GET /auth/me` | El usuario del token. El primero lo crea en `app_user`, **sin organización** (`org_id` null), y registra el login |
| `POST /me/org` `{name}` | El onboarding del primer login: crea la organización y deja al usuario como `owner`. 409 si ya tiene una |
| `GET /org/members` | Solo `owner`: los usuarios de la organización y su rol, el dueño primero. `last_login_at` es null para un miembro que todavía no entró |
| `POST /org/members` `{email}` | Solo `owner`: invita a un miembro, cualquiera que todavía no esté en una organización. Un email nuevo tiene su fila con rol `member` y su usuario en Cognito, que le manda una contraseña temporal; uno que Cognito ya tenía (se registró y nunca nombró una organización) entra con su contraseña, sin email. 201 con el miembro e `invitation_sent`; 409 si el email ya está en una organización; 422 si no es un email |

Todo lo demás pide un usuario con organización (`Auth`, o `OwnerAuth` para
lo del `owner`): sin ella responde 403 `organization required`, y el portal
lo manda al onboarding. La organización y el rol (`owner`, `member`) viven
en Postgres.

### Los roles

| Quién | Qué tiene de propio | Dependencia |
|---|---|---|
| `owner` | Invitar y listar miembros (`/org/members`) y el tablero de la ingesta (`GET /ingest/jobs`) | `OwnerAuth`: 403 `owner role required` para un `member` |
| `member` | Todo lo demás de su organización, igual que el dueño | `Auth` |
| `platform-admin` | `POST /admin/neighbors/refresh` y `POST /admin/catalog/normalize`, que tocan datos de todas las organizaciones | `PlatformAdminAuth`: 403 `platform-admin group required` para cualquier otro, también para un `owner`. No pide organización |

El admin de plataforma es el grupo `platform-admin` de Cognito, no un rol en
Postgres: viaja en el claim `cognito:groups` del ID token. API Gateway pasa
los claims de tipo lista aplanados en un string (`[platform-admin otro]`), y
`auth.py` acepta las dos formas. Al primer admin lo da de alta Deploy con los
secrets `BOOTSTRAP_EMAIL` y `BOOTSTRAP_PASSWORD`
([`scripts/bootstrap-platform-admin.sh`](../../scripts/bootstrap-platform-admin.sh)).

### La invitación

La API está en la VPC y Cognito no acepta requests por un VPC endpoint de un
user pool con dominio, así que la parte de Cognito la hace la Lambda
[`app/invite`](../invite/README.md), fuera de la VPC, que la API invoca por
el interface endpoint de Lambda
([ADR-0003](../../docs/adr/0003-invitacion-por-lambda-fuera-de-la-vpc.md)).
Todo pasa en una transacción: la fila del miembro primero (un email que ya
está en una organización es 409 sin tocar Cognito), después Cognito, y si
Cognito dice que no, rollback. Pedirle a la Lambda dos veces el mismo email
es seguro (la segunda responde `exists`), así que la API reintenta una vez, y
una invitación cuya respuesta se perdió no deja un usuario de Cognito sin
fila: la próxima lo enlaza. En su primer login el miembro cambia la contraseña temporal en la
Hosted UI, y `GET /auth/me` lo encuentra por el `sub` que guardó la
invitación, ya en la organización del dueño.

Un usuario se busca por `sub`, y en el primer login se enlaza por email: si
ya hay una fila con ese email (alguien que se volvió a registrar en Cognito,
o un usuario del login propio del TP2) toma el `sub` nuevo. Es seguro porque
Cognito confirma el email antes del primer login.

## Por qué `/api/*`, y el CORS

Toda ruta vive bajo `/api` (`/api/health` incluida), y el callback del login
en `/auth/callback`, al lado, en la misma HTTP API. El portal le pega a
`<apiUrl>/api/*`, con `apiUrl` del `config.json` que escribe Terraform.

El portal (su bucket) y la API (API Gateway) son dos orígenes, así que cada
llamada del navegador es cross-origin. El CORS lo resuelve **API Gateway**
(`cors_configuration` en `infra/modules/api`): pone los headers en toda
respuesta, también en los 401 del authorizer, así el portal ve un token
vencido como un 401 y no como un error de CORS. Los preflights llegan sin
token, así que tienen su propia ruta pública (`OPTIONS /{proxy+}`): por
`$default` el authorizer los rechazaría. La app solo les responde 204
(`preflight` en `main.py`) y API Gateway agrega los headers; por eso no hay
`CORSMiddleware`. `allow_origins = ["*"]` no
cuesta nada: el token viaja en el header `Authorization`, nunca en una
cookie, así que una página hostil obtiene lo mismo que un `curl` anónimo.

## En AWS: la base por el RDS Proxy

Terraform pone en `DB_HOST` el endpoint del RDS Proxy, en `DB_PORT` el
`5432` y en `DB_SECRET_ARN` el secreto con la contraseña, que `common.py`
lee de Secrets Manager (por el interface endpoint) la primera vez que abre
una conexión. La contraseña nunca está en las variables de entorno.

`GET /api/ready` es la prueba de que eso funciona: hace `SELECT 1` con una
conexión del pool primario y responde 200 solo si la consigue en 5 segundos.
Si no, 503 `database unreachable`, bien antes de los 30 s de la Lambda y de
API Gateway. Es abierta, como `/api/health`, que en cambio no toca la base.

## Por qué los montos vienen como string en el JSON

`price`, `min_price`, `radius_km`, `pct_over_min` y cualquier otro campo
`NUMERIC` de Postgres se serializan como **string**, no como número. Es el
comportamiento de FastAPI/Pydantic para `Decimal`: perder precisión en un
precio por redondeo de punto flotante es peor que obligar al que consume la
API a hacer `parseFloat`. El frontend ya lo tiene en cuenta; si escribís un
cliente nuevo, hacelo vos también.

## El pool de conexiones

`common.connect()` sirve para un script que abre una conexión y se va —
la migradora (`app/db_migrator/main.py`), `app/batch/worker/main.py`, el `verify.py` de la ingesta. Acá no: sería una
conexión nueva por request, y peor, `connect()` llama a `database_port()`,
que le pregunta a la API de RDS en cada una salvo que `DB_PORT` esté fijo.
`db.py` abre los pools una sola vez por instancia de la Lambda, en el
primer request que pide una conexión (no al importar el módulo), y los
comparte entre los requests siguientes. `GET /api/health` no los toca: por eso
responde aunque RDS no esté.

Son **dos** pools, no uno. Los endpoints de sólo lectura (`ReadConn` en
`main.py`: comparación, head-to-head, catálogo, competidores, alertas) van
al host de `DB_HOST_RO`; todo lo que escribe —y `/uploads*`, `/rules*`,
`/me/*`, los dos `/admin/*`— va al primario.

Si `DB_HOST_RO` no está seteada, el pool de lectura apunta al host del
primario. Es lo que pasa en AWS: Terraform no se la pasa a la API, así que
los dos pools van al RDS Proxy, y la réplica la lee solo el
[precálculo](../batch/precompute/README.md), que es el que corre la
comparación pesada. Sigue siendo un pool aparte igual; ver abajo por qué.

### El control de tenencia va al primario, la consulta pesada a la réplica

Ocho endpoints piden las dos conexiones (`connection: ReadConn, primary: Conn`)
y no es redundancia. La consulta pesada lee sólo datos del pipeline
(`price`, `branch`, `branch_neighbor`, `product`): ahí el lag de la réplica
significa "precios un rato viejos", que es lo que un feed diario es de todos
modos. Pero el `_branch_or_404` lee `org_branch`, que el usuario pudo haber
escrito **hace segundos** al seguir la sucursal que está mirando justo ahora.
Resolverlo contra la réplica es decirle a alguien que acaba de suscribirse que
no está suscripto — un 404 en la cara, y encima el peor lag es durante la
ventana de ingesta, cuando el primario está haciendo `COPY` de 14,5 millones de
filas y es cuando más gente está haciendo clic.

Por eso el pool de lectura es siempre un pool propio, incluso sin réplica. Si
fuera el mismo objeto que el primario, un request que sostiene una conexión de
lectura y después pide una de escritura estaría sacando dos conexiones del
mismo pool de diez: con diez requests así en paralelo, cada uno espera una
conexión que otro tiene. Dos pools no pueden hacerse eso. De paso, un run local
ejercita el mismo camino que AWS en vez de ser un caso especial que nunca se
prueba.

## Las URLs prefirmadas necesitan dos endpoints, no uno

Una URL prefirmada solo sirve si quien la recibe puede resolver el host que
lleva firmado adentro. Con el `docker compose` del TP2, el backend hablaba con
floci por la red de Docker (`AWS_ENDPOINT_URL=http://floci:4566`), pero un
navegador corriendo en tu
máquina no resuelve `floci` — solo el puerto que compose publicaba en el host
(`http://localhost:4566`). En AWS real esto no pasa: S3 es resoluble desde
cualquier lado con el mismo endpoint. Por eso `presigning_s3_client()` en
`main.py` arma un cliente aparte solo para firmar, apuntado a
`AWS_PUBLIC_ENDPOINT_URL` — vacío (y por lo tanto ignorado) en AWS, y a
`http://localhost:4566` en el `docker compose` del TP2.

## El radio geográfico sin PostGIS

`cube` + `earthdistance` en vez de PostGIS: están en la lista de extensiones
que RDS permite crear sin pedir nada especial, y para el volumen de este
proyecto (sucursales en el orden de los miles, no millones) alcanzan de
sobra. El índice GiST sobre `ll_to_earth(latitude, longitude)` hace que el
filtro por radio (`earth_box(...) @> ll_to_earth(...)`, refinado con
`earth_distance(...) <= radio`) use índice en vez de recorrer toda la tabla.

Los pares de sucursales cercanas (`branch_neighbor`) están **precalculados**,
no se resuelven en cada consulta: `branch` cambia a lo sumo una vez por día,
así que recorrer el radio geográfico en cada pedido del portal sería trabajo
repetido de más. Los recalcula el **cierre del día**
([`app/batch/day_close`](../batch/day_close/README.md)), una vez, cuando
termina la última cadena del día (`maintenance.refresh_neighbors`), así que
en el ciclo normal no hay que llamar nada. La excepción es la sucursal propia
de un comercio: `POST /me/merchant` le calcula sus vecinos en el momento
(`maintenance.refresh_branch_neighbors`, solo esa sucursal, en los dos
sentidos), así su primera carga ya se compara contra la competencia.

`POST /admin/neighbors/refresh` sigue estando para forzar el rebuild completo,
y lo puede usar solo el admin de plataforma. Tarda más de lo que API Gateway
espera (30 s), así que la API no lo corre: invoca la Lambda del cierre del día
de forma asíncrona (`DAY_CLOSE_FUNCTION_NAME`) y responde `202
{"status": "started"}`. Esa Lambda reconstruye los pares y vuelve a encolar el
precálculo de cada sucursal seguida, para el último día cerrado.

## El catálogo unificado, de verdad esta vez

`app/batch/worker/main.py` guarda la descripción de cada cadena por separado en
`product_name` y arma la fila canónica de `product` por **mayoría de votos**
entre cadenas, no por quién cargó último (ver [`app/batch/worker/README.md`](../batch/worker/README.md)).
`maintenance.normalize_catalog` va un paso más: pliega esa mayoría a texto
sin acentos ni mayúsculas (`product_norm`) e intenta separarle el tamaño
(`900 g`, `1.5 l`) con una expresión regular, para que `GET /products?q=`
busque por trigramas (`pg_trgm`) en vez de un `LIKE` literal. Lo corre el
cierre del día junto con el otro rebuild; hasta que corra por primera vez,
`GET /products` cae a un `ILIKE` simple sobre `product` — funciona peor, nunca
rompe. `POST /admin/catalog/normalize` lo fuerza (solo el admin de plataforma).

## Cuando el radio es denso, la comparación necesita más memoria de la que Postgres da por default

`GET .../comparison` calcula, por cada producto propio, el mínimo/promedio/
máximo entre **todas** las sucursales del radio — para una sucursal con
pocos vecinos eso es trivial, pero para una densa (medido: una sucursal de
Coto en CABA con 621 vecinos en 5 km) el cruce antes de agrupar pasa el
millón de filas. Con eso, dos cosas salían caras:

1. **El `JOIN` a `product` se hacía antes de agrupar**, así que corría una
   vez por cada una de esas ~1.000.000 de filas en vez de una vez por cada
   ~8.000 productos del resultado final. `COMPARISON_BASE` en
   [`app/shared/comparison.py`](../shared/comparison.py) ahora
   agrega primero (en la CTE `matched`) y recién después hace ese `JOIN`,
   sobre el resultado ya chico.
2. **El `GROUP BY` de esa agregación no entraba en `work_mem`** (4 MB por
   default), así que Postgres ordenaba esas filas en disco (*external merge
   sort*) en vez de en memoria. El parameter group de la instancia
   (`infra/modules/database`) fija `work_mem` en 64 MB — alcanza para que esa
   ordenación entre en memoria sin comprometer demasiado. Antes lo pedía
   `db.py` al abrir cada conexión (`options='-c work_mem=64MB'`), pero RDS
   Proxy rechaza las opciones de arranque.

Las dos juntas: **~7 s → ~1,5 s** en esa misma sucursal, medido con
`EXPLAIN (ANALYZE, BUFFERS)`. Sigue sin ser instantáneo — es agregación en
vivo sobre un millón de filas, no una consulta indexada — pero ya no cuelga
el tablero. Por eso el tablero y Mis Precios ya no la corren en cada request:
la leen precalculada (ver abajo).

## El tablero y Mis Precios leen el precálculo

`GET .../dashboard` y `GET .../comparison` leen la comparación que la Lambda
del [precálculo](../batch/precompute/README.md) guarda una vez por día y por
sucursal seguida y radio en DynamoDB (`lgdc-comparison`, por el gateway
endpoint), y no tocan Postgres más que para el control de tenencia. La
decisión está en [`serving.py`](serving.py):

| El pedido | De dónde sale | `source` |
|---|---|---|
| El radio configurado de la sucursal (o ninguno) y `META` existe | El día de `META`, el último completo: `GetItem` de `META`, que trae el resumen, y `Query` de las filas de su generación con `Limit` | `precomputed` |
| Con `feed_date` igual al de `META` | Igual | `precomputed` |
| Con otro `feed_date`, u otro `radius_km` | La consulta en vivo, como antes; sin `feed_date`, del día de `META` si lo hay | `live` |
| Sin `META` (sucursal recién seguida, o un precálculo vencido) | La consulta en vivo, y **encola el precálculo** de esa sucursal a su radio configurado, con el día que respondió | `live` |

El encolado es a lo sumo uno cada 5 minutos por sucursal y radio (el item
`QUEUED` de la tabla, con una escritura condicional), para que recargar el
tablero no llene la cola. Si DynamoDB o SQS fallan, la respuesta sale en
vivo y queda en el log.

`DELETE /uploads/{id}` que borra precios también encola el precálculo de
las sucursales de ese comercio para ese día: si no, el tablero seguiría
mostrando los precios borrados.

### Mis Precios pagina por cursor

`GET .../comparison` responde `{feed_date, source, items, next_cursor}`, y
acepta `cursor` en lugar de `offset`. `next_cursor` es opaco (un JSON en
base64url con el día y la posición) y es `null` en la última página. Recuerda
de dónde salió la página: el de una página precalculada sigue leyendo **esa
escritura** del precálculo por rango (`generation` y `after`, el ranking de la
última fila), aunque `META` haya pasado a otra entre página y página; el de
una página en vivo sigue en vivo con `LIMIT`/`OFFSET` (`offset`). Un cursor
que la API no armó es un 400. Si DynamoDB falla a mitad de un recorrido sin
filtro, la página sigue en vivo desde el mismo lugar (el ranking es el
offset); con filtro no hay equivalente, y es un 503. Cada página pide una fila de más para saber si hay siguiente, así
que nunca hay una última página vacía.

El filtro por descripción (`q`) sigue andando: en vivo es el `ILIKE` de
siempre; sobre el precálculo, un `FilterExpression contains(desc_norm, :q)`
sobre las filas del día (la descripción en minúsculas), que recorre páginas
de DynamoDB hasta juntar la página pedida.

`GET .../comparison/summary` sigue siempre en vivo.

## Cómo encaja la carga propia

Un comercio que no está en el SEPA no tiene `company_id`: se le asigna uno
**sintético, arrancando en 900.000.000**, bien afuera del rango que usa el
SEPA (2 a 47 hoy). Eso es lo que hace que, para el worker, una carga propia
sea una cadena más: `int(job.chain_id)` no explota y el `DELETE` de reproceso
por `(feed_date, company_id)` funciona igual. La carga sigue el mismo camino
que el archivo de una cadena: **la API no encola nada**, el archivo que llega
a `raw/` es el que crea el job.

```
POST /me/merchant   crea el company_id sintético y la fila en `branch`, con
                    las coordenadas que da el comercio (cualquier sesión, no
                    solo el `owner`). Antes de esto, POST /uploads es 400
POST /uploads       crea la fila de `price_upload` (`awaiting_file`) y firma
                    un POST a raw/uploads/<upload_id>/<archivo>
[el navegador manda el archivo directo a S3: no pasa por la API]
[S3 avisa a la SQS de jobs, y el worker que toma el mensaje crea el job de
 tipo `upload`, lo vincula a la carga (`queued`) y la cierra con él
 (`done` o `failed`)]
GET /uploads/{id}   el estado, con el del job, sus filas y su error
GET /uploads/{id}/rejected
                    las filas que el worker no pudo cargar: línea, motivo y
                    contenido, de rejected/uploads/<upload_id>/rejected.jsonl
DELETE /uploads/{id}
                    la borra con sus precios (salvo que otra carga `done` del
                    día los explique). 409 mientras un worker la procesa
                    (`queued`): sus precios quedarían cargados sin carga
```

En `GET /ingest/jobs` (la pantalla de Pipeline) el job de una carga aparece
como `kind = upload`, en el día de la carga y con el `company_id` sintético
como cadena; `chain_name` trae el nombre de la sucursal propia, y para una
cadena del SEPA, su primera bandera.

Del nombre del archivo queda solo el último segmento: con una barra, el
archivo caería donde el worker no busca cargas. Un archivo que llega cuando
su carga ya se borró se descarta con un log.

**Un POST y no un PUT** ([ADR-0001](../../docs/adr/0001-alcance-tp3-tp4.md)):
la política firmada nombra la única clave que el navegador puede escribir y
limita el archivo a 50 MB (`MAX_UPLOAD_BYTES`), y S3 la hace cumplir. Un PUT
prefirmado no puede limitar el tamaño, así que un archivo de varios GB
llegaría hasta un worker.

### El CORS del bucket de datos

El portal (su bucket) y el bucket de datos son dos orígenes: el POST es
*cross-origin*. Es un formulario sin headers propios, así que el navegador lo
manda sin preflight, pero solo le deja leer la respuesta al portal si el
bucket permite su origen. Sin eso el archivo sube igual y el portal ve un
*"Failed to fetch"*. La regla está en el módulo `data_lake`
(`aws_s3_bucket_cors_configuration`): `POST` desde el origen del portal
(`local.portal_origin`), nada más. La política firmada es la que autoriza
la escritura; el origen solo deja que el portal se entere de que salió bien.
`make verify` lo chequea con el header `Origin` del portal, porque `urllib`
no aplica CORS: sin el chequeo, solo un navegador lo descubre.

`app/batch/worker/upload.py` lee un CSV plano (`ean,price[,description,brand,presentation]`)
en vez de un zip del SEPA, pero devuelve el mismo `sepa.Contents`: todo lo
que viene después (`store_curated`, `load`, la detección de cambios, las
alertas) no sabe de dónde vino la fila.

## Alertas: un límite a tener en cuenta

La API solo lista las alertas y las marca como leídas. Las genera la Lambda
alert-matcher ([`app/batch/alert_matcher`](../batch/alert_matcher/README.md))
cuando le llega el aviso de una cadena con cambios de precio.

El worker descarta un cambio de precio por debajo de `CHANGE_FLOOR_PCT`
(0.5% por default) **antes** de que exista como fila en `price_change` — es
un control de volumen sobre 14 millones de filas por día, no una decisión
por regla. Una regla de alerta con `threshold_pct` menor a ese piso nunca va
a dispararse, porque el cambio ni siquiera llegó a calcularse. Si hace falta
un umbral más fino, `CHANGE_FLOOR_PCT` es la perilla, no la regla.

También: reprocesar un día fuera de orden dejaría desactualizados los
cambios ya calculados para el día siguiente (que se compara contra el
anterior). Con un backfill que va de más viejo a más nuevo — como el que se
usa acá — nunca pasa; si hace falta reprocesar fuera de orden, hoy no hay un
endpoint que lo arregle solo.

## Configuración

Además de las [variables comunes](../../docs/desarrollo.md#configuración):

| Variable | Default | Para qué |
|---|---|---|
| `DB_PORT` | se pregunta a la API de RDS | En AWS Terraform lo fija en `5432`: la VPC no tiene endpoint de la API de RDS, así que la consulta no saldría |
| `DB_HOST_RO` | vacío | Endpoint de la réplica de lectura. Vacío manda las lecturas al primario. En AWS Terraform no la setea: todo va por el Proxy, y la réplica la usa el precálculo. Comparte `DB_PORT`, `DB_NAME`, `DB_USER` y la contraseña con el primario |
| `AWS_PUBLIC_ENDPOINT_URL` | vacío | Endpoint de S3 para firmar URLs que un navegador tiene que poder resolver. Vacío en AWS |
| `INVITE_FUNCTION_NAME` | vacío | La Lambda de la invitación (`lgdc-invite`). Vacía, `POST /org/members` responde 503 |
| `COMPARISON_TABLE` | — | La tabla DynamoDB del precálculo (`lgdc-comparison`), de donde leen el tablero y Mis Precios |
| `PRECOMPUTE_QUEUE_URL` | vacío | La SQS `lgdc-precompute`, donde la API encola el precálculo de una sucursal que no lo tiene |
| `MAX_RADIUS_KM` | `10.0` | Tope duro de radio, el mismo que `org_branch.radius_km` acepta y que `branch_neighbor` precalcula |
| `CHANGE_FLOOR_PCT` | `0.5` | Es de `app/batch/worker/`, no de acá, pero condiciona qué alertas puede haber (ver arriba) |

## Los chequeos

No tiene `verify.py` propio: la API se prueba por su puerta pública, con
tokens de Cognito, en `make verify` ([`tools/verify`](../../tools/verify/README.md)),
contra el stack desplegado. La cubren las suites `portal` (el portal, la Hosted UI, el
callback, el JWT y el onboarding), `competitors` (las lecturas de cada
pantalla, el aislamiento entre organizaciones y el radio del conjunto
competitivo), `precompute` (el tablero y Mis Precios desde el precálculo, el
cursor, el filtro y la consulta en vivo de respaldo) y `roles` (la
invitación, las acciones del dueño, `/admin/*` y el admin de plataforma de
Deploy).

## Lo que falta

- **Un miembro no se puede sacar** de la organización ni cambiar de rol: no
  hay `DELETE` ni `PATCH` en `/org/members`, y la organización tiene un solo
  `owner`.
- **El admin de plataforma pasa por el onboarding** si entra al portal: el
  portal pide una organización para todo. Para `/admin/*` no la necesita.
- **El portal no tiene botones para `/admin/*`**: el admin de plataforma los
  llama con su token (en el ciclo normal no hace falta: el cierre del día los
  corre una vez por día, ver [`app/shared/maintenance.py`](../shared/maintenance.py)).
- **Paginación por offset, no por cursor**, salvo Mis Precios. Alcanza para
  el volumen de hoy; no escala a listados enormes.
- **Otro radio sigue siendo la consulta en vivo**, a propósito: el radio es
  libre y solo el configurado se precalcula.
