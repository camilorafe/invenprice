# Caso de estudio · InvenPrice

**Un copiloto de pricing para pequeñas empresas que funciona para siempre con costo cero.**

## El problema

Una pequeña empresa (tienda, taller, distribuidor) toma decisiones de precio con intuición: no
distingue margen de markup, no sabe cuántas unidades necesita vender para cubrir el arriendo y
reacciona tarde a la competencia. Las herramientas que resuelven esto son SaaS con suscripción
mensual, requieren internet estable y, si incorporan IA, cobran por llamada a una API en la nube.

Las restricciones del proyecto eran duras y realistas para ese segmento:

- Presupuesto de una sola sesión de desarrollo y **ninguna inversión posterior**.
- **Cero dependencias de pago** en producción y **cero internet obligatorio**.
- La IA no puede ser un punto único de fallo: si no está, el sistema sigue funcionando.
- Ninguna cifra financiera puede salir de un modelo de lenguaje.

## Decisiones técnicas clave

### 1. Motor de reglas + LLM local, no API en la nube

Una API de LLM en la nube habría dado la mejor calidad de texto el primer día y habría roto las
tres restricciones a la vez: costo por uso, internet obligatorio y dependencia de un tercero. La
alternativa fue **compilar el razonamiento a código**:

1. Se escribió un dataset de **48 casos de pricing** con la calidad de una consultoría real: cada
   caso cita los números exactos (margen, mínimo viable, unidades necesarias) y explica el
   trade-off y el riesgo. El dataset se genera con un script que calcula las cifras derivadas con el
   motor financiero, así que las justificaciones nunca contradicen la aritmética.
2. Ese razonamiento se destiló en un **árbol de reglas ponderadas** (`rules.py`): piso de margen,
   ancla de competencia según velocidad de venta, presión del objetivo de ingreso, restricciones del
   dueño en lenguaje natural ("nunca bajar de 30 %", "temporada baja", "liquidar"), holgura de
   margen, tope gradual de ±15 % y redondeo comercial. Cada regla es una condición explícita que se
   muestra en pantalla. El motor reproduce los 48 casos con un **desvío medio del 0,25 %** (41 de
   48 exactos, máximo 3,5 %) y responde en milisegundos.
3. El **LLM local** (Qwen2.5-7B en 4 bits vía Ollama) se usa como capa de calidad de texto, con los
   casos más parecidos del dataset como few-shot. Su salida se valida contra el mismo schema JSON del
   dataset; cualquier fallo (no es JSON, faltan campos, el servidor no responde) cae al motor de
   reglas sin que el usuario lo note más que por una nota de "fuente: motor de reglas".

Resultado: el sistema tiene **un camino garantizado** (reglas) y **un camino mejorado** (LLM), y el
costo marginal de ambos es cero.

### 2. El guardrail de precio mínimo como garantía de integridad

Los modelos de lenguaje se equivocan en aritmética y pueden recomendar precios por debajo del costo
con una justificación convincente. En lugar de confiar en el prompt, el sistema impone una
**garantía matemática posterior**: el motor financiero determinista calcula el precio mínimo viable
`(costo + gastos variables) / (1 − margen mínimo)` y cualquier recomendación, venga del LLM o de las
reglas, que caiga por debajo se recorta a ese mínimo y se marca en la interfaz:

> Recomendación original ($26.000) ajustada al mínimo viable ($28.000) por restricción de margen (30,0 %).

El margen que ve el usuario también se recalcula con el motor determinista; si el LLM dijo "35 %"
y la aritmética da 34,5 %, se muestra 34,5 % y una nota. Hay tests que fuerzan una recomendación
por debajo del mínimo desde ambas fuentes y verifican la corrección.

El mismo principio se extendió al **texto**: una capa de auditoría extrae toda cifra que la
justificación presenta como hecho (precios, márgenes, unidades, brechas) y la compara con los
valores reales. Si alguna no coincide, el texto del modelo se descarta y se muestra una
justificación por plantilla con los números correctos. La necesidad quedó demostrada en el
benchmark: ambos modelos acertaron el precio pero escribieron márgenes y unidades erróneos.

### 3. Casos borde como estados explícitos, no excepciones

`costo = 0` da margen 100 % y markup *indefinido*; margen unitario ≤ 0 hace el punto de equilibrio
*no alcanzable*; un objetivo ≤ 0 es *objetivo inválido*. Ninguno produce `inf`, `nan` ni un error:
son valores de una enumeración que la interfaz traduce a texto claro. 35 tests del motor financiero
comparan contra valores calculados a mano (costo 10.000 / precio 15.000 → margen 33,3 %, markup 50 %,
mínimo viable al 40 % = 16.667; margen unitario 5.000 y objetivo 2.000.000 → 400 unidades).

### 4. Offline-first en todo

SQLite embebido, Flask con HTML renderizado en servidor, tasas de cambio manuales por defecto y
un Modo B opcional que, ante cualquier fallo de red, devuelve la última tasa guardada. La detección
de anomalías es estadística (z-scores sobre mermas, concentración por usuario, horario, discrepancias
de conteo) y no necesita modelo alguno.

## Resultados de ejemplo

**Caso de referencia del dataset.** Producto a 35.000 con costo 20.300 (margen 42 %), venta lenta,
competencia en 31.000, restricción "nunca bajar de 30 %". Mínimo viable: 29.000. El motor de reglas
recomienda **31.000** (igualar a la competencia, margen 34,5 %) y explica que el objetivo de
5.000.000 exigiría 467 unidades/mes, muy por encima de las ~40 que implica una venta lenta: el
precio no es la palanca del objetivo, la rotación sí. El LLM local dio la misma cifra.

**Guardrail en acción.** Producto con costo 19.600 y margen mínimo 30 % (mínimo viable 28.000). Se
fuerza una recomendación de 26.000: el sistema muestra 28.000, margen 30,0 % y la nota de ajuste.

**Anomalías.** Ocho mermas de 1-2 unidades seguidas de una de 30 registrada por otro usuario:
alerta *alta* con z-score > 3, media histórica y usuario. Tres conteos con faltante en 60 días:
alerta de discrepancia recurrente con el faltante acumulado.

**Benchmark del LLM local** en un portátil sin GPU (i5-8350U, 16 GB): 4/4 salidas válidas, pero
~4 minutos por recomendación (el modelo 3B, ~1,5 minutos). En un caso fuera del dataset ambos modelos
quedaron a menos del 5 % del motor de reglas, pero con errores aritméticos en el texto, lo que
confirma la decisión de recalcular siempre las cifras con el motor determinista. Conclusión honesta: en ese hardware el LLM es una consulta puntual y
el motor de reglas es el modo de uso diario; con una GPU modesta la latencia baja a segundos. El
proyecto documenta cómo hacer fine-tuning LoRA gratis en Colab para mejorar el modelo después.

## Cifras del proyecto

| | |
|---|---|
| Fases / commits | 9, uno por fase (el proyecto es utilizable desde la fase 2) |
| Tests | 255, todos en verde; CI en GitHub Actions (Ubuntu + Windows, Python 3.11 y 3.13) |
| Dependencias de producción | Flask (la BD, el motor y la IA local no requieren nada más) |
| Costo recurrente | $0 |

## Qué haría después

- Alimentar el dataset con casos reales anonimizados del negocio y re-derivar los pesos del motor
  de reglas a partir de ellos.
- Ejecutar el fine-tuning LoRA documentado y comparar contra el modelo base con el benchmark.
- Añadir estacionalidad medida (ventas por mes del año anterior) como señal explícita del motor, en
  lugar de depender de que el dueño la escriba como restricción.
