"""Detección de anomalías de inventario (Fase 7) — estadística pura, sin LLM.

Señales detectadas (cada alerta trae los números que la sustentan en `evidencia`):

| tipo                     | método                                                                 |
|--------------------------|------------------------------------------------------------------------|
| merma_grande             | z-score de la merma vs. historial del producto (>= 4 mermas previas, z > 2) |
|                          | o merma > 25 % del stock previo cuando no hay historial suficiente     |
| merma_frecuente          | >= 3 mermas del mismo producto en una ventana de 7 días                |
| usuario_mermas           | un usuario concentra >= 60 % de las mermas (>= 4) teniendo < 50 % de los movimientos |
| fuera_horario            | ajustes o mermas registrados fuera de [hora_apertura, hora_cierre) local |
| discrepancia_recurrente  | >= 2 ajustes por conteo con faltante en 60 días para el mismo producto |
| stock_negativo           | un movimiento dejó el stock por debajo de cero (integridad de datos)   |

Las fechas de la BD están en UTC; `offset_horas` las lleva a hora local (Colombia = -5).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

SEVERIDAD_ORDEN = {"alta": 0, "media": 1, "baja": 2}


@dataclass
class Alerta:
    tipo: str
    severidad: str                 # alta | media | baja
    descripcion: str
    producto_id: Optional[int] = None
    producto_nombre: Optional[str] = None
    usuario: Optional[str] = None
    fecha: Optional[str] = None    # fecha del hecho (o del último hecho)
    evidencia: dict = field(default_factory=dict)

    def a_dict(self) -> dict:
        return asdict(self)


def _parse(fecha: str) -> datetime:
    dt = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt(v) -> str:
    return f"{v:,.0f}".replace(",", ".") if isinstance(v, (int, float)) else str(v)


def detectar_anomalias(
    movimientos: list[dict],
    productos: Optional[dict[int, dict]] = None,
    ahora: Optional[datetime] = None,
    hora_apertura: int = 7,
    hora_cierre: int = 20,
    offset_horas: float = -5.0,
    z_umbral: float = 2.0,
    min_historial: int = 4,
    merma_pct_stock: float = 25.0,
    ventana_frecuencia_dias: int = 7,
    min_mermas_ventana: int = 3,
    ventana_discrepancia_dias: int = 60,
    min_discrepancias: int = 2,
) -> list[Alerta]:
    productos = productos or {}
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    nombre = lambda pid: (productos.get(pid) or {}).get("nombre") or f"producto #{pid}"  # noqa: E731
    movs = sorted((dict(m, _dt=_parse(m["fecha"])) for m in movimientos if m.get("fecha")), key=lambda m: m["_dt"])
    alertas: list[Alerta] = []

    por_producto: dict[int, list[dict]] = defaultdict(list)
    for m in movs:
        por_producto[m["producto_id"]].append(m)

    # ------------------------------------------------------------- mermas grandes / frecuentes
    for pid, lista in por_producto.items():
        mermas = [m for m in lista if m["tipo"] == "merma"]
        for idx, m in enumerate(mermas):
            unidades = abs(m["delta"])
            previas = [abs(x["delta"]) for x in mermas[:idx]]
            stock_previo = (m.get("stock_resultante") or 0) + unidades
            if len(previas) >= min_historial:
                media = statistics.mean(previas)
                desv = statistics.pstdev(previas)
                z = (unidades - media) / desv if desv > 0 else (float("inf") if unidades > media else 0.0)
                if z > z_umbral and unidades > media:
                    alertas.append(Alerta(
                        "merma_grande", "alta" if z > 3 or unidades >= stock_previo * 0.5 else "media",
                        f"Merma de {_fmt(unidades)} unidades de {nombre(pid)} registrada por {m.get('usuario') or 'usuario desconocido'}: "
                        f"{z:.1f} desviaciones estándar por encima de la media histórica ({media:.1f} unidades en {len(previas)} mermas previas).",
                        pid, nombre(pid), m.get("usuario"), m["fecha"],
                        {"unidades": unidades, "media_historica": round(media, 2), "desviacion": round(desv, 2),
                         "z_score": round(z, 2) if z != float("inf") else 99.0, "mermas_previas": len(previas)},
                    ))
            elif stock_previo > 0 and unidades / stock_previo * 100 >= merma_pct_stock:
                pct = unidades / stock_previo * 100
                alertas.append(Alerta(
                    "merma_grande", "alta" if pct >= 50 else "media",
                    f"Merma de {_fmt(unidades)} unidades de {nombre(pid)} ({pct:.0f}% del stock previo de {_fmt(stock_previo)}) "
                    f"registrada por {m.get('usuario') or 'usuario desconocido'}; no hay historial suficiente para comparar.",
                    pid, nombre(pid), m.get("usuario"), m["fecha"],
                    {"unidades": unidades, "stock_previo": stock_previo, "pct_stock": round(pct, 1)},
                ))
        # frecuencia: ventana deslizante
        ventana = timedelta(days=ventana_frecuencia_dias)
        reportado_hasta = None
        for i, m in enumerate(mermas):
            en_ventana = [x for x in mermas[i:] if x["_dt"] - m["_dt"] <= ventana]
            if len(en_ventana) >= min_mermas_ventana and (reportado_hasta is None or m["_dt"] > reportado_hasta):
                total = sum(abs(x["delta"]) for x in en_ventana)
                usuarios = Counter(x.get("usuario") for x in en_ventana)
                alertas.append(Alerta(
                    "merma_frecuente", "media" if len(en_ventana) < 2 * min_mermas_ventana else "alta",
                    f"{len(en_ventana)} mermas de {nombre(pid)} en {ventana_frecuencia_dias} días ({_fmt(total)} unidades en total) "
                    f"entre {en_ventana[0]['fecha'][:10]} y {en_ventana[-1]['fecha'][:10]}; usuarios: {dict(usuarios)}.",
                    pid, nombre(pid), usuarios.most_common(1)[0][0] if len(usuarios) == 1 else None, en_ventana[-1]["fecha"],
                    {"conteo": len(en_ventana), "unidades": total, "desde": en_ventana[0]["fecha"], "hasta": en_ventana[-1]["fecha"]},
                ))
                reportado_hasta = en_ventana[-1]["_dt"]

    # ------------------------------------------------------------- concentración por usuario
    mermas_todas = [m for m in movs if m["tipo"] == "merma" and m.get("usuario")]
    if len(mermas_todas) >= 4:
        por_usuario_mermas = Counter(m["usuario"] for m in mermas_todas)
        por_usuario_movs = Counter(m["usuario"] for m in movs if m.get("usuario"))
        total_movs = sum(por_usuario_movs.values())
        for u, n in por_usuario_mermas.items():
            part = n / len(mermas_todas) * 100
            part_movs = por_usuario_movs[u] / total_movs * 100 if total_movs else 0
            if n >= 4 and part >= 60 and part_movs < 50:
                unidades = sum(abs(m["delta"]) for m in mermas_todas if m["usuario"] == u)
                alertas.append(Alerta(
                    "usuario_mermas", "alta" if part >= 80 else "media",
                    f"{u} registró {n} de {len(mermas_todas)} mermas ({part:.0f}%, {_fmt(unidades)} unidades) aunque solo hizo el "
                    f"{part_movs:.0f}% de los movimientos de inventario.",
                    None, None, u, max(m["fecha"] for m in mermas_todas if m["usuario"] == u),
                    {"mermas_usuario": n, "mermas_total": len(mermas_todas), "participacion_mermas_pct": round(part, 1),
                     "participacion_movimientos_pct": round(part_movs, 1), "unidades": unidades},
                ))

    # ------------------------------------------------------------- fuera de horario
    for m in movs:
        if m["tipo"] not in ("ajuste", "merma"):
            continue
        local = m["_dt"] + timedelta(hours=offset_horas)
        h = local.hour
        if not (hora_apertura <= h < hora_cierre):
            alertas.append(Alerta(
                "fuera_horario", "media",
                f"{m['tipo'].capitalize()} de {nombre(m['producto_id'])} ({m['delta']:+d} unidades) registrado por "
                f"{m.get('usuario') or 'usuario desconocido'} a las {local.strftime('%H:%M')} (hora local), fuera del horario "
                f"{hora_apertura:02d}:00-{hora_cierre:02d}:00.",
                m["producto_id"], nombre(m["producto_id"]), m.get("usuario"), m["fecha"],
                {"hora_local": h, "delta": m["delta"], "tipo_movimiento": m["tipo"]},
            ))

    # ------------------------------------------------------------- discrepancias recurrentes
    ventana_d = timedelta(days=ventana_discrepancia_dias)
    for pid, lista in por_producto.items():
        ajustes = [m for m in lista if m["tipo"] == "ajuste" and m.get("stock_esperado") is not None
                   and m.get("stock_contado") is not None and m["stock_contado"] < m["stock_esperado"]]
        recientes = [m for m in ajustes if ahora - m["_dt"] <= ventana_d] or ajustes
        if len(recientes) >= min_discrepancias and recientes[-1]["_dt"] - recientes[0]["_dt"] <= ventana_d:
            faltante = sum(m["stock_esperado"] - m["stock_contado"] for m in recientes)
            usuarios = Counter(m.get("usuario") for m in recientes)
            alertas.append(Alerta(
                "discrepancia_recurrente", "alta" if len(recientes) >= 3 else "media",
                f"{len(recientes)} conteos de {nombre(pid)} con faltante en {ventana_discrepancia_dias} días: {_fmt(faltante)} unidades "
                f"menos de lo esperado en total (último: esperado {_fmt(recientes[-1]['stock_esperado'])}, contado "
                f"{_fmt(recientes[-1]['stock_contado'])}). Usuarios: {dict(usuarios)}.",
                pid, nombre(pid), usuarios.most_common(1)[0][0] if len(usuarios) == 1 else None, recientes[-1]["fecha"],
                {"conteo": len(recientes), "faltante_acumulado": faltante, "desde": recientes[0]["fecha"], "hasta": recientes[-1]["fecha"]},
            ))

    # ------------------------------------------------------------- stock negativo
    for m in movs:
        if m.get("stock_resultante") is not None and m["stock_resultante"] < 0:
            alertas.append(Alerta(
                "stock_negativo", "alta",
                f"El stock de {nombre(m['producto_id'])} quedó en {m['stock_resultante']} tras un movimiento de {m['tipo']} "
                f"({m['delta']:+d}) por {m.get('usuario') or 'usuario desconocido'}: hay ventas o salidas sin entrada registrada.",
                m["producto_id"], nombre(m["producto_id"]), m.get("usuario"), m["fecha"],
                {"stock_resultante": m["stock_resultante"], "delta": m["delta"]},
            ))

    alertas.sort(key=lambda a: (SEVERIDAD_ORDEN[a.severidad], a.fecha or ""), reverse=False)
    return alertas


def detectar_en_bd(conn, ahora: Optional[datetime] = None, dias: int = 120) -> list[Alerta]:
    """Lee movimientos recientes y configuración de horario de la BD y devuelve las alertas."""
    from . import db
    ahora = ahora or datetime.now(timezone.utc)
    desde = (ahora - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    movs = db.listar_movimientos(conn, desde=desde)
    productos = {p["id"]: p for p in db.listar_productos(conn, solo_activos=False)}
    return detectar_anomalias(
        movs, productos, ahora=ahora,
        hora_apertura=int(db.obtener_config(conn, "hora_apertura", "7")),
        hora_cierre=int(db.obtener_config(conn, "hora_cierre", "20")),
        offset_horas=float(db.obtener_config(conn, "zona_horaria_offset", "-5")),
    )
