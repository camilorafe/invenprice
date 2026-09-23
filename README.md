# InvenPrice

Inventario y pricing para pequeñas empresas. **Funciona sin internet, sin servicios en la nube y
sin costo recurrente.** La inteligencia artificial es opcional y corre en la propia máquina; si no
está, un motor de reglas determinista da la recomendación igual.

## Qué hace

- **Inventario auditado:** productos, ventas, entradas, salidas, mermas y ajustes por conteo. El
  stock solo cambia a través de movimientos con usuario, fecha y snapshot.
- **Motor financiero determinista y testeado:** margen bruto y neto, markup (y la diferencia
  entre ambos), punto de equilibrio por producto y del negocio, precio mínimo viable dado un margen
  objetivo, unidades necesarias para el objetivo mensual, ranking de rentabilidad marginal.
- **Multi-moneda offline-first:** cada producto vive en USD, COP o CNY; todo se muestra en las tres.
  Tabla de tasas manual (Modo A) y actualización opcional desde internet con fallback (Modo B).
- **Copiloto de pricing:** modelo local (Qwen2.5 vía Ollama) con ejemplos few-shot de un dataset de
  48 casos de consultoría; validación estricta de la salida; fallback automático a un motor de
  reglas inspeccionable; **guardrail de precio mínimo** que recorta cualquier recomendación por
  debajo del mínimo viable y lo marca en pantalla.
- **Detección de anomalías de inventario:** mermas inusuales por tamaño, frecuencia o usuario;
  ajustes fuera de horario; discrepancias recurrentes entre stock esperado y contado. Estadística
  pura (z-scores, umbrales), con la evidencia numérica de cada alerta.
- **Interfaz web local** simple (Flask + HTML) pensada para un usuario no técnico.

## Instalación

Requisitos: Python 3.11+ (probado con 3.13). Nada más es obligatorio.

```bash
git clone <este-repo> invenprice && cd invenprice
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
python -m invenprice.web.app
```

Abre <http://127.0.0.1:5000>. La base de datos SQLite se crea sola en `data/invenprice.db`
(respáldala copiando ese archivo).

Tests:

```bash
python -m pytest
```

## Modo B de tasas de cambio (opcional)

Por defecto el sistema usa la **tabla manual** (Modo A): edítala en *Tasas de cambio*.

El **Modo B** consulta una API pública gratuita (`open.er-api.com`) para refrescar COP y CNY.

- Activar/desactivar: botón en la página *Tasas de cambio*, o directamente la clave
  `modo_tasas_online` (`"0"`/`"1"`) de la tabla `configuracion`.
- Si no hay internet, la API falla o devuelve datos raros, se conserva la última tasa guardada. El
  sistema **nunca** se detiene ni lanza errores por esto (hay tests que lo verifican).

## Copiloto: instalar el modelo local (opcional)

1. Instala [Ollama](https://ollama.com/download) (Windows, macOS o Linux).
2. Descarga el modelo cuantizado a 4 bits:

   ```bash
   ollama pull qwen2.5:7b-instruct-q4_K_M      # 4,7 GB, mejor calidad
   # o, para equipos modestos:
   ollama pull qwen2.5:3b-instruct-q4_K_M      # 1,9 GB, más rápido
   ```

3. En *Configuración* indica el nombre del modelo y deja "Usar modelo local" en *Sí*.

Si Ollama no está corriendo o el modelo no existe, el copiloto usa el **motor de reglas** al
instante y lo indica en la recomendación. Para fine-tuning ligero (LoRA) gratuito ver
[`docs/FINETUNING_LORA.md`](docs/FINETUNING_LORA.md).

### Benchmark real (máquina de desarrollo)

Medido con `python scripts/benchmark_llm.py` sobre 4 casos del dataset (prompt few-shot completo,
~1.700-2.000 tokens de entrada, salida JSON de ~250-320 tokens). Detalle en
[`docs/benchmark.md`](docs/benchmark.md).

Hardware: **Intel Core i5-8350U** (4 núcleos / 8 hilos, 1,7 GHz) · 16 GB RAM · **sin GPU dedicada** ·
Windows 11 · Ollama 0.34 en CPU, cuantización Q4_K_M.

| | `qwen2.5:7b-instruct-q4_K_M` (4,7 GB) | `qwen2.5:3b-instruct-q4_K_M` (1,9 GB) |
|---|---|---|
| Carga inicial del modelo | ~30 s (una vez; luego queda en memoria) | no medido por separado |
| Latencia por recomendación | **210-286 s (promedio 238 s)** | **87-111 s (promedio 98 s)** |
| Velocidad de generación | 2,9-3,8 tokens/s | 7,5-7,7 tokens/s |
| Salidas JSON válidas | 4/4 | 4/4 |
| Precio igual al del dataset | 4/4 | 4/4 |

Los 4 casos evaluados pertenecen al dataset y probablemente aparecen entre los ejemplos few-shot
del prompt, así que la coincidencia de precio mide adherencia al formato y a las reglas, **no
generalización**. Detalle por caso: [`docs/benchmark.md`](docs/benchmark.md) y
[`docs/benchmark_3b.md`](docs/benchmark_3b.md).

**Caso fuera del dataset** (generalización): precio 27.000, costo 15.000, mínimo 30 %, venta media,
competencia 24.500, restricción "temporada baja", objetivo 3.000.000.

| Fuente | Precio | Margen (recalculado) | Tiempo | Salida válida |
|---|---|---|---|---|
| Motor de reglas | 24.500 | 38,8 % | < 1 ms | — |
| Qwen2.5-3B | 25.500 | 41,2 % | 102 s | sí |
| Qwen2.5-7B | 25.750 | 41,7 % | 248 s | sí |

Los dos modelos quedaron a menos del 5 % del motor de reglas y por encima del mínimo viable (21.429).
Ambos cometieron pequeños errores aritméticos en el texto de la justificación (el 3B escribió
"margen 40,0 %" donde la cifra real es 41,2 %; el 7B calculó 200 unidades donde son 280): por eso
el margen y las unidades que se muestran en la interfaz siempre se recalculan con el motor
determinista y el texto del modelo se presenta como razonamiento, no como fuente de cifras.

**Requisitos mínimos observados:** el modelo 7B necesita ~5 GB de RAM libres y en un CPU de 4
núcleos tarda ~4 minutos por recomendación (consulta puntual, no uso continuo). El 3B necesita ~2,5 GB
y tarda ~1,5 minutos. Con GPU (≥ 6 GB VRAM) ambos bajan a segundos. En equipos más limitados,
desactivar el LLM: el motor de reglas responde en milisegundos y reproduce los 48 casos del dataset
con un desvío medio del 0,25 % respecto a la recomendación experta.

## Copiloto: modo por lotes (default) y "Regenerar ahora"

Con ~4 minutos por recomendación (7B) o ~1,5 (3B) en un equipo sin GPU, el dashboard **no llama al
modelo al cargar páginas**. La decisión es:

- **Por lotes (default).** `python -m invenprice.batch_pricing` recorre el inventario activo, genera
  una recomendación por producto (LLM si está disponible, motor de reglas si no) y la guarda en la
  tabla `recomendaciones`. Se corre bajo demanda o programado, típicamente de noche. Cada producto
  se procesa de forma independiente: un error en uno no detiene el lote.
- **Consulta.** El inicio y la página de cada producto muestran la **última recomendación guardada
  con su fecha** ("hace 3 h", "hace 2 días"), su fuente, si fue ajustada por el guardrail y si el
  texto es del modelo o de plantilla. Si el precio del producto cambió después, se avisa.
- **Regenerar ahora (síncrono).** Botón en la página del producto para forzar una recomendación
  puntual esperando en pantalla; permite fijar velocidad y restricciones a mano. Si el modelo no está
  disponible responde el motor de reglas al instante.

```bash
python -m invenprice.batch_pricing                 # todo el inventario, LLM según configuración
python -m invenprice.batch_pricing --sin-llm       # solo motor de reglas (milisegundos)
python -m invenprice.batch_pricing --solo 3 7      # productos concretos
python -m invenprice.batch_pricing --modelo qwen2.5:3b-instruct-q4_K_M --timeout 300
```

Programarlo cada noche a las 02:00:

```powershell
# Windows (Programador de tareas)
schtasks /Create /SC DAILY /ST 02:00 /TN "InvenPrice pricing" /TR "python -m invenprice.batch_pricing" /F
```

```cron
# Linux/macOS (crontab -e); ajustar la ruta del proyecto
0 2 * * * cd /ruta/invenprice && .venv/bin/python -m invenprice.batch_pricing >> data/batch.log 2>&1
```

## Datos de demostración

```bash
python scripts/demo_seed.py            # 7 productos, 60 días de ventas, alertas de ejemplo
python -m invenprice.web.app
python scripts/smoke_web.py            # prueba end-to-end del servidor real (añade --llm para probar el modelo local)
```

## Arquitectura

```
 Navegador ──► Flask (invenprice/web)  Productos · Rentabilidad · Copiloto · Alertas · Tasas
                       │
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
  copilot.py      anomalies.py       currency.py
  1. LLM local    z-scores,          USD/COP/CNY
  2. validación   umbrales,          Modo A manual
  3. rules.py     horario            Modo B opcional
  4. GUARDRAIL         │                  │
        │              │                  │
        ▼              ▼                  ▼
  finance.py  ◄── funciones puras, testeadas: márgenes, markup, break-even, mínimo viable
        │
        ▼
  db.py + schema.sql ── SQLite (data/invenprice.db)
```

Detalle de esquema y decisiones: [`ARCHITECTURE.md`](ARCHITECTURE.md). Caso de estudio:
[`CASE_STUDY.md`](CASE_STUDY.md).

## Estructura del proyecto

```
invenprice/            paquete principal
  schema.sql, db.py    Fase 1 · esquema y persistencia
  finance.py           Fase 2 · motor financiero
  currency.py          Fase 3 · multi-moneda
  rules.py             Fase 5 · motor de reglas
  copilot.py           Fase 6 · LLM local + fallback + guardrail
  auditoria.py         Fase 6 · verificación de cifras en justificación/riesgo
  batch_pricing.py     Fase 6/8 · recomendaciones por lotes (python -m invenprice.batch_pricing)
  anomalies.py         Fase 7 · anomalías
  web/                 Fase 8 · interfaz Flask
data/pricing_reasoning_dataset.jsonl   Fase 4 · 48 casos de razonamiento experto
scripts/               generar_dataset.py, benchmark_llm.py, exportar_finetune.py
docs/                  benchmark.md, FINETUNING_LORA.md
tests/                 suite pytest (una por fase)
```

## Garantías de auditabilidad

Cada recomendación pasa por tres capas deterministas, en este orden, sin importar si la produjo el
modelo local o el motor de reglas:

1. **Validación de schema.** La salida del LLM debe ser JSON con exactamente los campos
   `precio_recomendado`, `margen_resultante_pct`, `justificacion` y `riesgo`, con tipos válidos. Si
   no, se descarta entera y responde el motor de reglas.
2. **Guardrail de precio mínimo.** Si el precio (de cualquier fuente) queda por debajo del precio
   mínimo viable calculado por `finance.py`, se recorta al mínimo y la pantalla muestra:
   *"Recomendación original (X) ajustada al mínimo viable (Y) por restricción de margen"*.
3. **Auditoría de cifras en el texto** (`invenprice/auditoria.py`). Toda cifra que la
   justificación o el riesgo presentan como hecho (precios, márgenes, unidades, brechas con la
   competencia, cambios porcentuales) se extrae del texto y se compara con los valores reales del
   motor financiero para ese producto, con tolerancia de redondeo. Si alguna no coincide, **el texto
   del modelo no se muestra**: se sustituye por una justificación generada por plantilla con los
   números reales (la misma plantilla del motor de reglas) y se anota qué cifras fallaron. La
   plantilla se regenera siempre con el precio *final* (tras el guardrail), así que tampoco puede
   quedar un texto que hable de un precio que ya fue corregido.

Esto es lo que ocurrió en el caso fuera del dataset del benchmark: el 3B escribió "margen 40,0 %"
(real 41,2 %) y el 7B "200 unidades" (reales 280). Con la auditoría activa ambas justificaciones se
habrían reemplazado por la plantilla, conservando el precio del modelo, que sí era razonable.

Además:

- El margen y las unidades que ve el usuario nunca vienen del LLM: se recalculan con `finance.py`.
- Si el LLM no está, falla, tarda demasiado o responde mal formado, el usuario recibe la
  recomendación del motor de reglas con la explicación de por qué.
- La pantalla de recomendación indica la fuente del precio (`llm_local` / `motor_reglas`) y la del
  texto (`llm_local` / `plantilla`) por separado.
