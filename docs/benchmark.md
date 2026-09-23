# Benchmark copiloto local — qwen2.5:7b-instruct-q4_K_M

Fecha: 2026-09-22 21:09  
Hardware: Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz · 8 hilos · 15.9 GB RAM · sin GPU dedicada  
SO: Windows-11-10.0.26200-SP0 · Python 3.13.1 · Ollama (CPU, cuantización Q4_K_M)

| caso | velocidad | segundos | tokens prompt | tokens salida | tok/s salida | JSON válido | precio LLM | precio dataset | precio final | fuente | guardrail |
|---|---|---|---|---|---|---|---|---|---|---|---|
| caso_001 | lenta | 285.6 | 2011 | 319 | 3.23 | sí | 31000.0 | 31000 | 31000.0 | llm_local | no |
| caso_008 | muy_rapida | 209.3 | 1857 | 322 | 3.82 | sí | 20700.0 | 20700 | 20700.0 | llm_local | no |
| caso_014 | lenta | 230.2 | 1718 | 247 | 3.18 | sí | 40000.0 | 40000 | 40000.0 | llm_local | no |
| caso_021 | lenta | 225.9 | 1717 | 249 | 2.94 | sí | 12500.0 | 12500 | 12500.0 | llm_local | no |

**Latencia promedio:** 237.8 s por recomendación · **salidas válidas:** 4/4
