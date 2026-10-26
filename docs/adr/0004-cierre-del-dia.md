---
status: accepted
---

# Cómo sabe el cierre del día que terminó la última cadena

La arquitectura dice que la Lambda de cierre marca la cadena en `feed_day` y
gana el cierre con `UPDATE … SET closed_at = now() WHERE closed_at IS NULL`
«y no quedan jobs pendientes de ese día». Al implementarlo (ticket 11)
quedaron dos preguntas abiertas: qué es un job pendiente, y qué pasa con una
cadena que llega después del cierre.

## Decisión

- **Las cadenas del día son los archivos de `raw/`.** El cierre lista
  `raw/feed_date=<día>/chain_id=<n>/` y cierra cuando cada una de esas
  cadenas tiene su job en `done` o `failed`. `ingest_job` sola no alcanza:
  el worker crea el job recién cuando recibe el mensaje de la cola, así que
  con un worker y veinte cadenas, al terminar la primera las otras
  diecinueve no tienen fila y el día se cerraría con una. Un archivo en
  `raw/` es un job por definición (el único camino que crea jobs), y S3
  lista con consistencia fuerte.
- **`feed_day_chain` guarda las cadenas que entraron en el día**: las marca
  el aviso de cada una y el cierre, a todas las que encontró terminadas.
- **Una cadena que llega tarde reabre el día y lo vuelve a cerrar.** El
  aviso de una cadena que el día no tenía marcada, con el día cerrado, pone
  `closed_at = NULL` y cierra otra vez en el acto, con lo que haya, como el
  cron: el mantenimiento y el precálculo vuelven a correr con esa cadena.
  Pasa cuando el cron de respaldo cerró sin ella, o cuando entra una cadena
  a un día ya cerrado. No espera a las que siguen cargando: si una no
  termina nunca, el día quedaría abierto para siempre, porque el cron solo
  mira el día de su fecha. Un aviso repetido de una cadena ya marcada no
  reabre nada.
- **El cron de respaldo** cierra el día de su fecha en Buenos Aires con lo
  que haya, si tiene algo en `raw/`.

## Consecuencias

- «Una sola vez» es por cada vez que el día se completa: cada cadena que
  llega tarde corre el mantenimiento y encola el precálculo de nuevo.
  En el día normal (15 a 20 cadenas, una ingesta) es una vez.
- El mantenimiento sale del worker (antes corría cuando la cola se vaciaba):
  los vecinos y el catálogo se reconstruyen al cerrarse cada día. Las
  suites de `make verify` que cargan sucursales nuevas con la fecha de hoy
  reabren el día y lo cierran.
- El cierre necesita leer el bucket de datos: corre en la VPC y lista `raw/`
  por el gateway endpoint de S3.
- Si un worker terminara una cadena mientras la ingesta todavía sube las
  demás, el día se cerraría antes, y cada cadena siguiente lo reabriría. La
  ingesta sube todo en segundos y un worker tarda minutos, así que no se
  agrega un manifiesto de la ingesta para evitarlo.
- Reprocesar una cadena de un día ya cerrado no lo reabre: la cadena ya está
  marcada. El admin de plataforma puede forzar el mantenimiento.

## Alternativas descartadas

- **Mirar la cola de jobs** (`ApproximateNumberOfMessages` en cero): es
  aproximada, mezcla días y cargas, y no dice de qué día es cada mensaje.
- **Un manifiesto de la ingesta** con las cadenas del día: exacto, pero
  agrega un archivo más y un contrato entre la ingesta y el cierre para un
  caso que no ocurre en la práctica.
- **No reabrir**: una cadena que llega después del cron, o las sucursales
  nuevas de un día ya cerrado, quedarían sin vecinos ni precálculo hasta el
  cierre del día siguiente.
- **Reabrir y esperar a que el día esté completo**: un día que el cron cerró
  sin una cadena que nunca termina no se volvería a cerrar.
