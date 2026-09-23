"""Motor de reglas de pricing (Fase 5) — el razonamiento del dataset compilado a código.

Es el fallback PERMANENTE y gratuito del copiloto: no usa IA, corre en microsegundos y cada
decisión es una lista explícita de reglas activadas (nombre, condición, efecto), inspeccionable
en la UI y en `catalogo_reglas()`.

Orden de evaluación (árbol de decisión con ajustes ponderados):

 0. Piso: precio mínimo viable = (costo + gastos variables) / (1 - margen_min).  Una restricción
    "nunca bajar de X%" eleva el margen mínimo.
 1. bajo_minimo: si el precio actual ya está bajo el piso -> subir al piso y terminar (no hay
    ajuste gradual posible cuando se vende bajo el margen mínimo).
 2. Ancla: si hay precio de competencia, la brecha y la velocidad fijan un precio objetivo
    (igualar, acercarse a medias, mantener el premium, o subir hacia el competidor).  Sin
    competencia, la velocidad da un ajuste base (lenta -8 %, media 0, rápida +6 %, muy rápida +12 %).
 3. Objetivo de ingreso: unidades necesarias = objetivo / margen unitario, comparadas con las
    unidades/mes que implica la velocidad.  Si el objetivo está lejos (>1.3x) y la venta es rápida
    -> +3 % (más margen por unidad); si es lenta/media y no hay ancla que ya haya bajado el precio
    -> -3 % (volumen).  Nunca cruza el ancla de competencia.
 4. Restricciones del dueño: temporada alta +5 %, baja -5 %, liquidar/perecedero -10 %,
    lanzamiento -5 %, escasez +3 %, "no subir" (tope = precio actual), "no bajar"/premium (piso =
    precio actual).
 5. holgura_insuficiente: si el margen actual está a <3 puntos del mínimo, no se permite bajar.
 6. tope_gradual: el cambio total se limita a ±15 % por paso (salvo para alcanzar el piso).
 7. piso_margen_minimo: nunca por debajo del precio mínimo viable.
 8. redondeo comercial (3 cifras significativas, y hacia arriba si tocara el piso).
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import finance as f

# ------------------------------------------------------------------ parámetros calibrables
UNIDADES_POR_VELOCIDAD = {"lenta": 40, "media": 120, "rapida": 300, "muy_rapida": 600}
AJUSTE_BASE_VELOCIDAD = {"lenta": -8.0, "media": 0.0, "rapida": 6.0, "muy_rapida": 12.0}
TOPE_CAMBIO_PCT = 15.0          # cambio máximo por paso
HOLGURA_MINIMA_PP = 3.0         # puntos porcentuales de margen sobre el mínimo para permitir rebajas
UMBRAL_OBJETIVO_LEJOS = 1.3     # unidades_necesarias / unidades_estimadas
PARIDAD_PCT = 2.0               # |brecha| <= 2 % se considera paridad con la competencia
BRECHA_PEQUENA_PCT = 5.0        # brecha <= 5 % con velocidad media -> mantener
MARGEN_MINIMO_GLOBAL_DEFAULT = 20.0

MODIFICADORES_RESTRICCION = {
    "temporada_alta": +5.0,
    "temporada_baja": -5.0,
    "liquidar": -10.0,
    "perecedero": -10.0,
    "lanzamiento": -5.0,
    "escasez": +3.0,
}


@dataclass(frozen=True)
class ReglaActivada:
    nombre: str
    condicion: str      # qué se evaluó (con los números concretos)
    efecto: str         # qué hizo con el precio
    ajuste_pct: float   # efecto neto sobre el precio, en % del precio actual (0 si informativo)


@dataclass
class Recomendacion:
    precio_sugerido: float
    margen_resultante_pct: Optional[float]
    justificacion: str
    riesgo: str
    fuente: str = "motor_reglas"
    precio_minimo_viable: Optional[float] = None
    reglas_activadas: list[ReglaActivada] = field(default_factory=list)
    detalle: dict = field(default_factory=dict)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["reglas_activadas"] = [asdict(r) for r in self.reglas_activadas]
        return d


# ------------------------------------------------------------------ utilidades
def _fmt(v) -> str:
    if v is None:
        return "n/a"
    v = float(v)
    if abs(v) >= 1000 or v.is_integer():
        return f"{v:,.0f}".replace(",", ".")
    return f"{v:.2f}"


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def redondear_comercial(precio: float, piso: float) -> float:
    """3 cifras significativas para precios >= 1000; 2 decimales por debajo.
    Si el redondeo cae bajo el piso, se redondea hacia arriba al siguiente escalón."""
    if precio <= 0:
        return max(0.0, piso)
    if precio >= 1000:
        paso = 10 ** (math.floor(math.log10(precio)) - 2)
        r = math.floor(precio / paso + 0.5) * paso
        if r < piso - 1e-9:
            r = math.ceil(piso / paso - 1e-9) * paso
        return float(r)
    r = math.floor(precio * 100 + 0.5) / 100
    if r < piso - 1e-9:
        r = math.ceil(piso * 100 - 1e-9) / 100
    return r


def interpretar_restricciones(restricciones: list[str]) -> dict:
    """Convierte las restricciones en texto libre del dueño en banderas inspeccionables."""
    flags = {
        "no_subir": False, "no_bajar": False, "temporada_alta": False, "temporada_baja": False,
        "liquidar": False, "perecedero": False, "lanzamiento": False, "escasez": False,
        "margen_minimo_restriccion": None, "textos": list(restricciones or []),
    }
    for r in restricciones or []:
        t = _normalizar(str(r))
        m = re.search(r"nunca bajar de\s*(\d+(?:[.,]\d+)?)\s*%", t)
        if m:
            v = float(m.group(1).replace(",", "."))
            flags["margen_minimo_restriccion"] = max(flags["margen_minimo_restriccion"] or 0, v)
            continue
        if any(k in t for k in ("no subir", "precio fijo", "mantener precio", "contrato de precio", "congelar")):
            flags["no_subir"] = True
        if any(k in t for k in ("no bajar", "premium", "no descontar")):
            flags["no_bajar"] = True
        if "temporada alta" in t or "pico de demanda" in t:
            flags["temporada_alta"] = True
        if "temporada baja" in t:
            flags["temporada_baja"] = True
        if "liquidar" in t or "liquidacion" in t:
            flags["liquidar"] = True
        if any(k in t for k in ("perecedero", "vence", "vencimiento", "caduca")):
            flags["perecedero"] = True
        if any(k in t for k in ("lanzamiento", "penetracion", "ganar cuota")):
            flags["lanzamiento"] = True
        if any(k in t for k in ("stock limitado", "escasez", "reposicion")):
            flags["escasez"] = True
    return flags


# ------------------------------------------------------------------ motor
def evaluar_reglas(producto: dict, contexto: Optional[dict] = None) -> Recomendacion:
    """Evalúa el árbol de reglas y devuelve una Recomendacion trazable.

    producto: precio_venta (o precio_actual), costo, gastos_variables?, margen_minimo_pct?,
              precio_competencia?
    contexto: velocidad_venta?, objetivo_ingreso_mensual?, restricciones?,
              margen_minimo_global_pct?, unidades_por_velocidad?
    """
    contexto = contexto or {}
    P = float(producto.get("precio_venta", producto.get("precio_actual", 0)) or 0)
    C = float(producto.get("costo", 0) or 0)
    gv = float(producto.get("gastos_variables", 0) or 0)
    Pc = producto.get("precio_competencia")
    Pc = float(Pc) if Pc not in (None, "", 0) else None
    vel = contexto.get("velocidad_venta") or "media"
    if vel not in AJUSTE_BASE_VELOCIDAD:
        vel = "media"
    G = contexto.get("objetivo_ingreso_mensual")
    G = float(G) if G is not None else None
    flags = interpretar_restricciones(contexto.get("restricciones") or [])
    u_est = (contexto.get("unidades_por_velocidad") or UNIDADES_POR_VELOCIDAD)[vel]

    reglas: list[ReglaActivada] = []

    # 0. piso ---------------------------------------------------------------
    m_min = producto.get("margen_minimo_pct")
    m_min = float(m_min) if m_min is not None else float(contexto.get("margen_minimo_global_pct", MARGEN_MINIMO_GLOBAL_DEFAULT))
    if flags["margen_minimo_restriccion"] is not None and flags["margen_minimo_restriccion"] > m_min:
        reglas.append(ReglaActivada(
            "restriccion_margen_minimo",
            f"restricción del dueño 'nunca bajar de {_pct(flags['margen_minimo_restriccion'])}' > mínimo configurado {_pct(m_min)}",
            f"el margen mínimo pasa a {_pct(flags['margen_minimo_restriccion'])}", 0.0))
        m_min = flags["margen_minimo_restriccion"]
    pmin_r = f.precio_minimo_viable(C, m_min, gv)
    P_min = pmin_r.precio if pmin_r.precio is not None else 0.0
    m_act = f.margen_neto_pct(P, C, gv) if P > 0 else None
    mu_act = f.margen_neto_unitario(P, C, gv)

    # 1. bajo mínimo --------------------------------------------------------
    if P < P_min - 1e-9:
        objetivo = P_min
        reglas.append(ReglaActivada(
            "bajo_minimo",
            f"precio actual {_fmt(P)} < precio mínimo viable {_fmt(P_min)} (margen actual {_pct(m_act)} vs mínimo {_pct(m_min)})",
            f"subir directamente al mínimo viable {_fmt(P_min)}; no aplica ajuste gradual", (P_min - P) / P * 100 if P else 0.0))
        return _cerrar(P, C, gv, P_min, m_min, m_act, mu_act, objetivo, Pc, vel, G, u_est, flags, reglas, dominante="bajo_minimo")

    # 2. ancla --------------------------------------------------------------
    objetivo = P
    ancla_bajo = False
    if Pc is not None and Pc > 0:
        brecha = (P - Pc) / Pc * 100  # >0: somos más caros
        if abs(brecha) <= PARIDAD_PCT:
            adj = {"muy_rapida": 5.0, "rapida": 3.0}.get(vel, 0.0)
            objetivo = P * (1 + adj / 100)
            reglas.append(ReglaActivada(
                "ancla_competencia",
                f"paridad con la competencia ({_fmt(Pc)} vs {_fmt(P)}, brecha {brecha:+.1f}%) y venta {vel}",
                f"{'subir ' + _pct(adj) + ' aprovechando la preferencia del cliente' if adj else 'mantener el precio'}", adj))
        elif brecha > 0:  # más caros que la competencia
            if vel == "lenta":
                objetivo = Pc
                efecto = f"igualar a la competencia: {_fmt(Pc)}"
            elif vel == "media":
                if brecha <= BRECHA_PEQUENA_PCT:
                    objetivo, efecto = P, "brecha pequeña: mantener el precio"
                else:
                    objetivo, efecto = P - (P - Pc) / 2, f"acercarse a medias hacia la competencia: {_fmt(P - (P - Pc) / 2)}"
            else:
                objetivo, efecto = P, "la rotación valida el sobreprecio: mantener el premium"
            ancla_bajo = objetivo < P - 1e-9
            reglas.append(ReglaActivada(
                "ancla_competencia",
                f"somos {brecha:.1f}% más caros que la competencia ({_fmt(P)} vs {_fmt(Pc)}) con venta {vel}",
                efecto, (objetivo - P) / P * 100))
        else:  # más baratos que la competencia
            g = (Pc - P) / P * 100
            if vel == "muy_rapida":
                objetivo, efecto = Pc, f"igualar a la competencia: {_fmt(Pc)} (la demanda lo permite)"
            elif vel == "rapida":
                objetivo, efecto = P + (Pc - P) * 0.6, f"subir 60% de la brecha hacia la competencia: {_fmt(P + (Pc - P) * 0.6)}"
            elif vel == "media":
                if g <= BRECHA_PEQUENA_PCT:
                    objetivo, efecto = P, "brecha pequeña: mantener el precio"
                else:
                    objetivo, efecto = P + (Pc - P) * 0.3, f"recuperar 30% de la brecha: {_fmt(P + (Pc - P) * 0.3)}"
            else:
                objetivo, efecto = P, "venta lenta pese a ser más baratos: el precio no es la palanca, mantener"
            reglas.append(ReglaActivada(
                "ancla_competencia",
                f"somos {g:.1f}% más baratos que la competencia ({_fmt(P)} vs {_fmt(Pc)}) con venta {vel}",
                efecto, (objetivo - P) / P * 100))
    else:
        adj = AJUSTE_BASE_VELOCIDAD[vel]
        objetivo = P * (1 + adj / 100)
        reglas.append(ReglaActivada(
            "velocidad",
            f"sin datos de competencia; velocidad de venta {vel}",
            f"ajuste base {adj:+.0f}%", adj))
        ancla_bajo = adj < 0

    # 3. objetivo de ingreso ------------------------------------------------
    u_req = None
    if G is not None and G > 0:
        mu_obj = f.margen_neto_unitario(objetivo, C, gv)
        r = f.unidades_para_objetivo(G, mu_obj, objetivo)
        if r.estado is f.Estado.OK:
            u_req = r.unidades
            ratio = u_req / u_est
            if ratio > UMBRAL_OBJETIVO_LEJOS:
                if vel in ("rapida", "muy_rapida") and (Pc is None or Pc > objetivo):
                    nuevo = objetivo * 1.03
                    if Pc is not None:
                        nuevo = min(nuevo, Pc)
                    reglas.append(ReglaActivada(
                        "objetivo_ingreso",
                        f"objetivo {_fmt(G)} exige {u_req} unidades/mes vs ~{u_est} que implica venta {vel} ({ratio:.1f}x)",
                        f"venta rápida: +{(nuevo / objetivo - 1) * 100:.1f}% para capturar más margen por unidad", (nuevo - objetivo) / P * 100))
                    objetivo = nuevo
                elif vel in ("lenta", "media") and not ancla_bajo and (Pc is None or Pc < P) and not (Pc is not None and objetivo <= Pc * (1 + BRECHA_PEQUENA_PCT / 100)):
                    nuevo = objetivo * 0.97
                    reglas.append(ReglaActivada(
                        "objetivo_ingreso",
                        f"objetivo {_fmt(G)} exige {u_req} unidades/mes vs ~{u_est} que implica venta {vel} ({ratio:.1f}x)",
                        "hace falta volumen: -3% para estimular demanda", (nuevo - objetivo) / P * 100))
                    objetivo = nuevo
                else:
                    reglas.append(ReglaActivada(
                        "objetivo_ingreso",
                        f"objetivo {_fmt(G)} exige {u_req} unidades/mes vs ~{u_est} que implica venta {vel} ({ratio:.1f}x)",
                        "objetivo lejano, pero el precio ya está anclado al mercado: no se ajusta más", 0.0))
            else:
                reglas.append(ReglaActivada(
                    "objetivo_ingreso",
                    f"objetivo {_fmt(G)} exige {u_req} unidades/mes vs ~{u_est} que implica venta {vel} ({ratio:.1f}x)",
                    "sin presión de objetivo sobre el precio", 0.0))
        else:
            reglas.append(ReglaActivada("objetivo_ingreso", f"objetivo {_fmt(G)} con margen unitario {_fmt(mu_obj)}",
                                        f"estado {r.estado.value}: no se puede estimar unidades", 0.0))
    elif G is not None and G <= 0:
        reglas.append(ReglaActivada("objetivo_ingreso", f"objetivo registrado {_fmt(G)} (cero o negativo)",
                                    "objetivo inválido: se ignora y se decide con las señales de mercado", 0.0))

    # 4. restricciones ------------------------------------------------------
    for flag, mod in MODIFICADORES_RESTRICCION.items():
        if flags[flag]:
            nuevo = objetivo * (1 + mod / 100)
            reglas.append(ReglaActivada(f"restriccion_{flag}", f"restricción del dueño: {flag.replace('_', ' ')}",
                                        f"{mod:+.0f}% sobre el precio objetivo", (nuevo - objetivo) / P * 100 if P else 0.0))
            objetivo = nuevo
    if flags["no_subir"] and objetivo > P:
        reglas.append(ReglaActivada("restriccion_no_subir", "restricción del dueño: no subir el precio",
                                    f"tope en el precio actual {_fmt(P)} (se descarta subir a {_fmt(objetivo)})", (P - objetivo) / P * 100))
        objetivo = P
    if flags["no_bajar"] and objetivo < P:
        reglas.append(ReglaActivada("restriccion_no_bajar", "restricción del dueño: no bajar del precio actual (premium)",
                                    f"piso en el precio actual {_fmt(P)} (se descarta bajar a {_fmt(objetivo)})", (P - objetivo) / P * 100))
        objetivo = P

    # 5. holgura ------------------------------------------------------------
    if objetivo < P - 1e-9 and m_act is not None and (m_act - m_min) < HOLGURA_MINIMA_PP:
        reglas.append(ReglaActivada("holgura_insuficiente",
                                    f"margen actual {_pct(m_act)} está a {m_act - m_min:.1f} puntos del mínimo {_pct(m_min)} (< {HOLGURA_MINIMA_PP:.0f})",
                                    "no hay espacio real para bajar: mantener el precio y trabajar el costo", (P - objetivo) / P * 100))
        objetivo = P

    # 6. tope gradual -------------------------------------------------------
    if P > 0:
        cambio = (objetivo - P) / P * 100
        if abs(cambio) > TOPE_CAMBIO_PCT + 1e-9:
            tope = P * (1 + math.copysign(TOPE_CAMBIO_PCT, cambio) / 100)
            reglas.append(ReglaActivada("tope_gradual", f"cambio propuesto {cambio:+.1f}% supera el tope de ±{TOPE_CAMBIO_PCT:.0f}% por paso",
                                        f"limitar a {_fmt(tope)} y reevaluar en 30 días", (tope - objetivo) / P * 100))
            objetivo = tope

    # 7. piso ---------------------------------------------------------------
    if objetivo < P_min - 1e-9:
        reglas.append(ReglaActivada("piso_margen_minimo", f"precio objetivo {_fmt(objetivo)} < mínimo viable {_fmt(P_min)} (margen mínimo {_pct(m_min)})",
                                    f"elevar al mínimo viable {_fmt(P_min)}", (P_min - objetivo) / P * 100 if P else 0.0))
        objetivo = P_min

    dominante = max(reglas, key=lambda r: abs(r.ajuste_pct)).nombre if reglas else "velocidad"
    return _cerrar(P, C, gv, P_min, m_min, m_act, mu_act, objetivo, Pc, vel, G, u_est, flags, reglas, dominante)


def _cerrar(P, C, gv, P_min, m_min, m_act, mu_act, objetivo, Pc, vel, G, u_est, flags, reglas, dominante) -> Recomendacion:
    precio = redondear_comercial(objetivo, P_min)
    if precio < P_min - 1e-9:  # defensa final
        precio = P_min
    m_res = f.margen_neto_pct(precio, C, gv)
    mu_res = f.margen_neto_unitario(precio, C, gv)
    u_req = None
    if G is not None and G > 0:
        r = f.unidades_para_objetivo(G, mu_res, precio)
        u_req = r.unidades if r.estado is f.Estado.OK else None

    detalle = {
        "precio_actual": P, "costo": C, "gastos_variables": gv,
        "margen_actual_pct": m_act, "margen_unitario_actual": mu_act,
        "margen_minimo_pct": m_min, "precio_minimo_viable": P_min,
        "precio_competencia": Pc, "velocidad_venta": vel, "unidades_mes_estimadas": u_est,
        "objetivo_ingreso_mensual": G, "unidades_objetivo": u_req,
        "precio_sugerido": precio, "margen_resultante_pct": m_res, "margen_unitario_resultante": mu_res,
        "cambio_pct": (precio - P) / P * 100 if P else None,
        "restricciones": flags["textos"],
    }
    return Recomendacion(
        precio_sugerido=precio,
        margen_resultante_pct=m_res,
        justificacion=_justificacion(detalle, reglas),
        riesgo=_riesgo(dominante, detalle),
        precio_minimo_viable=P_min,
        reglas_activadas=reglas,
        detalle=detalle,
    )


# ------------------------------------------------------------------ plantillas de texto
def _justificacion(d: dict, reglas: list[ReglaActivada]) -> str:
    P, C = d["precio_actual"], d["costo"]
    partes = [
        f"Situación: precio actual {_fmt(P)}, costo {_fmt(C)}, margen {_pct(d['margen_actual_pct'])} "
        f"({_fmt(d['margen_unitario_actual'])} por unidad); margen mínimo {_pct(d['margen_minimo_pct'])} → precio mínimo viable {_fmt(d['precio_minimo_viable'])}; "
        f"venta {d['velocidad_venta']}"
        + (f"; competencia en {_fmt(d['precio_competencia'])}" if d["precio_competencia"] else "; sin datos de competencia")
        + "."
    ]
    if reglas:
        partes.append("Reglas aplicadas: " + " | ".join(f"[{r.nombre}] {r.condicion} → {r.efecto}" for r in reglas) + ".")
    cambio = d["cambio_pct"]
    verbo = "mantener" if cambio is not None and abs(cambio) < 0.05 else ("subir" if cambio and cambio > 0 else "bajar")
    partes.append(
        f"Recomendación: {verbo} a {_fmt(d['precio_sugerido'])}"
        + (f" ({cambio:+.1f}%)" if cambio is not None and abs(cambio) >= 0.05 else "")
        + f", con margen resultante {_pct(d['margen_resultante_pct'])} ({_fmt(d['margen_unitario_resultante'])} por unidad)."
    )
    G = d["objetivo_ingreso_mensual"]
    if G is not None and G > 0:
        if d["unidades_objetivo"] is not None:
            u, e = d["unidades_objetivo"], d["unidades_mes_estimadas"]
            veredicto = "alcanzable al ritmo actual" if u <= e else "por encima del ritmo actual: el objetivo requiere volumen, no solo precio"
            partes.append(f"Objetivo de {_fmt(G)} de margen mensual: se necesitan {u} unidades/mes frente a ~{e} que implica una venta {d['velocidad_venta']} ({veredicto}).")
        else:
            partes.append(f"Objetivo de {_fmt(G)}: no alcanzable con margen unitario {_fmt(d['margen_unitario_resultante'])}.")
    if d["restricciones"]:
        partes.append("Restricciones consideradas: " + "; ".join(d["restricciones"]) + ".")
    return " ".join(partes)


_RIESGOS = {
    "bajo_minimo": "El aumento necesario para cumplir el margen mínimo es grande y puede frenar la venta; si el cliente no lo acepta, la alternativa es bajar el costo o retirar el producto, no vender bajo el piso.",
    "ancla_competencia": "Seguir a la competencia supone que el cliente decide por precio; si la rotación no responde en 30 días, la causa estaba en otro lado (visibilidad, surtido, servicio) y se habrá cedido margen.",
    "velocidad": "El ajuste se basa solo en la velocidad de venta, sin referencia de mercado; medir unidades semanales y revertir si la respuesta no aparece en 4 semanas.",
    "objetivo_ingreso": "Ajustar el precio para perseguir un objetivo puede distorsionar la relación con el cliente; si el objetivo es desproporcionado para este producto, repartirlo entre varias líneas.",
    "tope_gradual": "El cambio total recomendado supera lo prudente en un solo paso; hacerlo por etapas evita rechazo, pero prolonga el periodo con precio subóptimo.",
    "piso_margen_minimo": "El precio quedó en el piso de margen: cualquier aumento del costo obliga a subir de inmediato; no hay margen de maniobra.",
    "holgura_insuficiente": "Mantener el precio con margen al límite y sin poder bajar inmoviliza capital; si no se reduce el costo en 60 días, evaluar descontinuar.",
    "restriccion_no_subir": "Costo de oportunidad: se deja margen sin capturar por respetar el compromiso; preparar el aumento para cuando la restricción expire.",
    "restriccion_no_bajar": "Sostener el posicionamiento premium con venta lenta puede prolongar la baja rotación; revisar la propuesta de valor con plazo definido.",
    "restriccion_temporada_alta": "Un aumento de temporada puede percibirse como oportunismo; comunicarlo y prever volver al precio regular al terminar el pico.",
    "restriccion_temporada_baja": "La rebaja de temporada puede fijar una referencia difícil de revertir; anunciarla con fecha de fin.",
    "restriccion_liquidar": "Si la rebaja no mueve el inventario, el stock queda obsoleto; definir un umbral de stock y fecha para decidir un segundo recorte.",
    "restriccion_perecedero": "Si el producto no se vende antes de vencer se pierde el 100% del costo; un segundo recorte hasta el piso debe decidirse en días, no semanas.",
    "restriccion_lanzamiento": "Un precio de lanzamiento fija expectativas: subir después puede sentirse como aumento; comunicarlo como temporal desde el inicio.",
    "restriccion_escasez": "Subir por escasez puede percibirse como abuso y perder clientes de forma permanente; limitar el aumento al periodo sin stock.",
    "restriccion_margen_minimo": "El piso impuesto por el dueño puede ser incompatible con el mercado; si el producto no rota, la decisión es relajar el piso o retirar la línea.",
}


def _riesgo(dominante: str, d: dict) -> str:
    base = _RIESGOS.get(dominante, _RIESGOS["velocidad"])
    if d["precio_competencia"] and d["precio_sugerido"] > d["precio_competencia"] * 1.05:
        base += f" Además, el precio sugerido queda {((d['precio_sugerido'] / d['precio_competencia']) - 1) * 100:.0f}% por encima de la competencia ({_fmt(d['precio_competencia'])})."
    return base


def catalogo_reglas() -> list[dict]:
    """Lista estática de todas las reglas del motor, para mostrarlas en la UI/documentación."""
    return [
        {"nombre": "restriccion_margen_minimo", "condicion": "restricción 'nunca bajar de X%' con X > margen mínimo configurado", "efecto": "el margen mínimo pasa a X"},
        {"nombre": "bajo_minimo", "condicion": "precio actual < precio mínimo viable", "efecto": "subir al mínimo viable sin ajuste gradual"},
        {"nombre": "ancla_competencia", "condicion": "hay precio de competencia; según brecha (paridad ±2 %, más caros, más baratos) y velocidad", "efecto": "igualar / acercarse a medias / mantener premium / subir 60 % o 30 % de la brecha / +3-5 % en paridad con venta rápida"},
        {"nombre": "velocidad", "condicion": "sin competencia; velocidad lenta/media/rápida/muy rápida", "efecto": "-8 % / 0 / +6 % / +12 %"},
        {"nombre": "objetivo_ingreso", "condicion": "unidades necesarias (objetivo / margen unitario) > 1.3× unidades que implica la velocidad", "efecto": "+3 % si venta rápida y no cruza la competencia; -3 % si lenta/media y el ancla no bajó ya el precio"},
        {"nombre": "restriccion_temporada_alta", "condicion": "restricción menciona 'temporada alta'", "efecto": "+5 %"},
        {"nombre": "restriccion_temporada_baja", "condicion": "restricción menciona 'temporada baja'", "efecto": "-5 %"},
        {"nombre": "restriccion_liquidar", "condicion": "restricción menciona 'liquidar'", "efecto": "-10 %"},
        {"nombre": "restriccion_perecedero", "condicion": "restricción menciona 'perecedero' / 'vence'", "efecto": "-10 %"},
        {"nombre": "restriccion_lanzamiento", "condicion": "restricción menciona 'lanzamiento' / 'ganar cuota'", "efecto": "-5 %"},
        {"nombre": "restriccion_escasez", "condicion": "restricción menciona 'stock limitado' / 'escasez'", "efecto": "+3 %"},
        {"nombre": "restriccion_no_subir", "condicion": "restricción 'no subir' / 'contrato de precio' y el objetivo > precio actual", "efecto": "tope en el precio actual"},
        {"nombre": "restriccion_no_bajar", "condicion": "restricción 'no bajar' / 'premium' y el objetivo < precio actual", "efecto": "piso en el precio actual"},
        {"nombre": "holgura_insuficiente", "condicion": "se propone bajar y margen actual − margen mínimo < 3 puntos", "efecto": "mantener el precio"},
        {"nombre": "tope_gradual", "condicion": "|cambio| > 15 %", "efecto": "limitar a ±15 % por paso"},
        {"nombre": "piso_margen_minimo", "condicion": "precio objetivo < precio mínimo viable", "efecto": "elevar al mínimo viable (garantía matemática)"},
        {"nombre": "redondeo_comercial", "condicion": "siempre", "efecto": "3 cifras significativas; hacia arriba si tocara el piso"},
    ]
