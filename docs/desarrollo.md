# Desarrollo y operación a mano

Lo que hacen los workflows, corrido desde una máquina propia, y lo que hace
falta para trabajar en el código. Para desplegar y corregir no hace falta
nada de esto: alcanza con el [README](../README.md) y GitHub Actions.

## Herramientas

| Herramienta | Versión |
|---|---|
| Terraform | `>= 1.10, < 2.0` ([`infra/versions.tf`](../infra/versions.tf)); los workflows usan 1.16.1 |
| AWS CLI | v2 |
| Python, con `pip` y `zip` | 3.12 o más nuevo (probado con 3.14): los zips se arman igual con las ruedas de 3.12, el runtime de las Lambdas |
| Docker | Con BuildKit (el builder por defecto desde Docker 23), para la imagen de los workers y para `make verify` |
| Node | 22, para el portal |
| tflint | v0.64.0, como el CI, para `make lint`. Si el gestor de paquetes no lo tiene, el binario está en sus [releases](https://github.com/terraform-linters/tflint/releases/tag/v0.64.0) |

## Construir los artefactos

```bash
make                # las Lambdas y la imagen de los workers
make lambdas        # un zip por Lambda en build/: api, auth_callback, ingest, db_migrator, invite, alert_matcher, day_close y precompute
make worker-image   # la imagen lgdc-worker:<hash> (linux/amd64, como Fargate)
make worker-push    # la sube a ECR, solo si ECR no tiene ese tag
make portal         # el portal en app/frontend/dist (npm ci y npm run build)
make clean          # borra build/
```

- **Cada Lambda es un zip plano** con sus módulos, los de `app/shared` y sus
  dependencias (las de su `requirements.txt`, como ruedas de Linux x86_64 para
  Python 3.12). Lo arma
  [`scripts/build-lambda.sh`](../scripts/build-lambda.sh), siempre igual para las
  mismas fuentes, así que Terraform no ve cambios cuando no los hay. `make`
  solo lo rearma si cambió algo.
- **La imagen de los workers lleva el código adentro**: una tarea de Fargate
  sin salida a internet no tiene otra forma de conseguirlo. Su tag es un hash
  de sus fuentes (las que deja pasar `Dockerfile.dockerignore`): cambia solo
  si cambió el código, y Deploy sube la imagen solo entonces
  ([`scripts/push-worker-image.sh`](../scripts/push-worker-image.sh)). En una
  cuenta vacía, si el repositorio ECR todavía no existe, el script lo crea
  antes con un apply dirigido.

## Terraform a mano

Lo mismo que hacen los workflows, con las credenciales de AWS en el entorno:

```bash
make init      # crea el bucket del state si falta y corre terraform init
make worker-push  # sube la imagen de los workers si ECR no la tiene
make plan      # arma las Lambdas y escribe build/tfplan, con el tag de la imagen
make apply     # crea los buckets de datos y del portal si faltan y aplica build/tfplan
make portal-publish  # arma el portal y lo sube a su bucket
make bootstrap-platform-admin  # el admin de plataforma, con BOOTSTRAP_EMAIL y BOOTSTRAP_PASSWORD en el entorno
make ingest    # la ingesta del SEPA ya, como run-ingest; SEPA_API_URL en el entorno reemplaza la del deploy
make destroy   # destroy, y después borra los buckets de datos y del portal
make lint      # fmt, validate y tflint, sin AWS
```

Para que `make plan` despliegue lo mismo que Deploy, los secrets van en el
entorno con los nombres que les da el workflow: `TF_VAR_admin_email` (el
valor de `BOOTSTRAP_EMAIL`, para la suscripción por email) y, si hay proxy,
`TF_VAR_sepa_api_url`. Sin ellos el plan sale igual, sin la suscripción y
con la ingesta sin proxy. Por ejemplo:

```bash
export BOOTSTRAP_EMAIL=admin@example.com BOOTSTRAP_PASSWORD=LgdcCatedra2026
make init && make worker-push
TF_VAR_admin_email="$BOOTSTRAP_EMAIL" make plan
make apply && make portal-publish && make bootstrap-platform-admin
```

El state vive en `lgdc-tfstate-<account_id>`, versionado, con el lock nativo
de S3 (`use_lockfile`, sin tabla de DynamoDB). El bucket se pasa en
`terraform init -backend-config`, porque depende de la cuenta.

El esquema de la base se aplica dentro del apply: [`infra/migration.tf`](../infra/migration.tf)
invoca la Lambda migradora ([`app/db_migrator`](../app/db_migrator/README.md))
cuando la base y la función existen, y el apply falla si la migración falla.
Vuelve a correr solo si cambia el código de la migradora o se reemplaza la
instancia; el SQL es idempotente igual.

**Un stack desplegado antes del log group del RDS Proxy.** Terraform crea
`/aws/rds/proxy/lgdc-db` antes que el Proxy; si no, lo crea RDS sin
retención y Destroy no lo borra. En un stack que ya estaba desplegado antes
de ese cambio, el primer apply falla con *already exists* hasta borrar ese
log group o importarlo
(`terraform import module.database.aws_cloudwatch_log_group.proxy /aws/rds/proxy/lgdc-db`).
En una cuenta limpia no pasa.

## Cargar a mano el archivo de una cadena

Todo archivo nuevo en `raw/` genera, por S3 Event Notifications, un mensaje en
la SQS `lgdc-jobs`, y los workers de Fargate lo cargan. Para cargar a mano el
archivo de una cadena (el ZIP interno del SEPA), alcanza con la CLI:

```bash
aws s3 cp sepa_1_comercio-sepa-9_2026-08-28_09-05-10.zip \
  "s3://$(terraform -chdir=infra output -raw data_bucket_name)/raw/feed_date=2026-08-28/chain_id=9/"
```

La clave dice la fecha y la cadena: `raw/feed_date=<día>/chain_id=<cadena>/<archivo>`.
El job aparece en `ingest_job` (la pantalla de Pipeline) y termina en `done`
con los precios en Postgres y el Parquet en `curated/`. Un archivo roto va a
`rejected/` sin reintento; un error transitorio se reintenta y, al tercer
fallo, el mensaje pasa a la DLQ. Los detalles (reintentos, DLQ, inbox,
escalado) están en [`app/batch/worker`](../app/batch/worker/README.md).

## Cómo se resuelven los imports

Ningún componente es un paquete: hacen `from common import ...` e
`import maintenance` como módulos sueltos. Por eso cada artefacto pone los
módulos de `app/shared` en el mismo directorio que los del componente, y desde
el repo se corre con `PYTHONPATH=app/shared`. Por ejemplo, la API:

```bash
pip install -r app/api/requirements-dev.txt
PYTHONPATH=app/shared uvicorn main:app --app-dir app/api
```

`GET /api/health` responde sin base. El resto necesita un Postgres y un S3 a
los que apuntar con las variables de abajo, y además los claims del token que
pone API Gateway: corrida así, sin API Gateway delante, toda ruta con token
responde 401.

## Configuración

Todo por variable de entorno, que en AWS pone Terraform. El código no lleva
credenciales: en AWS salen del rol (`LabRole`). Las propias de cada
componente están en su README; las comunes, en
[`app/shared/common.py`](../app/shared/common.py):

| Variable | Default | Para qué |
|---|---|---|
| `AWS_ENDPOINT_URL` | vacío | Un emulador de AWS para correr en local. Vacío contra AWS real |
| `AWS_REGION` | `us-east-1` | |
| `S3_DATA_BUCKET` | `lgdc-data` | El bucket de datos |
| `DB_HOST` | `floci` (el emulador del TP2) | El host de Postgres. En AWS, el RDS Proxy o la instancia |
| `DB_PORT` | se pregunta a la API de RDS | Fijo, evita esa consulta. En AWS Terraform lo fija en `5432` |
| `DB_NAME` · `DB_USER` · `DB_PASSWORD` | `lgdc` · `lgdc` · `lgdcpass` | |
| `DB_SECRET_ARN` | vacío | El secreto de Secrets Manager con la contraseña (`{"password": ...}`). Si está, reemplaza a `DB_PASSWORD`; en AWS siempre está |
| `DB_HOST_RO` | vacío (las lecturas van al primario) | El endpoint de la réplica |

El código está en inglés; esta documentación, en español.

## Verificación

No hay tests en el CI ([ADR-0001](adr/0001-alcance-tp3-tp4.md)). La
verificación es `make verify`, que se corre a mano contra el stack desplegado:

```bash
make init      # una vez por checkout
make verify    # con las credenciales de AWS en el entorno
```

Lee las URLs de `terraform output`, crea sus propios usuarios de prueba y
cadenas sintéticas, imprime cada chequeo con ✓ o ✗ y termina con código
distinto de cero si alguno falló. Cubre el esquema, el portal y el login de
Cognito, el pipeline de jobs, la carga propia, la ingesta del SEPA por
run-ingest (con un proxy de prueba detrás de un túnel de Cloudflare, en
Docker), el aislamiento entre organizaciones, el radio del conjunto
competitivo, el aviso de cadena procesada (a quién le llega cada aviso, y la
alerta que termina en el portal), el cierre del día, el precálculo y los
roles (`owner`, `member` y `platform-admin`). Al terminar borra los usuarios
de Cognito y los archivos que dejó en el bucket de datos; sus filas en
Postgres quedan. Tarda entre 20 y 30 minutos. Qué chequea cada suite, y cómo
correr solo algunas: [`tools/verify`](../tools/verify/README.md).

Además:

- `make` construye los artefactos.
- En `app/frontend/`, `npm run lint` y `npm run build` (el build corre `tsc`).
- `make lint`: `terraform fmt -check`, `validate` y tflint, como el CI.

## Consultas en la base

La base no tiene acceso público: estas consultas se corren desde dentro de la
VPC.

```sql
-- Cómo terminó cada cadena, y quién la procesó
SELECT chain_id, status, worker_id, rows_ok, rows_rejected
FROM ingest_job ORDER BY chain_id::int;

-- Cuánto entró
SELECT (SELECT count(*) FROM price)   AS precios,
       (SELECT count(*) FROM product) AS productos,
       (SELECT count(*) FROM branch)  AS sucursales;

-- El mismo producto en distintas sucursales, que es para lo que existe todo esto
SELECT b.banner_name, b.name, b.locality, p.price
FROM price p JOIN branch b USING (company_id, banner_id, branch_id)
WHERE p.ean = '7790895000270'
ORDER BY p.price;

-- Dónde más conviene elegir bien: mayor diferencia entre sucursales
SELECT p.ean, t.description,
       count(*) AS sucursales, min(p.price), max(p.price),
       round((max(p.price) / min(p.price) - 1) * 100) AS diferencia_pct
FROM price p JOIN product t USING (ean)
GROUP BY p.ean, t.description
HAVING count(*) > 50
ORDER BY diferencia_pct DESC LIMIT 20;
```
