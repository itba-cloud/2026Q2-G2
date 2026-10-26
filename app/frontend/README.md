# app/frontend · el portal

El tablero, el comparador, el mapa del conjunto competitivo, las alertas y la
carga propia. **No calcula nada**: cada pantalla le pide a la API
([`app/api`](../api/README.md)) lo que necesita y muestra lo que vuelve.

```bash
cd app/frontend
npm ci
npm run dev
```

Queda en `http://localhost:5173`. Para el bucket de producción, `npm run build`
genera estáticos en `dist/` — no hay SSR ni router del lado del servidor —, y
Deploy los sube (`make portal-publish`).

## De dónde sale la URL de la API: `config.json`

El build es el mismo para cualquier cuenta: no lleva ninguna URL adentro.
Antes del primer render, `main.tsx` baja `config.json`, que está al lado de
`index.html` en el bucket y que escribe **Terraform** (`infra/modules/frontend`):

```json
{
  "apiUrl": "https://<id>.execute-api.us-east-1.amazonaws.com",
  "authUrl": "https://lgdc-<cuenta>.auth.us-east-1.amazoncognito.com",
  "clientId": "<client id de Cognito>",
  "redirectUri": "https://<id>.execute-api.us-east-1.amazonaws.com/auth/callback",
  "logoutUri": "https://lgdc-portal-<cuenta>.s3.us-east-1.amazonaws.com/index.html"
}
```

`src/config.ts` lo carga y `portalConfig()` lo lee. `api/client.ts` hace
`fetch(apiUrl + "/api" + path)`: el portal (su bucket) y la API (API
Gateway) son dos orígenes, y el CORS lo resuelve API Gateway. El client ID no
es secreto; el secret vive solo en la Lambda del callback.

Para `npm run dev`, copiá el `config.json` del bucket a `public/config.json`
(no se commitea). Con `apiUrl` vacío las llamadas quedan relativas y las toma
el proxy de Vite (`VITE_API_PROXY_TARGET`). El login, eso sí, vuelve siempre al portal
desplegado: el callback redirige a la URL del bucket, no a `localhost`.

## Se sirve por HTTPS desde S3, con HashRouter

Deploy (`make portal-publish`) arma `dist/` y lo sincroniza al bucket
`lgdc-portal-<cuenta>`, sin tocar `config.json`. El portal se abre por el
REST endpoint de S3 (`https://lgdc-portal-<cuenta>.s3.us-east-1.amazonaws.com/index.html`),
que es HTTPS; el website endpoint solo habla HTTP, y por ahí viajaría el
token. Como S3 sirve un solo `index.html` y nada más, las rutas van en el
fragmento (`index.html#/branches`): `HashRouter`.

## El login es de Cognito

No hay formulario de contraseña. `/login` manda el navegador a la Hosted UI
(`/oauth2/authorize`, o `/signup` para crear una cuenta) con un `state` al
azar guardado en `sessionStorage`. Cognito vuelve a la Lambda del callback
([`app/auth_callback`](../auth_callback/README.md)), que canjea el código y
redirige a `#/auth/callback?id_token=…&state=…`. `AuthCallbackPage`
compara el `state`, guarda el ID token y pide `GET /api/auth/me`.

- **Primer login**: el usuario existe pero sin organización (`org_id` null).
  `ProtectedRoute` lo manda a `/onboarding`, que pide el nombre de la
  organización (`POST /api/me/org`) y lo deja como `owner`.
- **Un miembro invitado** entra por el mismo "Ingresar", con la contraseña
  temporal que le mandó Cognito: la Hosted UI le pide una nueva y sigue el
  login de siempre. Ya tiene organización (la del dueño que lo invitó), así
  que no pasa por el onboarding.
- **Cerrar sesión** borra el token y pasa por `/logout` de la Hosted UI, para
  cerrar también la sesión de Cognito; si no, "Ingresar" volvería a entrar
  con el mismo usuario sin preguntar.
- **El token dura 8 horas.** El portal no guarda refresh token: cuando vence,
  la API responde 401 y el portal vuelve a `/login`.

## Los precios llegan como string

La API serializa `NUMERIC` (`price`, `radius_km`, `pct_over_min`, etc.)
como string en el JSON, a propósito — ver [`app/api/README.md`](../api/README.md). `types.ts`
los declara `string`, no `number`, para que TypeScript obligue a pasar por
`lib/format.ts` antes de mostrarlos o compararlos. Nadie debería hacer
`row.price - row.min_price` a mano: `toNumber()` es el único lugar que hace
ese `parseFloat`.

## La carga no pasa por la API

`api/client.ts` expone `postToPresignedUrl()`: un `fetch` sin el prefijo
`/api` y sin token, con un formulario (los campos de la política firmada y,
al final, el archivo), porque un POST prefirmado de S3 no es una ruta de la
API, es una URL absoluta que hay que golpear directo. `uploadPriceList()` en
`endpoints.ts` hace los dos pasos: pedir el POST (`POST /uploads`) y mandar
el archivo a S3. No hay un tercero: S3 avisa a la cola de jobs, y el worker
que toma el archivo crea el job y lo vincula a la carga. Cargar precios
consulta `GET /uploads` cada 4 segundos mientras haya una carga sin cerrar,
y una carga con filas rechazadas (o que falló) abre su detalle con el motivo
y cada fila rechazada (`GET /uploads/{id}/rejected`). S3 limita el archivo a
50 MB y el portal traduce ese rechazo. El `fetch` normal y este quedan
separados a propósito, para que nadie mande un bearer token a un bucket de
S3 por accidente.

## El 401 desloguea desde cualquier pantalla

`client.ts` expone `setUnauthorizedHandler()`, que `AuthContext.tsx` registra
una sola vez al montar la app. Así el módulo de fetch puede reaccionar a un
401 — token vencido, sesión revocada — sin importar `react-router-dom`: solo
llama al handler que le dejaron, y quien lo dejó es el único que sabe
navegar.

## Qué es cada pantalla

| Ruta | Qué muestra |
|---|---|
| `/login` | Ir a la Hosted UI de Cognito, a ingresar o a crear una cuenta |
| `/auth/callback` | Donde vuelve el login con el token; lo guarda y sigue |
| `/onboarding` | El primer login: el nombre de la organización |
| `/` | Tablero: comparados, más baratos/caros, peor posicionados, movimientos recientes |
| `/branches` | Mis sucursales: buscar sucursales del SEPA y seguirlas con un radio |
| `/branches/:c/:b/:s` | Mapa del conjunto competitivo, radio ajustable en vivo, comparación producto a producto |
| `/products` | Mis Precios: el surtido propio contra el mínimo del radio (paginado por cursor, ver abajo) y, en otra pestaña, la búsqueda en el catálogo unificado del SEPA |
| `/products/:ean` | Ficha de un producto, con su historial de precio |
| `/compare` | Comparar: la sucursal propia contra las que se elijan, una por una, estén o no dentro de su radio |
| `/alerts` | Feed de alertas y ABM de reglas de vigilancia |
| `/upload` | Carga propia: subida directa a S3, el estado de cada carga y sus filas rechazadas |
| `/account` | Tu usuario, la sucursal propia y, solo para el `owner`, los miembros de la organización con el formulario para invitar por email (`MembersCard`) |
| `/pipeline` | Los `ingest_job` por día y cadena (con su nombre), los del SEPA y los de las cargas propias — **fuera de la navegación del comercio** |

`/pipeline` no aparece en la lista principal: que la cadena 24 haya parseado hoy
no dice nada sobre los precios de un comercio. Vive en un grupo aparte,
"Administración", visible solo con rol `owner`, y `ProtectedRoute
requireOwner` manda a un `member` que la escribe a mano de vuelta al tablero.
Igual con la tarjeta de miembros de `/account`. Esconder no es proteger: lo
que decide es la API, que le responde 403 a un `member` en
`/ingest/jobs` y `/org/members`.

## La UI se arma de un vocabulario chico

`components/ui.tsx` tiene las piezas —`Card`, `PageHeader`, `Field`, `Button`,
`TableFrame`, `StatTile`, `StackedBar`, `Pagination`, los esqueletos de carga— y
`index.css` los tokens. La razón no es estética: es que las decisiones de
accesibilidad se toman una vez y no nueve. `Field` asocia el `<label>` por
`htmlFor` y cablea el `hint` por `aria-describedby`; `Button` trae el objetivo
táctil de 44px por defecto; `TableFrame` exige un `<caption>` y hace scroll
horizontal en un teléfono en vez de recortar. El shell es una columna con la
navegación en un drawer hasta `lg`, y un riel fijo de ahí para arriba.

## Los colores de los gráficos están validados, no elegidos

Las tres series (`--color-series-*`) pasan los pisos de separación para daltonismo
y para visión normal contra la superficie clara, en todos los pares. El aqua queda
por debajo de 3:1 de contraste, así que todo gráfico que lo use lleva etiquetas
directas y una tabla con los mismos números (`PriceHistoryChart` la trae en un
`<details>`).

Dos consecuencias concretas: la distancia de los competidores en el mapa usa una
rampa de un solo tono (`icons.ts`), no un barrido de matiz de rojo a azul, porque
la distancia es una magnitud continua y un arcoíris la lee como cuatro categorías;
y el reparto de posición del tablero usa el par divergente azul↔rojo con gris al
medio, no verde↔rojo. La dirección de un cambio nunca depende del color: siempre
va con `▲`/`▼` y con el signo.

## Paginación

Los endpoints que aceptan `offset` (`/branches`, `/alerts`) se paginan con
`Pagination`. La API no devuelve un total, así que no hay "página N de M" ni
salto al final: se ofrece "siguiente" solo cuando la página volvió completa,
que es lo único que una página corta dice con certeza.

Mis Precios (`/comparison`) pagina **por cursor**, con `CursorPagination`:
cada respuesta trae `next_cursor`, opaco, que pedido como `cursor` devuelve la
página siguiente del mismo día, y es `null` en la última. La pantalla guarda
los cursores que recorrió, y "Anterior" vuelve al de antes. No le pasa
`feed_date`: la API contesta el último día completo del precálculo (o el
último día, en vivo, si la sucursal todavía no tiene precálculo, y entonces
la pantalla lo dice) y la respuesta trae qué día es. El orden lo pone la API
(peor posicionados primero) y los encabezados reordenan **solo la página
visible** — está dicho en la pantalla, porque un orden que miente sobre el
conjunto es peor que no tenerlo.

El tablero y Mis Precios reciben además `source` (`precomputed` o `live`): de
dónde salió la respuesta ([`app/api`](../api/README.md)).

`/products` no se pagina: `GET /products` acepta `limit` pero no `offset`.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | A dónde reenvía el proxy de Vite `/api` |

Es la única variable, y solo la usa el servidor de desarrollo. Lo que cambia
por despliegue está en `config.json` (ver arriba).

## Los chequeos

```bash
cd app/frontend
npm run lint
npm run build
```

No hay un harness de checks como en los otros componentes: `tsc -b` ya es la
prueba de que el contrato con la API (`types.ts`, `api/endpoints.ts`) tipa
contra lo que declaran los componentes, y `vite build` es la prueba de que
Tailwind v4, el CSS de Leaflet y los `manualChunks` de `vite.config.ts`
(leaflet y recharts en su propio chunk, para que un deploy de código de la
app no invalide su caché en el navegador) arman un `dist/` real.

## Lo que falta

- **Sin tests de componentes.** El typecheck y el build son la única red.
- **Los precios imposibles se tapan, no se arreglan.** Unas 2.100 filas de 14,5
  millones traen precios de $0,01, casi seguro marcadores de "no se vende". Una
  sola alcanza para ser el mínimo de un conjunto competitivo y convertir la
  comparación en un `+58.689.900%`. `lib/format.ts` los detecta
  (`isImplausibleMin`) y la fila queda marcada "sin comparar" en lugar de
  mostrar el número. El arreglo de fondo es excluirlos en la consulta de la
  API, no en la presentación.
- **El ID token vive en `localStorage`.** Expuesto a XSS si algún día se suma
  contenido de terceros a la página; lo correcto sería una cookie
  `HttpOnly`, que a su vez obliga a CSRF y a que la API maneje sesión.
- **Sin i18n.** Todo el texto está en español, hardcodeado.
