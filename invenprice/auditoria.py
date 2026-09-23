"""Auditoría de cifras en texto (Fase 6, refuerzo): verifica que toda cifra que una justificación
presenta como hecho coincida con los valores reales del motor financiero determinista.

Uso: `verificar_cifras(texto, hechos)` devuelve la lista de discrepancias (vacía si el texto es
consistente). `hechos_desde_detalle(detalle, reglas)` construye los valores admitidos a partir del
`detalle` de una recomendación (precios, márgenes, unidades, brechas, cambios) y de los textos de
las reglas activadas (números que el propio motor calculó).

Qué se verifica
---------------
* Porcentajes ("41,2 %", "40.0%", "15%"): deben coincidir con un porcentaje real (margen actual,
  resultante, mínimo, markup, brecha con la competencia en ambos sentidos, cambio de precio,
  diferencia en puntos con el mínimo) o con una constante de las reglas (3, 5, 6, 8, 10, 12, 15,
  30, 60, 100). Tolerancia: 0,15 puntos si el texto trae decimales, 0,55 si es entero.
* Importes y unidades ("25.500", "25500", "3.99", "285 unidades"): deben coincidir con un importe
  o cantidad real (precio actual/sugerido/original/mínimo/competencia, costo, gastos variables,
  márgenes unitarios, objetivo, unidades necesarias/estimadas, diferencias entre ellos).
  Tolerancia: 0,5 % relativo (mínimo 0,011 para importes con centavos).
* Se ignoran: enteros < 100 sin la palabra "unidad" al lado (días, semanas, conteos), ratios
  ("2.4x"), y números dentro de fechas u horas.

Formatos aceptados: "25.500" y "3.000.000" (puntos de miles), "25,500" (coma de miles),
"25500", "3.99" / "3,99" (decimales). Cuando un número es ambiguo ("3.857") se aceptan ambas
lecturas (3857 o 3,857) si alguna coincide con un hecho.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

CONSTANTES_PCT = {3.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 30.0, 60.0, 100.0}

_RE_PCT = re.compile(r"(?<![\d.,])([+-]?\d{1,3}(?:[.,]\d+)?)\s*%")
_RE_RATIO = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?\s*x\b", re.I)
_RE_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?Z?)?|\b\d{1,2}:\d{2}\b")
_RE_NUM = re.compile(r"(?<![\w.,])[+-]?\d[\d.,]*(?<![.,])")
_RE_UNIDAD = re.compile(r"^\s*(?:unidades?|u\b|u/mes|uds?\b)", re.I)


@dataclass(frozen=True)
class Discrepancia:
    texto: str          # cómo aparece en la justificación
    valor: float        # lectura numérica principal
    tipo: str           # "porcentaje" | "importe_o_unidades"
    motivo: str


def _lecturas(token: str) -> list[float]:
    """Todas las interpretaciones numéricas razonables de un token como '25.500' o '3,99'."""
    t = token.strip().lstrip("+")
    neg = t.startswith("-")
    t = t.lstrip("-")
    out: list[float] = []

    def add(v: Optional[float]):
        if v is not None and v not in out:
            out.append(-v if neg else v)

    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):        # 25.500 / 3.000.000 / 1.234,5
        add(float(t.replace(".", "").replace(",", ".")))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", t):        # 25,500 / 1,234.5
        add(float(t.replace(",", "")))
    if re.fullmatch(r"\d+\.\d+", t):                            # 3.99 / 3.857 (decimal)
        add(float(t))
    if re.fullmatch(r"\d+,\d+", t):                             # 3,99 (decimal con coma)
        add(float(t.replace(",", ".")))
    if re.fullmatch(r"\d+", t):
        add(float(t))
    return out


def _pct_valor(token: str) -> float:
    return float(token.replace(",", "."))


def _coincide_pct(v: float, admitidos: Iterable[float], con_decimales: bool) -> bool:
    tol = 0.15 if con_decimales else 0.55
    return any(abs(abs(v) - abs(a)) <= tol for a in admitidos)


def _coincide_monto(lecturas: Iterable[float], admitidos: Iterable[float]) -> bool:
    for v in lecturas:
        for a in admitidos:
            tol = max(0.011, 0.005 * abs(a))
            if abs(abs(v) - abs(a)) <= tol:
                return True
    return False


def extraer_cifras(texto: str) -> tuple[list[tuple[str, float, bool]], list[tuple[str, list[float], bool]]]:
    """Devuelve (porcentajes, numeros). porcentajes: (token, valor, tiene_decimales).
    numeros: (token, lecturas, exige_verificacion)."""
    limpio = _RE_FECHA.sub(" ", texto)
    limpio = _RE_RATIO.sub(" ", limpio)
    porcentajes = []
    for m in _RE_PCT.finditer(limpio):
        tok = m.group(1)
        porcentajes.append((tok, _pct_valor(tok), ("," in tok or "." in tok)))
    sin_pct = _RE_PCT.sub(" ", limpio)
    numeros = []
    for m in _RE_NUM.finditer(sin_pct):
        tok = m.group(0)
        lect = _lecturas(tok)
        if not lect:
            continue
        resto = sin_pct[m.end():m.end() + 12]
        es_unidades = bool(_RE_UNIDAD.match(resto))
        con_separador = any(c in tok for c in ".,")
        principal = max(lect, key=abs)
        exige = con_separador or abs(principal) >= 100 or es_unidades
        numeros.append((tok, lect, exige))
    return porcentajes, numeros


def hechos_desde_detalle(detalle: dict, reglas: Optional[list] = None) -> dict:
    """Construye {'pct': set, 'montos': set} con todos los valores reales admisibles."""
    d = detalle
    g = lambda k: d.get(k)  # noqa: E731
    P, Ps, Po, C, Pc, Pm = g("precio_actual"), g("precio_sugerido"), g("precio_original"), g("costo"), g("precio_competencia"), g("precio_minimo_viable")
    montos = set()
    for v in (P, Ps, Po, C, Pc, Pm, g("gastos_variables"), g("margen_unitario_actual"), g("margen_unitario_resultante"),
              g("objetivo_ingreso_mensual"), g("unidades_objetivo"), g("unidades_mes_estimadas")):
        if isinstance(v, (int, float)) and v is not None:
            montos.add(float(v))
    # diferencias que un texto puede citar legítimamente
    pares = [(P, Pc), (Ps, Pc), (Ps, P), (Po, P), (P, C), (Ps, C), (P, Pm), (Ps, Pm),
             (g("margen_unitario_resultante"), g("margen_unitario_actual")), (Pc, C)]
    for a, b in pares:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            montos.add(abs(float(a) - float(b)))
    pct = set(CONSTANTES_PCT)
    for v in (g("margen_actual_pct"), g("margen_resultante_pct"), g("margen_minimo_pct"), g("markup_pct"), g("cambio_pct")):
        if isinstance(v, (int, float)):
            pct.add(abs(float(v)))
    if isinstance(g("margen_actual_pct"), (int, float)) and isinstance(g("margen_minimo_pct"), (int, float)):
        pct.add(abs(g("margen_actual_pct") - g("margen_minimo_pct")))
    if isinstance(g("margen_resultante_pct"), (int, float)) and isinstance(g("margen_minimo_pct"), (int, float)):
        pct.add(abs(g("margen_resultante_pct") - g("margen_minimo_pct")))
    if isinstance(g("margen_resultante_pct"), (int, float)) and isinstance(g("margen_actual_pct"), (int, float)):
        pct.add(abs(g("margen_resultante_pct") - g("margen_actual_pct")))
    for a, b in ((P, Pc), (Pc, P), (Ps, Pc), (Pc, Ps), (Ps, P), (P, Ps), (Po, P), (Pm, P), (P, Pm), (Ps, Pm), (Ps, C), (P, C)):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b:
            pct.add(abs((a - b) / b * 100))
    # números que el propio motor de reglas escribió (deterministas)
    for r in reglas or []:
        cond = r["condicion"] if isinstance(r, dict) else r.condicion
        efe = r["efecto"] if isinstance(r, dict) else r.efecto
        ps, ns = extraer_cifras(f"{cond} {efe}")
        pct.update(abs(v) for _, v, _ in ps)
        for _, lect, _ in ns:
            montos.update(abs(v) for v in lect)
    return {"pct": pct, "montos": montos}


def verificar_cifras(texto: str, hechos: dict) -> list[Discrepancia]:
    """Lista de cifras del texto que no coinciden con ningún hecho real. Vacía = texto auditado OK."""
    if not texto:
        return []
    porcentajes, numeros = extraer_cifras(texto)
    out: list[Discrepancia] = []
    for tok, v, dec in porcentajes:
        if not _coincide_pct(v, hechos["pct"], dec):
            out.append(Discrepancia(f"{tok}%", v, "porcentaje", "no coincide con ningún porcentaje calculado"))
    for tok, lect, exige in numeros:
        if exige and not _coincide_monto(lect, hechos["montos"]):
            out.append(Discrepancia(tok, max(lect, key=abs), "importe_o_unidades", "no coincide con ningún importe ni cantidad calculada"))
    return out
