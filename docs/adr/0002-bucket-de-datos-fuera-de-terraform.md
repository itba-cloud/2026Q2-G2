---
status: accepted
---

# El bucket de datos se crea con la CLI, y Terraform gestiona todo lo demás

El Learner Lab tiene una SCP que niega `s3:GetBucketObjectLockConfiguration` en toda la cuenta. El resource `aws_s3_bucket` del provider AWS lee esa configuración en cada refresh, y en la partición `aws` cualquier error ahí es fatal, incluido el `AccessDenied` (está así en 6.69.1, en la rama principal y también en 5.x). Lo comprobamos en el ticket 04: el apply crea el bucket y falla al leerlo, el bucket queda *tainted* en el state, y desde ahí todo `plan` falla. El módulo `terraform-aws-modules/s3-bucket/aws` usa ese mismo resource, así que con él pasa lo mismo. Las demás lecturas del bucket (versionado, lifecycle, política, public access block, notificaciones) sí están permitidas.

Por eso el bucket de datos (`lgdc-data-<account_id>`) lo crea `scripts/buckets.sh create` antes del apply (`make apply`), igual que `infra/init-backend.sh` crea el del state, y `scripts/buckets.sh delete` lo vacía (todas las versiones) y lo borra después del destroy (`make destroy`). Todo lo demás del bucket lo gestiona Terraform, en el módulo propio `data_lake` y con resources sueltos: `aws_s3_bucket_versioning`, `aws_s3_bucket_lifecycle_configuration`, `aws_s3_bucket_public_access_block`, `aws_s3_bucket_policy` y `aws_s3_bucket_cors_configuration`. La notificación a la SQS de jobs (`aws_s3_bucket_notification`) está en `infra/notifications.tf`.

## Consecuencias

- No se usa `terraform-aws-modules/s3-bucket/aws`. El criterio de módulo externo de la rúbrica queda cubierto por `terraform-aws-modules/vpc/aws` en `network`. Esto cambia lo que decían [ADR-0001](0001-alcance-tp3-tp4.md), `docs/arquitectura.md`, `docs/terraform.md` y la spec.
- El nombre del bucket se arma en dos lugares, `infra/locals.tf` y el script, con la misma regla (`lgdc-data-<account_id>`). El plan no necesita que el bucket exista: Terraform lo nombra por `local.data_bucket_name` y arma su ARN, sin data source.
- El bucket del portal (`lgdc-portal-<account_id>`, ticket 05) tiene el mismo problema y sigue el mismo camino: lo crea y lo borra el mismo script, que desde entonces se llama `scripts/buckets.sh` (antes `data-bucket.sh`), y su public access block, su política y su `config.json` están en el módulo `frontend`.
- Si el Learner Lab levanta la SCP, volver al resource (o al módulo) es importar el bucket con `terraform import` y sacar el script.

## Alternativas descartadas

- **`awscc_s3_bucket`** (Cloud Control): no se probó. Es probable que el handler de CloudFormation también lea el object lock, suma un segundo provider y tampoco es el módulo externo.
- **Esperar a la cátedra**: el ticket quedaba bloqueado con la entrega encima, y la cuenta de corrección es otra cuenta del mismo Learner Lab, seguramente con la misma SCP.
