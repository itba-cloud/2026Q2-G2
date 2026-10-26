# app/auth_callback · el callback del login

La Lambda detrás de `GET /auth/callback`, la ruta pública de la HTTP API
(`infra/modules/api`). Es el único lugar que conoce el client secret de
Cognito. `make lambdas` la empaqueta en `build/auth_callback.zip`; el handler
es `main.handler` y no tiene dependencias: usa solo la biblioteca estándar.

## El login, de punta a punta

```
portal          "Ingresar" → Hosted UI /oauth2/authorize?…&state=<al azar>
Hosted UI       el usuario se loguea (o se registra en /signup)
Cognito         302 → <api>/auth/callback?code=…&state=…
auth_callback   POST <hosted ui>/oauth2/token con client_id:client_secret (Basic)
                302 → <portal>#/auth/callback?id_token=…&expires_in=…&state=…
portal          compara el state con el que guardó, guarda el ID token,
                GET /api/auth/me (el primero crea el usuario sin organización)
```

- **El secret no llega al navegador**: el código se canjea acá, contra el
  token endpoint de Cognito. Por eso la Lambda corre **fuera de la VPC**: ese
  endpoint está en internet y la VPC no tiene salida.
- **Los tokens no viajan a ningún servidor**: van en el fragmento de la URL
  (`#…`), que el navegador no manda en el request a S3. El portal es HTTPS
  (el REST endpoint de S3), así que tampoco viajan en claro. El `Location` va
  con `Cache-Control: no-store` y `Referrer-Policy: no-referrer`.
- **Solo el ID token**: es el que trae `email`, y el que la API acepta. El
  refresh token no se pasa: cuando el ID token vence (8 horas), el portal
  vuelve a la Hosted UI.
- **El `state`** lo inventa el portal y lo guarda en `sessionStorage`; la
  Lambda lo devuelve tal cual. Un `state` que no coincide es un login que no
  empezó en ese navegador, y el portal lo descarta.

Si algo sale mal (Cognito devuelve `error`, falta el código, el código venció
o ya se usó, el token endpoint no responde), la Lambda manda al navegador a
`<portal>#/login?error=<motivo>` y la pantalla de login lo muestra.

## Configuración

Todas las pone Terraform (`infra/main.tf`, `module "auth_callback_function"`).

| Variable | Para qué |
|---|---|
| `COGNITO_URL` | La base de la Hosted UI, donde está `/oauth2/token` |
| `CLIENT_ID` · `CLIENT_SECRET` | El app client del portal |
| `REDIRECT_URI` | Esta misma ruta: el token endpoint exige el mismo `redirect_uri` que recibió el authorize |
| `PORTAL_URL` | A dónde vuelve el navegador: `https://lgdc-portal-<cuenta>.s3.<región>.amazonaws.com/index.html` |

## Los chequeos

No tiene `verify.py` propio: el login de punta a punta (el formulario de la
Hosted UI, el canje del código y la vuelta al portal con el token) lo chequea
la suite `portal` de `make verify` ([`tools/verify`](../../tools/verify/README.md)),
contra el stack desplegado.
