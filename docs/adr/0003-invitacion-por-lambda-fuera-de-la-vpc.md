---
status: accepted
---

# La invitación crea el usuario de Cognito desde una Lambda fuera de la VPC

Invitar a un miembro es escribir en dos lados: la fila del miembro en Postgres, que solo se alcanza desde la VPC, y su usuario en Cognito (`AdminCreateUser`, que le manda la contraseña temporal). La Lambda de la API corre en la VPC, y la VPC no tiene salida a internet. Lo natural sería un interface endpoint de `cognito-idp`, pero [Cognito no lo admite para un user pool con dominio](https://docs.aws.amazon.com/cognito/latest/developerguide/vpc-interface-endpoints.html): «user pools with domains assigned are incompatible with AWS PrivateLink», y todo request a la API del pool que pasa por el endpoint falla. El nuestro tiene dominio: es el de la Hosted UI, de la que depende el login.

Por eso la parte de Cognito la hace una Lambda propia, `lgdc-invite` ([`app/invite`](../../app/invite/README.md)), **fuera de la VPC** como el callback, y la API la invoca de forma sincrónica por un interface endpoint de `lambda`, que se suma al set de `interface_endpoint_services`. La API abre una transacción, inserta la fila del miembro (si el email ya está en una organización es un 409 antes de tocar Cognito), invoca la Lambda y, según la respuesta, guarda el `sub` o hace rollback. La Lambda crea el usuario y responde `created` con su `sub`; si Cognito ya lo tenía, `exists` con el `sub` del existente, y si rechaza el email, `invalid`.

## Consecuencias

- Son ocho Lambdas y no siete: `lambda_function` se instancia una vez más, y `docs/arquitectura.md` lo dice. La spec del TP3 queda como estaba; esto la amplía.
- Un interface endpoint más (`lambda`), con su costo por hora en las 2 AZ, igual que los otros seis.
- Son dos sistemas sin transacción común, así que puede quedar un usuario de Cognito sin fila: la Lambda lo creó y la respuesta se perdió (un timeout) o el commit falló. Para eso la invitación de un email que Cognito ya tiene lo enlaza en vez de fallar: la API reintenta una vez por su cuenta, y si no, la próxima invitación del dueño lo arregla. El mismo camino suma a quien se registró solo y nunca nombró una organización. Ese usuario no recibe email: entra con la contraseña que ya tiene.
- El admin de plataforma no pasa por acá: lo da de alta Deploy con la CLI (`scripts/bootstrap-platform-admin.sh`), desde fuera de AWS.

## Alternativas descartadas

- **Interface endpoint de `cognito-idp`**: no funciona con el dominio de la Hosted UI (ver arriba), y sacar el dominio es perder el login.
- **Asincrónica por SQS** (la API encola el email y una Lambda fuera de la VPC crea el usuario): no suma un endpoint, porque el de SQS ya está, pero el dueño no se entera en el momento de que el email no sirve o ya tiene cuenta, y la fila queda creada aunque Cognito la rechace.
- **La API fuera de la VPC**: perdería el acceso a la base por el RDS Proxy.
- **Un NAT Gateway**: es justo lo que la red evita ([arquitectura](../arquitectura.md), Red).
