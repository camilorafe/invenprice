"""Carga datos de demostración en la BD (productos, ventas, movimientos, costos fijos, objetivo).

Uso:  python scripts/demo_seed.py [ruta_db]     (por defecto data/invenprice.db)
Incluye un patrón de mermas sospechoso y un ajuste fuera de horario para que la página de alertas
tenga contenido.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from invenprice import db  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    ruta = sys.argv[1] if len(sys.argv) > 1 else db.DB_PATH_DEFAULT
    conn = db.abrir(ruta)
    if db.listar_productos(conn):
        print("La base ya tiene productos; no se cargan datos de demostración.")
        return
    rnd = random.Random(7)
    ahora = datetime.now(timezone.utc)
    hoy = ahora.replace(hour=15, minute=0, second=0, microsecond=0)  # 10:00 hora Colombia

    productos = [
        dict(nombre="Camiseta básica", categoria="Ropa", sku="CAM-001", costo=20300, precio_venta=35000, moneda="COP", margen_minimo_pct=30, precio_competencia=31000, ventas_dia=1.2),
        dict(nombre="Gorra bordada", categoria="Accesorios", sku="GOR-001", costo=11000, precio_venta=18000, moneda="COP", margen_minimo_pct=30, precio_competencia=22000, ventas_dia=18),
        dict(nombre="Chaqueta impermeable", categoria="Ropa", sku="CHA-001", costo=45000, precio_venta=90000, moneda="COP", margen_minimo_pct=35, precio_competencia=63000, ventas_dia=0.6),
        dict(nombre="Medias deportivas (par)", categoria="Ropa", sku="MED-001", costo=4800, precio_venta=5000, moneda="COP", margen_minimo_pct=20, precio_competencia=None, ventas_dia=15),
        dict(nombre="Cinturón cuero", categoria="Accesorios", sku="CIN-001", costo=16000, precio_venta=20000, moneda="COP", margen_minimo_pct=30, precio_competencia=None, ventas_dia=3),
        dict(nombre="Auriculares importados", categoria="Electrónica", sku="AUR-001", costo=4.5, precio_venta=9.9, moneda="USD", margen_minimo_pct=35, precio_competencia=11.5, ventas_dia=4),
        dict(nombre="Termo acero", categoria="Hogar", sku="TER-001", costo=38, precio_venta=59, moneda="CNY", margen_minimo_pct=25, precio_competencia=None, ventas_dia=2),
    ]
    ids = {}
    for p in productos:
        vd = p.pop("ventas_dia")
        pid = db.crear_producto(conn, **p)
        ids[pid] = vd
        db.registrar_movimiento(conn, pid, "entrada", int(vd * 60) + 30, usuario="ana", motivo="compra inicial", fecha=iso(hoy - timedelta(days=61)))

    usuarios = ["ana", "luis", "pedro"]
    for d in range(60, 0, -1):
        fecha = hoy - timedelta(days=d)
        for pid, vd in ids.items():
            n = max(0, int(rnd.gauss(vd, vd * 0.4 + 0.3)))
            for _ in range(min(n, 6)):
                db.registrar_venta(conn, pid, cantidad=max(1, n // min(n, 6) if n else 1), usuario=rnd.choice(usuarios),
                                   fecha=iso(fecha + timedelta(hours=rnd.randint(0, 8), minutes=rnd.randint(0, 59))))
                if n <= 6:
                    break
        if d % 9 == 0:
            db.registrar_movimiento(conn, 1, "merma", 1, usuario=rnd.choice(usuarios), motivo="rotura", fecha=iso(fecha))
        if d % 20 == 0:
            for pid in ids:
                db.registrar_movimiento(conn, pid, "entrada", int(ids[pid] * 25) + 10, usuario="ana", motivo="reposición", fecha=iso(fecha))

    # patrón sospechoso: luis registra mermas grandes y un ajuste a las 23:00
    for d in (12, 9, 6, 3):
        db.registrar_movimiento(conn, 2, "merma", 6 + d, usuario="luis", motivo="faltante", fecha=iso(hoy - timedelta(days=d)))
    db.registrar_movimiento(conn, 3, "merma", 4, usuario="luis", motivo="daño", fecha=iso(hoy - timedelta(days=5)))
    p2 = db.obtener_producto(conn, 2)
    db.registrar_movimiento(conn, 2, "ajuste", max(0, p2["stock_actual"] - 7), usuario="luis", motivo="conteo nocturno",
                            fecha=iso(hoy - timedelta(days=2) + timedelta(hours=13)))  # 04:00Z = 23:00 Colombia
    p5 = db.obtener_producto(conn, 5)
    for d in (40, 25, 8):
        p5 = db.obtener_producto(conn, 5)
        db.registrar_movimiento(conn, 5, "ajuste", max(0, p5["stock_actual"] - 3), usuario="pedro", motivo="conteo", fecha=iso(hoy - timedelta(days=d)))

    db.agregar_costo_fijo(conn, "Arriendo", 1_800_000)
    db.agregar_costo_fijo(conn, "Servicios", 350_000)
    db.agregar_costo_fijo(conn, "Nómina auxiliar", 1_600_000)
    db.fijar_objetivo(conn, ahora.year, ahora.month, 6_000_000, moneda="COP")
    print(f"Datos de demostración cargados en {ruta}: {len(productos)} productos, 60 días de ventas, alertas de ejemplo.")


if __name__ == "__main__":
    main()
