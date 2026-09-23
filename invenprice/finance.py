"""Motor financiero determinista (Fase 2).

Todas las funciones son PURAS: reciben números, devuelven números o dataclasses inmutables.
Ninguna cifra financiera del sistema sale de otro lugar que no sea este módulo.

Convenciones
------------
* Los importes se expresan en la moneda base del producto (nunca se mezclan monedas aquí).
* Los porcentajes se devuelven en escala 0-100 (33.33 significa 33,33 %).
* Los casos borde NO lanzan excepciones ni devuelven inf/nan: devuelven un `Estado` explícito.

MARGEN vs MARKUP (no son lo mismo)
----------------------------------
* margen  = (precio - costo) / PRECIO   -> "qué parte de cada peso vendido es ganancia"
* markup  = (precio - costo) / COSTO    -> "cuánto se le suma al costo"
  Ejemplo: costo 10.000, precio 15.000 -> margen 33,3 %, markup 50 %.
  Identidad: margen = markup / (1 + markup)  ;  markup = margen / (1 - margen).

Casos borde y su representación
-------------------------------
* costo = 0        -> margen = 100 %; markup = Estado.INDEFINIDO (división por cero: no existe).
* precio = 0       -> margen % = None (no hay base sobre la que medir).
* margen negativo  -> se devuelve el número negativo tal cual (venta por debajo del costo).
* margen unitario <= 0 con costos fijos > 0 u objetivo > 0 -> Estado.NO_ALCANZABLE.
* objetivo <= 0    -> Estado.OBJETIVO_INVALIDO.
* margen objetivo >= 100 % -> Estado.INDEFINIDO (no existe precio finito).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

PRECISION_PCT = 4      # decimales en porcentajes
PRECISION_MONTO = 4    # decimales en importes (la UI redondea según la moneda)


class Estado(str, Enum):
    OK = "ok"
    INDEFINIDO = "indefinido"              # la magnitud no existe matemáticamente (p.ej. markup con costo 0)
    NO_ALCANZABLE = "no_alcanzable"        # con margen <= 0 nunca se cubre el objetivo
    OBJETIVO_INVALIDO = "objetivo_invalido"  # objetivo <= 0
    ENTRADA_INVALIDA = "entrada_invalida"  # costo negativo, costos fijos negativos, etc.


def _r_pct(x: float) -> float:
    return round(float(x), PRECISION_PCT)


def _r_monto(x: float) -> float:
    return round(float(x), PRECISION_MONTO)


# =============================================================================== márgenes
def margen_bruto_unitario(precio: float, costo: float) -> float:
    """Ganancia bruta por unidad = precio - costo. Puede ser negativa."""
    return _r_monto(precio - costo)


def margen_bruto_pct(precio: float, costo: float) -> Optional[float]:
    """Margen bruto sobre el PRECIO. None si precio == 0 (indefinido)."""
    if precio == 0:
        return None
    return _r_pct((precio - costo) / precio * 100)


def margen_neto_unitario(
    precio: float, costo: float, gastos_variables: float = 0.0, costo_fijo_asignado: float = 0.0
) -> float:
    """Ganancia neta por unidad tras gastos variables (comisiones, empaque, impuestos sobre
    la venta) y la porción de costos fijos asignada a cada unidad."""
    return _r_monto(precio - costo - gastos_variables - costo_fijo_asignado)


def margen_neto_pct(
    precio: float, costo: float, gastos_variables: float = 0.0, costo_fijo_asignado: float = 0.0
) -> Optional[float]:
    if precio == 0:
        return None
    return _r_pct(margen_neto_unitario(precio, costo, gastos_variables, costo_fijo_asignado) / precio * 100)


@dataclass(frozen=True)
class Markup:
    estado: Estado
    valor_pct: Optional[float]


def markup_pct(precio: float, costo: float) -> Markup:
    """Markup sobre el COSTO. Con costo 0 no existe (INDEFINIDO)."""
    if costo == 0:
        return Markup(Estado.INDEFINIDO, None)
    return Markup(Estado.OK, _r_pct((precio - costo) / costo * 100))


def margen_desde_markup(markup_pct_: float) -> float:
    """margen = markup / (1 + markup)."""
    m = markup_pct_ / 100
    return _r_pct(m / (1 + m) * 100)


def markup_desde_margen(margen_pct_: float) -> Optional[float]:
    """markup = margen / (1 - margen). None si margen >= 100 %."""
    m = margen_pct_ / 100
    if m >= 1:
        return None
    return _r_pct(m / (1 - m) * 100)


# =============================================================================== precio mínimo
@dataclass(frozen=True)
class PrecioMinimo:
    estado: Estado
    precio: Optional[float]
    margen_objetivo_pct: float
    costo_total_unitario: float  # costo + gastos variables


def precio_minimo_viable(
    costo: float, margen_objetivo_pct: float, gastos_variables: float = 0.0
) -> PrecioMinimo:
    """Precio más bajo que aún cumple el margen objetivo (sobre precio).

    precio_min = (costo + gastos_variables) / (1 - margen/100)
    Ejemplo: costo 10.000, margen 40 % -> 16.666,67.
    """
    base = costo + gastos_variables
    if costo < 0 or gastos_variables < 0:
        return PrecioMinimo(Estado.ENTRADA_INVALIDA, None, margen_objetivo_pct, base)
    if margen_objetivo_pct >= 100:
        return PrecioMinimo(Estado.INDEFINIDO, None, margen_objetivo_pct, base)
    precio = base / (1 - margen_objetivo_pct / 100)
    return PrecioMinimo(Estado.OK, _r_monto(precio), margen_objetivo_pct, base)


# =============================================================================== break-even
@dataclass(frozen=True)
class PuntoEquilibrio:
    estado: Estado
    unidades: Optional[int]            # redondeado hacia arriba (no se venden fracciones)
    unidades_exactas: Optional[float]
    ingreso: Optional[float]           # ventas necesarias para cubrir costos fijos
    margen_unitario: float             # margen de contribución por unidad usado
    costos_fijos: float
    detalle: list = field(default_factory=list)  # solo en el agregado: unidades por producto


def punto_equilibrio(
    costos_fijos: float, precio: float, costo: float, gastos_variables: float = 0.0
) -> PuntoEquilibrio:
    """Unidades que hay que vender para cubrir `costos_fijos` con este producto.

    unidades = costos_fijos / (precio - costo - gastos_variables)
    * costos_fijos == 0 -> 0 unidades (ya en equilibrio).
    * margen unitario <= 0 con costos fijos > 0 -> NO_ALCANZABLE.
    """
    mu = margen_neto_unitario(precio, costo, gastos_variables)
    if costos_fijos < 0:
        return PuntoEquilibrio(Estado.ENTRADA_INVALIDA, None, None, None, mu, costos_fijos)
    if costos_fijos == 0:
        return PuntoEquilibrio(Estado.OK, 0, 0.0, 0.0, mu, costos_fijos)
    if mu <= 0:
        return PuntoEquilibrio(Estado.NO_ALCANZABLE, None, None, None, mu, costos_fijos)
    exactas = costos_fijos / mu
    unidades = math.ceil(exactas - 1e-9)
    return PuntoEquilibrio(Estado.OK, unidades, _r_monto(exactas), _r_monto(unidades * precio), mu, costos_fijos)


def punto_equilibrio_agregado(costos_fijos: float, productos: Sequence[dict]) -> PuntoEquilibrio:
    """Break-even del negocio completo dado un mix de ventas.

    Cada producto aporta `precio_venta`, `costo`, `gastos_variables` (opcional) y `mix`
    (proporción o unidades relativas vendidas; se normaliza). Se calcula el margen de
    contribución ponderado por unidad y se reparten las unidades según el mix.
    """
    if costos_fijos < 0:
        return PuntoEquilibrio(Estado.ENTRADA_INVALIDA, None, None, None, 0.0, costos_fijos)
    if costos_fijos == 0:
        return PuntoEquilibrio(Estado.OK, 0, 0.0, 0.0, 0.0, costos_fijos)
    total_mix = sum(max(float(p.get("mix", 1)), 0.0) for p in productos)
    if not productos or total_mix <= 0:
        return PuntoEquilibrio(Estado.NO_ALCANZABLE, None, None, None, 0.0, costos_fijos)

    margen_pond = 0.0
    for p in productos:
        w = max(float(p.get("mix", 1)), 0.0) / total_mix
        margen_pond += w * margen_neto_unitario(p["precio_venta"], p["costo"], p.get("gastos_variables", 0.0))
    if margen_pond <= 0:
        return PuntoEquilibrio(Estado.NO_ALCANZABLE, None, None, None, _r_monto(margen_pond), costos_fijos)

    exactas = costos_fijos / margen_pond
    unidades = math.ceil(exactas - 1e-9)
    detalle, ingreso = [], 0.0
    for p in productos:
        w = max(float(p.get("mix", 1)), 0.0) / total_mix
        u = exactas * w
        ingreso += u * p["precio_venta"]
        detalle.append({"nombre": p.get("nombre"), "id": p.get("id"), "unidades": _r_monto(u), "peso": _r_pct(w * 100)})
    return PuntoEquilibrio(Estado.OK, unidades, _r_monto(exactas), _r_monto(ingreso), _r_monto(margen_pond), costos_fijos, detalle)


# =============================================================================== objetivo mensual
@dataclass(frozen=True)
class UnidadesObjetivo:
    estado: Estado
    unidades: Optional[int]
    unidades_exactas: Optional[float]
    ingreso_bruto_estimado: Optional[float]  # unidades * precio
    objetivo: float
    margen_unitario: Optional[float]


def unidades_para_objetivo(objetivo_mensual: float, margen_unitario: float, precio: float) -> UnidadesObjetivo:
    """Unidades a vender para que la GANANCIA (margen) del mes alcance `objetivo_mensual`.

    unidades = objetivo / margen_unitario   (ej.: 2.000.000 / 5.000 = 400)
    * objetivo <= 0 -> OBJETIVO_INVALIDO.
    * margen_unitario <= 0 -> NO_ALCANZABLE (vender más no acerca al objetivo).
    """
    if objetivo_mensual <= 0:
        return UnidadesObjetivo(Estado.OBJETIVO_INVALIDO, None, None, None, objetivo_mensual, margen_unitario)
    if margen_unitario <= 0:
        return UnidadesObjetivo(Estado.NO_ALCANZABLE, None, None, None, objetivo_mensual, margen_unitario)
    exactas = objetivo_mensual / margen_unitario
    unidades = math.ceil(exactas - 1e-9)
    return UnidadesObjetivo(Estado.OK, unidades, _r_monto(exactas), _r_monto(unidades * precio), objetivo_mensual, margen_unitario)


def unidades_para_ingreso_bruto(objetivo_mensual: float, precio: float) -> UnidadesObjetivo:
    """Variante por FACTURACIÓN: unidades = objetivo / precio."""
    if objetivo_mensual <= 0:
        return UnidadesObjetivo(Estado.OBJETIVO_INVALIDO, None, None, None, objetivo_mensual, None)
    if precio <= 0:
        return UnidadesObjetivo(Estado.NO_ALCANZABLE, None, None, None, objetivo_mensual, None)
    exactas = objetivo_mensual / precio
    unidades = math.ceil(exactas - 1e-9)
    return UnidadesObjetivo(Estado.OK, unidades, _r_monto(exactas), _r_monto(unidades * precio), objetivo_mensual, None)


# =============================================================================== rentabilidad marginal
@dataclass(frozen=True)
class Rentabilidad:
    id: Optional[int]
    nombre: str
    margen_unitario: float
    margen_pct: Optional[float]
    unidades_vendidas: float
    contribucion_total: float     # margen_unitario * unidades
    participacion_pct: float      # % sobre la contribución positiva total
    clasificacion: str            # estrella | margen_alto_rotacion_baja | margen_bajo_rotacion_alta | no_conviene | sin_ventas | normal


def rentabilidad_marginal(productos: Sequence[dict]) -> list[Rentabilidad]:
    """Ordena productos por contribución total y los clasifica.

    Umbrales (relativos al propio catálogo, para no depender del sector):
    * rotación alta  = unidades >= mediana de unidades vendidas
    * margen alto    = margen % >= mediana de márgenes
    """
    if not productos:
        return []
    filas = []
    for p in productos:
        mu = margen_neto_unitario(p["precio_venta"], p["costo"], p.get("gastos_variables", 0.0))
        mpct = margen_neto_pct(p["precio_venta"], p["costo"], p.get("gastos_variables", 0.0))
        u = float(p.get("unidades_vendidas", 0) or 0)
        filas.append((p, mu, mpct, u, _r_monto(mu * u)))

    total_pos = sum(c for *_, c in filas if c > 0)
    unidades_ord = sorted(u for *_, u, _ in filas)
    margenes_ord = sorted(m for _, _, m, _, _ in filas if m is not None)
    med_u = unidades_ord[len(unidades_ord) // 2] if unidades_ord else 0
    med_m = margenes_ord[len(margenes_ord) // 2] if margenes_ord else 0

    out = []
    for p, mu, mpct, u, contrib in filas:
        if u == 0:
            clas = "sin_ventas"
        elif mu <= 0:
            clas = "no_conviene"
        else:
            alto_m = mpct is not None and mpct >= med_m
            alta_r = u >= med_u
            if alto_m and alta_r:
                clas = "estrella"
            elif alto_m:
                clas = "margen_alto_rotacion_baja"
            elif alta_r:
                clas = "margen_bajo_rotacion_alta"
            else:
                clas = "normal"
        part = _r_pct(contrib / total_pos * 100) if total_pos > 0 and contrib > 0 else 0.0
        out.append(Rentabilidad(p.get("id"), p.get("nombre", ""), mu, mpct, u, contrib, part, clas))
    out.sort(key=lambda r: r.contribucion_total, reverse=True)
    return out


# =============================================================================== análisis integral
def analisis_producto(
    producto: dict,
    costos_fijos_asignados: float = 0.0,
    objetivo_mensual: Optional[float] = None,
    margen_minimo_global_pct: float = 20.0,
) -> dict:
    """Reúne todas las métricas de un producto en un dict listo para UI/auditoría."""
    precio = float(producto["precio_venta"])
    costo = float(producto["costo"])
    gv = float(producto.get("gastos_variables") or 0.0)
    margen_min = producto.get("margen_minimo_pct")
    margen_min = float(margen_min) if margen_min is not None else float(margen_minimo_global_pct)

    pmin = precio_minimo_viable(costo, margen_min, gv)
    pe = punto_equilibrio(costos_fijos_asignados, precio, costo, gv)
    mu = margen_neto_unitario(precio, costo, gv)
    uo = unidades_para_objetivo(objetivo_mensual, mu, precio) if objetivo_mensual is not None else None
    mk = markup_pct(precio, costo)
    return {
        "precio_venta": precio,
        "costo": costo,
        "gastos_variables": gv,
        "margen_bruto_unitario": margen_bruto_unitario(precio, costo),
        "margen_bruto_pct": margen_bruto_pct(precio, costo),
        "margen_neto_unitario": mu,
        "margen_neto_pct": margen_neto_pct(precio, costo, gv),
        "markup_pct": mk.valor_pct,
        "markup_estado": mk.estado,
        "margen_minimo_pct": margen_min,
        "precio_minimo_viable": pmin.precio,
        "precio_minimo_estado": pmin.estado,
        "bajo_minimo": pmin.precio is not None and precio < pmin.precio - 1e-9,
        "punto_equilibrio": pe,
        "unidades_objetivo": uo,
    }
