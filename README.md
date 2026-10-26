# LGDC · plataforma de precios

Ingiere a diario el dataset público del SEPA, arma el conjunto competitivo de
cada sucursal por radio geográfico, y expone todo eso — comparación de
precios, historial, alertas, carga propia — por una API y un portal. Todo se
despliega en AWS con Terraform, desde GitHub Actions, en una cuenta del
Learner Lab.

| Documento | Qué tiene |
|---|---|
| [`docs/arquitectura.md`](docs/arquitectura.md) | El **diagrama de arquitectura** (lo que crea el Terraform), los flujos de usuario y de datos, y las decisiones de diseño |
| [`docs/terraform.md`](docs/terraform.md) | Las convenciones del Terraform |
| [`docs/desarrollo.md`](docs/desarrollo.md) | Correr todo a mano: herramientas, artefactos, `make plan`/`apply`, configuración y `make verify` |
| [ADR-0001](docs/adr/0001-alcance-tp3-tp4.md) y los demás de [`docs/adr/`](docs/adr/) | El alcance del TP3 y las decisiones tomadas en el camino |

---

## Integrantes

| Nombre | Legajo |
|---|---|
| Santiago Bassi | 64643 |
| Nicolás Priotto | 64663 |
| Juan Ignacio Lategana | 64476 |
| Filipo Ardenghi | 64306 |
| Ezequiel Testoni | 64709 |

---

## Prerrequisitos

- **Una sesión del Learner Lab**: sus credenciales van como secrets del repo.
  Desplegar, correr la ingesta y destruir se hace desde **GitHub Actions**:
  no hace falta instalar Terraform ni nada más.
- **El proxy SEPA y su túnel**, corriendo en una máquina propia, para la
  ingesta del SEPA (abajo).

### El proxy SEPA y su túnel

El SEPA bloquea las IPs de AWS, así que la ingesta no le pide el archivo del
día a él: se lo pide al proxy de [`tools/sepa-proxy`](tools/sepa-proxy/README.md),
que está en este repo y tiene que correr **fuera de AWS** (una laptop sirve;
una instancia de EC2 o Cloud9, no), expuesto a internet con un túnel de
Cloudflare. Solo lo usa la ingesta: Deploy, el portal y la carga propia
andan sin él.

Hace falta **Node.js 20** o más nuevo y
[`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
Desde la raíz del repo, en dos terminales:

```bash
cd tools/sepa-proxy && npm start                 # el proxy, en http://localhost:3000 (no tiene dependencias)
cloudflared tunnel --url http://127.0.0.1:3000   # el túnel: imprime una URL https://<id>.trycloudflare.com
```

Para comprobar que responde desde afuera:

```bash
curl --fail "https://<id>.trycloudflare.com/health"
```

Esa URL, **sin** `/v1/sepa/precios`, es el secret `SEPA_API_URL` (paso 1).

- Los dos procesos tienen que seguir corriendo mientras dura la ingesta:
  bajar el archivo del día (unos 330 MB) tarda de 2 a 5 minutos.
- Un Quick Tunnel cambia de URL cada vez que se reinicia: hay que actualizar
  el secret antes de la próxima ingesta.
- Con Docker, en lugar de `npm start`:
  `docker build -t sepa-proxy tools/sepa-proxy && docker run --rm -p 3000:3000 sepa-proxy`.

---

## Pasos a seguir

### 1. Configurar los secrets

En el repositorio de GitHub, **Settings → Secrets and variables → Actions →
New repository secret**, crear:

| Secret | Obligatorio | Para qué |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | Sí | Access key de la sesión del Learner Lab (*AWS Details → AWS CLI*) |
| `AWS_SECRET_ACCESS_KEY` | Sí | Secret access key de la sesión |
| `AWS_SESSION_TOKEN` | Sí | Session token de la sesión |
| `BOOTSTRAP_EMAIL` | Sí | El email del admin de plataforma. Deploy lo da de alta en Cognito, en el grupo `platform-admin`, y lo suscribe por email a las cadenas que fallan al procesarse |
| `BOOTSTRAP_PASSWORD` | Sí | La contraseña de `BOOTSTRAP_EMAIL` en Cognito (ver los requisitos abajo). Deploy se la vuelve a poner en cada corrida |
| `SEPA_API_URL` | Para la ingesta | La URL del túnel al proxy SEPA ([Prerrequisitos](#el-proxy-sepa-y-su-túnel)). Sin ella todo se despliega igual y solo la ingesta falla, diciendo que falta el proxy |

Las credenciales del Learner Lab vencen con la sesión (unas horas): al
empezar una sesión nueva hay que volver a cargar los tres `AWS_*`.

**Requisitos de `BOOTSTRAP_PASSWORD`** (la política del user pool, en
[`infra/modules/auth/main.tf`](infra/modules/auth/main.tf)): al menos 8
caracteres, con al menos una mayúscula, una minúscula y un número; los
símbolos no son obligatorios. Por ejemplo: `LgdcCatedra2026`.

### 2. Los workflows

Están en la pestaña **Actions**. Los manuales piden escribir una palabra de
confirmación, para no correrlos por accidente.

| Workflow | Cuándo / confirmación | Qué hace |
|---|---|---|
| **Validate** | Solo, en cada push y pull request | `make lambdas`, `terraform fmt -check`, `init`, `validate` y tflint. Con los secrets de AWS, además `terraform plan` |
| **Plan** | Manual, escribir `plan` | `terraform plan` contra el state remoto; sube el plan legible como artefacto `plan` |
| **Deploy** | Manual, escribir `deploy` | Levanta todo, desde una cuenta vacía, en una corrida |
| **run-ingest** | Manual, escribir `ingest` | Corre la ingesta del SEPA ya, sin esperar al cron |
| **Destroy** | Manual, escribir `destroy` | Borra todo lo que creó Deploy |

El detalle de cada uno está en [El pipeline de GitHub Actions](#el-pipeline-de-github-actions).

### 3. Desplegar

1. Opcional: **Actions → Plan → Run workflow**, escribir `plan`. Muestra qué
   se va a crear sin aplicar nada (Deploy hace su propio plan).
2. **Actions → Deploy → Run workflow**, en la rama `main`, escribir `deploy`.
3. Esperar el run en verde. La primera vez tarda unos 20 minutos: lo más
   lento es crear la base, su réplica y el RDS Proxy.
4. Abrir el **resumen del run** (*Summary*): la sección *LGDC desplegado*
   tiene la URL del portal, la de la API, la del health check y el bucket de
   datos.

Deploy hace, en orden: `make init` (crea el bucket del state
`lgdc-tfstate-<account_id>` si no existe y corre `terraform init`); sube la
imagen de los workers a ECR si su código cambió; `terraform plan`; crea los
buckets de datos y del portal si faltan y `terraform apply` (que aplica el
esquema de la base, ver [`app/db_migrator`](app/db_migrator/README.md));
arma el portal y lo sube a su bucket; da de alta al admin de plataforma; y
termina con un `curl` a `/api/health`, que reintenta mientras la API arranca
en frío. Correrlo otra vez sobre un despliegue vivo aplica solo lo que
cambió: sirve, por ejemplo, después de renovar las credenciales del lab.

> **Confirmar la suscripción de SNS.** Al desplegar, SNS le manda a
> `BOOTSTRAP_EMAIL` un email (*AWS Notification - Subscription
> Confirmation*). La suscripción queda **pendiente hasta que se hace clic en
> *Confirm subscription***: hasta entonces el admin de plataforma no recibe ningún aviso de
> cadena fallida. Nada más depende de esa confirmación.

### 4. Primer acceso al portal

1. Abrir la URL **Portal** del resumen del run
   (`https://lgdc-portal-<account_id>.s3.us-east-1.amazonaws.com/index.html`).
   Se ve *Iniciar sesión*.
2. **Ingresar** lleva a la Hosted UI de Cognito. Entrar con `BOOTSTRAP_EMAIL`
   y `BOOTSTRAP_PASSWORD`.
3. La primera vez el portal pide el nombre de la organización (*Creá tu
   organización*). Quien la crea queda como su `owner`, y entra al tablero.

Otros usuarios:

- **Cualquiera puede registrarse** con *Crear una cuenta*: Cognito le manda
  un código por email para confirmar la dirección, y en el primer login crea
  su propia organización.
- **El `owner` invita miembros** desde *Tu cuenta → Miembros → Invitar por
  email*. Cognito le manda al invitado una contraseña temporal, y la Hosted
  UI le pide una nueva en el primer login. Entra como `member` de esa
  organización, sin pasar por el onboarding.
- **El admin de plataforma** (`BOOTSTRAP_EMAIL`) es además el único que puede
  usar `POST /api/admin/*` (reconstruir los vecinos y el catálogo a mano). El
  portal no tiene botones para eso: se llama con su token
  ([`app/api`](app/api/README.md#los-roles)).

Un tablero vacío es lo esperable en una cuenta nueva: la base arranca sin
precios. Los datos llegan con el paso siguiente.

### 5. Probar el flujo

**Con la ingesta del SEPA** (el proxy y su túnel andando, y `SEPA_API_URL`
cargado):

1. **Actions → run-ingest → Run workflow**, escribir `ingest`. El resumen del
   run dice cuántas cadenas dejó en `raw/`.
2. En el portal, **Mis sucursales**: buscar una sucursal del SEPA y seguirla
   con un radio. Es lo que define contra quién se comparan los precios.
3. *Pipeline* (en *Administración*, solo para el `owner`) muestra el job de
   cada cadena en `processing` mientras un worker la carga, y después en
   `done` (o `failed`). Los workers arrancan desde 0, así que el primero
   tarda unos minutos en aparecer.
4. Cuando termina la última cadena del día, el cierre del día reconstruye
   los vecinos y el precálculo llena el **Tablero** y **Mis Precios** de
   cada sucursal seguida. **Comparar** y la ficha de cada producto (con su
   historial) leen Postgres en vivo.
5. **Alertas**: una regla de vigilancia creada ahí genera alertas cuando una
   cadena vecina cambia un precio entre dos días cargados. Con un solo día
   cargado todavía no hay contra qué comparar.

**La carga propia**: *Cargar precios* pide primero la sucursal propia
(nombre y un punto en el mapa) y después acepta un CSV
(`ean,price[,description,brand,presentation]`) con su fecha. En
[`demo/`](demo/README.md) hay dos listas de precios de prueba y lo que
muestra cada una. La carga no pasa por el proxy, pero para que el tablero
compare contra algo hacen falta los precios de las cadenas vecinas, que
llegan con la ingesta.

**Todo junto, con `make verify`**: los chequeos del TP3 contra el stack
desplegado, desde una máquina propia ([`docs/desarrollo.md`](docs/desarrollo.md#verificación)).

### 6. Destruir al terminar

**Actions → Destroy → Run workflow**, escribir `destroy`.

Corre `terraform destroy` y después vacía (todas las versiones) y borra los
buckets de datos y del portal. Tarda unos 30 minutos, y unos 20 de ellos son
las dos subredes de la banda `app` y su security group: AWS tarda ese tiempo
en soltar las interfaces de red de las Lambdas dentro de la VPC, aunque las
funciones ya estén borradas. Terraform las espera; mientras tanto el log
repite *Still destroying…*, y es lo esperable. El bucket del state,
`lgdc-tfstate-<account_id>`, **no se borra**: queda vacío de recursos para el
próximo Deploy. Es lo único que habría que borrar a mano para dejar la cuenta
sin rastros de LGDC. Si la suscripción por email de `BOOTSTRAP_EMAIL` nunca
se confirmó, sigue listada como *PendingConfirmation* aunque el topic ya no
exista: SNS no deja borrarla, y la borra solo a los tres días.

---

## La ingesta y `SEPA_API_URL`

- **Deploy no depende del proxy.** Pasa el secret a la Lambda
  (`TF_VAR_sepa_api_url`, una variable `sensitive`: el plan no la muestra) y
  termina igual si está vacío o el proxy no responde.
- **El cron diario** (01:00 de Buenos Aires) usa la URL que tenía el secret
  en el último Deploy. **run-ingest** usa la del secret en cada corrida:
  cargar o cambiar la URL alcanza para correr la ingesta, sin volver a
  desplegar.
- **Si el proxy no responde, o falta la URL,** la ingesta falla con un
  mensaje que nombra al proxy, y run-ingest lo muestra en rojo en su
  resumen (`SEPA_API_URL is not set…`, o `the SEPA proxy at <url> did not
  answer…`). No se reintenta, y nada más se entera: la plataforma sigue
  andando con los datos que ya tiene.

Los detalles, en [`app/batch/ingest`](app/batch/ingest/README.md).

---

## Módulos

Todo el Terraform vive en [`infra/`](infra/): una composición
([`main.tf`](infra/main.tf)) que llama a once módulos propios de
[`infra/modules/`](infra/modules/) y a un módulo externo. Los módulos no se
leen entre sí: lo que uno necesita de otro se lo pasa `main.tf` por variable,
y las reglas de security group que cruzan módulos están en
[`infra/security_groups.tf`](infra/security_groups.tf). El
[diagrama](docs/arquitectura.md#diagrama) muestra lo que crean.

### Módulo externo

[`terraform-aws-modules/vpc/aws`](https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws),
versión `~> 6.7` (hoy baja la 6.7.3), llamado desde
[`infra/modules/network/main.tf`](infra/modules/network/main.tf). Crea la
VPC, las subredes de las tres bandas, sus route tables y el DB subnet group.

### `network`

[`infra/modules/network`](infra/modules/network/) · la red, sin salida a
internet.

- La VPC con el módulo externo, **sin Internet Gateway ni NAT**, con DNS
  privado. Tres bandas privadas, una subred `/20` por AZ en cada una: `app`
  (las Lambdas dentro de la VPC y los interface endpoints), `batch` (los
  workers) y `data` (RDS y el Proxy), en 2 AZ.
- `aws_vpc_endpoint.gateway`: gateway endpoints de S3 y DynamoDB, en las
  route tables de las tres bandas.
- `aws_vpc_endpoint.interface`: interface endpoints de ECR (`ecr.api` y
  `ecr.dkr`), Lambda (para que la API invoque la Lambda de la invitación),
  CloudWatch Logs, Secrets Manager, SNS y SQS, en la banda `app`.
- Los security groups de los endpoints (HTTPS desde la VPC) y de las Lambdas
  dentro de la VPC (salida solo a los endpoints).

### `database`

[`infra/modules/database`](infra/modules/database/) · Postgres, la fuente de
verdad.

- `aws_db_instance.this`: RDS PostgreSQL 17, `db.t3.micro`, gp3 cifrado, 20 GB
  con autoscaling de storage hasta 100 GB, en la banda de datos, sin acceso
  público. Guarda 7 días de backups automáticos (la réplica los necesita en
  su origen).
- `aws_db_instance.replica`: la réplica de lectura, con `count` sobre
  `read_replica_enabled`. La lee el precálculo.
- `aws_db_parameter_group.this`: precarga `pg_cron` (rota las particiones de
  `price`) y sube `work_mem` para la comparación.
- `aws_db_proxy.this`, con su target group y su target: el RDS Proxy, que
  agrupa las conexiones de las Lambdas. Su log group
  (`/aws/rds/proxy/lgdc-db`, con retención) se crea antes que él: si no, lo
  crea RDS sin retención, y Destroy no lo borra.
- La credencial maestra: `random_password` y el secreto en Secrets Manager
  (`{username, password}`), que leen el Proxy y el código.
- Los security groups de la instancia y del Proxy.

### `data_lake`

[`infra/modules/data_lake`](infra/modules/data_lake/) · todo lo que Terraform
gestiona del bucket de datos `lgdc-data-<account_id>`.

- **Versionado** (`aws_s3_bucket_versioning`).
- Una regla de lifecycle por prefijo, con un `dynamic "rule"`: `raw/` vence a
  los 90 días, `curated/` pasa a `STANDARD_IA` a los 30, `rejected/` vence a
  los 30; las versiones reemplazadas, a los 7.
- Public access block, una política que niega todo acceso sin TLS y el CORS
  que deja al portal subir la carga propia con un POST prefirmado (solo
  `POST`, solo desde el origen del portal).

El bucket en sí lo crea y lo borra [`scripts/buckets.sh`](scripts/buckets.sh),
llamado por `make apply` y `make destroy`: el Learner Lab niega una lectura
que el provider hace sobre todo `aws_s3_bucket`
([ADR-0002](docs/adr/0002-bucket-de-datos-fuera-de-terraform.md)). La
notificación del bucket a la SQS de jobs está en la composición
([`infra/notifications.tf`](infra/notifications.tf)), porque une este módulo
con el de la cola.

### `queue`

[`infra/modules/queue`](infra/modules/queue/) · una SQS con su DLQ.

- La cola y su DLQ, cifradas, con *long polling*. Un mensaje recibido 3
  veces sin borrarse (`max_receive_count`, validado) pasa a la DLQ.
- La redrive allow policy (solo esta cola puede mover mensajes a su DLQ).
- La política de la cola: niega todo sin TLS y deja enviar a cada servicio de
  `allowed_senders` solo en nombre de su ARN de origen.

Se usa **cuatro veces**: `lgdc-jobs` (le escribe S3), `lgdc-alerts` y
`lgdc-day-close` (les escribe el topic) y `lgdc-precompute` (le escriben el
cierre del día y la API).

### `topic`

[`infra/modules/topic`](infra/modules/topic/) · un topic SNS con sus
suscripciones.

- El topic y su política (niega publicar sin TLS).
- `aws_sns_topic_subscription.this`, con `for_each` sobre un map: cada
  suscripción con su protocolo (`sqs` o `email`, validado), su endpoint y su
  filter policy. Las de SQS van con *raw message delivery*: la cola recibe el
  aviso tal cual, con sus atributos.

Se usa para `lgdc-cadena-procesada`, con tres suscripciones: la SQS de
alertas (solo `done` con `price_changes > 0`), la del cierre del día (todos
los avisos) y el email del admin de plataforma (solo `failed`). Ver
[los flujos de datos](docs/arquitectura.md#datos).

### `lambda_function`

[`infra/modules/lambda_function`](infra/modules/lambda_function/) · una
Lambda y lo que la invoca.

- La función (Python 3.12, desde un zip de `build/`), con sus variables de
  entorno, su `/tmp` y, si recibe `vpc_config`, dentro de la VPC.
- Su log group, con retención, creado acá para que Destroy lo borre.
- Cuántas veces se reintenta una invocación asíncrona que falló
  (`maximum_retry_attempts`, validado entre 0 y 2).
- Con `schedule_expression`: el cron (una regla de EventBridge, su target y
  el permiso para invocarla).
- Con `sqs_trigger`: el event source mapping de la cola, con lotes de hasta
  `batch_size` mensajes, su concurrencia máxima y `ReportBatchItemFailures`,
  para que vuelvan a la cola solo los mensajes que fallaron.

Se usa **ocho veces**:

| Lambda | Dónde corre | Qué la invoca |
|---|---|---|
| `lgdc-api` | VPC | API Gateway |
| `lgdc-auth-callback` | Fuera de la VPC | API Gateway (`GET /auth/callback`) |
| `lgdc-invite` | Fuera de la VPC ([ADR-0003](docs/adr/0003-invitacion-por-lambda-fuera-de-la-vpc.md)) | La API |
| `lgdc-db-migrator` | VPC | El apply ([`infra/migration.tf`](infra/migration.tf)) |
| `lgdc-ingest` | Fuera de la VPC | Su cron (01:00) y run-ingest |
| `lgdc-alert-matcher` | VPC | La SQS de alertas |
| `lgdc-day-close` | VPC | La SQS del cierre y su cron de respaldo (10:00) |
| `lgdc-precompute` | VPC | La SQS del precálculo, de a una entrada y a lo sumo 2 a la vez |

### `batch`

[`infra/modules/batch`](infra/modules/batch/) · los workers.

- El repositorio ECR `lgdc-worker` (tags inmutables, escaneo al subir) y su
  lifecycle policy (guarda las últimas 5 imágenes).
- El cluster ECS, el log group, la task definition de Fargate (1 vCPU, 4 GB)
  y el servicio en la banda batch, sin IP pública.
- El autoscaling: arranca en 0 tareas. Una alarma de mensajes visibles en la
  cola suma tareas hasta `worker_max_capacity`; otra, de 5 minutos sin
  mensajes visibles ni en vuelo, lo vuelve a 0.
- Su security group, sin entrada; la salida (a los endpoints y a la base) la
  agrega la composición.

### `api`

[`infra/modules/api`](infra/modules/api/) · la HTTP API de API Gateway.

- La API, con CORS (pone los headers en toda respuesta, también en los 401 del
  authorizer), y un authorizer JWT que valida el token de Cognito.
- La ruta `$default` va a la Lambda de la API y exige el token. Las de
  `public_route_keys` (`GET /api/health`, `GET /api/ready` y los preflights
  `OPTIONS /{proxy+}`) van a la misma Lambda sin token, y `GET /auth/callback`
  a la Lambda del callback, también sin token.
- El stage y los permisos para que API Gateway invoque las dos Lambdas.

### `auth`

[`infra/modules/auth`](infra/modules/auth/) · Cognito.

- El user pool, con registro abierto: el email es el usuario y se confirma
  con un código. Con el email de invitación (la URL del portal y la
  contraseña temporal).
- El app client, con secret (flujo `code`, tokens de 8 horas). El secret solo
  lo recibe la Lambda del callback.
- El dominio de la Hosted UI (`lgdc-<account_id>`) y el grupo
  `platform-admin`.

### `serving`

[`infra/modules/serving`](infra/modules/serving/) · la tabla DynamoDB
`lgdc-comparison`.

On-demand (`PAY_PER_REQUEST`), con clave `PK`/`SK` y TTL sobre `expires_at`:
la comparación precalculada de cada sucursal seguida y radio, que escribe la
Lambda del precálculo y leen el tablero y Mis Precios. Es un dato derivado,
que se regenera desde Postgres: sin backups, y el TTL borra los días viejos.
Cifrada en reposo con la clave administrada `aws/dynamodb`.

### `frontend`

[`infra/modules/frontend`](infra/modules/frontend/) · todo lo que Terraform
gestiona del bucket del portal `lgdc-portal-<account_id>`.

- El public access block y la política: cualquiera lee los objetos, solo por
  HTTPS; nadie lista el bucket.
- `config.json`, lo que el portal lee al arrancar: las URLs de la API y de la
  Hosted UI, el client ID y las URLs de redirección. Así el mismo build sirve
  en cualquier cuenta.

El bucket lo crea [`scripts/buckets.sh`](scripts/buckets.sh), como el de
datos, y **no está versionado** (ver
[Correcciones de la entrega anterior](#correcciones-de-la-entrega-anterior)).
El build del portal lo sube Deploy (`make portal-publish`), sin tocar
`config.json`.

---

## Funciones y meta-argumentos de Terraform

### Meta-argumentos

| Meta-argumento | Dónde | Para qué |
|---|---|---|
| `for_each` | Los interface y gateway endpoints, sobre los sets de [`locals.tf`](infra/locals.tf) (`infra/modules/network/main.tf`) | Un endpoint por servicio; sacar uno no recrea los demás, como pasaría con `count` sobre una lista |
| `for_each` | Las reglas de salida hacia cada gateway endpoint: las de las Lambdas (`infra/modules/network/main.tf`, sobre `aws_vpc_endpoint.gateway`) y las de los workers (`infra/security_groups.tf`, sobre el output `gateway_prefix_list_ids`) | Una regla por prefix list (S3, DynamoDB) |
| `for_each` | Las suscripciones del topic (`infra/modules/topic/main.tf`), sobre el map `chain_processed_subscriptions` de `locals.tf` | Una suscripción por destino, cada una con su filter policy |
| `for_each` | Las rutas públicas de la API (`infra/modules/api/main.tf`), sobre el set `api_public_route_keys` | Una ruta sin authorizer por cada clave |
| `count` | La réplica de lectura: `var.read_replica_enabled ? 1 : 0` (`infra/modules/database/main.tf`) | Apagarla con una variable, sin tocar el módulo |
| `count` | El cron de una Lambda (la regla, su target y su permiso): `var.schedule_expression != null ? 1 : 0`, y su cola (el event source mapping): `var.sqs_trigger != null ? 1 : 0` (`infra/modules/lambda_function/main.tf`) | El mismo módulo sirve para Lambdas con cron, con cola, con las dos (el cierre del día) o sin ninguna |
| `depends_on` | El RDS Proxy espera a que el secreto tenga valor y a su log group (`infra/modules/database/main.tf`) | El Proxy lee el secreto apenas existe, y escribe sus logs apenas arranca: si el log group no existe, lo crea RDS, fuera de Terraform |
| `depends_on` | La invocación de la migradora espera las reglas de security group que la dejan llegar a la base (`infra/migration.tf`) | Nada en sus argumentos las nombra |
| `depends_on` | La notificación de S3 espera al módulo de la cola de jobs (`infra/notifications.tf`) | S3 verifica al crearla que puede escribir en la cola, y para eso tiene que existir la política de la cola |
| `depends_on` | El lifecycle del bucket de datos espera su versionado, y las políticas de los dos buckets, su public access block (`infra/modules/data_lake/main.tf`, `infra/modules/frontend/main.tf`); el `config.json` del portal, la política | Las reglas de versiones viejas piden versionado, y S3 rechaza dos cambios de la política del bucket a la vez |
| `lifecycle` | `ignore_changes = [desired_count]` en el servicio de los workers (`infra/modules/batch/main.tf`) | La cantidad de tareas es del autoscaling: sin esto cada apply la volvería a 0 en medio de un job |

Otras construcciones del lenguaje que se usan:

| Qué | Dónde | Para qué |
|---|---|---|
| Bloques `dynamic` | `environment` y `vpc_config` de la Lambda (`infra/modules/lambda_function/main.tf`); `rule`, `expiration` y `transition` del lifecycle del bucket (`infra/modules/data_lake/main.tf`) | Bloques que aparecen solo si hay qué poner, o uno por elemento de un map |
| `moved` | `infra/modules/api/main.tf` | Los recursos que se llamaban `this` cuando la API tenía una sola integración pasaron a `api` y `default` sin recrearse |
| `validation` | `vpc_cidr` y `availability_zone_count` (`infra/variables.tf`); `max_receive_count` (`queue`); `max_capacity` (`batch`); `maximum_retry_attempts` y `sqs_trigger` (`lambda_function`); el protocolo de las suscripciones (`topic`) | Que un valor inválido falle en el plan y no en AWS |
| `sensitive` | `sepa_api_url` y `admin_email` (`infra/variables.tf`); el output `cognito_client_secret` | El plan no los muestra |
| `optional` | `batch_size` (10) y `maximum_concurrency` (2) de `sqs_trigger` (`lambda_function`); `expiration_days` y `transition_days` de cada regla de lifecycle (`data_lake`); la filter policy de una suscripción (`topic`) | Atributos de un `object` con default, o que se pueden omitir |
| Expresiones `for` | Las subredes de cada banda y la suscripción condicional del email (`infra/locals.tf`); los nombres de las subredes y el output `gateway_prefix_list_ids` (`network`); las variables de entorno de los workers (`batch`); los statements de la política de la cola (`queue`); el output `subscription_arns` y la validación del protocolo (`topic`) | Armar listas y maps a partir de otros |

### Funciones

| Función | Dónde | Para qué |
|---|---|---|
| `format` | `infra/locals.tf`, `infra/main.tf`, `infra/security_groups.tf` y todos los módulos menos `serving` y `topic` | Los nombres de AWS con el prefijo (`lgdc-api`, `lgdc-app-us-east-1a`, `lgdc-data-<account_id>`), las URLs del portal, los ARNs de los buckets y el email de invitación con la URL del portal (`infra/modules/auth/main.tf`) |
| `merge` | `infra/locals.tf`, `infra/main.tf` | Los tags comunes con `var.tags`; las suscripciones fijas del topic con la del email; las variables de la base (`local.db_environment`) con las propias de cada Lambda |
| `cidrsubnet` | `infra/locals.tf`, `infra/variables.tf` | Los `/20` de cada banda y AZ a partir de `vpc_cidr`, y su validación (dentro de `can`) |
| `can` | `infra/variables.tf` | La validación de `vpc_cidr`: `can(cidrsubnet(var.vpc_cidr, 8, 0))` falla si no es un CIDR IPv4, o si es más chico que un `/24` (cada subred es un dieciseisavo del bloque, y AWS no acepta subredes más chicas que un `/28`) |
| `slice` | `infra/locals.tf` | Las primeras `availability_zone_count` AZs de la región |
| `toset` | `infra/locals.tf` | Los servicios de los endpoints y las rutas públicas de la API, como sets para `for_each` |
| `nonsensitive` | `infra/locals.tf` | `admin_email` es `sensitive`, y un `for_each` no acepta claves que dependan de un valor sensible: `nonsensitive(var.admin_email != "")` revela solo si hay dirección, no cuál |
| `concat`, `distinct` | `infra/modules/network/main.tf`; `concat` también en `infra/modules/queue/main.tf` | Las route tables de las tres bandas para los gateway endpoints, sin repetir; los statements fijos y los de `allowed_senders` de la política de la cola |
| `replace` | `infra/modules/network/main.tf` | El `Name` de cada interface endpoint (`ecr.api` → `ecr-api`) |
| `upper`, `split` | `infra/modules/queue/main.tf` | El `Sid` de cada servicio en la política de la cola (`s3.amazonaws.com` → `AllowSendFromS3`) |
| `split` | `infra/modules/database/main.tf`, `infra/modules/auth/outputs.tf` | La familia del parameter group (`postgres17`) sale de la versión del motor; la región de la Hosted UI, del ID del user pool (`us-east-1_…`) |
| `trimsuffix` | `infra/modules/api/main.tf`, `infra/modules/data_lake/main.tf` | La URL de la API sin la barra final; el id de cada regla de lifecycle (`raw/` → `raw`) |
| `jsonencode` | `queue`, `topic`, `data_lake`, `frontend`, `batch`, `database`, `infra/locals.tf`, `infra/migration.tf` | Las políticas de la cola, del topic y de los buckets, la redrive policy, las filter policies, el `config.json` del portal, la lifecycle policy de ECR, las container definitions de los workers, el secreto de la base y la entrada de la migradora |
| `jsondecode` | `infra/outputs.tf` | La respuesta de la migradora: el output `db_schema_tables` |
| `tostring` | `infra/locals.tf`, `infra/main.tf` | El puerto de la base y el `MAX_RECEIVE_COUNT` de los workers, como variables de entorno (que son strings) |
| `filebase64sha256` | `infra/modules/lambda_function/main.tf` | El hash del zip: la Lambda se actualiza solo si cambió el código |
| `length` y el splat `[*]` | `length` en `infra/modules/lambda_function/main.tf`; `[*]` ahí y en `infra/modules/data_lake/main.tf` | Los bloques `dynamic`: `environment` solo si hay variables (`length`), `vpc_config` solo si no es `null`, `expiration` y `transition` solo si la regla los tiene (`[*]` convierte un valor o `null` en una lista de uno o de cero) |
| `try` | `infra/modules/database/outputs.tf`, `infra/modules/lambda_function/variables.tf` | El output `replica_address`: con la réplica apagada `aws_db_instance.replica[0]` no existe y queda vacío (vacío y no `null`, porque un output `null` no se guarda en el state y `terraform output db_replica_address` fallaría). Y las validaciones de `sqs_trigger`, que con la variable en `null` no tienen qué validar |
| `alltrue`, `contains`, `values` | `infra/modules/topic/variables.tf` | Que el protocolo de cada suscripción sea `sqs` o `email` |

## Locals y data sources

[`infra/locals.tf`](infra/locals.tf) junta todo lo que se calcula una vez y se
usa en varios lugares:

| Local | Qué es |
|---|---|
| `name_prefix`, `tags` | El prefijo `lgdc` de todos los nombres, y los tags comunes (`Project`, `ManagedBy`, `Course`, más `var.tags`), que el provider pone en todo recurso con `default_tags` |
| `azs`, `subnet_cidrs` | Las AZs y los CIDRs de las tres bandas. Cada banda empieza cuatro `/20` después de la anterior, así que sumar una AZ no renumera ninguna subred |
| `*_name` | El nombre de cada Lambda y de los workers |
| `alert_matcher_timeout`, `day_close_timeout`, `precompute_timeout` | El timeout de cada Lambda que consume una cola; el *visibility timeout* de su cola sale de él (6 veces) |
| `data_bucket_name`, `portal_bucket_name`, `auth_domain` | Los nombres globales, con el account ID; `scripts/buckets.sh` arma los de los buckets igual |
| `portal_origin`, `portal_url` | La URL HTTPS del portal, que usan el `config.json`, el logout de Cognito, el email de invitación y el CORS del bucket de datos |
| `api_public_route_keys` | Las rutas de la API sin token |
| `data_lifecycle_rules` | Qué guarda cada prefijo del bucket de datos y por cuánto |
| `db_environment` | Cómo llega a Postgres todo lo que está en la VPC, menos el host |
| `gateway_endpoint_services`, `interface_endpoint_services` | Los servicios de AWS con endpoint |
| `chain_processed_subscriptions` | Quién recibe cada aviso de cadena procesada, y con qué filtro |

[`infra/data.tf`](infra/data.tf): nada que dependa de la cuenta se escribe a
mano.

| Data source | Para qué |
|---|---|
| `aws_caller_identity.current` | El account ID, en los nombres de los buckets y del dominio de la Hosted UI |
| `aws_region.current` | La región, en los endpoints, los logs de los workers y la URL del portal |
| `aws_availability_zones.available` | Las AZs de la región, de donde `slice` toma las primeras |
| `aws_iam_role.lab` | `LabRole`, el rol de cada Lambda, de las tareas de Fargate y del RDS Proxy: el Learner Lab no deja crear roles |

---

## Variables y outputs

Toda variable de [`infra/variables.tf`](infra/variables.tf) tiene default: la
cátedra no carga ningún `tfvars`.
[`infra/terraform.tfvars.example`](infra/terraform.tfvars.example) las
documenta para una corrida local.

| Variable | Default | Qué parametriza |
|---|---|---|
| `region` | `us-east-1` | La región de todo |
| `tags` | `{}` | Tags extra, sumados a los comunes |
| `vpc_cidr` | `10.0.0.0/16` | El bloque de la VPC (validado: `/24` o más grande) |
| `availability_zone_count` | `2` | En cuántas AZs van las subredes (validado: de 2 a 4) |
| `log_retention_days` | `7` | La retención de los logs de cada Lambda, de los workers y del RDS Proxy |
| `read_replica_enabled` | `true` | Si se crea la réplica de lectura; sin ella el precálculo lee del primario |
| `db_instance_class` | `db.t3.micro` | La clase de la instancia y de la réplica |
| `worker_image_tag` | `latest` | El tag de la imagen de los workers. El Makefile pasa siempre un hash de sus fuentes; el default no lo sube nadie |
| `worker_max_capacity` | `4` | Cuántos workers corren a la vez como máximo |
| `sepa_api_url` | vacío, `sensitive` | La URL del túnel al proxy SEPA. Deploy la carga desde el secret `SEPA_API_URL` |
| `ingest_schedule_expression` | `cron(0 4 * * ? *)` | El cron de la ingesta, en UTC (01:00 en Buenos Aires) |
| `day_close_schedule_expression` | `cron(0 13 * * ? *)` | El cron de respaldo del cierre del día, en UTC (10:00 en Buenos Aires) |
| `admin_email` | vacío, `sensitive` | El email que recibe las cadenas que fallan. Deploy lo carga desde `BOOTSTRAP_EMAIL`; vacío, no hay suscripción por email |

Los outputs de [`infra/outputs.tf`](infra/outputs.tf) son el contrato con los
workflows y con `make verify`:

| Output | Quién lo lee |
|---|---|
| `api_endpoint` | **Deploy**: el `curl` a `/api/health` y el resumen del run |
| `portal_url` | **Deploy**: el resumen del run |
| `data_bucket_name` | **Deploy**: el resumen del run |
| `portal_bucket_name` | **Deploy**: `make portal-publish` sube el portal ahí |
| `cognito_user_pool_id` | **Deploy**: `make bootstrap-platform-admin` da de alta al admin ahí |
| `ingest_function_name` | **run-ingest**: `make ingest` invoca esa función |
| `auth_callback_url`, `cognito_hosted_ui_url`, `cognito_client_id`, `cognito_client_secret` (`sensitive`) | `make verify`, para loguearse sin navegador |
| `db_migrator_function_name`, `db_schema_tables`, `jobs_queue_url`, `jobs_dlq_url`, `worker_cluster_name`, `worker_service_name`, `chain_processed_topic_arn`, `chain_processed_subscription_arns`, `alerts_queue_arn`, `day_close_queue_arn`, `alert_matcher_function_name`, `day_close_function_name`, `precompute_queue_url`, `precompute_queue_arn`, `precompute_function_name`, `comparison_table_name` | `make verify` ([`tools/verify`](tools/verify/README.md)); los de arriba también |
| `api_function_name`, `vpc_id`, `db_proxy_endpoint`, `db_replica_address`, `db_secret_arn`, `worker_repository_url`, `alerts_dlq_url`, `day_close_queue_url`, `day_close_dlq_url` | Nadie los lee: son para quien mire el stack (`terraform output`). `scripts/push-worker-image.sh` arma la URL del repositorio con la cuenta y la región, porque en una cuenta vacía corre antes del primer apply |

---

## El pipeline de GitHub Actions

Cinco workflows en [`.github/workflows/`](.github/workflows/). Todos usan
Terraform 1.16.1 y las credenciales de los secrets `AWS_*`; los que tocan el
state (Plan, Deploy, Destroy) comparten el grupo de concurrencia `terraform`,
así que nunca corren dos a la vez.

| Workflow | Cuándo corre | Qué hace |
|---|---|---|
| [Validate](.github/workflows/validate.yml) | En cada push y en cada pull request | `make lambdas` (los zips que el plan necesita para calcular sus hashes); `make lint`: `terraform fmt -check -recursive`, `terraform init -backend=false`, `terraform validate` y tflint con el ruleset de AWS ([`infra/.tflint.hcl`](infra/.tflint.hcl)), nada de lo cual necesita AWS. Después, si hay secrets de AWS, `make init` y `terraform plan` contra el state remoto, con `-lock=false` para no chocar con un Deploy en curso |
| [Plan](.github/workflows/plan.yml) | A mano, escribiendo `plan` | `make init` y `make plan` con los mismos secrets que Deploy, y sube el plan legible (`terraform show`) como artefacto `plan`. No sube el plan binario: lleva los valores sensibles en claro |
| [Deploy](.github/workflows/deploy.yml) | A mano, escribiendo `deploy` | `make init`, `make worker-push`, `make plan`, `make apply`, `make portal-publish`, `make bootstrap-platform-admin`, el `curl` a `/api/health` y el resumen con las URLs (ver [Desplegar](#3-desplegar)) |
| [run-ingest](.github/workflows/run-ingest.yml) | A mano, escribiendo `ingest` | `make init` (para leer el nombre de la función del state) y `make ingest`: invoca la ingesta, espera su resultado en CloudWatch y lo muestra en el resumen. Tiene su propio grupo de concurrencia: puede correr mientras Deploy aplica |
| [Destroy](.github/workflows/destroy.yml) | A mano, escribiendo `destroy` | `make init` y `make destroy`: `terraform destroy` y después vacía y borra los buckets de datos y del portal |

**`plan` sin secrets.** Validate es lo que corre en el pull request de
entrega. Si el repo no tiene `AWS_ACCESS_KEY_ID` (un fork, por ejemplo, al
que GitHub no le pasa los secrets), el paso de plan no corre: un aviso dice
*Plan skipped: the repo has no AWS secrets* y el workflow termina en verde,
con fmt, init, validate y tflint hechos. Si los secrets están pero vencieron
(una sesión del Learner Lab dura unas horas), el plan también se saltea, con
una advertencia, en vez de poner en rojo cada push.

---

## Correcciones de la entrega anterior

La devolución del TP2
recomendó versionar los buckets, salvo el del frontend, cuya versión está en
git.

- **El bucket de datos está versionado**
  ([`infra/modules/data_lake/main.tf`](infra/modules/data_lake/main.tf),
  `aws_s3_bucket_versioning`). Además de poder recuperar un archivo pisado,
  el pipeline lo usa: cada versión de un objeto tiene su propio evento de S3,
  y el inbox de los workers registra cada mensaje como `<key>#<versionId>`,
  así que volver a subir un archivo lo reprocesa y el mismo evento dos veces
  no carga nada dos veces. Para que las versiones no se acumulen, el
  lifecycle borra las reemplazadas a los 7 días.
- **El bucket del portal no está versionado**, a propósito: lo que tiene es el
  build de `app/frontend`, que se regenera desde git en cada Deploy, y el
  `config.json`, que escribe Terraform.
- **El bucket del state** (`lgdc-tfstate-<account_id>`) también está
  versionado: lo crea así [`infra/init-backend.sh`](infra/init-backend.sh).

---

## Decisiones de seguridad

El repo de la cátedra trae tres workflows propios que corren en cada pull
request, además de los nuestros: `Terraform Format` (`terraform fmt -check`),
`Generate terraform docs` (apunta a `terraform/modules`, que acá no existe,
así que no genera nada) y `Terraform Security`. Este último pasa
[tfsec](https://github.com/aquasecurity/tfsec) y comenta los hallazgos en el
PR. En la plantilla, el commenter apuntaba a `terraform/organization`. Acá
apunta a `infra/`
([`.github/workflows/tfsec.yaml`](.github/workflows/tfsec.yaml)).

tfsec no reporta nada de severidad media o mayor en `infra/`. Lo que marcaba se
arregló, o quedó aceptado con un `#tfsec:ignore:<regla>` y su motivo, en un
comentario justo encima:

| Hallazgo | Dónde | Qué se hizo |
|---|---|---|
| La tabla de DynamoDB no declara cifrado (`aws-dynamodb-enable-at-rest-encryption`) | [`serving`](infra/modules/serving/main.tf) | **Arreglado**: `server_side_encryption` con la clave administrada `aws/dynamodb` |
| Retención de backups de RDS de 1 día (`aws-rds-specify-backup-retention`) | [`database`](infra/modules/database/main.tf) | **Arreglado**: 7 días. Los backups automáticos se borran con la instancia en el destroy |
| Topic de SNS sin cifrar (`aws-sns-enable-topic-encryption`) | [`topic`](infra/modules/topic/main.tf) | **Aceptado**: el Learner Lab no deja crear una clave KMS, y la clave `aws/sns` requiere permisos de KMS en quien publica que ningún deploy probó. La policy del topic exige TLS para publicar |
| DynamoDB sin point-in-time recovery (`aws-dynamodb-enable-recovery`) | [`serving`](infra/modules/serving/main.tf) | **Aceptado**: la tabla es una capa derivada que se reconstruye desde Postgres en cualquier momento, así que no lleva backups |
| RDS sin deletion protection, en la instancia y en la réplica (`aws-rds-enable-deletion-protection`) | [`database`](infra/modules/database/main.tf) | **Aceptado**: con la protección puesta, Destroy fallaría y no dejaría limpia la cuenta del lab |
| RDS sin autenticación IAM (`aws-rds-enable-iam-auth`) | [`database`](infra/modules/database/main.tf) | **Aceptado**: la autenticación es por contraseña, que el Proxy y cada componente que se conecta leen de Secrets Manager, así que la autenticación IAM quedaría sin uso |
| Stage de API Gateway sin access logs (`aws-api-gateway-enable-access-logging`) | [`api`](infra/modules/api/main.tf) | **Aceptado**: en una HTTP API los escribe el service-linked role de API Gateway, que no se probó en el Learner Lab. Cada invocación de las Lambdas detrás queda en CloudWatch |

Quedan hallazgos de severidad baja, que tfsec marca como notas: los log
groups y los secrets cifrados con claves de AWS y no con una KMS propia (que
el lab no permite), las Lambdas sin X-Ray, el cluster de ECS sin Container
Insights y RDS sin Performance Insights.

Para correr tfsec en local, sin los módulos que descarga `terraform init`:

```sh
docker run --rm -v "$PWD":/src aquasec/tfsec /src/infra --exclude-downloaded-modules --minimum-severity MEDIUM
```

---

## Estructura del repo

| Carpeta | Qué es | |
|---|---|---|
| [`infra/`](infra/) | Todo el Terraform: la composición, `modules/` e `init-backend.sh` | [Módulos](#módulos) |
| [`app/shared/`](app/shared/) | Configuración, cliente de AWS, conexión a la base, los rebuilds del catálogo, la consulta de comparación y la tabla del precálculo | [detalle](app/shared/README.md) |
| [`app/api/`](app/api/) | La API (FastAPI) · Lambda | [detalle](app/api/README.md) |
| [`app/auth_callback/`](app/auth_callback/) | Canjea el código de Cognito por los tokens y se los pasa al portal · Lambda | [detalle](app/auth_callback/README.md) |
| [`app/invite/`](app/invite/) | Crea en Cognito el usuario del miembro que invita un dueño, fuera de la VPC · Lambda | [detalle](app/invite/README.md) |
| [`app/batch/ingest/`](app/batch/ingest/) | Baja el archivo del día del SEPA por el proxy y deja el de cada cadena en `raw/` · Lambda fuera de la VPC, con un cron diario | [detalle](app/batch/ingest/README.md) |
| [`app/batch/worker/`](app/batch/worker/) | Toma los jobs de la SQS, parsea y carga el catálogo, y avisa cada cadena procesada en el topic `lgdc-cadena-procesada` · Fargate | [detalle](app/batch/worker/README.md) |
| [`app/batch/alert_matcher/`](app/batch/alert_matcher/) | Convierte los cambios de precio de una cadena en las alertas de las reglas de cada comercio · Lambda | [detalle](app/batch/alert_matcher/README.md) |
| [`app/batch/day_close/`](app/batch/day_close/) | Cierra cada día cuando termina su última cadena: reconstruye los vecinos y el catálogo y encola el precálculo · Lambda, con un cron de respaldo | [detalle](app/batch/day_close/README.md) |
| [`app/batch/precompute/`](app/batch/precompute/) | Calcula la comparación de cada sucursal seguida y radio y la guarda en DynamoDB · Lambda | [detalle](app/batch/precompute/README.md) |
| [`app/db_migrator/`](app/db_migrator/) | El esquema de la base · Lambda | [detalle](app/db_migrator/README.md) |
| [`app/frontend/`](app/frontend/) | El portal (React + Vite): tablero, comparador, mapa, alertas, carga propia | [detalle](app/frontend/README.md) |
| [`tools/sepa-proxy/`](tools/sepa-proxy/) | El proxy al SEPA, que corre fuera de AWS | [detalle](tools/sepa-proxy/README.md) |
| [`tools/verify/`](tools/verify/) | `make verify`: los chequeos del TP3 contra el stack desplegado | [detalle](tools/verify/README.md) |
| [`scripts/`](scripts/) | Lo que llama el Makefile: `build-lambda.sh`, `buckets.sh`, `push-worker-image.sh`, `bootstrap-platform-admin.sh`, `run-ingest.sh` | |
| [`demo/`](demo/) | Dos listas de precios de prueba para la carga propia | [detalle](demo/README.md) |
| [`docs/`](docs/) | Arquitectura, ADRs, convenciones de Terraform, desarrollo, consignas | |
| [`.github/workflows/`](.github/workflows/) | Validate, Plan, Deploy, Destroy, run-ingest y los tres de la cátedra | [Pipeline](#el-pipeline-de-github-actions) |
