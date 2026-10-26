---
status: accepted
---

# Alcance de las entregas TP3 (IaC) y TP4 (demo final)

Las dos últimas entregas llevan LGDC desde la infraestructura levantada a mano de TP1/TP2 a la arquitectura objetivo: todo en Terraform, Lambda para todo menos los workers (Fargate), Cognito, y la comparación de precios precalculada en DynamoDB. Se implementa desde cero, con el código actual como base. El diseño completo vive en [`docs/arquitectura.md`](../arquitectura.md). Este ADR fija qué entra en cada entrega y qué reglas no se negocian.

| Entrega | Fecha |
| --- | --- |
| TP3 · IaC | mié 21/10/2026, 19:00 |
| TP4 · demo final | mié 18/11/2026, 19:00 |

## Reglas para las dos entregas

- **Se mantienen todas las funcionalidades del portal.** Una funcionalidad es una capacidad que ve el usuario: tablero, Mis Precios, cara a cara, historial de producto, búsqueda, radio libre, alertas, carga CSV, organizaciones con owner/member. Los mecanismos pueden cambiar, y dos *deben* cambiar: el login propio se reemplaza por Cognito, y la paginación por offset por cursor.
- **Estructura del repo.** Todo el Terraform en `infra/` (la composición, `infra/modules/` y lo que la acompaña: lock, tflint, tfvars de ejemplo, init del backend), el código en `app/` (`shared`, `api`, `auth_callback`, `invite`, `db_migrator`, `frontend` y, en `batch/`, `ingest`, `worker`, `alert_matcher`, `day_close` y `precompute`) y las herramientas que no se despliegan en `tools/`: el proxy (`tools/sepa-proxy/`) y los chequeos de `make verify` (`tools/verify/`). Se borran `deploy.sh`, LocalStack, `compose.ci.yaml` y `seed_demo.py`. La documentación de estas entregas va en `docs/` (ADRs, arquitectura, convenciones).
- **Interfaz con la cátedra.** Igual que `2026Q1-G1`: la cátedra carga los secrets y corre **Deploy**, que levanta todo desde una cuenta vacía en una sola corrida (state bucket incluido) y deja las URLs en el job summary. No hay `tfvars` ni variables que tocar: toda variable de Terraform tiene default. Workflows: `validate` (push/PR), y `plan`, `deploy`, `destroy`, `run-ingest` manuales con palabra de confirmación. El README sigue los «Pasos a seguir» del repo de referencia.

  | Secret | Obligatorio |
  | --- | --- |
  | `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` · `AWS_SESSION_TOKEN` | Sí |
  | `BOOTSTRAP_EMAIL` · `BOOTSTRAP_PASSWORD` | Sí |
  | `SEPA_API_URL` | No |

- **Deploy no depende del túnel al SEPA.** El SEPA bloquea las IPs de AWS, así que el proxy corre fuera de AWS. Si `SEPA_API_URL` falta o no responde, el apply termina bien y solo la ingesta falla, con un error claro. Se elimina la URL de Quick Tunnel hardcodeada en la ingesta.
- **El proxy lo levanta quien despliega.** Para corregir con datos del SEPA, la cátedra corre el proxy de `tools/sepa-proxy/` y su túnel en una máquina propia, fuera de AWS, y carga la URL del túnel como `SEPA_API_URL`. El README lo pide como prerrequisito.
- **CI sin tests, con init, validate y plan en el PR.** La consigna pide un pipeline de GitHub Actions con init, validate y plan que corra bien en el pull request de entrega. En cada push y PR, `validate` corre `terraform fmt -check`, `terraform init`, `terraform validate` y tflint sin credenciales, y `terraform plan` contra el state remoto cuando el repo tiene cargados los secrets de AWS (si no los tiene, el paso de plan lo dice y no falla). Deploy termina con un `curl` a `/api/health`. Se sacan los tests de Python, del proxy y del frontend del CI, y no se usa checkov. Los tests reales se agregan después.
- **Correcciones de la devolución del TP2.** El bucket de datos va versionado; el del portal no, porque su versión está en git.

## TP3

El objetivo es entregar todo lo siguiente. Todo es parte del piso: lo que tiene que funcionar con Deploy desde una cuenta vacía.

- **Red**: VPC sin NAT ni Internet Gateway; gateway endpoints de S3 y DynamoDB; interface endpoints de ECR, Logs, SQS, SNS y Secrets Manager (y Lambda, por [ADR-0003](0003-invitacion-por-lambda-fuera-de-la-vpc.md)).
- **Datos**: bucket de datos versionado con lifecycle; RDS PostgreSQL con réplica de lectura y RDS Proxy; Lambda que aplica el esquema durante el apply (incluye `processed_message`, `outbox` y `feed_day`).
- **Auth y API**: Cognito con registro abierto, grupo `platform-admin` y callback por Lambda; FastAPI en Lambda detrás de API Gateway con authorizer JWT.
- **Portal**: servido desde S3 por HTTPS, con HashRouter, login por redirect y subida con POST prefirmado.
- **Pipeline**: Lambda de ingesta fuera de la VPC con cron de EventBridge; S3 → SQS de jobs → workers en Fargate con autoscaling, inbox y outbox; topic SNS `cadena-procesada` con sus tres suscripciones (SQS alertas → Lambda alert-matcher, SQS cierre, email al admin cuando una cadena falla); Lambda cierre con su cron de respaldo.
- **Precálculo**: SQS y Lambda precálculo, tabla `lgdc-comparison` en DynamoDB con TTL; la API lee de DynamoDB y, si la comparación no está, responde con la consulta en vivo y encola el precálculo. Gracias a ese respaldo, un problema del precálculo no deja el tablero sin datos.
- **Entrega**: los cinco workflows; un README con la descripción de cada módulo, la explicación de las funciones y meta-argumentos usados, la guía paso a paso y el pipeline; el diagrama actualizado y alineado con el Terraform; y un ensayo completo en una cuenta limpia siguiendo solo el README. Se entrega con un pull request al repo de la cátedra y el hash del commit en el campus.

Son 11 módulos propios (`network`, `data_lake`, `database`, `queue`, `topic`, `lambda_function`, `batch`, `api`, `auth`, `frontend`, `serving`) más el externo `terraform-aws-modules/vpc`. `s3-bucket` estaba previsto y quedó afuera por una SCP del Learner Lab ([ADR-0002](0002-bucket-de-datos-fuera-de-terraform.md)).

**Si el tiempo no alcanza**, se recorta en este orden y lo recortado pasa al TP4:

1. Réplica de lectura (`count = 0`; el precálculo lee de la primaria).
2. alert-matcher como Lambda aparte: las alertas se siguen generando en el worker, así que la funcionalidad no se pierde.
3. Email al admin cuando una cadena falla.

El ensayo en una cuenta limpia no se recorta.

## TP4

1. Lo que se haya recortado del TP3.
2. Módulo `analytics`: catálogo, crawler y job diario de Glue; Redshift Serverless, o Athena según una variable; Firehose para los eventos de uso; Lambda ref-data (dólar BCRA e IPC INDEC).
3. Pantallas Tendencias (comercio) y Observatorio (`platform-admin`).
4. Topic `alertas-comercios`, con la suscripción por email que cada usuario activa desde el portal.
5. Historial de más de 90 días con Spectrum sobre `curated/`.
6. Módulo `observability`: alarmas (DLQs, errores de Lambda) y dashboard de CloudWatch.
7. Presentación y ensayo de cambios en vivo.

## Abierto

- **Learner Lab y TP4.** Falta verificar qué permite de Redshift Serverless, Glue y Firehose. Si Redshift no está disponible, se usa Athena sobre el mismo catálogo.

## Rúbrica del TP3

De la rúbrica del TP3: módulos 20 %, despliegue correcto 20 %, que funcione 20 %, módulo externo 10 %, calidad del código 10 %, README 10 %, funciones y meta-argumentos 5 %, locals y datasources 5 %. El 40 % depende de que la cátedra pueda desplegar y usar el sistema, por eso el ensayo en una cuenta limpia no se recorta.

## Consecuencias

- Sin LocalStack ni tests en el CI, los checks de aislamiento entre organizaciones, del radio y de la subida prefirmada quedan sin cobertura automática hasta que se agreguen tests reales: los corre a mano `make verify`, contra el stack desplegado.
- Sin el módulo `observability` en el TP3, los fallos que no pasan por el worker (una Lambda que cae, un mensaje en la DLQ) solo se ven en los logs hasta el TP4.
