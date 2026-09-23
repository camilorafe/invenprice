# Benchmark copiloto local — qwen2.5:3b-instruct-q4_K_M

Fecha: 2026-09-22 21:21  
Hardware: Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz · 8 hilos · 15.9 GB RAM · sin GPU dedicada  
SO: Windows-11-10.0.26200-SP0 · Python 3.13.1 · Ollama (CPU, cuantización Q4_K_M)

| caso | velocidad | segundos | tokens prompt | tokens salida | tok/s salida | JSON válido | precio LLM | precio dataset | precio final | fuente | guardrail |
|---|---|---|---|---|---|---|---|---|---|---|---|
| caso_001 | lenta | 111.0 | 2011 | 319 | 7.67 | sí | 31000.0 | 31000 | 31000.0 | llm_local | no |
| caso_008 | muy_rapida | 106.7 | 1857 | 330 | 7.47 | sí | 20700.0 | 20700 | 20700.0 | llm_local | no |
| caso_014 | lenta | 88.6 | 1718 | 248 | 7.59 | sí | 40000.0 | 40000 | 40000.0 | llm_local | no |
| caso_021 | lenta | 87.1 | 1717 | 247 | 7.7 | sí | 12500 | 12500 | 12500.0 | llm_local | no |

**Latencia promedio:** 98.3 s por recomendación · **salidas válidas:** 4/4
