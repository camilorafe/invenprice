"""Fase 4 — validación del dataset de razonamiento experto."""
import json
from pathlib import Path

import pytest

from invenprice import finance as f

RUTA = Path(__file__).resolve().parent.parent / "data" / "pricing_reasoning_dataset.jsonl"
CLAVES_INPUT = {
    "precio_actual", "costo", "margen_actual_pct", "margen_minimo_pct", "velocidad_venta",
    "objetivo_ingreso_mensual", "restricciones", "precio_competencia",
}
CLAVES_OUTPUT = {"precio_recomendado", "margen_resultante_pct", "justificacion", "riesgo"}
VELOCIDADES = {"lenta", "media", "rapida", "muy_rapida"}


@pytest.fixture(scope="module")
def casos():
    lineas = RUTA.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(l) for l in lineas]


def test_cantidad_de_casos(casos):
    assert 40 <= len(casos) <= 50


def test_ids_unicos_y_formato(casos):
    ids = [c["id"] for c in casos]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("caso_") and len(i) == 8 for i in ids)


def test_schema_exacto(casos):
    for c in casos:
        assert set(c) == {"id", "input", "output"}, c["id"]
        assert set(c["input"]) == CLAVES_INPUT, c["id"]
        assert set(c["output"]) == CLAVES_OUTPUT, c["id"]
        assert isinstance(c["input"]["restricciones"], list)
        assert c["input"]["velocidad_venta"] in VELOCIDADES
        pc = c["input"]["precio_competencia"]
        assert pc is None or pc > 0


def test_margenes_consistentes_con_motor_financiero(casos):
    for c in casos:
        i, o = c["input"], c["output"]
        assert i["margen_actual_pct"] == pytest.approx(f.margen_bruto_pct(i["precio_actual"], i["costo"]), abs=0.06), c["id"]
        assert o["margen_resultante_pct"] == pytest.approx(
            f.margen_bruto_pct(o["precio_recomendado"], i["costo"]), abs=0.06
        ), c["id"]


def test_recomendacion_nunca_bajo_precio_minimo_viable(casos):
    for c in casos:
        i, o = c["input"], c["output"]
        pmin = f.precio_minimo_viable(i["costo"], i["margen_minimo_pct"]).precio
        assert o["precio_recomendado"] >= pmin - 1e-6, f"{c['id']}: {o['precio_recomendado']} < {pmin}"


def test_textos_con_calidad_y_citando_numeros(casos):
    for c in casos:
        o = c["output"]
        assert len(o["justificacion"]) >= 200, c["id"]
        assert len(o["riesgo"]) >= 60, c["id"]
        # la justificación debe citar el precio recomendado y el costo con formato de miles
        assert _fmt(o["precio_recomendado"]) in o["justificacion"], c["id"]
        assert _fmt(c["input"]["costo"]) in o["justificacion"], c["id"]


def test_diversidad_de_escenarios(casos):
    vel = {c["input"]["velocidad_venta"] for c in casos}
    assert vel == VELOCIDADES
    con_comp = sum(1 for c in casos if c["input"]["precio_competencia"] is not None)
    assert 10 <= con_comp <= len(casos) - 10  # hay casos con y sin competencia
    assert any(c["input"]["margen_actual_pct"] < c["input"]["margen_minimo_pct"] for c in casos)  # bajo mínimo
    assert any(c["input"]["margen_actual_pct"] < 0 for c in casos)  # margen negativo
    assert any(c["input"]["costo"] == 0 for c in casos)  # costo cero
    assert any(c["input"]["objetivo_ingreso_mensual"] <= 0 for c in casos)  # objetivo 0/negativo
    assert any("temporada" in " ".join(c["input"]["restricciones"]).lower() for c in casos)


def _fmt(v: float) -> str:
    if abs(v) >= 1000 or float(v).is_integer():
        return f"{v:,.0f}".replace(",", ".")
    return f"{v:.2f}"
