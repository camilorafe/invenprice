"""Auditoría de cifras: toda cifra citada como hecho debe coincidir con el cálculo determinista."""
import pytest

from invenprice import auditoria as au
from invenprice.rules import evaluar_reglas

# caso de referencia: 35.000 / 20.300, mínimo 30 %, competencia 31.000 -> sugerido 31.000
REC = evaluar_reglas(
    {"precio_venta": 35000, "costo": 20300, "margen_minimo_pct": 30, "precio_competencia": 31000},
    {"velocidad_venta": "lenta", "objetivo_ingreso_mensual": 5_000_000, "restricciones": ["nunca bajar de 30% de margen"]},
)
DET = dict(REC.detalle, precio_original=REC.precio_sugerido, cambio_pct=(31000 - 35000) / 35000 * 100, markup_pct=72.4138)
HECHOS = au.hechos_desde_detalle(DET, REC.reglas_activadas)


def test_lecturas_de_formatos():
    assert 25500.0 in au._lecturas("25.500")          # ambiguo (25.500 o 25,5): se aceptan ambas
    assert au._lecturas("3.000.000") == [3_000_000.0]
    assert 25500.0 in au._lecturas("25,500")
    assert au._lecturas("25500") == [25500.0]
    assert au._lecturas("3.99") == [3.99]
    assert au._lecturas("3,99") == [3.99]
    assert set(au._lecturas("3.857")) == {3857.0, 3.857}   # ambiguo: se aceptan ambas
    assert au._lecturas("1.234,5") == [1234.5]


def test_texto_correcto_sin_discrepancias():
    texto = ("Igualar a la competencia en 31.000 respeta el piso de 30% (mínimo viable 29.000). Con costo 20.300 el margen "
             "pasa de 42.0% a 34,5% (10.700 por unidad). Estamos 12.9% por encima del competidor. El objetivo de 5.000.000 "
             "exige 468 unidades/mes frente a ~40; revisar a 30 días y en 3 semanas.")
    assert au.verificar_cifras(texto, HECHOS) == []


def test_margen_incorrecto_detectado():
    d = au.verificar_cifras("Recomendamos 31.000 con margen resultante de 40.0% sobre costo 20.300.", HECHOS)
    assert [x.texto for x in d] == ["40.0%"]
    assert d[0].tipo == "porcentaje"


def test_precio_incorrecto_detectado():
    d = au.verificar_cifras("Bajar a 30.500 mantiene el margen en 34,5%.", HECHOS)
    assert [x.texto for x in d] == ["30.500"]


def test_unidades_incorrectas_detectadas():
    assert [x.texto for x in au.verificar_cifras("El objetivo requiere 200 unidades al mes.", HECHOS)] == ["200"]
    # con menos de 100 también se verifica si dice 'unidades'
    assert [x.texto for x in au.verificar_cifras("Bastan 42 unidades.", HECHOS)] == ["42"]


def test_enteros_pequenos_y_ratios_se_ignoran():
    assert au.verificar_cifras("Revisar en 30 días, 2 semanas y 4 revisiones; presión 11.7x.", HECHOS) == []


def test_formatos_equivalentes_del_mismo_hecho():
    assert au.verificar_cifras("31000 o 31.000 o 31,000 son el mismo precio; margen 34.5% o 35%.", HECHOS) == []


def test_tolerancia_de_redondeo():
    assert au.verificar_cifras("margen 34,5%", HECHOS) == []
    assert au.verificar_cifras("margen 35%", HECHOS) == []      # entero: ±0,55
    assert au.verificar_cifras("margen 35,1%", HECHOS) != []    # decimal: ±0,15


def test_importes_usd_con_centavos():
    rec = evaluar_reglas({"precio_venta": 4.5, "costo": 2.7, "margen_minimo_pct": 30, "precio_competencia": 3.99}, {"velocidad_venta": "lenta"})
    det = dict(rec.detalle, precio_original=rec.precio_sugerido, cambio_pct=(3.99 - 4.5) / 4.5 * 100)
    hechos = au.hechos_desde_detalle(det, rec.reglas_activadas)
    assert au.verificar_cifras("Igualar a 3.99 con costo 2.70 deja 1.29 por unidad; el mínimo viable es 3.86.", hechos) == []
    assert au.verificar_cifras("Igualar a 3.75 deja margen.", hechos) != []


def test_texto_vacio_o_sin_cifras():
    assert au.verificar_cifras("", HECHOS) == []
    assert au.verificar_cifras("Mantener el precio y trabajar la rotación.", HECHOS) == []


def test_las_justificaciones_del_motor_de_reglas_pasan_la_auditoria():
    import json
    from pathlib import Path
    ruta = Path(__file__).resolve().parent.parent / "data" / "pricing_reasoning_dataset.jsonl"
    for linea in ruta.read_text(encoding="utf-8").strip().splitlines():
        i = json.loads(linea)["input"]
        rec = evaluar_reglas(
            {"precio_venta": i["precio_actual"], "costo": i["costo"], "margen_minimo_pct": i["margen_minimo_pct"], "precio_competencia": i["precio_competencia"]},
            {"velocidad_venta": i["velocidad_venta"], "objetivo_ingreso_mensual": i["objetivo_ingreso_mensual"], "restricciones": i["restricciones"]},
        )
        det = dict(rec.detalle, precio_original=rec.precio_sugerido)
        hechos = au.hechos_desde_detalle(det, rec.reglas_activadas)
        assert au.verificar_cifras(rec.justificacion, hechos) == [], rec.justificacion
        assert au.verificar_cifras(rec.riesgo, hechos) == [], rec.riesgo
