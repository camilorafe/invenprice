"""Prueba de humo end-to-end: siembra una BD temporal, arranca el servidor real y visita las páginas.

Uso:  python scripts/smoke_web.py            (usa data/demo.db, la recrea)
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "data" / "demo.db"
PUERTO = 5055


def get(ruta, datos=None, timeout=600):
    url = f"http://127.0.0.1:{PUERTO}{ruta}"
    cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=cuerpo), timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main() -> None:
    if DB.exists():
        DB.unlink()
    subprocess.run([sys.executable, str(RAIZ / "scripts" / "demo_seed.py"), str(DB)], check=True, cwd=RAIZ)
    srv = subprocess.Popen(
        [sys.executable, "-c", f"from invenprice.web.app import crear_app; crear_app(r'{DB}').run(port={PUERTO})"],
        cwd=RAIZ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            try:
                get("/", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        for ruta in ("/", "/productos", "/productos/1", "/productos/6", "/alertas", "/tasas", "/configuracion", "/reglas", "/api/productos/1/analisis"):
            st, html = get(ruta)
            print(f"{ruta:32s} -> {st} ({len(html)} bytes)")
        # recomendación con LLM desactivado (rápida) y luego con LLM si está disponible
        get("/configuracion", {"copiloto_llm_activo": "0", "margen_minimo_global_pct": "20"})
        t0 = time.perf_counter()
        st, html = get("/productos/1/recomendar", {"velocidad_venta": "lenta", "restricciones": "temporada baja"})
        print(f"recomendar (reglas)              -> {st} en {time.perf_counter() - t0:.1f}s; fuente motor_reglas: {'motor_reglas' in html}")
        if "--llm" in sys.argv:
            get("/configuracion", {"copiloto_llm_activo": "1"})
            t0 = time.perf_counter()
            st, html = get("/productos/1/recomendar", {"velocidad_venta": "lenta", "restricciones": ""})
            print(f"recomendar (LLM)                 -> {st} en {time.perf_counter() - t0:.1f}s; fuente llm_local: {'llm_local' in html}; guardrail: {'Ajustada por el guardrail' in html}")
    finally:
        srv.kill()


if __name__ == "__main__":
    main()
