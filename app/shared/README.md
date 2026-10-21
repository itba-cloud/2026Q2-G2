# app/shared · lo que usan todos

Los módulos que no son de ningún componente en particular:

| Archivo | Qué es | Quién lo usa |
|---|---|---|
| [`common.py`](common.py) | La configuración (todo por variable de entorno), el cliente de AWS y la conexión a la base | Todos |
| [`maintenance.py`](maintenance.py) | Los dos rebuilds del catálogo que no van por job: `refresh_neighbors` (el conjunto competitivo) y `normalize_catalog` (la búsqueda por trigramas) | La Lambda de cierre del día, una vez por día, y la API en `POST /admin/*` |
| [`comparison.py`](comparison.py) | La comparación de una sucursal contra su conjunto competitivo: la consulta (`COMPARISON_BASE` y sus piezas), una página (`page`), el resumen (`summary`) y el tablero (`dashboard`, que con `limit=None` da el ranking entero) | La API, en vivo, y la Lambda del precálculo, sin `LIMIT` |
| [`precomputed.py`](precomputed.py) | Todo lo del precálculo que usa más de un componente: la sucursal seguida (`FollowedBranch`), cuáles hay (`followed_branches`), el mensaje de su cola (`queue`), y la tabla DynamoDB `lgdc-comparison` (`ComparisonTable`): sus claves, la escritura de un día como generación (filas, resumen y `META`, en ese orden, y `META` solo hacia adelante) y su lectura paginada por rango | El cierre del día y la API encolan; la Lambda del precálculo escribe; la API lee |

`maintenance.py`, `comparison.py` y `precomputed.py` están acá y no en
`app/api` porque los usan una Lambda de `app/batch` y la API, y ninguna de
las dos es dueña de la otra. Las claves de la tabla viven solo en
`precomputed.py`: quien escribe y quien lee no pueden discrepar.

## Cómo llega a cada componente

No es un paquete: los componentes hacen `from common import ...` e
`import maintenance` como módulos sueltos. Cada artefacto los copia al lado
de su propio código:

- **Las Lambdas**: `scripts/build-lambda.sh` arma un zip plano con estos
  módulos, los del componente y sus dependencias.
- **La imagen de los workers**: [`app/batch/Dockerfile`](../batch/Dockerfile)
  copia estos módulos y los del worker a `/app`.
- **Desde el repo**, para correr algo en tu máquina: `PYTHONPATH=app/shared`.

Las dependencias están en [`requirements.txt`](requirements.txt), y el
`requirements.txt` de cada componente empieza por este.
