"""Fase 7 — detección estadística de anomalías de inventario."""
from datetime import datetime, timedelta, timezone

import pytest

from invenprice import anomalies as an
from invenprice import db

BASE = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)  # 10:00 hora local con offset -5


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def mov(pid, tipo, delta, dias=0, horas=0, usuario="ana", esperado=None, contado=None, stock=100):
    return {
        "producto_id": pid, "tipo": tipo, "delta": delta, "stock_resultante": stock,
        "stock_esperado": esperado, "stock_contado": contado,
        "fecha": iso(BASE + timedelta(days=dias, hours=horas)), "usuario": usuario, "motivo": None,
    }


PRODUCTOS = {1: {"id": 1, "nombre": "Camiseta"}, 2: {"id": 2, "nombre": "Gorra"}}
AHORA = BASE + timedelta(days=40)


def detectar(movs, **kw):
    kw.setdefault("ahora", AHORA)
    kw.setdefault("offset_horas", -5)
    return an.detectar_anomalias(movs, PRODUCTOS, **kw)


def test_sin_movimientos_sin_alertas():
    assert detectar([]) == []


def test_operacion_normal_sin_alertas():
    movs = [mov(1, "entrada", 50, dias=0)]
    movs += [mov(1, "salida", -3, dias=d) for d in range(1, 30)]
    movs += [mov(1, "merma", -1, dias=d, usuario="ana" if d % 2 else "luis") for d in (5, 15, 25)]
    assert detectar(movs) == []


def test_merma_inusualmente_grande_por_z_score():
    movs = [mov(1, "merma", -d, dias=i) for i, d in enumerate([1, 2, 1, 2, 1, 2, 1, 2])]
    movs.append(mov(1, "merma", -30, dias=9, usuario="luis", stock=70))
    alertas = detectar(movs)
    grandes = [a for a in alertas if a.tipo == "merma_grande"]
    assert len(grandes) == 1
    a = grandes[0]
    assert a.producto_id == 1 and a.producto_nombre == "Camiseta" and a.usuario == "luis"
    assert a.severidad == "alta"
    assert a.evidencia["unidades"] == 30 and a.evidencia["z_score"] > 2
    assert "30" in a.descripcion and "Camiseta" in a.descripcion


def test_merma_grande_relativa_al_stock_sin_historial():
    # sin historial suficiente para z-score, pero la merma se lleva el 40 % del stock
    movs = [mov(2, "merma", -40, dias=1, stock=60)]
    alertas = detectar(movs)
    assert any(a.tipo == "merma_grande" and a.producto_id == 2 for a in alertas)


def test_mermas_frecuentes():
    movs = [mov(1, "merma", -1, dias=30 + i) for i in range(4)]  # 4 mermas en 4 días, recientes
    alertas = detectar(movs)
    frec = [a for a in alertas if a.tipo == "merma_frecuente"]
    assert len(frec) == 1 and frec[0].evidencia["conteo"] == 4


def test_mermas_concentradas_en_usuario():
    movs = []
    for d in range(20):
        movs.append(mov(1, "salida", -2, dias=d, usuario="ana"))
        movs.append(mov(1, "salida", -2, dias=d, usuario="luis"))
        movs.append(mov(1, "salida", -2, dias=d, usuario="pedro"))
    # luis hace 7 de 8 mermas aunque solo es 1/3 de los movimientos
    movs += [mov(1, "merma", -1, dias=d, usuario="luis") for d in range(1, 15, 2)]
    movs.append(mov(1, "merma", -1, dias=16, usuario="ana"))
    alertas = detectar(movs)
    us = [a for a in alertas if a.tipo == "usuario_mermas"]
    assert len(us) == 1
    assert us[0].usuario == "luis"
    assert us[0].evidencia["mermas_usuario"] == 7 and us[0].evidencia["mermas_total"] == 8
    assert us[0].evidencia["participacion_mermas_pct"] == pytest.approx(87.5)


def test_ajuste_fuera_de_horario():
    # 23:00 local = 04:00 UTC del día siguiente
    movs = [mov(1, "ajuste", -3, dias=1, horas=13, usuario="luis", esperado=100, contado=97)]  # 15+13 = 04:00Z -> 23:00 local
    alertas = detectar(movs, hora_apertura=7, hora_cierre=20)
    fh = [a for a in alertas if a.tipo == "fuera_horario"]
    assert len(fh) == 1 and fh[0].usuario == "luis"
    assert fh[0].evidencia["hora_local"] == 23


def test_ajuste_en_horario_no_alerta():
    movs = [mov(1, "ajuste", -3, dias=1, esperado=100, contado=97)]  # 10:00 local
    assert not [a for a in alertas_de(movs) if a.tipo == "fuera_horario"]


def alertas_de(movs):
    return detectar(movs, hora_apertura=7, hora_cierre=20)


def test_salida_fuera_de_horario_no_alerta_solo_ajustes_y_mermas():
    movs = [mov(1, "salida", -1, dias=1, horas=13)]
    assert not [a for a in alertas_de(movs) if a.tipo == "fuera_horario"]


def test_discrepancias_recurrentes():
    movs = [
        mov(2, "ajuste", -5, dias=5, esperado=100, contado=95),
        mov(2, "ajuste", -4, dias=20, esperado=95, contado=91),
        mov(2, "ajuste", -6, dias=35, esperado=91, contado=85),
    ]
    alertas = detectar(movs)
    disc = [a for a in alertas if a.tipo == "discrepancia_recurrente"]
    assert len(disc) == 1
    assert disc[0].producto_id == 2 and disc[0].evidencia["conteo"] == 3 and disc[0].evidencia["faltante_acumulado"] == 15


def test_ajuste_sin_discrepancia_no_cuenta():
    movs = [mov(2, "ajuste", 0, dias=d, esperado=100, contado=100) for d in (5, 20, 35)]
    assert not [a for a in detectar(movs) if a.tipo == "discrepancia_recurrente"]


def test_stock_negativo():
    movs = [mov(1, "salida", -5, dias=1, stock=-2)]
    assert any(a.tipo == "stock_negativo" for a in detectar(movs))


def test_alertas_ordenadas_por_severidad():
    movs = [mov(1, "merma", -d, dias=i) for i, d in enumerate([1, 2, 1, 2, 1, 2, 1, 2])]
    movs.append(mov(1, "merma", -30, dias=9, stock=70))
    movs.append(mov(2, "ajuste", -1, dias=1, horas=13, esperado=50, contado=49))
    alertas = detectar(movs)
    orden = {"alta": 0, "media": 1, "baja": 2}
    sev = [orden[a.severidad] for a in alertas]
    assert sev == sorted(sev)


def test_alerta_serializable():
    movs = [mov(1, "salida", -5, dias=1, stock=-2)]
    d = detectar(movs)[0].a_dict()
    import json
    json.dumps(d, ensure_ascii=False)


def test_desde_bd():
    conn = db.abrir(":memory:")
    pid = db.crear_producto(conn, nombre="Camiseta", costo=1, precio_venta=2, moneda="COP", stock_actual=100)
    for i in range(8):
        db.registrar_movimiento(conn, pid, "merma", 1, usuario="ana", fecha=iso(BASE + timedelta(days=i)))
    db.registrar_movimiento(conn, pid, "merma", 30, usuario="luis", fecha=iso(BASE + timedelta(days=9)))
    alertas = an.detectar_en_bd(conn, ahora=AHORA)
    assert any(a.tipo == "merma_grande" and a.usuario == "luis" for a in alertas)
