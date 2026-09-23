"""Fase 5 — motor de reglas de pricing (fallback permanente sin LLM)."""
import json
from pathlib import Path

import pytest

from invenprice import finance as f
from invenprice import rules
from invenprice.rules import Recomendacion, evaluar_reglas

RUTA = Path(__file__).resolve().parent.parent / "data" / "pricing_reasoning_dataset.jsonl"
CASOS = [json.loads(l) for l in RUTA.read_text(encoding="utf-8").strip().splitlines()]
TOLERANCIA = 0.10


def _prod_ctx(inp: dict):
    producto = {
        "precio_venta": inp["precio_actual"],
        "costo": inp["costo"],
        "margen_minimo_pct": inp["margen_minimo_pct"],
        "precio_competencia": inp["precio_competencia"],
    }
    contexto = {
        "velocidad_venta": inp["velocidad_venta"],
        "objetivo_ingreso_mensual": inp["objetivo_ingreso_mensual"],
        "restricciones": inp["restricciones"],
    }
    return producto, contexto


# ------------------------------------------------------------------ dataset (±10 %)
@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_reproduce_dataset_dentro_de_tolerancia(caso):
    producto, contexto = _prod_ctx(caso["input"])
    rec = evaluar_reglas(producto, contexto)
    esperado = caso["output"]["precio_recomendado"]
    desvio = abs(rec.precio_sugerido - esperado) / esperado
    assert desvio <= TOLERANCIA, (
        f"{caso['id']}: sugerido {rec.precio_sugerido} vs esperado {esperado} ({desvio:.1%})\n"
        + "\n".join(f"  - {r.nombre}: {r.condicion} -> {r.efecto}" for r in rec.reglas_activadas)
    )


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_nunca_bajo_minimo_viable_en_dataset(caso):
    producto, contexto = _prod_ctx(caso["input"])
    rec = evaluar_reglas(producto, contexto)
    pmin = f.precio_minimo_viable(caso["input"]["costo"], caso["input"]["margen_minimo_pct"]).precio
    assert rec.precio_sugerido >= pmin - 1e-9
    assert rec.precio_minimo_viable == pytest.approx(pmin)


def test_desvio_medio_del_dataset_es_bajo():
    desvios = []
    for caso in CASOS:
        producto, contexto = _prod_ctx(caso["input"])
        rec = evaluar_reglas(producto, contexto)
        desvios.append(abs(rec.precio_sugerido - caso["output"]["precio_recomendado"]) / caso["output"]["precio_recomendado"])
    assert sum(desvios) / len(desvios) < 0.03


# ------------------------------------------------------------------ estructura / inspección
def test_estructura_recomendacion():
    producto, contexto = _prod_ctx(CASOS[0]["input"])
    rec = evaluar_reglas(producto, contexto)
    assert isinstance(rec, Recomendacion)
    assert rec.fuente == "motor_reglas"
    assert rec.precio_sugerido > 0
    assert rec.margen_resultante_pct == pytest.approx(f.margen_bruto_pct(rec.precio_sugerido, 20300), abs=0.01)
    assert isinstance(rec.justificacion, str) and len(rec.justificacion) > 100
    assert isinstance(rec.riesgo, str) and len(rec.riesgo) > 30
    assert rec.reglas_activadas, "debe listar reglas activadas"
    for r in rec.reglas_activadas:
        assert r.nombre and r.condicion and r.efecto
    # los números de sustento van en detalle
    for k in ("precio_actual", "costo", "margen_actual_pct", "precio_minimo_viable", "unidades_objetivo"):
        assert k in rec.detalle


def test_justificacion_cita_numeros():
    producto, contexto = _prod_ctx(CASOS[0]["input"])
    rec = evaluar_reglas(producto, contexto)
    assert "20.300" in rec.justificacion  # costo
    assert "31.000" in rec.justificacion  # competencia / precio sugerido
    assert "30" in rec.justificacion      # margen mínimo


def test_catalogo_de_reglas_inspeccionable():
    cat = rules.catalogo_reglas()
    assert len(cat) >= 10
    nombres = {r["nombre"] for r in cat}
    assert {"bajo_minimo", "ancla_competencia", "velocidad", "objetivo_ingreso", "tope_gradual", "piso_margen_minimo"} <= nombres
    for r in cat:
        assert r["condicion"] and r["efecto"]


def test_recomendacion_serializable():
    producto, contexto = _prod_ctx(CASOS[0]["input"])
    d = evaluar_reglas(producto, contexto).a_dict()
    json.dumps(d, ensure_ascii=False)
    assert d["fuente"] == "motor_reglas"


# ------------------------------------------------------------------ reglas específicas
def test_bajo_minimo_sube_al_minimo_viable():
    rec = evaluar_reglas({"precio_venta": 20000, "costo": 16000, "margen_minimo_pct": 30}, {"velocidad_venta": "media"})
    assert rec.precio_sugerido == 22900  # 22.857 redondeado hacia arriba
    assert any(r.nombre == "bajo_minimo" for r in rec.reglas_activadas)


def test_margen_negativo_sube_al_minimo_sin_tope_gradual():
    rec = evaluar_reglas({"precio_venta": 9000, "costo": 10000, "margen_minimo_pct": 20}, {"velocidad_venta": "lenta"})
    assert rec.precio_sugerido == 12500
    assert rec.detalle["margen_actual_pct"] < 0


def test_restriccion_no_subir_bloquea_aumentos():
    rec = evaluar_reglas(
        {"precio_venta": 8000, "costo": 5000, "margen_minimo_pct": 30, "precio_competencia": 9500},
        {"velocidad_venta": "muy_rapida", "restricciones": ["no subir precio este trimestre"]},
    )
    assert rec.precio_sugerido == 8000
    assert any(r.nombre == "restriccion_no_subir" for r in rec.reglas_activadas)


def test_restriccion_no_bajar_bloquea_rebajas():
    rec = evaluar_reglas(
        {"precio_venta": 120000, "costo": 60000, "margen_minimo_pct": 40, "precio_competencia": 95000},
        {"velocidad_venta": "lenta", "restricciones": ["posicionamiento premium: no bajar del precio actual"]},
    )
    assert rec.precio_sugerido == 120000


def test_nunca_bajar_de_x_eleva_el_piso():
    # el producto dice 20 %, la restricción dice 40 % -> manda la restricción
    rec = evaluar_reglas(
        {"precio_venta": 20000, "costo": 15000, "margen_minimo_pct": 20},
        {"velocidad_venta": "media", "restricciones": ["nunca bajar de 40% de margen"]},
    )
    assert rec.precio_minimo_viable == pytest.approx(25000)
    assert rec.precio_sugerido == 25000


def test_tope_gradual_15_pct():
    rec = evaluar_reglas(
        {"precio_venta": 18000, "costo": 11000, "margen_minimo_pct": 30, "precio_competencia": 22000},
        {"velocidad_venta": "muy_rapida"},
    )
    assert rec.precio_sugerido == 20700
    assert any(r.nombre == "tope_gradual" for r in rec.reglas_activadas)


def test_poca_holgura_de_margen_bloquea_rebaja():
    rec = evaluar_reglas({"precio_venta": 30000, "costo": 22000, "margen_minimo_pct": 25}, {"velocidad_venta": "lenta"})
    assert rec.precio_sugerido == 30000
    assert any(r.nombre == "holgura_insuficiente" for r in rec.reglas_activadas)


def test_costo_cero_no_falla():
    rec = evaluar_reglas({"precio_venta": 25000, "costo": 0, "margen_minimo_pct": 50, "precio_competencia": 30000}, {"velocidad_venta": "media"})
    assert rec.precio_sugerido == 26500
    assert rec.precio_minimo_viable == 0


def test_precio_cero_no_falla():
    rec = evaluar_reglas({"precio_venta": 0, "costo": 1000, "margen_minimo_pct": 30}, {"velocidad_venta": "media"})
    assert rec.precio_sugerido >= 1000 / 0.7 - 1e-9


def test_velocidad_desconocida_se_trata_como_media():
    a = evaluar_reglas({"precio_venta": 12500, "costo": 8000, "margen_minimo_pct": 30}, {"velocidad_venta": "???"})
    b = evaluar_reglas({"precio_venta": 12500, "costo": 8000, "margen_minimo_pct": 30}, {"velocidad_venta": "media"})
    assert a.precio_sugerido == b.precio_sugerido


def test_contexto_vacio_usa_defaults():
    rec = evaluar_reglas({"precio_venta": 15000, "costo": 9000}, {})
    assert rec.precio_sugerido == 15000
    assert rec.detalle["margen_minimo_pct"] == 20  # global por defecto


def test_objetivo_cero_o_negativo_no_rompe():
    for g in (0, -100, None):
        rec = evaluar_reglas({"precio_venta": 15000, "costo": 9000, "margen_minimo_pct": 30}, {"velocidad_venta": "media", "objetivo_ingreso_mensual": g})
        assert rec.precio_sugerido == 15000
        assert rec.detalle["unidades_objetivo"] is None


def test_gastos_variables_elevan_el_minimo():
    rec = evaluar_reglas({"precio_venta": 15000, "costo": 10000, "gastos_variables": 1000, "margen_minimo_pct": 40}, {"velocidad_venta": "media"})
    assert rec.precio_minimo_viable == pytest.approx(18333.33, abs=0.01)
    assert rec.precio_sugerido == 18400


def test_redondeo_comercial():
    assert rules.redondear_comercial(46153.8, 46153.8) == 46200
    assert rules.redondear_comercial(2375000, 2375000) == 2380000
    assert rules.redondear_comercial(3.857, 3.857) == 3.86
    assert rules.redondear_comercial(40158, 0) == 40200
    assert rules.redondear_comercial(999.4, 0) == 999.4
