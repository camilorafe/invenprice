"""Fase 3 — multi-moneda offline-first (Modo A manual, Modo B opcional con fallback)."""
import pytest

from invenprice import currency as cu
from invenprice import db

A = pytest.approx
TASAS = {"USD": 1.0, "COP": 4000.0, "CNY": 7.2}


@pytest.fixture
def conn():
    c = db.abrir(":memory:")
    db.fijar_tasa(c, "COP", 4000.0)
    db.fijar_tasa(c, "CNY", 7.2)
    yield c
    c.close()


# ------------------------------------------------------------------ conversión pura
def test_convertir_cop_a_usd():
    assert cu.convertir(15000, "COP", "USD", TASAS) == A(3.75)


def test_convertir_usd_a_cop_y_cny():
    assert cu.convertir(10, "USD", "COP", TASAS) == A(40000)
    assert cu.convertir(10, "USD", "CNY", TASAS) == A(72)


def test_convertir_cruzada_cop_cny():
    # 15.000 COP = 3,75 USD = 27 CNY
    assert cu.convertir(15000, "COP", "CNY", TASAS) == A(27.0)


def test_convertir_misma_moneda_identidad():
    assert cu.convertir(123.45, "COP", "COP", TASAS) == 123.45


def test_convertir_ida_y_vuelta():
    ida = cu.convertir(15000, "COP", "CNY", TASAS)
    assert cu.convertir(ida, "CNY", "COP", TASAS) == A(15000)


def test_convertir_moneda_invalida():
    with pytest.raises(ValueError):
        cu.convertir(1, "EUR", "USD", TASAS)
    with pytest.raises(ValueError):
        cu.convertir(1, "USD", "MXN", TASAS)


def test_convertir_tasa_faltante_o_invalida():
    with pytest.raises(ValueError):
        cu.convertir(1, "USD", "COP", {"USD": 1.0})
    with pytest.raises(ValueError):
        cu.convertir(1, "USD", "COP", {"USD": 1.0, "COP": 0})


def test_en_todas_las_monedas():
    r = cu.en_todas_las_monedas(15000, "COP", TASAS)
    assert r["COP"] == A(15000) and r["USD"] == A(3.75) and r["CNY"] == A(27.0)


def test_redondeo_por_moneda():
    assert cu.redondear(16666.666, "COP") == 16667      # COP sin decimales
    assert cu.redondear(3.7549, "USD") == 3.75
    assert cu.redondear(27.005, "CNY") == 27.01


def test_redondeo_hacia_arriba_para_precios_minimos():
    # un precio mínimo nunca debe redondearse hacia abajo (rompería el margen)
    assert cu.redondear_arriba(16666.1, "COP") == 16667
    assert cu.redondear_arriba(3.751, "USD") == 3.76
    assert cu.redondear_arriba(3.75, "USD") == 3.75


def test_formatear():
    assert cu.formatear(15000, "COP") == "$ 15.000 COP"
    assert cu.formatear(1234567.5, "COP") == "$ 1.234.568 COP"
    assert cu.formatear(3.75, "USD") == "US$ 3.75"
    assert cu.formatear(27, "CNY") == "¥ 27.00"


# ------------------------------------------------------------------ Modo A (manual, en BD)
def test_tasas_desde_bd(conn):
    t = cu.tasas_actuales(conn)
    assert t == {"USD": 1.0, "COP": 4000.0, "CNY": 7.2}


def test_editar_tasa_manual(conn):
    cu.fijar_tasa_manual(conn, "COP", 4200)
    assert cu.tasas_actuales(conn)["COP"] == 4200
    assert db.obtener_tasas(conn)["COP"]["fuente"] == "manual"


def test_usd_siempre_uno(conn):
    cu.fijar_tasa_manual(conn, "USD", 999)
    assert cu.tasas_actuales(conn)["USD"] == 1.0


def test_convertir_desde_bd(conn):
    assert cu.convertir_con_bd(conn, 15000, "COP", "USD") == A(3.75)


# ------------------------------------------------------------------ Modo B (opcional, fallback)
def test_modo_b_desactivado_por_defecto_no_llama_internet(conn):
    assert cu.modo_online_activo(conn) is False

    def fetcher_prohibido(timeout):
        raise AssertionError("no debería llamarse con Modo B desactivado")

    r = cu.actualizar_tasas_online(conn, fetcher=fetcher_prohibido)
    assert r.exito is False and r.motivo == "modo_b_desactivado"
    assert r.tasas == {"USD": 1.0, "COP": 4000.0, "CNY": 7.2}


def test_modo_b_activar_desactivar_con_una_opcion(conn):
    cu.activar_modo_online(conn, True)
    assert cu.modo_online_activo(conn) is True
    cu.activar_modo_online(conn, False)
    assert cu.modo_online_activo(conn) is False


def test_modo_b_exito_actualiza_tasas(conn):
    cu.activar_modo_online(conn, True)
    r = cu.actualizar_tasas_online(conn, fetcher=lambda timeout: {"COP": 4100.0, "CNY": 7.1})
    assert r.exito is True
    assert r.tasas["COP"] == 4100.0 and r.tasas["CNY"] == 7.1 and r.tasas["USD"] == 1.0
    assert db.obtener_tasas(conn)["COP"]["fuente"] == "api"


def test_modo_b_sin_internet_cae_a_ultima_tasa_guardada(conn):
    cu.activar_modo_online(conn, True)

    def sin_internet(timeout):
        raise OSError("Network is unreachable")

    r = cu.actualizar_tasas_online(conn, fetcher=sin_internet)  # no lanza
    assert r.exito is False and "unreachable" in r.motivo
    assert r.tasas == {"USD": 1.0, "COP": 4000.0, "CNY": 7.2}
    assert db.obtener_tasas(conn)["COP"]["tasa_por_usd"] == 4000.0


@pytest.mark.parametrize(
    "basura",
    [None, {}, {"COP": "abc"}, {"COP": -5, "CNY": 7}, {"COP": 4100.0}, "no es un dict", {"COP": 0, "CNY": 7}],
)
def test_modo_b_respuesta_malformada_cae_a_fallback(conn, basura):
    cu.activar_modo_online(conn, True)
    r = cu.actualizar_tasas_online(conn, fetcher=lambda timeout: basura)
    assert r.exito is False
    assert r.tasas["COP"] == 4000.0


def test_modo_b_fetcher_real_contra_host_inalcanzable_no_rompe(conn):
    cu.activar_modo_online(conn, True)
    # puerto 9 (discard) en loopback: conexión rechazada de inmediato, sin depender de internet
    r = cu.actualizar_tasas_online(conn, url="http://127.0.0.1:9/latest/USD", timeout=1)
    assert r.exito is False
    assert r.tasas["COP"] == 4000.0


def test_parsear_respuesta_api_formato_open_er_api():
    payload = {"result": "success", "rates": {"USD": 1, "COP": 4100.5, "CNY": 7.15, "EUR": 0.9}}
    assert cu.parsear_respuesta_api(payload) == {"COP": 4100.5, "CNY": 7.15}
