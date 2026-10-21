# app/invite · la invitación de miembros en Cognito

La Lambda `lgdc-invite`: crea en Cognito el usuario de un miembro que invitó
el dueño de una organización. `make lambdas` la empaqueta en
`build/invite.zip`; el handler es `main.handler` y no tiene dependencias
propias (`boto3` viene con el runtime).

## Por qué existe

La API corre en la VPC, que no tiene salida a internet, y Cognito no acepta
requests por un VPC endpoint de un user pool con dominio (el nuestro tiene el
de la Hosted UI). Así que la API no puede llamar a `AdminCreateUser`: lo hace
esta Lambda, **fuera de la VPC**, y la API la invoca por el interface
endpoint de Lambda y espera la respuesta.
[ADR-0003](../../docs/adr/0003-invitacion-por-lambda-fuera-de-la-vpc.md)
cuenta la decisión y lo que se descartó.

## La invitación, de punta a punta

```
portal     el dueño escribe el email en Tu cuenta → Miembros
API        POST /api/org/members {email}, solo `owner`
           abre una transacción e inserta la fila (org del dueño, rol member),
           o se la pone a la fila de un usuario que todavía no tiene org;
           si el email ya está en una organización, 409 y no toca Cognito
           lambda:Invoke lgdc-invite {email}, sincrónico
invite     AdminCreateUser: email verificado, invitación por EMAIL
           → {"status": "created", "sub": …}
           si Cognito ya lo tenía, AdminGetUser → {"status": "exists", "sub": …}
           si Cognito rechaza el email → {"status": "invalid"}
API        created o exists: guarda el sub y hace commit (201, con
           invitation_sent true o false)
           invalid: 422; error de la Lambda: 502. Las dos, rollback
Cognito    solo si lo creó, le manda al miembro el email de invitación (infra/modules/auth):
           la URL del portal, su usuario y la contraseña temporal (vence en 7 días)
Hosted UI  en el primer login le pide una contraseña nueva, y sigue el login
           de siempre (app/auth_callback)
API        GET /api/auth/me encuentra la fila por el sub: ya está en la
           organización, como member
```

- **El email queda verificado** al crearlo: lo escribió el dueño, y el
  miembro lo confirma al entrar con lo que le llegó ahí. Sin eso no podría
  recuperar la contraseña.
- **Pedir dos veces el mismo email es seguro**: la segunda vez Cognito ya
  tiene el usuario y la Lambda responde `exists` con su `sub`. Por eso la API
  puede reintentar (`lambda_client` en `app/api/main.py`, dos intentos) y una
  invitación cuya respuesta se perdió no deja un usuario de Cognito suelto:
  la próxima invitación lo enlaza.

## Configuración

| Variable | Para qué |
|---|---|
| `USER_POOL_ID` | El user pool donde se crea el usuario. Terraform la carga desde el módulo `auth` |

La API la encuentra por `INVITE_FUNCTION_NAME`, que también carga Terraform.

## Los chequeos

No tiene `verify.py` propio: la invitación se prueba por la puerta del dueño,
`POST /api/org/members`, en la suite `roles` de `make verify`
([`tools/verify`](../../tools/verify/README.md)).
