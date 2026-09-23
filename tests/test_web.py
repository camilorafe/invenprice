"""Fase 8 — interfaz web local (Flask). Smoke tests con cliente de pruebas, sin LLM."""
import pytest

from invenprice import db
from invenprice.web.app import crear_app


@pytest.fixture
def cliente():
    app = crear_app(ruta_db=":memory:", testing=True)
    with app.test_client() as c:
        yield c


def _crear_producto(cliente, **extra):
    datos = {"nombre": "Camiseta", "categoria": "Ropa", "costo": "10000", "precio_venta": "15000", "moneda": "COP",
             "stock_actual": "20", "margen_minimo_pct": "30", "precio_competencia": "", "gastos_variables": "0", "sku": "CAM-1"}
    datos.update(extra)
    return cliente.post("/productos/nuevo", data=datos, follow_redirects=True)


def test_dashboard_vacio(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "InvenPrice" in r.get_data(as_text=True)


def test_crear_y_ver_producto(cliente):
    r = _crear_producto(cliente)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Camiseta" in html
    r = cliente.get("/productos/1")
    html = r.get_data(as_text=True)
    assert "33,3" in html or "33.3" in html          # margen bruto
    assert "50" in html                               # markup
    assert "14.286" in html                           # precio mínimo viable al 30 % (10.000 / 0,7)
    assert "US$" in html and "¥" in html              # multi-moneda


def test_precio_minimo_en_detalle(cliente):
    _crear_producto(cliente, margen_minimo_pct="40")
    html = cliente.get("/productos/1").get_data(as_text=True)
    assert "16.667" in html


def test_registrar_movimiento_y_venta(cliente):
    _crear_producto(cliente)
    r = cliente.post("/productos/1/movimiento", data={"tipo": "entrada", "cantidad": "10", "usuario": "ana", "motivo": ""}, follow_redirects=True)
    assert r.status_code == 200 and "30" in r.get_data(as_text=True)
    r = cliente.post("/productos/1/venta", data={"cantidad": "5", "usuario": "ana"}, follow_redirects=True)
    assert r.status_code == 200 and "25" in r.get_data(as_text=True)


def test_movimiento_invalido_muestra_error(cliente):
    _crear_producto(cliente)
    r = cliente.post("/productos/1/movimiento", data={"tipo": "salida", "cantidad": "0", "usuario": "ana"}, follow_redirects=True)
    assert r.status_code == 200 and "positiva" in r.get_data(as_text=True)


def test_recomendacion_sin_llm_usa_reglas_y_muestra_guardrail(cliente):
    # precio 20.000 con costo 16.000 y mínimo 30 % -> bajo mínimo -> reglas suben a 22.900
    _crear_producto(cliente, costo="16000", precio_venta="20000", margen_minimo_pct="30")
    cliente.post("/configuracion", data={"margen_minimo_global_pct": "20", "copiloto_llm_activo": "0", "modelo_local": "x",
                                         "ollama_url": "http://127.0.0.1:9", "hora_apertura": "7", "hora_cierre": "20",
                                         "zona_horaria_offset": "-5", "objetivo_mes": "2000000"}, follow_redirects=True)
    r = cliente.post("/productos/1/recomendar", data={"velocidad_venta": "media", "restricciones": ""}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "motor_reglas" in html or "Motor de reglas" in html
    assert "22.900" in html
    assert "bajo_minimo" in html  # reglas activadas visibles


def test_recomendacion_con_ollama_apagado_no_rompe(cliente):
    _crear_producto(cliente)
    cliente.post("/configuracion", data={"margen_minimo_global_pct": "20", "copiloto_llm_activo": "1", "modelo_local": "x",
                                         "ollama_url": "http://127.0.0.1:9", "hora_apertura": "7", "hora_cierre": "20",
                                         "zona_horaria_offset": "-5", "objetivo_mes": ""}, follow_redirects=True)
    r = cliente.post("/productos/1/recomendar", data={"velocidad_venta": "lenta", "restricciones": "temporada baja"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "motor de reglas" in html.lower()


def test_tasas_editar_y_modo_b(cliente):
    r = cliente.post("/tasas", data={"COP": "4200", "CNY": "7.1"}, follow_redirects=True)
    assert "4.200" in r.get_data(as_text=True) or "4200" in r.get_data(as_text=True)
    r = cliente.post("/tasas/modo", data={"modo_tasas_online": "1"}, follow_redirects=True)
    assert "activado" in r.get_data(as_text=True).lower()
    # actualizar con host inalcanzable: fallback sin error
    r = cliente.post("/tasas/actualizar", follow_redirects=True)
    assert r.status_code == 200 and ("última tasa guardada" in r.get_data(as_text=True).lower() or "no se pudo" in r.get_data(as_text=True).lower())


def test_alertas_pagina(cliente):
    _crear_producto(cliente)
    cliente.post("/productos/1/movimiento", data={"tipo": "merma", "cantidad": "15", "usuario": "luis", "motivo": "rotura"}, follow_redirects=True)
    r = cliente.get("/alertas")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "merma_grande" in html and "luis" in html


def test_reglas_pagina(cliente):
    r = cliente.get("/reglas")
    assert r.status_code == 200 and "ancla_competencia" in r.get_data(as_text=True)


def test_api_json_producto(cliente):
    _crear_producto(cliente)
    r = cliente.get("/api/productos/1/analisis")
    assert r.status_code == 200
    data = r.get_json()
    assert data["margen_bruto_pct"] == pytest.approx(33.3333, abs=1e-3)
    assert set(data["precios"]) == {"USD", "COP", "CNY"}


# ------------------------------------------------------------------ modo por lotes + última recomendación guardada
def test_producto_sin_recomendacion_guardada_indica_como_generarla(cliente):
    _crear_producto(cliente)
    html = cliente.get("/productos/1").get_data(as_text=True)
    assert "batch_pricing" in html and "Regenerar ahora" in html


def test_dashboard_y_producto_muestran_ultima_recomendacion_guardada(cliente):
    from invenprice import batch_pricing
    _crear_producto(cliente, costo="16000", precio_venta="20000", margen_minimo_pct="30")
    _crear_producto(cliente, nombre="Otro", sku="OTR-1")
    # la app de pruebas comparte una única conexión en memoria; el lote se ejecuta sobre ella
    conn = cliente.application.config["CONN_TEST"]
    conn.execute("UPDATE configuracion SET valor='0' WHERE clave='copiloto_llm_activo'")
    resumen = batch_pricing.ejecutar(conn, usar_llm=False)
    assert len(resumen) == 2
    html = cliente.get("/productos/1").get_data(as_text=True)
    assert "Última recomendación guardada" in html and "22.900" in html and "hace 0 min" in html
    home = cliente.get("/").get_data(as_text=True)
    assert "última guardada por producto" in home and "22.900" in home and "hace 0 min" in home
    assert "sin recomendación guardada" not in home


def test_regenerar_ahora_actualiza_la_ultima(cliente):
    _crear_producto(cliente, costo="16000", precio_venta="20000", margen_minimo_pct="30")
    cliente.post("/configuracion", data={"copiloto_llm_activo": "0", "margen_minimo_global_pct": "20"}, follow_redirects=True)
    cliente.post("/productos/1/recomendar", data={"velocidad_venta": "media", "restricciones": ""}, follow_redirects=True)
    html = cliente.get("/productos/1").get_data(as_text=True)
    assert "Última recomendación guardada" in html and "22.900" in html
