# InvenPrice — Arquitectura

Sistema de inventario y pricing para pequeñas empresas. **Offline-first, costo recurrente $0.**

## Principios no negociables

1. **Cero dependencias de pago en producción.** Ninguna llamada a APIs de LLM en la nube.
2. **Cero internet obligatorio.** Lo único que puede usar internet (tasas de cambio, Modo B) es
   opcional, desactivado por defecto y con fallback a la última tasa guardada.
3. **La IA es opcional.** Si el modelo local (Ollama) no está instalado o falla, el sistema cae al
   motor de reglas. Nunca se rompe por falta de IA.
4. **Toda matemática financiera es código determinista y testeado** (`invenprice/finance.py`).
   Un LLM jamás produce, aproxima ni corrige una cifra.
5. **Guardrail de precio mínimo:** ninguna recomendación llega al usuario por debajo del precio
   mínimo viable calculado por el motor financiero.

## Vista general

```
 ┌───────────────────────────── Interfaz web local (Flask) ─────────────────────────────┐
 │  Productos · Rentabilidad · Copiloto · Anomalías · Tasas de cambio                    │
 └───────────────┬───────────────────────────────────────────────────────┬──────────────┘
                 │                                                       │
   ┌─────────────▼─────────────┐                          ┌──────────────▼──────────────┐
   │  copilot.py (Fase 6)      │                          │  anomalies.py (Fase 7)      │
   │  1. LLM local (Ollama)    │──falla/JSON inválido──┐  │  z-scores, umbrales, horario │
   │  2. rules.py (Fase 5)     │◄──────────────────────┘  └──────────────┬──────────────┘
   │  3. GUARDRAIL min. viable │                                         │
   └─────────────┬─────────────┘                                         │
                 │                                                       │
   ┌─────────────▼───────────────────────────────────────────────────────▼──────────────┐
   │  finance.py (Fase 2) — funciones puras   ·   currency.py (Fase 3) — conversión     │
   └─────────────────────────────────────┬──────────────────────────────────────────────┘
                                         │
                              ┌──────────▼──────────┐
                              │  db.py + schema.sql  │   SQLite embebido (data/invenprice.db)
                              └─────────────────────┘
```

## Módulos

| Módulo | Fase | Responsabilidad |
|---|---|---|
| `invenprice/schema.sql`, `db.py` | 1 | Esquema SQLite, CRUD, movimientos auditados |
| `invenprice/finance.py` | 2 | Márgenes, markup, break-even, precio mínimo, unidades objetivo |
| `invenprice/currency.py` | 3 | Conversión USD/COP/CNY, Modo A manual, Modo B opcional |
| `data/pricing_reasoning_dataset.jsonl` | 4 | 40–50 casos de razonamiento experto |
| `invenprice/rules.py` | 5 | Motor de reglas inspeccionable (fallback permanente) |
| `invenprice/copilot.py` | 6 | Orquestador: LLM local → validación → reglas → guardrail |
| `invenprice/anomalies.py` | 7 | Detección estadística de anomalías de inventario |
| `invenprice/web/` | 8 | Dashboard Flask + HTML simple |

## Modelo de datos (SQLite)

Todos los importes se guardan en la **moneda del producto** (moneda base elegida por el usuario
por producto). Las otras monedas son solo de visualización.

### `productos`
| columna | tipo | notas |
|---|---|---|
| id | INTEGER PK | |
| sku | TEXT UNIQUE | opcional |
| nombre, categoria | TEXT | |
| costo | REAL ≥ 0 | costo variable unitario |
| precio_venta | REAL ≥ 0 | |
| moneda | TEXT ∈ {USD, COP, CNY} | moneda base del producto |
| stock_actual | INTEGER | **solo cambia vía movimientos** |
| gastos_variables | REAL ≥ 0 | comisiones/empaque/impuestos por unidad → margen neto |
| margen_minimo_pct | REAL < 100, nullable | restricción del dueño ("nunca bajar de X%") |
| precio_competencia | REAL, nullable | puede no existir |
| activo | INTEGER | borrado lógico |

### `ventas`
Guarda `precio_unitario` y `costo_unitario` como **snapshot** del momento de la venta, para que
los márgenes históricos no cambien al editar el producto. Cada venta genera además un
movimiento de tipo `salida` enlazado por `venta_id`.

### `movimientos_inventario`
| tipo | delta | uso |
|---|---|---|
| entrada | + | compras / recepciones |
| salida | − | ventas / despachos |
| merma | − | roturas, vencimientos, robos (señal clave para anomalías) |
| ajuste | ± | conteo físico; guarda `stock_esperado` y `stock_contado` |

Cada fila guarda `stock_resultante` (snapshot), `fecha` ISO-8601 UTC y `usuario`, que son las
tres señales que necesita la detección de anomalías (Fase 7).

### `objetivos_ingreso`
Un monto por `(anio, mes)` con su moneda. Upsert.

### `costos_fijos`
Costos fijos mensuales del negocio (arriendo, nómina). Se usan para el **break-even agregado**
y para asignar costo fijo por unidad en el **margen neto**.

### `tasas_cambio`
Una fila por moneda, expresada como *unidades por 1 USD* (USD = 1.0). `fuente` ∈
{semilla, manual, api} y `actualizado_en` permiten mostrar al usuario qué tan vieja es la tasa.

### `configuracion`
Clave/valor. Claves relevantes: `moneda_base`, `margen_minimo_global_pct`,
`modo_tasas_online` (Modo B, "0"/"1"), `modelo_local`, `ollama_url`, `copiloto_llm_activo`,
`hora_apertura`, `hora_cierre`.

### `recomendaciones`
Bitácora de cada recomendación del copiloto: fuente (`llm_local` | `motor_reglas`), precio
original, precio final, si fue ajustada por el guardrail, mínimo viable aplicado y un
`detalle_json` con los números que la sustentan (capa de auditoría).

## Decisiones de arquitectura

- **SQLite embebido, sin ORM.** Un archivo, cero infraestructura, respaldable copiando
  `data/invenprice.db`. SQL explícito facilita auditar qué se guarda.
- **Números como REAL + redondeo explícito en `finance.py`.** Para importes de pequeña empresa la
  precisión de `float` (53 bits) es más que suficiente; cada función financiera redondea sus
  salidas a una precisión definida y los tests verifican contra valores calculados a mano.
  Se evitó `Decimal` para mantener el código simple y las fórmulas legibles.
- **Estados explícitos en lugar de excepciones o infinitos.** Los casos borde (costo 0, margen ≤ 0,
  objetivo ≤ 0) se representan con enumeraciones/`None` documentados, nunca con `inf`, `nan`
  ni `ZeroDivisionError` (detalle en Fase 2).
- **Stock solo por movimientos.** Evita "editar el stock a mano" sin rastro; los ajustes por
  conteo registran esperado vs. contado para detectar discrepancias recurrentes.
- **El LLM local es un acelerador, no una dependencia.** El motor de reglas es el camino
  garantizado; el LLM solo se usa si está disponible y su salida pasa validación de schema.
- **Flask + HTML servidor-side** (una dependencia, sin build de JS) para que el usuario final no
  técnico ejecute un solo comando y abra el navegador.
