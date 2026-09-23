"""Modo por lotes del copiloto: genera y guarda recomendaciones para todo el inventario sin LLM."""
import pytest

from invenprice import batch_pricing, db


@pytest.fixture
def conn():
    c = db.abrir(":memory:")
    db.fijar_config(c, "copiloto_llm_activo", "0")
    db.crear_producto(c, nombre="A", costo=20300, precio_venta=35000, moneda="COP", margen_minimo_pct=30, precio_competencia=31000)
    db.crear_producto(c, nombre="B bajo mínimo", costo=16000, precio_venta=20000, moneda="COP", margen_minimo_pct=30)
    db.crear_producto(c, nombre="C usd", costo=2.7, precio_venta=4.5, moneda="USD", margen_minimo_pct=30, precio_competencia=3.99)
    db.crear_producto(c, nombre="Inactivo", costo=1, precio_venta=2, moneda="COP")
    db.actualizar_producto(c, 4, activo=0)
    yield c
    c.close()


def test_ejecutar_guarda_una_recomendacion_por_producto_activo(conn):
    lineas = []
    resumen = batch_pricing.ejecutar(conn, usar_llm=False, log=lineas.append)
    assert [r["producto_id"] for r in resumen] == [1, 2, 3]
    assert all(r["error"] is None and r["fuente"] == "motor_reglas" for r in resumen)
    ultimas = db.ultimas_recomendaciones_por_producto(conn)
    assert set(ultimas) == {1, 2, 3}
    assert ultimas[1]["precio_sugerido"] == 31000
    assert ultimas[2]["precio_sugerido"] == 22900 and ultimas[2]["detalle"]["justificacion_fuente"] == "motor_reglas"
    assert ultimas[3]["precio_sugerido"] == 3.99
    assert any("3/3" in l for l in lineas)


def test_solo_ids(conn):
    resumen = batch_pricing.ejecutar(conn, usar_llm=False, solo_ids=[3])
    assert [r["producto_id"] for r in resumen] == [3]
    assert db.ultima_recomendacion(conn, 1) is None


def test_repetir_lote_agrega_historial_y_actualiza_ultima(conn):
    batch_pricing.ejecutar(conn, usar_llm=False)
    db.actualizar_producto(conn, 1, precio_competencia=33000)
    batch_pricing.ejecutar(conn, usar_llm=False)
    assert len(db.listar_recomendaciones(conn, producto_id=1)) == 2
    assert db.ultima_recomendacion(conn, 1)["precio_sugerido"] == 33000


def test_llm_apagado_por_configuracion_cae_a_reglas_sin_error(conn):
    db.fijar_config(conn, "copiloto_llm_activo", "1")
    db.fijar_config(conn, "ollama_url", "http://127.0.0.1:9")
    resumen = batch_pricing.ejecutar(conn)  # usar_llm=None -> según configuración; servidor inexistente
    assert all(r["fuente"] == "motor_reglas" for r in resumen)
    assert "Ollama apagado" in db.ultima_recomendacion(conn, 1)["detalle"]["motivo_fallback"]


def test_error_en_un_producto_no_detiene_el_lote(conn, monkeypatch):
    original = batch_pricing.copilot.recomendar_para_producto

    def roto(conn_, p, ctx, usar_llm=None):
        if p["id"] == 2:
            raise RuntimeError("boom")
        return original(conn_, p, ctx, usar_llm=usar_llm)

    monkeypatch.setattr(batch_pricing.copilot, "recomendar_para_producto", roto)
    resumen = batch_pricing.ejecutar(conn, usar_llm=False)
    assert [r.get("error") is not None for r in resumen] == [False, True, False]


def test_cli(tmp_path, capsys):
    ruta = tmp_path / "t.db"
    c = db.abrir(ruta)
    db.crear_producto(c, nombre="A", costo=20300, precio_venta=35000, moneda="COP", margen_minimo_pct=30)
    c.close()
    codigo = batch_pricing.main(["--db", str(ruta), "--sin-llm", "--modelo", "modelo-x"])
    assert codigo == 0
    out = capsys.readouterr().out
    assert "1/1" in out and "35.000" in out
    c = db.abrir(ruta)
    assert db.ultima_recomendacion(c, 1) is not None
    assert db.obtener_config(c, "modelo_local") == "modelo-x"
    c.close()
