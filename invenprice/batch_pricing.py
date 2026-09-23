"""Generación de recomendaciones por lotes (modo por defecto del copiloto).

Dado el tiempo medido del modelo local en CPU (≈4 min con 7B, ≈1,5 min con 3B por producto), el
dashboard NO llama al LLM al cargar páginas: muestra la última recomendación guardada con su fecha.
Este proceso recorre el inventario y guarda una recomendación por producto en la tabla
`recomendaciones`; se corre bajo demanda o programado (Programador de tareas / cron), por ejemplo
de noche. El botón "Regenerar ahora" del dashboard sigue siendo síncrono para un solo producto.

Uso:
    python -m invenprice.batch_pricing                 # todos los productos activos, LLM según configuración
    python -m invenprice.batch_pricing --sin-llm       # solo motor de reglas (milisegundos)
    python -m invenprice.batch_pricing --solo 3 7      # solo esos productos
    python -m invenprice.batch_pricing --db otra.db --modelo qwen2.5:3b-instruct-q4_K_M --timeout 300
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Callable, Iterable, Optional

from . import copilot, db, rules


def ejecutar(
    conn,
    usar_llm: Optional[bool] = None,
    solo_ids: Optional[Iterable[int]] = None,
    log: Callable[[str], None] = lambda s: None,
) -> list[dict]:
    """Genera y guarda una recomendación por producto activo. Devuelve un resumen por producto.
    Nunca lanza por un producto individual: los errores se registran en el resumen y se continúa."""
    productos = db.listar_productos(conn)
    if solo_ids is not None:
        ids = {int(i) for i in solo_ids}
        productos = [p for p in productos if p["id"] in ids]
    resumen = []
    log(f"Procesando {len(productos)} producto(s); LLM: {'según configuración' if usar_llm is None else ('sí' if usar_llm else 'no')}")
    for p in productos:
        t0 = time.perf_counter()
        try:
            ctx = copilot.contexto_desde_bd(conn, p)
            rec = copilot.recomendar_para_producto(conn, p, ctx, usar_llm=usar_llm)
            fila = {
                "producto_id": p["id"], "nombre": p["nombre"], "moneda": p["moneda"],
                "precio_actual": p["precio_venta"], "precio_sugerido": rec.precio_sugerido,
                "margen_resultante_pct": rec.margen_resultante_pct, "fuente": rec.fuente,
                "ajustado_guardrail": rec.ajustado_guardrail, "justificacion_fuente": rec.detalle.get("justificacion_fuente"),
                "segundos": round(time.perf_counter() - t0, 1), "error": None,
            }
            log(f"  #{p['id']:<4} {p['nombre'][:28]:<28} {rules._fmt(p['precio_venta']):>12} -> {rules._fmt(rec.precio_sugerido):>12} {p['moneda']}"
                f"  [{rec.fuente}{' · guardrail' if rec.ajustado_guardrail else ''}{' · texto plantilla' if fila['justificacion_fuente'] == 'plantilla' else ''}]  {fila['segundos']}s")
        except Exception as e:  # un producto roto no detiene el lote
            fila = {"producto_id": p["id"], "nombre": p["nombre"], "error": f"{type(e).__name__}: {e}",
                    "segundos": round(time.perf_counter() - t0, 1)}
            log(f"  #{p['id']:<4} {p['nombre'][:28]:<28} ERROR {fila['error']}")
        resumen.append(fila)
    ok = [r for r in resumen if not r.get("error")]
    total = sum(r["segundos"] for r in resumen)
    log(f"Listo: {len(ok)}/{len(resumen)} recomendaciones guardadas en {total:.1f}s"
        + (f" ({sum(1 for r in ok if r['fuente'] == 'llm_local')} con LLM, {sum(1 for r in ok if r['ajustado_guardrail'])} ajustadas por guardrail)" if ok else ""))
    return resumen


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Genera recomendaciones de precio para todo el inventario.")
    ap.add_argument("--db", default=str(db.DB_PATH_DEFAULT), help="ruta de la base SQLite")
    ap.add_argument("--sin-llm", action="store_true", help="usar solo el motor de reglas")
    ap.add_argument("--con-llm", action="store_true", help="forzar el uso del LLM aunque esté desactivado en configuración")
    ap.add_argument("--solo", nargs="*", type=int, help="ids de producto a procesar")
    ap.add_argument("--modelo", help="modelo Ollama a usar (sobrescribe la configuración solo en esta corrida)")
    ap.add_argument("--timeout", type=float, help="segundos máximos por llamada al LLM")
    args = ap.parse_args(argv)

    conn = db.abrir(args.db)
    if args.modelo:
        db.fijar_config(conn, "modelo_local", args.modelo)
    if args.timeout:
        db.fijar_config(conn, "timeout_llm_seg", str(args.timeout))
    usar_llm = False if args.sin_llm else (True if args.con_llm else None)
    resumen = ejecutar(conn, usar_llm=usar_llm, solo_ids=args.solo, log=print)
    conn.close()
    return 0 if all(not r.get("error") for r in resumen) else 1


if __name__ == "__main__":
    sys.exit(main())
