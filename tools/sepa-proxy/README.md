# Proxy de precios SEPA

API HTTP sin dependencias que busca el catálogo oficial de SEPA y transmite el ZIP de precios solicitado. No guarda el archivo ni lo carga completo en memoria: lo envía por streaming desde `datos.produccion.gob.ar`.

El recurso oficial cambia a diario y solo conserva una ventana de siete días. Por eso `latest` resuelve el ZIP cuyo `last_modified` es más reciente en el catálogo CKAN oficial, en vez de depender de una URL fija.

## Ejecutar

Requiere Node.js 20 o superior.

```bash
npm start
```

La API queda disponible en `http://localhost:3000`. Se puede configurar con `HOST` y `PORT`; ver `.env.example`.

## Exponerla a la ingesta en AWS

La ingesta se baja el ZIP en cada corrida; no hay que descargarlo ni subirlo
antes a S3. En la máquina externa a AWS donde corre este servicio, abrir otro
proceso con `cloudflared`:

```bash
cloudflared tunnel --url http://127.0.0.1:3000
```

El comando informa una URL HTTPS, por ejemplo
`https://<id>.trycloudflare.com`. Esa base, sin `/v1/sepa/precios`, es el
secret `SEPA_API_URL` del repo. Comprobar que responde:

```bash
export SEPA_API_URL=https://<id>.trycloudflare.com
curl --fail --silent --show-error "$SEPA_API_URL/health"
```

El workflow run-ingest usa el secret en cada corrida, así que cargarlo
alcanza para correr la ingesta ya, sin volver a desplegar; el cron diario usa
el que tenía el secret en el último Deploy. Lo mismo desde una máquina con
las credenciales: `make ingest` con `SEPA_API_URL` en el entorno.

El proceso de Node y el túnel deben mantenerse activos durante la ingesta. Un
Quick Tunnel cambia de URL al reiniciarse; para una operación persistente usar
un túnel nombrado y actualizar `SEPA_API_URL` antes de la próxima corrida (y
correr Deploy, para el cron). Sin el proxy, la ingesta falla con un mensaje
que lo nombra, y el resto de la plataforma sigue andando.

## Endpoints

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/health` | Estado del servicio. |
| GET | `/v1/sepa/precios` | Descarga el ZIP más recientemente publicado por SEPA (o el respaldo local si SEPA no responde). |
| GET | `/v1/sepa/precios?dia=lunes` | Descarga el ZIP del día indicado. Acepta lunes a domingo (con o sin tilde). |

La respuesta de `/v1/sepa/precios` tiene `Content-Type: application/zip` y `Content-Disposition: attachment`, por lo que los clientes la pueden guardar directamente. El header `X-Sepa-Origen` indica si el ZIP vino de `sepa` (upstream real) o de `respaldo-local`.

## Respaldo local

Si SEPA no responde (`502` o `504`: catálogo inválido, timeout, conexión rechazada), el proxy sirve un ZIP local desde `sepa_files/` en vez de fallar. Los archivos deben llamarse `sepa_lunes.zip` … `sepa_domingo.zip`; no se generan solos, hay que dejarlos ahí a mano.

- Con `?dia=X`, se sirve `sepa_X.zip`. Si no existe, responde `503`.
- Sin `?dia=` (`latest`), se sirve el ZIP cuya fecha de datos interna (la carpeta `YYYY-MM-DD/` dentro del ZIP) sea más reciente entre los siete disponibles — no el archivo cuyo nombre coincide con el día de hoy, porque SEPA solo conserva una ventana de siete días y el archivo de "hoy" suele contener los datos más viejos del set. Si ningún ZIP es legible, responde `503`.
- Los errores `400` (parámetro `dia` inválido) y `404` (día no publicado por SEPA) nunca activan el respaldo: siempre indican un problema con el pedido, no con la disponibilidad de SEPA.
- Si SEPA ya empezó a transmitir el ZIP y la conexión se corta a mitad de la descarga, el proxy no puede cambiar a un respaldo (los headers ya salieron): la conexión simplemente se corta.
- Cada vez que se sirve un respaldo, el proxy imprime una línea JSON a stderr (`sepa_fallback_served`, y `sepa_fallback_stale` si los datos tienen más de un día) para que quede rastro de que se sirvieron datos congelados.

Se puede configurar o desactivar con variables de entorno; ver `.env.example`.

```bash
curl --fail --location \
  http://localhost:3000/v1/sepa/precios \
  --output sepa-$(date +%F).zip
```

## Comportamiento y seguridad

- Solo hace solicitudes a las URLs HTTPS publicadas por el catálogo oficial de SEPA; no acepta URLs arbitrarias de los clientes.
- Aplica un límite de 30 segundos para recibir los headers de cada conexión con SEPA, pero no corta una descarga que ya empezó: un ZIP grande puede tardar varios minutos en transmitirse.
- Reenvía en streaming, por lo que el proceso no necesita memoria proporcional a ZIPs de cientos de MB.
- Devuelve errores JSON claros: `400` para parámetros inválidos, `502` para respuestas inválidas de SEPA, `504` ante problemas de conexión y `503` si SEPA falló y tampoco hay respaldo local disponible.
- Habilita CORS de solo lectura (`GET, OPTIONS`) para que pueda consumirse desde aplicaciones web.
- El nombre de archivo del respaldo local sale siempre de la lista fija de días válidos, nunca del parámetro `dia` crudo, así que no hay forma de leer archivos fuera de `sepa_files/`.

## Verificación

```bash
npm test
```

## Contenedor

```bash
docker build -t sepa-prices-proxy .
docker run --rm -p 3000:3000 sepa-prices-proxy
```

La imagen no incluye `sepa_files/` (son ~2,3 GB). Para tener respaldo local dentro del contenedor, montarlo como volumen:

```bash
docker run --rm -p 3000:3000 -v "$PWD/sepa_files:/app/sepa_files:ro" sepa-prices-proxy
```

Fuente: [Precios Claros - Base SEPA](https://datos.produccion.gob.ar/dataset/sepa-precios), Secretaría de Comercio de Argentina.
