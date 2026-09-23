"""Benchmark del copiloto local (Fase 6): tiempo real de respuesta en ESTA máquina.

Corre 4 casos del dataset contra el modelo local vía Ollama, mide latencia total, tokens/s,
si la salida pasó la validación estricta y si el guardrail tuvo que intervenir.

Uso:  python scripts/benchmark_llm.py [--modelo qwen2.5:7b-instruct-q4_K_M] [--salida docs/benchmark.md]
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from invenprice import copilot, finance as f  # noqa: E402

CASOS_IDX = [0, 7, 13, 20]  # lenta+competencia, muy rápida+competencia, lenta sin comp., margen negativo


def info_hardware() -> dict:
    info = {"os": platform.platform(), "cpu": platform.processor() or "n/d", "python": platform.python_version()}
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB; (Get-CimInstance Win32_Processor).Name; (Get-CimInstance Win32_Processor).NumberOfLogicalProcessors"],
            capture_output=True, text=True, timeout=20,
        ).stdout.split("\n")
        out = [o.strip() for o in out if o.strip()]
        if len(out) >= 3:
            info["ram_gb"] = round(float(out[0].replace(",", ".")), 1)
            info["cpu"] = out[1]
            info["hilos"] = out[2]
    except Exception:
        pass
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default=copilot.MODELO_DEFAULT)
    ap.add_argument("--url", default=copilot.OLLAMA_URL_DEFAULT)
    ap.add_argument("--salida", default="docs/benchmark.md")
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()

    cliente = copilot.ClienteOllama(url=args.url, modelo=args.modelo, timeout=args.timeout)
    hw = info_hardware()
    print("Hardware:", hw)
    if not cliente.disponible():
        print(f"Modelo {args.modelo} no disponible en {args.url}. Instala Ollama y corre: ollama pull {args.modelo}")
        sys.exit(1)

    casos = copilot.cargar_dataset()
    filas = []
    for idx in CASOS_IDX:
        c = casos[idx]
        i = c["input"]
        producto = {"precio_venta": i["precio_actual"], "costo": i["costo"], "margen_minimo_pct": i["margen_minimo_pct"],
                    "precio_competencia": i["precio_competencia"]}
        contexto = {"velocidad_venta": i["velocidad_venta"], "objetivo_ingreso_mensual": i["objetivo_ingreso_mensual"],
                    "restricciones": i["restricciones"]}
        prompt = copilot.construir_prompt(producto, contexto)

        # llamada cruda para obtener métricas de tokens de Ollama
        cuerpo = json.dumps({"model": args.modelo, "prompt": prompt, "stream": False, "format": "json",
                             "options": {"temperature": 0.2, "num_predict": 400}}).encode()
        req = urllib.request.Request(f"{args.url}/api/generate", data=cuerpo, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                data = json.loads(resp.read().decode())
            dt = time.perf_counter() - t0
            texto = data.get("response", "")
            salida, motivo = copilot.parsear_salida_llm(texto)
            rec = copilot.recomendar(producto, contexto, llm=lambda p, timeout=None: texto)
            filas.append({
                "caso": c["id"], "velocidad": i["velocidad_venta"], "segundos": round(dt, 1),
                "tokens_prompt": data.get("prompt_eval_count"), "tokens_salida": data.get("eval_count"),
                "tok_s_salida": round(data["eval_count"] / (data["eval_duration"] / 1e9), 2) if data.get("eval_duration") else None,
                "json_valido": salida is not None, "motivo": motivo,
                "precio_llm": salida["precio_recomendado"] if salida else None,
                "precio_dataset": c["output"]["precio_recomendado"],
                "precio_final": rec.precio_sugerido, "fuente": rec.fuente, "guardrail": rec.ajustado_guardrail,
                "p_min": round(f.precio_minimo_viable(i["costo"], i["margen_minimo_pct"]).precio, 0),
            })
        except Exception as e:
            dt = time.perf_counter() - t0
            filas.append({"caso": c["id"], "velocidad": i["velocidad_venta"], "segundos": round(dt, 1), "json_valido": False,
                          "motivo": f"{type(e).__name__}: {e}", "fuente": "motor_reglas", "guardrail": False,
                          "precio_dataset": c["output"]["precio_recomendado"], "precio_llm": None, "precio_final": None,
                          "tokens_prompt": None, "tokens_salida": None, "tok_s_salida": None, "p_min": None})
        print(filas[-1])

    validos = [r for r in filas if r["json_valido"]]
    prom = sum(r["segundos"] for r in filas) / len(filas)
    md = [
        f"# Benchmark copiloto local — {args.modelo}", "",
        f"Fecha: {time.strftime('%Y-%m-%d %H:%M')}  ",
        f"Hardware: {hw.get('cpu')} · {hw.get('hilos', '?')} hilos · {hw.get('ram_gb', '?')} GB RAM · sin GPU dedicada  ",
        f"SO: {hw.get('os')} · Python {hw.get('python')} · Ollama (CPU, cuantización Q4_K_M)", "",
        "| caso | velocidad | segundos | tokens prompt | tokens salida | tok/s salida | JSON válido | precio LLM | precio dataset | precio final | fuente | guardrail |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in filas:
        md.append(f"| {r['caso']} | {r['velocidad']} | {r['segundos']} | {r['tokens_prompt']} | {r['tokens_salida']} | {r['tok_s_salida']} | "
                  f"{'sí' if r['json_valido'] else 'NO (' + str(r['motivo']) + ')'} | {r['precio_llm']} | {r['precio_dataset']} | {r['precio_final']} | {r['fuente']} | {'sí' if r['guardrail'] else 'no'} |")
    md += ["", f"**Latencia promedio:** {prom:.1f} s por recomendación · **salidas válidas:** {len(validos)}/{len(filas)}", ""]
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text("\n".join(md), encoding="utf-8")
    print(f"\nEscrito {args.salida}. Promedio {prom:.1f} s, válidos {len(validos)}/{len(filas)}")


if __name__ == "__main__":
    main()
