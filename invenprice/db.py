"""Capa de persistencia SQLite (Fase 1).

Reglas:
* El stock SOLO cambia a través de `registrar_movimiento` / `registrar_venta`, para que cada
  cambio quede auditado en `movimientos_inventario` con su snapshot.
* Sin ORM: SQL explícito y filas como dict. Cero dependencias externas.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import MONEDAS

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DB_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "invenprice.db"

TIPOS_MOVIMIENTO = ("entrada", "salida", "merma", "ajuste")

CONFIG_DEFAULT = {
    "moneda_base": "COP",
    "margen_minimo_global_pct": "20",
    "modo_tasas_online": "0",          # Modo B desactivado por defecto
    "modelo_local": "qwen2.5:7b-instruct-q4_K_M",
    "ollama_url": "http://localhost:11434",
    "copiloto_llm_activo": "1",
    "hora_apertura": "7",
    "hora_cierre": "20",
}

# tasas semilla (aprox. septiembre 2026; el usuario debe editarlas en la UI)
TASAS_SEMILLA = {"USD": 1.0, "COP": 4000.0, "CNY": 7.2}


def ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


def conectar(ruta: str | Path = DB_PATH_DEFAULT) -> sqlite3.Connection:
    if str(ruta) != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ruta))
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def inicializar(conn: sqlite3.Connection) -> None:
    """Crea tablas si no existen y siembra configuración/tasas. Idempotente."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for clave, valor in CONFIG_DEFAULT.items():
        conn.execute(
            "INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?, ?)", (clave, valor)
        )
    for moneda, tasa in TASAS_SEMILLA.items():
        conn.execute(
            "INSERT OR IGNORE INTO tasas_cambio (moneda, tasa_por_usd, fuente, actualizado_en) "
            "VALUES (?, ?, 'semilla', ?)",
            (moneda, tasa, ahora_iso()),
        )
    conn.commit()


def abrir(ruta: str | Path = DB_PATH_DEFAULT) -> sqlite3.Connection:
    conn = conectar(ruta)
    inicializar(conn)
    return conn


# ----------------------------------------------------------------------------- configuración
def obtener_config(conn, clave: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT valor FROM configuracion WHERE clave = ?", (clave,)).fetchone()
    return row["valor"] if row else default


def fijar_config(conn, clave: str, valor: Any) -> None:
    conn.execute(
        "INSERT INTO configuracion (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, str(valor)),
    )
    conn.commit()


# ----------------------------------------------------------------------------- productos
def crear_producto(
    conn,
    nombre: str,
    costo: float,
    precio_venta: float,
    moneda: str,
    categoria: Optional[str] = None,
    sku: Optional[str] = None,
    stock_actual: int = 0,
    gastos_variables: float = 0.0,
    margen_minimo_pct: Optional[float] = None,
    precio_competencia: Optional[float] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO productos (sku, nombre, categoria, costo, precio_venta, moneda, stock_actual, "
        "gastos_variables, margen_minimo_pct, precio_competencia) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sku, nombre, categoria, costo, precio_venta, moneda, stock_actual, gastos_variables,
         margen_minimo_pct, precio_competencia),
    )
    conn.commit()
    return cur.lastrowid


CAMPOS_EDITABLES = {
    "sku", "nombre", "categoria", "costo", "precio_venta", "moneda", "gastos_variables",
    "margen_minimo_pct", "precio_competencia", "activo",
}


def actualizar_producto(conn, producto_id: int, **campos) -> None:
    """Edita campos del producto. NO permite tocar stock_actual (usar movimientos)."""
    invalidos = set(campos) - CAMPOS_EDITABLES
    if invalidos:
        raise ValueError(f"Campos no editables: {sorted(invalidos)}")
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn.execute(
        f"UPDATE productos SET {sets}, actualizado_en = ? WHERE id = ?",
        (*campos.values(), ahora_iso(), producto_id),
    )
    conn.commit()


def obtener_producto(conn, producto_id: int) -> Optional[dict]:
    return conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()


def listar_productos(conn, solo_activos: bool = True) -> list[dict]:
    q = "SELECT * FROM productos"
    if solo_activos:
        q += " WHERE activo = 1"
    return conn.execute(q + " ORDER BY nombre").fetchall()


# ----------------------------------------------------------------------------- movimientos
def registrar_movimiento(
    conn,
    producto_id: int,
    tipo: str,
    cantidad: int,
    usuario: Optional[str] = None,
    motivo: Optional[str] = None,
    fecha: Optional[str] = None,
    venta_id: Optional[int] = None,
) -> int:
    """Registra un movimiento y actualiza el stock del producto atómicamente.

    `cantidad` es siempre positiva. Para `ajuste`, `cantidad` es el stock CONTADO físicamente
    (el sistema calcula la discrepancia contra el stock esperado).
    """
    if tipo not in TIPOS_MOVIMIENTO:
        raise ValueError(f"Tipo de movimiento inválido: {tipo!r}. Usa uno de {TIPOS_MOVIMIENTO}")
    if cantidad < 0 or (tipo != "ajuste" and cantidad == 0):
        raise ValueError("La cantidad debe ser positiva")
    prod = obtener_producto(conn, producto_id)
    if prod is None:
        raise ValueError(f"Producto {producto_id} no existe")

    stock_actual = prod["stock_actual"]
    stock_esperado = stock_contado = None
    if tipo == "entrada":
        delta = cantidad
    elif tipo in ("salida", "merma"):
        delta = -cantidad
    else:  # ajuste por conteo
        stock_esperado, stock_contado = stock_actual, cantidad
        delta = stock_contado - stock_esperado
    stock_resultante = stock_actual + delta

    cur = conn.execute(
        "INSERT INTO movimientos_inventario (producto_id, tipo, delta, stock_resultante, "
        "stock_esperado, stock_contado, fecha, usuario, motivo, venta_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (producto_id, tipo, delta, stock_resultante, stock_esperado, stock_contado,
         fecha or ahora_iso(), usuario, motivo, venta_id),
    )
    conn.execute(
        "UPDATE productos SET stock_actual = ?, actualizado_en = ? WHERE id = ?",
        (stock_resultante, ahora_iso(), producto_id),
    )
    conn.commit()
    return cur.lastrowid


def listar_movimientos(
    conn, producto_id: Optional[int] = None, desde: Optional[str] = None, limite: Optional[int] = None
) -> list[dict]:
    q, params = "SELECT * FROM movimientos_inventario WHERE 1=1", []
    if producto_id is not None:
        q += " AND producto_id = ?"
        params.append(producto_id)
    if desde is not None:
        q += " AND fecha >= ?"
        params.append(desde)
    q += " ORDER BY fecha, id"
    if limite:
        q += f" LIMIT {int(limite)}"
    return conn.execute(q, params).fetchall()


# ----------------------------------------------------------------------------- ventas
def registrar_venta(
    conn,
    producto_id: int,
    cantidad: int,
    precio_unitario: Optional[float] = None,
    usuario: Optional[str] = None,
    fecha: Optional[str] = None,
    nota: Optional[str] = None,
) -> int:
    if cantidad <= 0:
        raise ValueError("La cantidad vendida debe ser positiva")
    prod = obtener_producto(conn, producto_id)
    if prod is None:
        raise ValueError(f"Producto {producto_id} no existe")
    fecha = fecha or ahora_iso()
    cur = conn.execute(
        "INSERT INTO ventas (producto_id, cantidad, precio_unitario, costo_unitario, moneda, fecha, "
        "usuario, nota) VALUES (?,?,?,?,?,?,?,?)",
        (producto_id, cantidad,
         prod["precio_venta"] if precio_unitario is None else precio_unitario,
         prod["costo"], prod["moneda"], fecha, usuario, nota),
    )
    venta_id = cur.lastrowid
    registrar_movimiento(conn, producto_id, "salida", cantidad, usuario=usuario,
                         motivo="venta", fecha=fecha, venta_id=venta_id)
    return venta_id


def listar_ventas(conn, producto_id: Optional[int] = None, desde: Optional[str] = None) -> list[dict]:
    q, params = "SELECT * FROM ventas WHERE 1=1", []
    if producto_id is not None:
        q += " AND producto_id = ?"
        params.append(producto_id)
    if desde is not None:
        q += " AND fecha >= ?"
        params.append(desde)
    return conn.execute(q + " ORDER BY fecha, id", params).fetchall()


# ----------------------------------------------------------------------------- objetivos / costos
def fijar_objetivo(conn, anio: int, mes: int, monto: float, moneda: str = "COP") -> None:
    conn.execute(
        "INSERT INTO objetivos_ingreso (anio, mes, monto, moneda) VALUES (?,?,?,?) "
        "ON CONFLICT(anio, mes) DO UPDATE SET monto = excluded.monto, moneda = excluded.moneda",
        (anio, mes, monto, moneda),
    )
    conn.commit()


def obtener_objetivo(conn, anio: int, mes: int) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM objetivos_ingreso WHERE anio = ? AND mes = ?", (anio, mes)
    ).fetchone()


def agregar_costo_fijo(conn, nombre: str, monto_mensual: float, moneda: str = "COP") -> int:
    cur = conn.execute(
        "INSERT INTO costos_fijos (nombre, monto_mensual, moneda) VALUES (?,?,?)",
        (nombre, monto_mensual, moneda),
    )
    conn.commit()
    return cur.lastrowid


def listar_costos_fijos(conn) -> list[dict]:
    return conn.execute("SELECT * FROM costos_fijos ORDER BY nombre").fetchall()


def eliminar_costo_fijo(conn, costo_id: int) -> None:
    conn.execute("DELETE FROM costos_fijos WHERE id = ?", (costo_id,))
    conn.commit()


def total_costos_fijos(conn) -> float:
    row = conn.execute("SELECT COALESCE(SUM(monto_mensual), 0) AS t FROM costos_fijos").fetchone()
    return float(row["t"])


# ----------------------------------------------------------------------------- tasas
def obtener_tasas(conn) -> dict[str, dict]:
    return {r["moneda"]: r for r in conn.execute("SELECT * FROM tasas_cambio").fetchall()}


def fijar_tasa(conn, moneda: str, tasa_por_usd: float, fuente: str = "manual") -> None:
    if moneda not in MONEDAS:
        raise ValueError(f"Moneda no soportada: {moneda}")
    if moneda == "USD":
        tasa_por_usd = 1.0
    if tasa_por_usd <= 0:
        raise ValueError("La tasa debe ser positiva")
    conn.execute(
        "INSERT INTO tasas_cambio (moneda, tasa_por_usd, fuente, actualizado_en) VALUES (?,?,?,?) "
        "ON CONFLICT(moneda) DO UPDATE SET tasa_por_usd = excluded.tasa_por_usd, "
        "fuente = excluded.fuente, actualizado_en = excluded.actualizado_en",
        (moneda, float(tasa_por_usd), fuente, ahora_iso()),
    )
    conn.commit()


# ----------------------------------------------------------------------------- recomendaciones
def guardar_recomendacion(conn, producto_id: int, rec: dict) -> int:
    cur = conn.execute(
        "INSERT INTO recomendaciones (producto_id, fecha, fuente, precio_sugerido, precio_original, "
        "ajustado_guardrail, precio_minimo_viable, margen_resultante_pct, justificacion, riesgo, "
        "detalle_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (producto_id, ahora_iso(), rec["fuente"], rec["precio_sugerido"], rec["precio_original"],
         int(rec["ajustado_guardrail"]), rec.get("precio_minimo_viable"),
         rec.get("margen_resultante_pct"), rec.get("justificacion"), rec.get("riesgo"),
         json.dumps(rec.get("detalle", {}), ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def _con_detalle(fila: Optional[dict]) -> Optional[dict]:
    if fila is None:
        return None
    try:
        fila["detalle"] = json.loads(fila.get("detalle_json") or "{}")
    except (TypeError, ValueError):
        fila["detalle"] = {}
    return fila


def ultima_recomendacion(conn, producto_id: int) -> Optional[dict]:
    """Última recomendación guardada para un producto (con `detalle` ya parseado)."""
    fila = conn.execute(
        "SELECT * FROM recomendaciones WHERE producto_id = ? ORDER BY fecha DESC, id DESC LIMIT 1", (producto_id,)
    ).fetchone()
    return _con_detalle(fila)


def ultimas_recomendaciones_por_producto(conn) -> dict[int, dict]:
    """{producto_id: última recomendación} para todos los productos con alguna guardada."""
    filas = conn.execute(
        "SELECT r.* FROM recomendaciones r JOIN (SELECT producto_id, MAX(id) AS mid FROM recomendaciones GROUP BY producto_id) u "
        "ON r.id = u.mid"
    ).fetchall()
    return {f["producto_id"]: _con_detalle(f) for f in filas}


def listar_recomendaciones(conn, producto_id: Optional[int] = None, limite: int = 20) -> list[dict]:
    q, params = "SELECT * FROM recomendaciones", []
    if producto_id is not None:
        q += " WHERE producto_id = ?"
        params.append(producto_id)
    return conn.execute(q + f" ORDER BY fecha DESC, id DESC LIMIT {int(limite)}", params).fetchall()
