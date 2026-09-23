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

| Hardware | Intel Core i5-8350U (4 núcleos / 8 hilos, 1,7 GHz) · 16 GB RAM · **sin GPU dedicada** · Windows 11 |
|---|---|
| Modelo | `qwen2.5:7b-instruct-q4_K_M` (Ollama 0.34, CPU) |
| Carga inicial del modelo | ~30 s (una vez; luego queda en memoria) |
| Latencia por recomendación | **210-286 s (promedio 238 s)** |
| Velocidad de generación | 2,9-3,8 tokens/s |
| Salidas JSON válidas | 4/4 |
| Precio coincidente con el dataset | 4/4 (los casos evaluados forman parte del dataset few-shot, así que esto mide adherencia al formato y a las reglas, no generalización) |

**Requisitos mínimos observados:** 8 GB de RAM libres para el modelo 7B (≈5 GB residentes) y un
CPU de 4 núcleos dan ~4 minutos por recomendación, utilizable solo como consulta puntual. Con GPU
(≥ 6 GB VRAM) la misma consulta baja a segundos. En equipos más limitados usar el modelo 3B o
desactivar el LLM: el motor de reglas responde en milisegundos y cubre todos los casos del dataset
con un desvío medio del 0,25 % respecto a la recomendación experta.

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
  anomalies.py         Fase 7 · anomalías
  web/                 Fase 8 · interfaz Flask
data/pricing_reasoning_dataset.jsonl   Fase 4 · 48 casos de razonamiento experto
scripts/               generar_dataset.py, benchmark_llm.py, exportar_finetune.py
docs/                  benchmark.md, FINETUNING_LORA.md
tests/                 suite pytest (una por fase)
```

## Garantías

- Ninguna cifra financiera la produce un LLM: el margen que se muestra siempre se recalcula con
  `finance.py`, y el precio pasa por el guardrail.
- Ninguna recomendación llega al usuario por debajo del precio mínimo viable; si se recorta, la
  pantalla muestra: *"Recomendación original (X) ajustada al mínimo viable (Y) por restricción de
  margen"*.
- Si el LLM no está, falla, tarda demasiado o responde mal formado, el usuario recibe la
  recomendación del motor de reglas con la explicación de por qué.
