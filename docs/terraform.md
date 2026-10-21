# Convenciones de Terraform

Cómo se escribe el Terraform de LGDC. Sale de [terraform-best-practices.com](https://www.terraform-best-practices.com/), de [ozbillwang/terraform-best-practices](https://github.com/ozbillwang/terraform-best-practices) y de la [documentación del provider AWS](https://registry.terraform.io/providers/hashicorp/aws/latest/docs), adaptado a dos restricciones de este proyecto: el Learner Lab (no se pueden crear roles de IAM) y que la cátedra levante todo con un solo Deploy desde una cuenta vacía ([ADR-0001](adr/0001-alcance-tp3-tp4.md)).

## Estructura

Todo lo de Terraform vive en `infra/`: una sola **composición** que llama a **módulos de recursos** propios en `infra/modules/` y a módulos del Registry. Fuera de `infra/` no hay ningún archivo de Terraform; el Makefile y los workflows lo corren con `terraform -chdir=infra`.

```
infra/
├── versions.tf    required_version y required_providers
├── providers.tf   provider "aws" con region y default_tags
├── backend.tf     backend "s3" con configuración parcial
├── variables.tf   todas con default: la cátedra no carga tfvars
├── locals.tf      nombres, tags, CIDRs, listas para for_each
├── data.tf        cuenta, región, AZs, LabRole
├── main.tf        solo llamadas a módulos y el cableado entre ellos
├── security_groups.tf  reglas entre security groups de módulos distintos
├── notifications.tf  la notificación del bucket de datos a la SQS de jobs
├── migration.tf   la invocación de la Lambda migradora durante el apply
├── outputs.tf     lo que lee el workflow de Deploy y el job summary
├── .terraform.lock.hcl · .tflint.hcl · terraform.tfvars.example
├── init-backend.sh  crea el bucket del state si falta y corre terraform init
└── modules/<nombre>/
    ├── main.tf
    ├── variables.tf
    ├── outputs.tf
    └── versions.tf  required_providers, sin bloque provider
```

- Un módulo de recursos agrupa los recursos que cumplen una sola función (una cola con su DLQ, una Lambda con su log group y sus permisos). Si un módulo necesita saber de otro, recibe el valor por variable desde `main.tf`; los módulos no se leen entre sí.
- Los módulos no declaran bloques `provider`: lo heredan de la raíz.
- Las reglas de security group que cruzan módulos van en la composición (`infra/security_groups.tf`), para que no haya dependencias circulares entre módulos.
- `terraform.tfvars` solo existiría en `infra/`, y no hace falta: toda variable tiene default. No se commitea (`*.tfvars` en `.gitignore`), con un `terraform.tfvars.example` documentando los valores.

**Desvío deliberado.** La guía de ozbillwang recomienda separar en capas (red, datos, aplicación) con state propio y `terraform_remote_state`. No lo hacemos: con capas, Deploy tendría que aplicar varias composiciones en orden y la cátedra no podría levantar todo en una corrida. Tampoco se usan workspaces ni Terragrunt: hay un solo entorno.

## Nombres

- Identificadores de Terraform (recursos, data sources, variables, outputs, locals): `snake_case`, en minúscula, en singular.
- No repetir el tipo en el nombre: `resource "aws_sqs_queue" "jobs"`, no `"jobs_queue"`. Si el módulo crea un único recurso de ese tipo, se llama `this`.
- Nombres de AWS (el argumento `name`): con guiones y el prefijo `local.name_prefix` (`lgdc`), por ejemplo `lgdc-jobs`, `lgdc-day-close`. Cortos, porque varios servicios limitan el largo (los target groups a 32 caracteres, por ejemplo).
- Buckets: llevan el account ID para ser únicos (`lgdc-data-<account_id>`).

## Orden dentro de un bloque

1. `count` o `for_each`, y una línea en blanco.
2. Los argumentos.
3. `tags`, último de los argumentos.
4. `depends_on`, después `lifecycle`, separados por una línea en blanco.

Para recursos condicionales, `count = var.read_replica_enabled ? 1 : 0`, con un booleano y no `length(...)`. Para conjuntos de recursos parecidos (endpoints, suscripciones, colas), `for_each` sobre un map de `locals`, nunca `count` sobre una lista: con `count`, sacar un elemento del medio recrea los siguientes.

## Variables

- Orden de los argumentos: `description`, `type`, `default`, `validation`. Siempre con `description`; si existe el argumento equivalente en el provider, se usa su nombre y su descripción.
- `list(...)` y `map(...)` con nombre en plural.
- Booleanos en positivo y con sufijo `_enabled`: `read_replica_enabled`, no `disable_replica`.
- `nullable = false` en las que nunca pueden ser `null`.
- Tipos simples (`string`, `number`, `list(string)`, `map(string)`); `object({...})` solo si hace falta validar la forma.
- Lo secreto se marca `sensitive = true` y entra por `TF_VAR_<nombre>` desde los secrets de GitHub. Nunca en el código ni en un tfvars.

## Outputs

- Nombre `{nombre}_{tipo}_{atributo}`, sin el prefijo del provider: `jobs_queue_url`, `api_endpoint`, `portal_bucket_name`. Si el output junta varios recursos, un nombre genérico.
- Siempre con `description`.
- Los outputs de la raíz son el contrato con el workflow de Deploy: si Deploy los lee, se documentan como tales.
- `try(...)` para valores que pueden no existir (por ejemplo, la réplica con `count = 0`).

## Versiones

```hcl
terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```

- `>= 1.10` porque es la versión desde la que el backend S3 hace el lock de forma nativa (`use_lockfile`).
- Los módulos del Registry (`terraform-aws-modules/vpc/aws`) se fijan a una versión mayor y menor (`~> X.Y`, con la última versión compatible con el provider 6), nunca sin versión.
- `.terraform.lock.hcl` se commitea, para que la cátedra baje las mismas versiones de providers que probamos.

## Provider y datos de la cuenta

```hcl
provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}
```

- Sin credenciales en el código: el provider las toma de `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` y `AWS_SESSION_TOKEN`, que el workflow carga desde los secrets.
- Los tags comunes (`Project = "lgdc"`, `ManagedBy = "terraform"`, `Course = "82.08"`) van en `default_tags` y no se repiten en cada recurso. Un recurso agrega solo los suyos (`Name`, `Component`).
- Nada que dependa de la cuenta se escribe a mano: cuenta, región, AZs y el rol `LabRole` salen de data sources (`aws_caller_identity`, `aws_region`, `aws_availability_zones`, `aws_iam_role`). Las prefix lists de S3 y DynamoDB, que usan las reglas de security group, salen de los atributos de los gateway endpoints (`prefix_list_id`), no de un data source.
- **Buckets en el Learner Lab**: la cuenta niega `s3:GetBucketObjectLockConfiguration`, que el provider lee en cada refresh de un `aws_s3_bucket`. Los buckets se crean con la CLI (`infra/init-backend.sh` el del state, `scripts/buckets.sh` el de datos y el del portal) y Terraform gestiona todo lo demás con los resources sueltos (`aws_s3_bucket_versioning`, `_lifecycle_configuration`, `_policy`, …), nunca con `aws_s3_bucket` ni con `terraform-aws-modules/s3-bucket` ([ADR-0002](adr/0002-bucket-de-datos-fuera-de-terraform.md)).
- **IAM en el Learner Lab**: no se crean roles ni políticas. Cada Lambda, tarea de ECS y servicio que pide un rol recibe `data.aws_iam_role.lab.arn`. Lo que sí se escribe son las políticas de recursos (de bucket, de cola, de topic), que no son IAM de identidades.

## State

```hcl
terraform {
  backend "s3" {
    key          = "terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
```

- Configuración parcial: el bucket (`lgdc-tfstate-<account_id>`) se pasa en `terraform init -backend-config="bucket=..."`. `make init` (con `infra/init-backend.sh`) lo crea con versionado si no existe, como en `2026Q1-G1`, así que Deploy funciona en una cuenta vacía.
- Lock nativo de S3 (`use_lockfile`), sin tabla de DynamoDB: el lock por DynamoDB está deprecado en el backend S3.
- El state nunca se commitea (`*.tfstate*` y `.terraform/` en `.gitignore`).

## Calidad

- `terraform -chdir=infra fmt -recursive` antes de cada commit: no hay hook de pre-commit, y si se olvida, `make lint` y Validate lo marcan.
- El workflow Validate corre `make lint` (`fmt -check -recursive`, `init` + `validate` y `tflint` con el ruleset de AWS de `infra/.tflint.hcl`) y, si hay credenciales, `terraform plan` ([ADR-0001](adr/0001-alcance-tp3-tp4.md)).
- tfsec (el workflow `Terraform Security` de la cátedra) no tiene que reportar nada de severidad media o mayor en `infra/`. Un hallazgo que no se arregla se acepta con `#tfsec:ignore:<regla>` justo encima de lo que marca, después de un comentario con el motivo, y suma su fila a la tabla de «Decisiones de seguridad» del README.
- Cada módulo propio tiene una sección en el README de la raíz con qué hace y qué recursos crea, y el README explica cada función y meta-argumento usado y dónde.
