---
status: accepted
---

# El precálculo escribe por generaciones, y solo hacia adelante

La spec dice que el precálculo escribe en `lgdc-comparison` las filas
(`ROW#<feed_date>#R<ranking>`), después el resumen (`SUM#<feed_date>`) y al
final `META`, que la API pagina Mis Precios con `LastEvaluatedKey` y que, si
la comparación no está **o el radio pedido no es el configurado**, la API
responde en vivo y encola el precálculo. Al implementarlo (ticket 12)
aparecieron tres problemas con eso tal cual:

- **Un mismo día se reescribe seguido**: la carga de un comercio, un día que
  el cierre reabre por una cadena tardía, una carga borrada. Pisar las filas
  `ROW#<día>#R…` a las que `META` ya apunta deja ver, durante la escritura,
  filas nuevas con el resumen viejo, y una página siguiente puede repetir o
  saltear productos: un día «a medio calcular», justo lo que la spec prohíbe.
- **`META` con TTL se olvida del día más nuevo.** Cuando el TTL lo borra,
  cualquier entrada vieja de la cola (las hay: `make verify` cierra días de
  hace meses, y cada cierre encola todas las sucursales seguidas) pasaría la
  condición «no existe» y dejaría un día viejo como vigente.
- **Encolar otro radio no sirve**: solo el radio configurado se lee de
  DynamoDB (la arquitectura: «el radio sigue siendo libre: solo el radio
  configurado de cada sucursal sale de DynamoDB»), y el cierre del día no
  vuelve a encolar ese otro radio, así que su precálculo quedaría viejo.

## Decisión

- **Cada escritura de un día es una generación** (el momento en que se
  escribe, en milisegundos): sus filas van en
  `ROW#<feed_date>#<generación>#R00001…`. `META` nombra el día **y** la
  generación, y lleva el resumen. La API lee `META` y después las filas de
  esa generación: el cambio de un día a otro, o de una escritura a otra del
  mismo día, es la escritura de un solo item. `SUM#<feed_date>` queda como
  historial, un item por día (lo va a leer Tendencias, TP4).
- **`META` no vence.** No tiene `expires_at`, así que nunca se borra y
  siempre sabe cuál es el día más nuevo; guarda hasta cuándo duran sus filas
  (`rows_expire_at`) y, pasado eso, la API lo trata como ausente y responde en
  vivo. Una entrada de un día más viejo que el de `META` no calcula nada, y
  `META` se escribe con la condición `feed_date <= nuevo`.
- **El cursor de Mis Precios es nuestro, no `LastEvaluatedKey`**: un JSON en
  base64url con el día, la generación y el ranking de la última fila
  (precalculado) o el offset (en vivo). Es opaco igual, no expone la clave de
  la tabla, sirve también para las páginas en vivo y deja validar lo que
  llega. La página siguiente es un `Query` por rango desde la fila que sigue.
- **Se encola solo el radio configurado**, cuando no tiene `META`, cualquiera
  sea el radio que se pidió. Otro radio responde en vivo y no encola nada.
  Las respuestas en vivo sin `feed_date` usan el día de `META` si lo hay, para
  que cambiar de radio no cambie de día.

## Consecuencias

- Reescribir un día deja las filas de la generación anterior hasta que vence
  su TTL (7 días): más almacenamiento, del orden de una partición por
  reescritura. Una página que alguien recorre sigue leyendo su generación
  aunque `META` ya apunte a otra.
- Un item `META` por sucursal seguida y radio queda para siempre, aunque
  nadie la siga más: unos bytes cada uno.
- El `SK` de las filas no es el de la spec (`ROW#<feed_date>#R…`): lleva la
  generación en el medio.
