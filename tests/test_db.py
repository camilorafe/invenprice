"""Fase 1 — tests del esquema de datos y operaciones básicas de persistencia."""
import sqlite3

import pytest

from invenprice import db


@pytest.fixture
def conn():
    c = db.conectar(":memory:")
    db.inicializar(c)
    yield c
    c.close()


def test_tablas_existen(conn):
    tablas = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    esperadas = {
        "productos",
        "ventas",
        "movimientos_inventario",
        "objetivos_ingreso",
        "costos_fijos",
        "tasas_cambio",
        "configuracion",
        "recomendaciones",
    }
    assert esperadas <= tablas


def test_inicializar_es_idempotente(conn):
    db.inicializar(conn)
    db.inicializar(conn)
    assert db.obtener_config(conn, "moneda_base") == "COP"


def test_crear_y_obtener_producto(conn):
    pid = db.crear_producto(
        conn,
        nombre="Camiseta básica",
        costo=10000,
        precio_venta=15000,
        moneda="COP",
        categoria="Ropa",
        sku="CAM-001",
        stock_actual=20,
        margen_minimo_pct=30,
    )
    p = db.obtener_producto(conn, pid)
    assert p["nombre"] == "Camiseta básica"
    assert p["costo"] == 10000
    assert p["precio_venta"] == 15000
    assert p["moneda"] == "COP"
    assert p["stock_actual"] == 20
    assert p["margen_minimo_pct"] == 30
    assert len(db.listar_productos(conn)) == 1


def test_moneda_invalida_rechazada(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.crear_producto(conn, nombre="X", costo=1, precio_venta=2, moneda="EUR")


def test_costo_negativo_rechazado(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.crear_producto(conn, nombre="X", costo=-1, precio_venta=2, moneda="COP")


def test_movimiento_actualiza_stock(conn):
    pid = db.crear_producto(conn, nombre="A", costo=1, precio_venta=2, moneda="COP")
    db.registrar_movimiento(conn, pid, tipo="entrada", cantidad=50, usuario="ana")
    assert db.obtener_producto(conn, pid)["stock_actual"] == 50
    db.registrar_movimiento(conn, pid, tipo="salida", cantidad=10, usuario="ana")
    assert db.obtener_producto(conn, pid)["stock_actual"] == 40
    db.registrar_movimiento(conn, pid, tipo="merma", cantidad=3, usuario="luis", motivo="rotura")
    assert db.obtener_producto(conn, pid)["stock_actual"] == 37
    movs = db.listar_movimientos(conn, producto_id=pid)
    assert [m["tipo"] for m in movs] == ["entrada", "salida", "merma"]
    # el movimiento guarda el stock resultante como snapshot auditable
    assert movs[-1]["stock_resultante"] == 37
    assert movs[-1]["delta"] == -3


def test_ajuste_por_conteo_registra_discrepancia(conn):
    pid = db.crear_producto(conn, nombre="A", costo=1, precio_venta=2, moneda="COP", stock_actual=100)
    db.registrar_movimiento(conn, pid, tipo="ajuste", cantidad=95, usuario="ana", motivo="conteo")
    p = db.obtener_producto(conn, pid)
    assert p["stock_actual"] == 95
    m = db.listar_movimientos(conn, producto_id=pid)[0]
    assert m["stock_esperado"] == 100
    assert m["stock_contado"] == 95
    assert m["delta"] == -5


def test_tipo_movimiento_invalido(conn):
    pid = db.crear_producto(conn, nombre="A", costo=1, precio_venta=2, moneda="COP")
    with pytest.raises(ValueError):
        db.registrar_movimiento(conn, pid, tipo="robo", cantidad=1)


def test_venta_descuenta_stock_y_guarda_snapshot(conn):
    pid = db.crear_producto(conn, nombre="A", costo=10000, precio_venta=15000, moneda="COP", stock_actual=10)
    vid = db.registrar_venta(conn, pid, cantidad=3, usuario="ana")
    assert db.obtener_producto(conn, pid)["stock_actual"] == 7
    ventas = db.listar_ventas(conn, producto_id=pid)
    assert len(ventas) == 1
    v = ventas[0]
    assert v["id"] == vid
    assert v["cantidad"] == 3
    assert v["precio_unitario"] == 15000
    assert v["costo_unitario"] == 10000  # snapshot del costo al momento de vender
    assert v["moneda"] == "COP"
    # la venta también genera un movimiento de salida
    movs = db.listar_movimientos(conn, producto_id=pid)
    assert movs[-1]["tipo"] == "salida" and movs[-1]["delta"] == -3


def test_venta_cantidad_invalida(conn):
    pid = db.crear_producto(conn, nombre="A", costo=1, precio_venta=2, moneda="COP", stock_actual=10)
    with pytest.raises(ValueError):
        db.registrar_venta(conn, pid, cantidad=0)


def test_objetivo_mensual_upsert(conn):
    db.fijar_objetivo(conn, 2026, 9, 2_000_000, moneda="COP")
    assert db.obtener_objetivo(conn, 2026, 9)["monto"] == 2_000_000
    db.fijar_objetivo(conn, 2026, 9, 2_500_000, moneda="COP")
    assert db.obtener_objetivo(conn, 2026, 9)["monto"] == 2_500_000
    assert db.obtener_objetivo(conn, 2026, 10) is None


def test_costos_fijos(conn):
    db.agregar_costo_fijo(conn, "Arriendo", 1_200_000)
    db.agregar_costo_fijo(conn, "Servicios", 300_000)
    assert db.total_costos_fijos(conn) == 1_500_000


def test_configuracion_clave_valor(conn):
    assert db.obtener_config(conn, "modo_tasas_online") == "0"
    db.fijar_config(conn, "modo_tasas_online", "1")
    assert db.obtener_config(conn, "modo_tasas_online") == "1"
    assert db.obtener_config(conn, "no_existe", "def") == "def"


def test_tasas_cambio_semilla(conn):
    tasas = db.obtener_tasas(conn)
    assert set(tasas) == {"USD", "COP", "CNY"}
    assert tasas["USD"]["tasa_por_usd"] == 1.0
