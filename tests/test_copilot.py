"""Fase 6 — copiloto local: LLM opcional, validación estricta, fallback a reglas y guardrail."""
import json

import pytest

from invenprice import copilot, finance as f, rules
from invenprice.copilot import RecomendacionFinal, recomendar

PROD = {"precio_venta": 35000, "costo": 20300, "margen_minimo_pct": 30, "precio_competencia": 31000}
CTX = {"velocidad_venta": "lenta", "objetivo_ingreso_mensual": 5_000_000, "restricciones": ["nunca bajar de 30% de margen"]}
P_MIN = 20300 / 0.7  # 29.000


def llm_que_devuelve(texto):
    def _llm(prompt, timeout=None):
        return texto
    return _llm


def llm_que_falla(exc):
    def _llm(prompt, timeout=None):
        raise exc
    return _llm


SALIDA_VALIDA = json.dumps({
    "precio_recomendado": 31000,
    "margen_resultante_pct": 34.5,
    "justificacion": "Igualar a la competencia en 31.000 respeta el piso de 30% (mínimo 29.000).",
    "riesgo": "Si la rotación no mejora, el problema no era el precio.",
})


# ------------------------------------------------------------------ camino feliz LLM
def test_llm_valido_se_usa_y_se_marca_fuente():
    r = recomendar(PROD, CTX, llm=llm_que_devuelve(SALIDA_VALIDA))
    assert isinstance(r, RecomendacionFinal)
    assert r.fuente == "llm_local"
    assert r.precio_sugerido == 31000
    assert r.ajustado_guardrail is False
    assert r.precio_minimo_viable == pytest.approx(P_MIN)
    # el margen se RECALCULA con el motor determinista, no se confía en el del LLM
    assert r.margen_resultante_pct == pytest.approx(f.margen_bruto_pct(31000, 20300))
    assert r.detalle["precio_minimo_viable"] == pytest.approx(P_MIN)
    assert r.detalle["motivo_fallback"] is None


def test_llm_margen_incoherente_se_corrige_y_anota():
    salida = json.loads(SALIDA_VALIDA)
    salida["margen_resultante_pct"] = 80  # el LLM se equivocó en la aritmética
    r = recomendar(PROD, CTX, llm=llm_que_devuelve(json.dumps(salida)))
    assert r.fuente == "llm_local"
    assert r.margen_resultante_pct == pytest.approx(34.5, abs=0.1)
    assert any("margen" in n.lower() for n in r.notas)


def test_llm_con_texto_alrededor_del_json_se_extrae():
    r = recomendar(PROD, CTX, llm=llm_que_devuelve("Claro, aquí está:\n```json\n" + SALIDA_VALIDA + "\n```\nEspero que sirva."))
    assert r.fuente == "llm_local" and r.precio_sugerido == 31000


# ------------------------------------------------------------------ validación estricta -> fallback
@pytest.mark.parametrize(
    "salida",
    [
        "no soy json",
        "",
        "{",
        json.dumps({"precio_recomendado": 31000}),  # faltan campos
        json.dumps({"precio_recomendado": "treinta y uno", "margen_resultante_pct": 34, "justificacion": "x", "riesgo": "y"}),
        json.dumps({"precio_recomendado": -5, "margen_resultante_pct": 34, "justificacion": "x", "riesgo": "y"}),
        json.dumps({"precio_recomendado": 0, "margen_resultante_pct": 34, "justificacion": "x", "riesgo": "y"}),
        json.dumps({"precio_recomendado": 31000, "margen_resultante_pct": 34, "justificacion": "", "riesgo": "y"}),
        json.dumps({"precio_recomendado": 31000, "margen_resultante_pct": 34, "justificacion": "x", "riesgo": None}),
        json.dumps([31000, 34]),
        json.dumps({"precio_recomendado": float("nan"), "margen_resultante_pct": 34, "justificacion": "x", "riesgo": "y"}).replace("NaN", "NaN"),
        None,
        12345,
    ],
)
def test_salida_malformada_cae_a_motor_reglas(salida):
    r = recomendar(PROD, CTX, llm=llm_que_devuelve(salida))
    assert r.fuente == "motor_reglas"
    assert r.precio_sugerido == 31000  # lo que dice el motor de reglas para este caso
    assert r.detalle["motivo_fallback"]
    assert r.detalle["llm_intentado"] is True


@pytest.mark.parametrize("exc", [ConnectionRefusedError("no hay servidor"), TimeoutError("lento"), RuntimeError("modelo no instalado"), OSError("red")])
def test_llm_que_falla_cae_a_motor_reglas(exc):
    r = recomendar(PROD, CTX, llm=llm_que_falla(exc))
    assert r.fuente == "motor_reglas"
    assert r.precio_sugerido == 31000
    assert type(exc).__name__ in r.detalle["motivo_fallback"]


def test_sin_llm_configurado_usa_reglas_directamente():
    r = recomendar(PROD, CTX, llm=None, usar_llm=False)
    assert r.fuente == "motor_reglas"
    assert r.detalle["llm_intentado"] is False
    assert r.reglas_activadas  # trazabilidad del motor de reglas


def test_cliente_ollama_real_sin_servidor_no_rompe():
    cliente = copilot.ClienteOllama(url="http://127.0.0.1:9", modelo="cualquiera", timeout=1)
    assert cliente.disponible() is False
    r = recomendar(PROD, CTX, llm=cliente)
    assert r.fuente == "motor_reglas" and r.precio_sugerido == 31000


# ------------------------------------------------------------------ GUARDRAIL de precio mínimo
def test_guardrail_recorta_llm_bajo_minimo():
    # ejemplo de referencia: mínimo 28.000, recomendación 26.000 -> 28.000 con nota
    prod = {"precio_venta": 30000, "costo": 19600, "margen_minimo_pct": 30}  # 19.600/0,7 = 28.000
    salida = json.dumps({"precio_recomendado": 26000, "margen_resultante_pct": 24.6, "justificacion": "bajar para rotar", "riesgo": "margen"})
    r = recomendar(prod, {"velocidad_venta": "lenta"}, llm=llm_que_devuelve(salida))
    assert r.fuente == "llm_local"
    assert r.precio_minimo_viable == pytest.approx(28000)
    assert r.precio_sugerido == 28000
    assert r.precio_original == 26000
    assert r.ajustado_guardrail is True
    assert "26.000" in r.nota_guardrail and "28.000" in r.nota_guardrail and "mínimo viable" in r.nota_guardrail
    assert r.margen_resultante_pct == pytest.approx(30.0)


def test_guardrail_recorta_motor_reglas_bajo_minimo(monkeypatch):
    # forzamos que el motor de reglas devuelva un precio por debajo del mínimo
    prod = {"precio_venta": 30000, "costo": 19600, "margen_minimo_pct": 30}
    original = rules.evaluar_reglas

    def reglas_rotas(producto, contexto=None):
        rec = original(producto, contexto)
        rec.precio_sugerido = 26000
        return rec

    monkeypatch.setattr(copilot.rules, "evaluar_reglas", reglas_rotas)
    r = recomendar(prod, {"velocidad_venta": "lenta"}, llm=None, usar_llm=False)
    assert r.fuente == "motor_reglas"
    assert r.precio_sugerido == 28000
    assert r.precio_original == 26000
    assert r.ajustado_guardrail is True
    assert "26.000" in r.nota_guardrail and "28.000" in r.nota_guardrail


def test_guardrail_no_toca_precios_validos():
    r = recomendar(PROD, CTX, llm=llm_que_devuelve(SALIDA_VALIDA))
    assert r.ajustado_guardrail is False and r.nota_guardrail is None and r.precio_original == r.precio_sugerido


def test_guardrail_usa_gastos_variables_y_redondeo_hacia_arriba():
    prod = {"precio_venta": 20000, "costo": 10000, "gastos_variables": 1000, "margen_minimo_pct": 40, "moneda": "COP"}
    salida = json.dumps({"precio_recomendado": 15000, "margen_resultante_pct": 33, "justificacion": "x", "riesgo": "y"})
    r = recomendar(prod, {}, llm=llm_que_devuelve(salida))
    assert r.precio_minimo_viable == pytest.approx(18333.33, abs=0.01)
    assert r.precio_sugerido == 18334  # COP: entero, hacia arriba
    assert r.ajustado_guardrail


def test_guardrail_en_usd_redondea_a_centavos_hacia_arriba():
    prod = {"precio_venta": 5, "costo": 2.7, "margen_minimo_pct": 30, "moneda": "USD"}  # min 3.857142
    salida = json.dumps({"precio_recomendado": 3.5, "margen_resultante_pct": 22, "justificacion": "x", "riesgo": "y"})
    r = recomendar(prod, {}, llm=llm_que_devuelve(salida))
    assert r.precio_sugerido == 3.86 and r.ajustado_guardrail


# ------------------------------------------------------------------ explicabilidad / prompt
def test_detalle_de_auditoria_completo():
    r = recomendar(PROD, CTX, llm=llm_que_devuelve(SALIDA_VALIDA))
    for k in ("precio_actual", "costo", "margen_actual_pct", "margen_minimo_pct", "precio_minimo_viable",
              "precio_competencia", "velocidad_venta", "objetivo_ingreso_mensual", "unidades_objetivo",
              "fuente", "llm_intentado", "motivo_fallback", "ajustado_guardrail", "precio_original", "modelo"):
        assert k in r.detalle, k
    d = r.a_dict()
    json.dumps(d, ensure_ascii=False)


def test_prompt_incluye_few_shot_del_dataset_y_numeros_del_producto():
    prompt = copilot.construir_prompt(PROD, CTX, n_ejemplos=4)
    assert prompt.count('"precio_recomendado"') >= 4  # ejemplos few-shot
    assert "20.300" in prompt or "20300" in prompt
    assert "29.000" in prompt or "29000" in prompt  # precio mínimo viable calculado, dado al modelo
    assert "lenta" in prompt
    assert "JSON" in prompt


def test_seleccion_few_shot_prefiere_casos_parecidos():
    ejemplos = copilot.seleccionar_ejemplos({"velocidad_venta": "muy_rapida", "precio_competencia": None, "restricciones": []}, n=5)
    assert len(ejemplos) == 5
    assert sum(1 for e in ejemplos if e["input"]["velocidad_venta"] == "muy_rapida") >= 3
