# tools/verify · `make verify`

Los chequeos del TP3 contra el stack desplegado: la única costura de
verificación. Prueban
lo que se ve desde afuera, por las mismas puertas que usa un usuario o la
cátedra: la URL del portal, la Hosted UI de Cognito, la API pública con un
token de Cognito, el `raw/` del bucket de datos (donde la ingesta deja el
archivo de cada cadena), el POST prefirmado con el que el portal sube la
lista de un comercio, `make ingest`, lo que corre el workflow run-ingest, y
`make bootstrap-platform-admin`, el paso que corre Deploy. No prueban módulos de Terraform, handlers sueltos ni SQL por
separado. No corren en el CI ([ADR-0001](../../docs/adr/0001-alcance-tp3-tp4.md)).

Unas pocas llamadas van por la API de AWS, porque lo que chequean no tiene
otra puerta: la migradora se invoca como la invoca Terraform (`schema`), y
`pipeline` manda a la cola de jobs la misma notificación de S3 dos veces y
una por un archivo que no existe, lee la DLQ y cuenta las tareas de ECS; y
`ingest` lee la configuración de la función de ingesta y la regla de
EventBridge que la invoca; y `events` lee las suscripciones del topic
`lgdc-cadena-procesada` y el event source mapping del alert-matcher, y
suscribe al topic sus propias colas de prueba; y `close` lee el event source
mapping y la regla de EventBridge del cierre del día, invoca la función como
la invoca su cron, y lee lo que decidió de su log (una línea por aviso: la
cola de precálculo es del precálculo, y sus mensajes no se leen sin
tomarlos); y `precompute` lee el event source mapping del precálculo y la
configuración de la tabla `lgdc-comparison`, manda a la cola de precálculo
dos entradas como las del cierre del día, y lee del log lo que hizo con
cada una. Los
usuarios de prueba entran con `ADMIN_USER_PASSWORD_AUTH`, salvo los logins
que prueban la Hosted UI, que usan sus formularios como un navegador.

```bash
make init                         # una vez por checkout: terraform output lee el state remoto
make verify                       # todas las suites, en orden
make verify SUITES="portal roles" # solo esas
```

Necesita las mismas credenciales de AWS que `make plan`, en el entorno, y
Docker andando: la suite `ingest` abre un Quick Tunnel de Cloudflare con la
imagen `cloudflare/cloudflared`. Lee
las URLs, el user pool y el bucket de `terraform output`. Corre en su propio
virtualenv (`build/verify-venv`, con [`requirements.txt`](requirements.txt)),
que `make verify` arma la primera vez. Imprime cada chequeo con ✓ o ✗ y al
final el resumen; termina con código distinto de cero si alguno falló. Una
suite que se cae a la mitad cuenta como un chequeo fallido, y las demás corren
igual.

Entero tarda unos 20 minutos, casi todo esperando a los workers: arrancan
desde cero (la alarma, la tarea, la imagen) y al final se espera que vuelvan
a cero.

## Qué crea y qué limpia

- **Usuarios de Cognito**: los crea con la API de administración, como al
  admin de plataforma de Deploy, con emails `verify-…@lgdc.test`, y entran sin
  navegador (`ADMIN_USER_PASSWORD_AUTH`, que pide credenciales de AWS). Los
  miembros invitados tienen direcciones del simulador de SES
  (`success+verify-…@simulator.amazonses.com`): la invitación se manda de
  verdad y el simulador la descarta. Se borran todos al terminar.
- **Un túnel**: `ingest` levanta un proxy de prueba en un puerto local y un
  contenedor de `cloudflared` que lo expone; los dos se cierran al terminar
  la suite.
- **Cadenas sintéticas**: ids desde `899000001`, que ninguna cadena real usa,
  con la fecha de hoy (las de `events`, también con la de ayer; las de
  `close`, con cuatro días propios de cada corrida). Sus archivos en `raw/`, `curated/` y `rejected/` se
  borran al terminar, con todas sus versiones (el bucket es versionado).
- **La regla de alertas** que crea `competitors` la borra por la API. La de
  `events` queda, con su organización.
- **Las colas de prueba de `events`**: tres SQS `lgdc-verify-<suscripción>-…`,
  cada una suscripta al topic con la filter policy de una suscripción real.
  Se desuscriben y se borran al terminar.
- **Las cargas de `uploads`**: la buena la borra por la API (con sus
  precios), y los archivos de las dos (`raw/uploads/<upload_id>/`,
  `rejected/uploads/<upload_id>/` y el Parquet del día en `curated/`) se
  borran al terminar, con todas sus versiones.
- **Lo que queda en Postgres**: las filas de esos usuarios y organizaciones
  («LGDC verify…», con lo que siguen), las sucursales y precios de las cadenas
  sintéticas y sus jobs. Desde fuera de la VPC no hay cómo borrarlas, y la
  API no tiene cómo borrar una organización ni una cadena. Las sucursales
  sintéticas quedan en el catálogo, visibles en la búsqueda de sucursales del
  portal. Correrlo otra vez el mismo día reemplaza los mismos precios. De
  `uploads` quedan, por corrida, una sucursal propia en (-50, -35) y la
  carga rota con su job. De `close`, por corrida, los precios y jobs de sus
  cuatro días, esos días cerrados en `feed_day`, una sucursal propia en
  (-55, -40) con su carga, y sus entradas en la cola de precálculo. De
  `precompute`, por corrida, una sucursal propia en (-60, -30) con su carga,
  los precios y el job de su cadena vecina en uno de sus días, ese día
  cerrado, y en DynamoDB los precálculos de su sucursal (los borra el TTL a
  la semana) y de cada sucursal seguida en el día que cerró.

El último grupo, Limpieza, chequea que no quedó ningún usuario de prueba en
Cognito, ningún archivo de las cadenas sintéticas en el bucket ni ninguna
cola de prueba.

## Las suites

| Suite | Módulo | Qué establece |
|---|---|---|
| `schema` | [`schema.py`](schema.py) | El apply dejó las tablas del TP3, migrar otra vez deja exactamente las mismas, y `/api/ready` llega a la base por el Proxy |
| `portal` | [`portal.py`](portal.py) | El portal se sirve por HTTPS desde su bucket, sin versionado, con el `config.json` que generó Terraform y sin el client secret. La Hosted UI abre el login y el registro, y cualquiera puede registrarse. El callback, sin código o con uno inventado, vuelve al login sin token; un login real por el formulario de la Hosted UI vuelve al portal con el ID token y el `state` en el fragmento, y la API lo acepta. Toda ruta salvo `/api/health` pide un JWT válido (también un token falsificado es 401), y el login propio ya no existe. El primer login crea al usuario sin organización, sin ella los datos son 403, y nombrarla lo deja como `owner` |
| `pipeline` | [`pipeline.py`](pipeline.py) | Un ZIP del SEPA en `raw/` termina en `done`, en los precios que sirve la API y en Parquet en `curated/`, con la fila rechazada en `rejected/`; el mismo mensaje dos veces no carga nada dos veces; un archivo roto va a `rejected/` sin reintento; un error transitorio llega a la DLQ al tercer intento; y los workers escalan desde 0 y vuelven a 0 (este último, al final de todo). Si una carga anterior a la corrida, como un CSV subido desde el portal, dejó workers arriba, primero espera a que vuelvan a 0 |
| `ingest` | [`ingest.py`](ingest.py) | La ingesta del SEPA por la puerta de run-ingest, `make ingest`. La función corre fuera de la VPC y la invoca una regla de EventBridge con un cron, habilitada. **Sin `SEPA_API_URL`** (si el stack se desplegó sin ella; si no, se saltea) `make ingest` falla diciendo que falta y nombrando al proxy, y con un proxy que no responde (`https://lgdc-verify.invalid`) falla nombrando al proxy y su URL. **Con el proxy arriba**: un proxy de prueba, con el contrato del de `tools/sepa-proxy` (`/health` y `/v1/sepa/precios`) y un archivo diario sintético de dos cadenas, detrás de un Quick Tunnel; `make ingest` con su URL termina bien, dice que guardó las dos cadenas y bajó el archivo una vez por el túnel, y el job de cada cadena termina en `done` con sus 20 precios. **Dos veces el mismo día**: la segunda corrida termina bien, no guarda nada (saltea las dos), cada archivo tiene una sola versión en `raw/` y ningún job vuelve a correr |
| `uploads` | [`uploads.py`](uploads.py) | La carga propia como la hace el portal: `POST /uploads` firma un POST para la única clave `raw/uploads/<upload_id>/verify.csv`, con el archivo limitado a 50 MB, y la carga espera su archivo; el POST directo a S3 desde el origen del portal sale bien y el CORS le deja leer la respuesta (no a otro origen). **Sin ningún otro llamado a la API** (el viejo `/complete` ya no existe), la notificación de S3 genera el job y la carga termina en `done`: 49 precios y 1 fila rechazada, que `GET /uploads/{id}/rejected` devuelve con su línea, su motivo y su contenido. La API sirve esos precios en la sucursal propia, el tablero los cuenta como su surtido, y `GET /ingest/jobs` (la pantalla de Pipeline) lista el job en su día, como `upload`, con el nombre del comercio. Un archivo sin las columnas `ean` y `price` termina en `failed` con el motivo y un solo intento. Otra organización no ve la carga ni sus rechazos ni la puede borrar (404), y borrarla se lleva sus precios |
| `competitors` | [`competitors.py`](competitors.py) | Sobre tres sucursales que carga por el pipeline (ver abajo): las lecturas de cada pantalla responden 200; **aislamiento**: otra organización no sigue la sucursal de la primera, no ve su tablero, su comparación, sus competidores ni sus precios (404), ni sus reglas, y no puede borrarlas; **radio**: con 10 km el conjunto competitivo tiene los dos competidores, con 1 km solo el de 500 m, el más cercano primero y cada uno a su distancia; la comparación de cada producto es contra una oferta a 1 km y contra dos a 10 km; y guardar 1 km como radio de la sucursal deja un competidor. Y lo demás que chequeaba `backend/verify.py` sobre el conjunto competitivo: los competidores se paginan en orden y con el total aparte, y `q` filtra; la comparación trae el precio propio, el rango y las ofertas; el tablero dice lo mismo que `/comparison/summary` y `/comparison`, y con un radio que no alcanza a nadie compara cero productos pero cuenta los propios; reemplazar dos veces los productos de una regla no los duplica |
| `events` | [`events.py`](events.py) | **El aviso de cadena procesada.** El topic tiene la suscripción de la SQS de alertas (solo `done` con `price_changes > 0`), la de la SQS del cierre del día (todos) y la del email del admin (solo `failed`, a `BOOTSTRAP_EMAIL` si está en el entorno), las dos de SQS con *raw delivery*, y el alert-matcher consume la SQS de alertas. Con dos sucursales a 500 m cargadas ayer y una regla del dueño de una sobre uno de los tres productos: **hoy la otra sube 20 % dos productos**, y el portal muestra una sola alerta, la del producto de la regla, de 100 a 120 (+20 %), sin leer. **A quién le llega cada aviso**, por las colas de prueba: el de esa cadena (`done`, 2 cambios) a alertas y al cierre, no al email; el de una cadena sin cambios (`done`, 0) solo al cierre; el de un archivo roto (`failed`) al email y al cierre, no a alertas. **Una sola vez**: el mismo mensaje del job otra vez no genera otro aviso |
| `close` | [`close.py`](close.py) | **El cierre del día**, en cuatro días propios de cada corrida (ver abajo). La función consume la SQS de cierre (a lo sumo 2 a la vez, con `ReportBatchItemFailures`) y la invoca un cron a las 10:00 de Buenos Aires; la SQS de precálculo existe, con su DLQ. **La última cadena**: tres cadenas de un día (dos sucursales a 500 m y un archivo roto) terminan, llegan sus tres avisos y exactamente uno cierra el día, viendo las tres cadenas en `raw/` y las tres terminadas; y el portal muestra a la vecina a 500 m en el conjunto competitivo de la otra (el cierre reconstruyó los vecinos). **El precálculo**: con una organización que sigue una sucursal a 1 km y otra que sigue las dos a 5 km, el cierre del día siguiente encola las tres combinaciones, cada sucursal y radio una sola vez. **Una carga** de un comercio encola solo su sucursal, a su radio, y no cierra el día: el cron no encuentra nada que cerrar ese día. **El cron**, con las dos cadenas recién subidas y todavía cargando, cierra el día con lo que hay y encola el precálculo; otra vez, lo encuentra cerrado y no hace nada; y cuando las cadenas terminan, sus avisos reabren el día y cada reapertura lo vuelve a cerrar, la última con las dos |
| `precompute` | [`precompute.py`](precompute.py) | **El precálculo del tablero**, en días propios de cada corrida (ver abajo). La función consume la SQS de precálculo (a lo sumo 2 a la vez, con `ReportBatchItemFailures`) y la tabla `lgdc-comparison` es on-demand con TTL. **El respaldo en vivo**: el tablero de la sucursal recién creada de un comercio responde con la consulta en vivo (`source: live`), y el precálculo que eso encoló llega: el tablero pasa a responder desde él. Al final, con la sucursal pasada a 3 km (un radio nunca precalculado), Mis Precios también responde en vivo, las 25 filas en orden, y después desde el precálculo que encoló. **La carga**: un CSV del comercio y una cadena a 500 m, con 25 productos, terminan en el tablero precalculado con el resumen y los peores 20 en orden, el mismo resumen que da la consulta en vivo. **Mis Precios por cursor**: 25 filas en páginas de 10, 10 y 5, la peor brecha primero y sin repetir, la última sin cursor; filtrado por descripción, sus 10 coincidencias en páginas de 4, 4 y 2; un cursor inventado es 400. **Otro radio**: a 1 km responde en vivo, con el mismo día, resumen y peores 20, y pagina en vivo con cursor igual. **`META` solo avanza**: una entrada del día siguiente mueve el tablero a ese día, y una del día anterior queda como vieja (`stale`) sin moverlo |
| `roles` | [`roles.py`](roles.py) | El dueño invita a un miembro (201, rol `member`), que queda en Cognito esperando la contraseña temporal y con el email verificado; el mismo email otra vez o desde otra organización es 409, y uno inválido 422. El primer login del miembro por la Hosted UI le pide una contraseña nueva y vuelve al portal con su token, en la organización del dueño y viendo sus datos. **Las acciones del dueño** (`GET /ingest/jobs`, `GET` y `POST /org/members`) le responden 403 al miembro, y 200 al dueño. Invitar a un usuario de Cognito que nunca entró, o a uno sin organización, lo suma sin email. **`/admin/*`** es 403 para un dueño, un miembro y un usuario sin organización fuera de `platform-admin`, y 200 para alguien del grupo. `make bootstrap-platform-admin` sin email no hace nada; con él da de alta al admin de plataforma, otra vez también anda, y ese admin entra con su contraseña y usa `/admin/*` |

### Las sucursales de `competitors`

Tres cadenas sintéticas, `899000011` (la propia), `899000012` (a 500 m) y
`899000013` (a 5 km), una sucursal cada una, que venden los mismos cinco
productos a 100, 90 y 80. Entran por el pipeline como cualquier cadena, y el
cierre del día reconstruye `branch_neighbor` cuando terminan (si el día de
hoy ya estaba cerrado, sus avisos lo reabren). Están en el
Atlántico Sur (-45, -40), a más de 1.000 km de cualquier sucursal real: solo
son vecinas entre ellas y nunca entran en una comparación real. Así los
chequeos de radio y de aislamiento corren igual en una cuenta sin datos del
SEPA, como la del ensayo en cuenta limpia.

Las cadenas de `pipeline` (`899000001` a `899000003`) y las de `ingest`
(`899000021` y `899000022`) no tienen coordenadas, y la sucursal propia de
`uploads` está en (-50, -35), a más de 500 km de las de `competitors`: nunca
entra en sus comparaciones.

### Los días y las sucursales de `close`

El cierre pasa una vez por día, así que `close` no puede usar la fecha de
hoy, que ya cerraron las otras suites o la ingesta real. Cada corrida elige
cuatro días seguidos sin ningún job, entre 100 y 160 días atrás (dentro de
los seis meses de particiones de `price`): el de la última cadena, el del
precálculo, el de la carga y el del cron. Sus cadenas son `899000041` (la
propia), `899000042` (a 500 m) y `899000043` (el archivo roto), en
(-55, -45), y la sucursal propia de la carga está en (-55, -40), a 300 km:
lejos de las de las demás suites y entre ellas.

### Los días y la sucursal de `precompute`

Cada corrida crea un comercio nuevo con su sucursal propia en (-60, -30), que
nunca tuvo precálculo, y elige dos días seguidos sin ningún job, entre 100 y
160 días atrás, como `close`: el de los datos y el siguiente. La cadena
vecina, `899000051`, está a 500 m al norte; su aviso cierra el día de los
datos, que reconstruye los vecinos con la sucursal nueva adentro y encola
todas las sucursales seguidas. Las lecturas que esperan el precálculo piden
ese día (`feed_date`): sin él, la consulta en vivo respondería, y encolaría,
el último día de cualquier cadena.

### Las sucursales de `events`

Dos cadenas sintéticas, `899000031` (la propia) y `899000032` (a 500 m), en
(-40, -45), a más de 500 km de las demás, con tres productos a 100. Se
cargan **ayer y hoy**: un cambio de precio es contra el día anterior de la
cadena. Ayer las dos a 100; hoy la vecina sube los dos primeros a 120, y la
propia repite sus precios (el aviso sin cambios). `899000033` es el archivo
roto, sin sucursal. Correrlo otra vez el mismo día reemplaza los mismos
precios, y el worker borra y el alert-matcher vuelve a generar las alertas
de ese día.

Las colas de prueba existen porque las de verdad tienen dueño: la de
alertas la consume el alert-matcher, la del cierre el cierre del día, y el
email no se puede leer. Una cola de prueba
con la misma filter policy recibe lo mismo que la suscripción real, porque
el filtro lo aplica SNS. SNS puede tardar unos minutos en aplicar el filtro
de una suscripción nueva; las colas se suscriben al principio, y los
chequeos de lo que *no* tiene que llegar corren después de varios minutos de
cargas.

## Cómo está armado

[`main.py`](main.py) corre las suites y la limpieza. Lo que comparten:
[`harness.py`](harness.py) (el reporte, el stack desde `terraform output`, la
API por HTTP, la espera con los workers y la limpieza),
[`cognito.py`](cognito.py) (los usuarios de prueba y la Hosted UI como la usa
un navegador) y [`archives.py`](archives.py) (los ZIP del SEPA sintéticos y
los jobs como los lee la pantalla de Pipeline, de hoy o de otro día). Una
suite nueva es un módulo con `run(checks, stack)`, en `SUITES` de `main.py`;
lo que crea fuera de Postgres lo anota en `stack.users`, `stack.prefixes`,
`stack.subscriptions` o `stack.queues` para que se limpie.

La ingesta real, con el proxy SEPA y el SEPA de verdad, no es parte de
`make verify`: el proxy de prueba prueba el lado de la función, por un túnel
de verdad, sin depender del SEPA ni cargar 330 MB por corrida.
