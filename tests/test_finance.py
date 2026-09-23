"""Fase 2 — motor financiero determinista.

Cada test compara contra valores calculados a mano. Caso de referencia del enunciado:
costo 10.000, precio 15.000 -> margen bruto 33,3 %, markup 50 %; precio mínimo viable al 40 % =
16.667; margen unitario 5.000 y objetivo 2.000.000 -> 400 unidades.
"""
import math

import pytest

from invenprice import finance as f
from invenprice.finance import Estado

A = pytest.approx


# ------------------------------------------------------------------ margen bruto / markup
def test_margen_bruto_referencia():
    assert f.margen_bruto_unitario(15000, 10000) == 5000
    assert f.margen_bruto_pct(15000, 10000) == A(33.3333, abs=1e-3)


def test_markup_referencia():
    r = f.markup_pct(15000, 10000)
    assert r.estado is Estado.OK
    assert r.valor_pct == A(50.0)


def test_markup_y_margen_son_distintos():
    # 50 % de markup NO es 50 % de margen: la base es distinta (costo vs precio)
    assert f.markup_pct(15000, 10000).valor_pct != f.margen_bruto_pct(15000, 10000)
    # identidad: margen = markup / (1 + markup)
    mk = f.markup_pct(15000, 10000).valor_pct / 100
    assert f.margen_bruto_pct(15000, 10000) == A(mk / (1 + mk) * 100)
    assert f.margen_desde_markup(50) == A(33.3333, abs=1e-3)
    assert f.markup_desde_margen(33.3333) == A(50.0, abs=1e-2)


def test_costo_cero_margen_100_y_markup_indefinido():
    assert f.margen_bruto_pct(15000, 0) == A(100.0)
    r = f.markup_pct(15000, 0)
    assert r.estado is Estado.INDEFINIDO
    assert r.valor_pct is None


def test_precio_cero_margen_indefinido():
    assert f.margen_bruto_pct(0, 10000) is None
    assert f.margen_bruto_unitario(0, 10000) == -10000


def test_margen_negativo_venta_bajo_costo():
    assert f.margen_bruto_unitario(8000, 10000) == -2000
    assert f.margen_bruto_pct(8000, 10000) == A(-25.0)
    r = f.markup_pct(8000, 10000)
    assert r.estado is Estado.OK and r.valor_pct == A(-20.0)


def test_margen_neto_descuenta_gastos_variables_y_fijos():
    # (15000 - 10000 - 1000 - 500) / 15000 = 23,33 %
    assert f.margen_neto_unitario(15000, 10000, gastos_variables=1000, costo_fijo_asignado=500) == 3500
    assert f.margen_neto_pct(15000, 10000, gastos_variables=1000, costo_fijo_asignado=500) == A(23.3333, abs=1e-3)
    # sin gastos, neto == bruto
    assert f.margen_neto_pct(15000, 10000) == f.margen_bruto_pct(15000, 10000)


def test_margen_neto_precio_cero():
    assert f.margen_neto_pct(0, 10000) is None


# ------------------------------------------------------------------ precio mínimo viable
def test_precio_minimo_viable_referencia():
    r = f.precio_minimo_viable(10000, 40)
    assert r.estado is Estado.OK
    assert r.precio == A(16666.67, abs=0.01)
    # verificación inversa: a ese precio el margen es exactamente 40 %
    assert f.margen_bruto_pct(r.precio, 10000) == A(40.0, abs=1e-6)


def test_precio_minimo_viable_incluye_gastos_variables():
    # (10000 + 1000) / 0.6 = 18333,33
    r = f.precio_minimo_viable(10000, 40, gastos_variables=1000)
    assert r.precio == A(18333.33, abs=0.01)


def test_precio_minimo_viable_margen_cero_es_el_costo():
    assert f.precio_minimo_viable(10000, 0).precio == A(10000)


def test_precio_minimo_viable_costo_cero():
    r = f.precio_minimo_viable(0, 40)
    assert r.estado is Estado.OK and r.precio == 0.0


def test_precio_minimo_viable_margen_100_o_mas_invalido():
    assert f.precio_minimo_viable(10000, 100).estado is Estado.INDEFINIDO
    assert f.precio_minimo_viable(10000, 150).estado is Estado.INDEFINIDO
    assert f.precio_minimo_viable(10000, 100).precio is None


def test_precio_minimo_viable_costo_negativo_invalido():
    assert f.precio_minimo_viable(-1, 40).estado is Estado.ENTRADA_INVALIDA


# ------------------------------------------------------------------ punto de equilibrio
def test_punto_equilibrio_producto():
    # 1.000.000 de costos fijos / 5.000 por unidad = 200 unidades -> 3.000.000 de ingreso
    r = f.punto_equilibrio(costos_fijos=1_000_000, precio=15000, costo=10000)
    assert r.estado is Estado.OK
    assert r.unidades == 200
    assert r.ingreso == A(3_000_000)


def test_punto_equilibrio_redondea_hacia_arriba():
    # 1.000.000 / 3.000 = 333,33 -> 334 unidades (no se venden fracciones)
    r = f.punto_equilibrio(costos_fijos=1_000_000, precio=13000, costo=10000)
    assert r.unidades == 334
    assert r.unidades_exactas == A(333.333, abs=1e-2)


def test_punto_equilibrio_margen_cero_no_alcanzable():
    r = f.punto_equilibrio(costos_fijos=1_000_000, precio=10000, costo=10000)
    assert r.estado is Estado.NO_ALCANZABLE
    assert r.unidades is None and r.ingreso is None


def test_punto_equilibrio_margen_negativo_no_alcanzable():
    r = f.punto_equilibrio(costos_fijos=1_000_000, precio=8000, costo=10000)
    assert r.estado is Estado.NO_ALCANZABLE


def test_punto_equilibrio_sin_costos_fijos_es_cero():
    r = f.punto_equilibrio(costos_fijos=0, precio=15000, costo=10000)
    assert r.estado is Estado.OK and r.unidades == 0 and r.ingreso == 0


def test_punto_equilibrio_sin_costos_fijos_y_margen_cero_sigue_siendo_cero():
    # con 0 costos fijos ya se está en equilibrio aunque el margen sea 0
    r = f.punto_equilibrio(costos_fijos=0, precio=10000, costo=10000)
    assert r.estado is Estado.OK and r.unidades == 0


def test_punto_equilibrio_costos_fijos_negativos_invalido():
    assert f.punto_equilibrio(costos_fijos=-1, precio=15000, costo=10000).estado is Estado.ENTRADA_INVALIDA


def test_punto_equilibrio_agregado_con_mix():
    # A: margen 5.000, mix 3 ; B: margen 4.000, mix 1 -> margen ponderado 4.750/unidad
    # 950.000 / 4.750 = 200 unidades -> 150 de A y 50 de B -> ingreso 2.250.000 + 1.000.000
    productos = [
        {"nombre": "A", "precio_venta": 15000, "costo": 10000, "mix": 3},
        {"nombre": "B", "precio_venta": 20000, "costo": 16000, "mix": 1},
    ]
    r = f.punto_equilibrio_agregado(costos_fijos=950_000, productos=productos)
    assert r.estado is Estado.OK
    assert r.unidades == 200
    assert r.ingreso == A(3_250_000)
    assert r.margen_unitario == A(4750)
    assert {p["nombre"]: p["unidades"] for p in r.detalle} == {"A": A(150), "B": A(50)}


def test_punto_equilibrio_agregado_mix_con_perdidas_no_alcanzable():
    productos = [
        {"nombre": "A", "precio_venta": 9000, "costo": 10000, "mix": 1},
        {"nombre": "B", "precio_venta": 10000, "costo": 10000, "mix": 1},
    ]
    assert f.punto_equilibrio_agregado(1_000, productos).estado is Estado.NO_ALCANZABLE


def test_punto_equilibrio_agregado_sin_productos():
    assert f.punto_equilibrio_agregado(1_000, []).estado is Estado.NO_ALCANZABLE
    assert f.punto_equilibrio_agregado(0, []).estado is Estado.OK


# ------------------------------------------------------------------ unidades para objetivo
def test_unidades_para_objetivo_referencia():
    r = f.unidades_para_objetivo(objetivo_mensual=2_000_000, margen_unitario=5000, precio=15000)
    assert r.estado is Estado.OK
    assert r.unidades == 400
    assert r.ingreso_bruto_estimado == A(6_000_000)  # 400 * 15.000 de ventas necesarias


def test_unidades_para_objetivo_redondea_hacia_arriba():
    r = f.unidades_para_objetivo(1_000_000, margen_unitario=3000, precio=13000)
    assert r.unidades == 334
    assert r.unidades_exactas == A(333.333, abs=1e-2)


def test_unidades_para_objetivo_cero_invalido():
    r = f.unidades_para_objetivo(0, margen_unitario=5000, precio=15000)
    assert r.estado is Estado.OBJETIVO_INVALIDO and r.unidades is None


def test_unidades_para_objetivo_negativo_invalido():
    assert f.unidades_para_objetivo(-100, 5000, 15000).estado is Estado.OBJETIVO_INVALIDO


def test_unidades_para_objetivo_margen_cero_no_alcanzable():
    r = f.unidades_para_objetivo(2_000_000, margen_unitario=0, precio=10000)
    assert r.estado is Estado.NO_ALCANZABLE and r.unidades is None


def test_unidades_para_objetivo_margen_negativo_no_alcanzable():
    assert f.unidades_para_objetivo(2_000_000, margen_unitario=-2000, precio=8000).estado is Estado.NO_ALCANZABLE


def test_unidades_para_ingreso_bruto():
    # variante por facturación: 2.000.000 / 15.000 = 133,3 -> 134 unidades
    r = f.unidades_para_ingreso_bruto(2_000_000, precio=15000)
    assert r.estado is Estado.OK and r.unidades == 134
    assert f.unidades_para_ingreso_bruto(2_000_000, precio=0).estado is Estado.NO_ALCANZABLE
    assert f.unidades_para_ingreso_bruto(0, precio=15000).estado is Estado.OBJETIVO_INVALIDO


# ------------------------------------------------------------------ rentabilidad marginal
def test_rentabilidad_marginal_ranking_y_clasificacion():
    productos = [
        {"id": 1, "nombre": "Estrella", "precio_venta": 15000, "costo": 10000, "gastos_variables": 0, "unidades_vendidas": 100},
        {"id": 2, "nombre": "Lento", "precio_venta": 50000, "costo": 20000, "gastos_variables": 0, "unidades_vendidas": 2},
        {"id": 3, "nombre": "Volumen", "precio_venta": 5000, "costo": 4800, "gastos_variables": 0, "unidades_vendidas": 500},
        {"id": 4, "nombre": "Pierde", "precio_venta": 9000, "costo": 10000, "gastos_variables": 0, "unidades_vendidas": 30},
        {"id": 5, "nombre": "Regalo", "precio_venta": 1000, "costo": 0, "gastos_variables": 0, "unidades_vendidas": 10},
    ]
    r = f.rentabilidad_marginal(productos)
    por_nombre = {x.nombre: x for x in r}
    assert por_nombre["Estrella"].contribucion_total == A(500_000)
    assert por_nombre["Lento"].contribucion_total == A(60_000)
    assert por_nombre["Volumen"].contribucion_total == A(100_000)
    assert por_nombre["Pierde"].contribucion_total == A(-30_000)
    assert por_nombre["Pierde"].clasificacion == "no_conviene"
    assert por_nombre["Estrella"].clasificacion == "estrella"
    assert por_nombre["Lento"].clasificacion == "margen_alto_rotacion_baja"
    assert por_nombre["Volumen"].clasificacion == "margen_bajo_rotacion_alta"
    assert por_nombre["Regalo"].margen_pct == A(100.0)
    # ordenado por contribución total descendente
    assert [x.nombre for x in r] == ["Estrella", "Volumen", "Lento", "Regalo", "Pierde"]
    # participación sobre la contribución positiva total (500k+100k+60k+10k = 670k)
    assert por_nombre["Estrella"].participacion_pct == A(500_000 / 670_000 * 100)


def test_rentabilidad_marginal_sin_ventas_no_divide_por_cero():
    r = f.rentabilidad_marginal([{"id": 1, "nombre": "A", "precio_venta": 10, "costo": 5, "unidades_vendidas": 0}])
    assert r[0].contribucion_total == 0
    assert r[0].participacion_pct == 0
    assert r[0].clasificacion == "sin_ventas"


# ------------------------------------------------------------------ análisis integral
def test_analisis_producto_integra_todo():
    prod = {"precio_venta": 15000, "costo": 10000, "gastos_variables": 0, "margen_minimo_pct": 40}
    a = f.analisis_producto(prod, costos_fijos_asignados=1_000_000, objetivo_mensual=2_000_000)
    assert a["margen_bruto_pct"] == A(33.3333, abs=1e-3)
    assert a["markup_pct"] == A(50.0)
    assert a["precio_minimo_viable"] == A(16666.67, abs=0.01)
    assert a["punto_equilibrio"].unidades == 200
    assert a["unidades_objetivo"].unidades == 400
    assert a["bajo_minimo"] is True  # 15.000 < 16.667


def test_analisis_producto_usa_margen_global_si_no_hay_minimo():
    prod = {"precio_venta": 15000, "costo": 10000}
    a = f.analisis_producto(prod, margen_minimo_global_pct=20)
    assert a["margen_minimo_pct"] == 20
    assert a["precio_minimo_viable"] == A(12500)
    assert a["bajo_minimo"] is False
