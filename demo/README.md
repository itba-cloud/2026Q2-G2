# demo · listas de precios de prueba

Dos CSV para probar la **carga propia** sin inventar datos a mano: la lista
de precios de un comercio que no publica en el SEPA. Se suben desde el
portal, en *Cargar precios*, con cualquier usuario de una organización (no
hace falta ser `owner`). No dependen del proxy del SEPA.

| Archivo | Filas | Qué muestra |
|---|---|---|
| `lista_precios_dia_1.csv` | 45 | El surtido inicial, un **7 % por debajo** del competidor de al lado |
| `lista_precios_dia_2.csv` | 47 | El mismo surtido con movimientos, más 2 filas malas a propósito |

## Cómo usarlos

1. En el portal, **Cargar precios**. La primera vez pide la sucursal propia
   (*Primero, tu sucursal*): nombre, un punto en el mapa, localidad y
   provincia. Para que tenga competidores con estos precios, marcarla **en
   CABA, cerca de Almagro**.
2. Subir `lista_precios_dia_1.csv` con fecha **2026-09-07**.
3. Subir `lista_precios_dia_2.csv` con fecha **2026-09-08**.

Cada archivo va directo del navegador a S3 (`raw/uploads/<upload_id>/`), con
un POST que firma la API; desde ahí sigue el mismo camino que el archivo de
una cadena del SEPA: la SQS de jobs, un worker de Fargate, Postgres. *Cargas
anteriores* se actualiza sola mientras haya una carga sin terminar; los
workers arrancan desde 0, así que la primera tarda unos minutos.

El orden y las fechas importan: `detect_changes()`
([`app/batch/worker/main.py`](../app/batch/worker/main.py)) compara contra
la carga anterior *de la misma cadena*, así que sin el día 1 cargado el
día 2 no produce ningún cambio de precio (`price_change`).

## Lo que no se ve enseguida

**La comparación necesita a los vecinos.** Los pares de sucursales cercanas
(`branch_neighbor`) los reconstruye entera el cierre del día, pero la
sucursal propia no espera a eso: al darla de alta (o moverla) en *Primero,
tu sucursal*, la API le calcula sus vecinos en el momento
([`app/api`](../app/api/README.md)). La primera carga ya se compara contra la
competencia que tenga dentro del radio.

Además, para que haya precios con qué comparar, la base tiene que tener
cargados los de los competidores (las cadenas del SEPA) de esos días.

## De dónde salen los números

Los 45 productos son **reales** y elegidos con dos criterios, no al azar:

- **Los listan 6 cadenas distintas o más.** "En cuántas cadenas aparece" se
  cuenta sobre `product_name`, que tiene una fila por (ean, cadena) y es chica;
  agrupar ahí es barato, a diferencia de agrupar `price`, que tiene decenas de
  millones de filas. Un producto que 14 cadenas listan lo va a tener
  cualquier vecino, y eso es lo que hace que la comparación devuelva filas en
  lugar de una pantalla vacía.
- **Precios entre $500 y $20.000.** La primera versión de estos archivos salió
  ordenando por precio descendente y quedaron rarezas por kilo —"MOPA SPRAY",
  "TRUCHA SEMI DESPINADA X KG" a $27.899— que casi ningún competidor tiene y
  que además quedan ridículas en una demo.

El precio de referencia es el que tenía `Supermercados DIA · 689 - ALMAGRO`
el 2026-09-08.

Sobre esa base:

- **Día 1** = precio del vecino × 0,93. El comercio aparece más barato en casi
  todo.
- **Día 2** = 18 subas del 12-19 %, 16 bajas del 7-14 % y 11 sin cambio. Los
  movimientos superan con margen el piso de `CHANGE_FLOOR_PCT` (0,5 %) que el
  worker usa para guardar un cambio de precio.

## Las dos filas malas del día 2

Están a propósito, y son 2 de 47 (4,3 %), por **debajo** del
`REJECT_THRESHOLD` del 5 %:

```
123,1500.00,CODIGO DE BARRAS CORTO (se rechaza),
7790040999999,ochocientos,PRECIO NO NUMERICO (se rechaza),
```

Así la carga termina en `done` con 2 filas rechazadas en lugar de fallar
entera, y su detalle en *Cargas anteriores* muestra cada fila rechazada con
su línea y su motivo (`GET /uploads/{id}/rejected`, que lee
`rejected/uploads/<upload_id>/`). Para ver una carga **fallando**, duplicar
esas dos filas hasta pasar el 5 % del archivo.

## El formato, por si hay que armar otro

[`app/batch/worker/upload.py`](../app/batch/worker/upload.py) pide un CSV con
encabezado y dos columnas obligatorias:

```
ean,price[,description,brand,presentation]
```

`ean` tiene que ser 13 dígitos y distinto de cero; `price` un número mayor a
cero. Todo lo que no cumpla eso se rechaza con el número de línea y el
motivo, y cuenta para el `REJECT_THRESHOLD`. S3 acepta archivos de hasta
50 MB.
