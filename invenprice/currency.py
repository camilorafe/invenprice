"""Multi-moneda offline-first (Fase 3).

* Cada producto vive en su moneda base; el motor financiero SIEMPRE opera en esa moneda.
* Las demás monedas (USD, COP, CNY) son solo de visualización, vía `convertir`.
* Tasas expresadas como "unidades de la moneda por 1 USD" (USD = 1.0).

Modo A (default): tabla de tasas manual en la BD, editable desde la UI. Cero internet.
Modo B (opcional): `actualizar_tasas_online` consulta una API pública gratuita. Si el modo está
desactivado, no hay internet, la API falla o la respuesta es basura, se devuelve la ÚLTIMA tasa
guardada. Esta función jamás lanza excepciones.
"""
from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from . import MONEDAS
from . import db

DECIMALES = {"USD": 2, "COP": 0, "CNY": 2}
SIMBOLO = {"USD": "US$", "COP": "$", "CNY": "¥"}
API_URL_DEFAULT = "https://open.er-api.com/v6/latest/USD"  # gratuita, sin API key


# =============================================================================== conversión pura
def _validar_moneda(m: str) -> None:
    if m not in MONEDAS:
        raise ValueError(f"Moneda no soportada: {m!r}. Soportadas: {MONEDAS}")


def convertir(monto: float, de: str, a: str, tasas: dict[str, float]) -> float:
    """Convierte `monto` de la moneda `de` a la moneda `a` usando tasas por USD."""
    _validar_moneda(de)
    _validar_moneda(a)
    if de == a:
        return monto
    for m in (de, a):
        t = tasas.get(m)
        if t is None or not isinstance(t, (int, float)) or t <= 0:
            raise ValueError(f"Tasa inválida o faltante para {m}: {t!r}")
    return monto / tasas[de] * tasas[a]


def en_todas_las_monedas(monto: float, moneda_base: str, tasas: dict[str, float]) -> dict[str, float]:
    return {m: convertir(monto, moneda_base, m, tasas) for m in MONEDAS}


def redondear(monto: float, moneda: str) -> float:
    d = DECIMALES.get(moneda, 2)
    factor = 10 ** d
    # redondeo "half up" clásico, evita sorpresas del bancario de round()
    val = math.floor(monto * factor + 0.5) / factor
    return int(val) if d == 0 else val


def redondear_arriba(monto: float, moneda: str) -> float:
    """Para precios MÍNIMOS: nunca redondear hacia abajo (rompería el margen)."""
    d = DECIMALES.get(moneda, 2)
    factor = 10 ** d
    val = math.ceil(round(monto * factor, 6)) / factor
    return int(val) if d == 0 else val


def formatear(monto: float, moneda: str) -> str:
    d = DECIMALES.get(moneda, 2)
    r = redondear(monto, moneda)
    if d == 0:
        cuerpo = f"{int(r):,}".replace(",", ".")
        return f"{SIMBOLO[moneda]} {cuerpo} {moneda}"
    return f"{SIMBOLO[moneda]} {r:,.{d}f}"


# =============================================================================== Modo A (BD)
def tasas_actuales(conn) -> dict[str, float]:
    return {m: float(r["tasa_por_usd"]) for m, r in db.obtener_tasas(conn).items()}


def fijar_tasa_manual(conn, moneda: str, tasa_por_usd: float) -> None:
    db.fijar_tasa(conn, moneda, tasa_por_usd, fuente="manual")


def convertir_con_bd(conn, monto: float, de: str, a: str) -> float:
    return convertir(monto, de, a, tasas_actuales(conn))


# =============================================================================== Modo B (opcional)
@dataclass(frozen=True)
class ResultadoActualizacion:
    exito: bool
    motivo: str                 # "ok" | "modo_b_desactivado" | descripción del error
    tasas: dict[str, float]     # tasas vigentes tras el intento (nuevas o las guardadas)


def modo_online_activo(conn) -> bool:
    return db.obtener_config(conn, "modo_tasas_online", "0") == "1"


def activar_modo_online(conn, activo: bool) -> None:
    """Única opción de configuración para encender/apagar el Modo B."""
    db.fijar_config(conn, "modo_tasas_online", "1" if activo else "0")


def parsear_respuesta_api(payload) -> dict[str, float]:
    """Extrae {COP: x, CNY: y} del formato de open.er-api.com ({"rates": {...}}).
    Acepta también un dict plano {COP: x, CNY: y}. Lanza ValueError si falta algo o es inválido."""
    if not isinstance(payload, dict):
        raise ValueError("la respuesta no es un objeto JSON")
    fuente = payload.get("rates", payload)
    if not isinstance(fuente, dict):
        raise ValueError("campo 'rates' inválido")
    out = {}
    for m in MONEDAS:
        if m == "USD":
            continue
        v = fuente.get(m)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"tasa inválida para {m}: {v!r}")
        out[m] = float(v)
    return out


def _fetcher_http(url: str) -> Callable[[float], dict]:
    def fetch(timeout: float):
        req = urllib.request.Request(url, headers={"User-Agent": "InvenPrice/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL fija, configurable)
            return json.loads(resp.read().decode("utf-8"))
    return fetch


def actualizar_tasas_online(
    conn,
    fetcher: Optional[Callable[[float], dict]] = None,
    url: str = API_URL_DEFAULT,
    timeout: float = 5.0,
) -> ResultadoActualizacion:
    """Intenta refrescar COP y CNY desde la API. Nunca lanza; siempre devuelve tasas usables."""
    guardadas = tasas_actuales(conn)
    if not modo_online_activo(conn):
        return ResultadoActualizacion(False, "modo_b_desactivado", guardadas)
    try:
        fetch = fetcher or _fetcher_http(url)
        nuevas = parsear_respuesta_api(fetch(timeout))
        for m, t in nuevas.items():
            db.fijar_tasa(conn, m, t, fuente="api")
        return ResultadoActualizacion(True, "ok", tasas_actuales(conn))
    except Exception as e:  # red, timeout, JSON inválido, datos basura: todo cae al fallback
        return ResultadoActualizacion(False, f"{type(e).__name__}: {e}", guardadas)
