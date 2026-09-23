"""Copiloto de pricing (Fase 6): LLM local opcional + motor de reglas + guardrail.

Flujo de `recomendar()`:

    1. Motor financiero (Fase 2) calcula el precio mínimo viable  ->  siempre, determinista.
    2. Si hay LLM local disponible: prompt con few-shot del dataset (Fase 4) -> respuesta.
       La respuesta se valida ESTRICTAMENTE contra el schema `output` del dataset. Cualquier
       fallo (no es JSON, faltan campos, tipos raros, excepción, timeout) -> se descarta.
    3. Fallback: motor de reglas (Fase 5). El usuario nunca se queda sin recomendación.
    4. GUARDRAIL: si el precio sugerido (venga de donde venga) < mínimo viable, se recorta al
       mínimo, se marca `ajustado_guardrail=True` y se genera la nota para la UI.
    5. AUDITORÍA DE CIFRAS (`auditoria.py`): toda cifra que la justificación/riesgo presentan como
       hecho se compara con los valores reales; si alguna no coincide, el texto se sustituye por la
       plantilla del motor de reglas regenerada con los números finales.
    6. Capa de auditoría: `detalle` con todos los números que sustentan la recomendación.

El LLM NUNCA produce cifras que se usen directamente: el margen resultante se recalcula con el
motor financiero y el precio pasa por el guardrail.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import auditoria, currency, finance as f, rules

RUTA_DATASET = Path(__file__).resolve().parent.parent / "data" / "pricing_reasoning_dataset.jsonl"
CAMPOS_OUTPUT = ("precio_recomendado", "margen_resultante_pct", "justificacion", "riesgo")
MODELO_DEFAULT = "qwen2.5:7b-instruct-q4_K_M"
OLLAMA_URL_DEFAULT = "http://localhost:11434"
TIMEOUT_LLM_DEFAULT = 180.0   # CPU sin GPU: un prompt few-shot puede tardar 1-3 minutos


# =============================================================================== resultado
@dataclass
class RecomendacionFinal:
    precio_sugerido: float
    precio_original: float
    margen_resultante_pct: Optional[float]
    justificacion: str
    riesgo: str
    fuente: str                         # "llm_local" | "motor_reglas"
    precio_minimo_viable: float
    ajustado_guardrail: bool
    nota_guardrail: Optional[str]
    notas: list[str] = field(default_factory=list)
    reglas_activadas: list = field(default_factory=list)
    detalle: dict = field(default_factory=dict)

    def a_dict(self) -> dict:
        d = asdict(self)
        return d


# =============================================================================== cliente Ollama
class ClienteOllama:
    """Cliente mínimo (urllib, sin dependencias) para /api/generate de Ollama.
    Se usa como `llm(prompt, timeout) -> str`. Lanza excepciones ante cualquier fallo; el
    copiloto las captura y cae al motor de reglas."""

    def __init__(self, url: str = OLLAMA_URL_DEFAULT, modelo: str = MODELO_DEFAULT, timeout: float = TIMEOUT_LLM_DEFAULT):
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.timeout = timeout

    def disponible(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            modelos = {m.get("name", "") for m in data.get("models", [])}
            return any(n == self.modelo or n.split(":")[0] == self.modelo.split(":")[0] for n in modelos)
        except Exception:
            return False

    def __call__(self, prompt: str, timeout: Optional[float] = None) -> str:
        cuerpo = json.dumps({
            "model": self.modelo,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 400},
        }).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/api/generate", data=cuerpo, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "")


def cliente_desde_config(conn) -> ClienteOllama:
    from . import db
    return ClienteOllama(
        url=db.obtener_config(conn, "ollama_url", OLLAMA_URL_DEFAULT),
        modelo=db.obtener_config(conn, "modelo_local", MODELO_DEFAULT),
        timeout=float(db.obtener_config(conn, "timeout_llm_seg", str(TIMEOUT_LLM_DEFAULT))),
    )


# =============================================================================== prompt few-shot
_CACHE_DATASET: Optional[list[dict]] = None


def cargar_dataset() -> list[dict]:
    global _CACHE_DATASET
    if _CACHE_DATASET is None:
        try:
            _CACHE_DATASET = [json.loads(l) for l in RUTA_DATASET.read_text(encoding="utf-8").strip().splitlines()]
        except Exception:
            _CACHE_DATASET = []
    return _CACHE_DATASET


def seleccionar_ejemplos(contexto: dict, n: int = 4) -> list[dict]:
    """Elige los n casos del dataset más parecidos al contexto (velocidad, competencia, restricciones)."""
    vel = contexto.get("velocidad_venta") or "media"
    con_comp = contexto.get("precio_competencia") is not None
    tiene_restr = bool(contexto.get("restricciones"))

    def puntaje(c):
        i = c["input"]
        s = 0
        s += 3 if i["velocidad_venta"] == vel else 0
        s += 2 if (i["precio_competencia"] is not None) == con_comp else 0
        s += 1 if bool(i["restricciones"]) == tiene_restr else 0
        return s

    return sorted(cargar_dataset(), key=puntaje, reverse=True)[:n]


def _entrada_dataset(producto: dict, contexto: dict) -> dict:
    P = float(producto.get("precio_venta", producto.get("precio_actual", 0)) or 0)
    C = float(producto.get("costo", 0) or 0)
    m_min = producto.get("margen_minimo_pct")
    m_min = float(m_min) if m_min is not None else float(contexto.get("margen_minimo_global_pct", rules.MARGEN_MINIMO_GLOBAL_DEFAULT))
    return {
        "precio_actual": P,
        "costo": C,
        "margen_actual_pct": round(f.margen_bruto_pct(P, C) or 0.0, 1),
        "margen_minimo_pct": m_min,
        "velocidad_venta": contexto.get("velocidad_venta") or "media",
        "objetivo_ingreso_mensual": contexto.get("objetivo_ingreso_mensual") or 0,
        "restricciones": list(contexto.get("restricciones") or []),
        "precio_competencia": producto.get("precio_competencia"),
    }


def construir_prompt(producto: dict, contexto: dict, n_ejemplos: int = 4) -> str:
    entrada = _entrada_dataset(producto, contexto)
    ctx_sel = dict(contexto, precio_competencia=entrada["precio_competencia"])
    ejemplos = seleccionar_ejemplos(ctx_sel, n_ejemplos)
    pmin = f.precio_minimo_viable(entrada["costo"], entrada["margen_minimo_pct"], float(producto.get("gastos_variables") or 0)).precio or 0.0
    fmt = rules._fmt

    partes = [
        "Eres un consultor de pricing para pequeñas empresas. Analizas UN producto y devuelves una recomendación "
        "en JSON con exactamente estas claves: precio_recomendado (número), margen_resultante_pct (número), "
        "justificacion (texto que cita los números exactos del caso y explica el trade-off), riesgo (texto breve "
        "con el principal riesgo). Responde SOLO con el JSON, sin texto adicional.",
        f"Regla dura: el precio_recomendado NUNCA puede ser menor que el precio mínimo viable de {fmt(pmin)} "
        f"(costo {fmt(entrada['costo'])} con margen mínimo {entrada['margen_minimo_pct']}%). Los cambios de precio "
        "prudentes son de máximo ±15% por paso, salvo para alcanzar el mínimo viable.",
        "Ejemplos de casos resueltos por un experto:",
    ]
    for e in ejemplos:
        partes.append("Entrada: " + json.dumps(e["input"], ensure_ascii=False))
        partes.append("Salida: " + json.dumps(e["output"], ensure_ascii=False))
    partes.append("Ahora resuelve este caso.")
    partes.append("Entrada: " + json.dumps(entrada, ensure_ascii=False))
    partes.append(f"Dato calculado: precio mínimo viable = {fmt(pmin)}; velocidad de venta = {entrada['velocidad_venta']}.")
    partes.append("Salida:")
    return "\n".join(partes)


# =============================================================================== validación estricta
def parsear_salida_llm(texto) -> tuple[Optional[dict], Optional[str]]:
    """Devuelve (dict válido, None) o (None, motivo). Nunca lanza."""
    if not isinstance(texto, str) or not texto.strip():
        return None, "respuesta vacía o no textual"
    candidatos = [texto.strip()]
    m = re.search(r"\{.*\}", texto, flags=re.S)  # JSON incrustado en texto / bloque de código
    if m:
        candidatos.append(m.group(0))
    data = None
    for c in candidatos:
        try:
            data = json.loads(c)
            break
        except Exception:
            continue
    if data is None:
        return None, "la respuesta no es JSON válido"
    if not isinstance(data, dict):
        return None, "el JSON no es un objeto"
    faltan = [k for k in CAMPOS_OUTPUT if k not in data]
    if faltan:
        return None, f"faltan campos: {faltan}"
    precio = data["precio_recomendado"]
    if isinstance(precio, bool) or not isinstance(precio, (int, float)) or not math.isfinite(precio) or precio <= 0:
        return None, f"precio_recomendado inválido: {precio!r}"
    margen = data["margen_resultante_pct"]
    if isinstance(margen, bool) or not isinstance(margen, (int, float)) or not math.isfinite(margen):
        return None, f"margen_resultante_pct inválido: {margen!r}"
    for k in ("justificacion", "riesgo"):
        if not isinstance(data[k], str) or not data[k].strip():
            return None, f"campo {k} vacío o no textual"
    return {k: data[k] for k in CAMPOS_OUTPUT}, None


# =============================================================================== orquestación
def recomendar(
    producto: dict,
    contexto: Optional[dict] = None,
    llm: Optional[Callable[..., str]] = None,
    usar_llm: bool = True,
    timeout: Optional[float] = None,
) -> RecomendacionFinal:
    """Recomendación final con guardrail. `llm` es cualquier callable (prompt, timeout) -> str."""
    contexto = dict(contexto or {})
    P = float(producto.get("precio_venta", producto.get("precio_actual", 0)) or 0)
    C = float(producto.get("costo", 0) or 0)
    gv = float(producto.get("gastos_variables") or 0)
    moneda = producto.get("moneda")
    notas: list[str] = []

    # 1. motor de reglas: siempre se calcula (es barato y da el piso + trazabilidad)
    rec_reglas = rules.evaluar_reglas(producto, contexto)
    P_min = rec_reglas.precio_minimo_viable or 0.0  # ya incluye gastos variables y "nunca bajar de X%"

    fuente, motivo_fallback, llm_intentado = "motor_reglas", None, False
    precio, justificacion, riesgo = rec_reglas.precio_sugerido, rec_reglas.justificacion, rec_reglas.riesgo
    modelo = getattr(llm, "modelo", None) if llm is not None else None

    # 2. LLM local (opcional)
    if usar_llm and llm is not None:
        llm_intentado = True
        try:
            texto = llm(construir_prompt(producto, contexto), timeout=timeout)
            salida, motivo = parsear_salida_llm(texto)
            if salida is None:
                motivo_fallback = f"respuesta del LLM descartada: {motivo}"
            else:
                fuente = "llm_local"
                precio = float(salida["precio_recomendado"])
                justificacion, riesgo = salida["justificacion"].strip(), salida["riesgo"].strip()
                m_real = f.margen_neto_pct(precio, C, gv)
                if m_real is not None and abs(float(salida["margen_resultante_pct"]) - m_real) > 1.0:
                    notas.append(
                        f"El margen indicado por el modelo ({float(salida['margen_resultante_pct']):.1f}%) no coincidía con el "
                        f"cálculo determinista ({m_real:.1f}%); se muestra el valor calculado."
                    )
        except Exception as e:  # servidor caído, timeout, modelo no instalado, etc.
            motivo_fallback = f"LLM no disponible ({type(e).__name__}: {e})"
    elif usar_llm and llm is None:
        motivo_fallback = "sin cliente LLM configurado"

    if fuente == "motor_reglas" and motivo_fallback:
        notas.append(f"Se usó el motor de reglas. Motivo: {motivo_fallback}.")

    # 3. GUARDRAIL de precio mínimo viable (no negociable)
    precio_original = precio
    ajustado, nota_guardrail = False, None
    if precio < P_min - 1e-9:
        precio = currency.redondear_arriba(P_min, moneda) if moneda in currency.DECIMALES else P_min
        ajustado = True
        nota_guardrail = (
            f"Recomendación original ({rules._fmt(precio_original)}) ajustada al mínimo viable ({rules._fmt(precio)}) "
            f"por restricción de margen ({rec_reglas.detalle['margen_minimo_pct']:.1f}%)."
        )
        notas.append(nota_guardrail)

    margen_res = f.margen_neto_pct(precio, C, gv)
    mu_res = f.margen_neto_unitario(precio, C, gv)
    G = contexto.get("objetivo_ingreso_mensual")
    u_obj = None
    if G is not None and float(G) > 0:
        r = f.unidades_para_objetivo(float(G), mu_res, precio)
        u_obj = r.unidades if r.estado is f.Estado.OK else None

    detalle = {
        **rec_reglas.detalle,
        "precio_sugerido": precio,
        "precio_original": precio_original,
        "margen_resultante_pct": margen_res,
        "margen_unitario_resultante": mu_res,
        "unidades_objetivo": u_obj,
        "cambio_pct": (precio - P) / P * 100 if P else None,
        "markup_pct": f.markup_pct(P, C).valor_pct,
        "fuente": fuente,
        "modelo": modelo,
        "llm_intentado": llm_intentado,
        "motivo_fallback": motivo_fallback,
        "ajustado_guardrail": ajustado,
        "moneda": moneda,
    }

    # 4. AUDITORÍA DE CIFRAS: toda cifra del texto debe coincidir con el cálculo determinista.
    #    Si no, se descarta el texto y se usa la plantilla con los números reales (misma plantilla
    #    del motor de reglas, regenerada con el precio FINAL tras el guardrail).
    reglas_dicts = [asdict(r) for r in rec_reglas.reglas_activadas]
    hechos = auditoria.hechos_desde_detalle(detalle, reglas_dicts)
    justif_plantilla = rules.justificacion_plantilla(detalle, reglas_dicts)
    riesgo_plantilla = rules.riesgo_plantilla(detalle, reglas_dicts)
    if fuente == "motor_reglas":
        # el texto del motor se regenera siempre con los números finales (p. ej. tras el guardrail)
        justificacion, riesgo = justif_plantilla, riesgo_plantilla
    disc_j = auditoria.verificar_cifras(justificacion, hechos)
    disc_r = auditoria.verificar_cifras(riesgo, hechos)
    justificacion_fuente = fuente
    if disc_j:
        justificacion, justificacion_fuente = justif_plantilla, "plantilla"
        notas.append(
            "La justificación del modelo citaba cifras que no coinciden con el cálculo determinista ("
            + ", ".join(d.texto for d in disc_j[:5]) + "); se muestra una explicación generada por plantilla con los números reales."
        )
    riesgo_fuente = fuente
    if disc_r:
        riesgo, riesgo_fuente = riesgo_plantilla, "plantilla"
        notas.append("El texto de riesgo del modelo citaba cifras incorrectas (" + ", ".join(d.texto for d in disc_r[:5]) + "); se muestra el riesgo por plantilla.")
    detalle.update({
        "justificacion_fuente": justificacion_fuente,
        "riesgo_fuente": riesgo_fuente,
        "cifras_discrepantes": [d.texto for d in disc_j + disc_r],
        "justificacion_auditada": True,
    })
    return RecomendacionFinal(
        precio_sugerido=precio,
        precio_original=precio_original,
        margen_resultante_pct=margen_res,
        justificacion=justificacion,
        riesgo=riesgo,
        fuente=fuente,
        precio_minimo_viable=P_min,
        ajustado_guardrail=ajustado,
        nota_guardrail=nota_guardrail,
        notas=notas,
        reglas_activadas=[asdict(r) for r in rec_reglas.reglas_activadas],
        detalle=detalle,
    )


def recomendar_para_producto(conn, producto: dict, contexto: Optional[dict] = None, usar_llm: Optional[bool] = None) -> RecomendacionFinal:
    """Atajo para la UI: toma configuración (modelo, URL, activación) de la BD y guarda la bitácora."""
    from . import db
    if usar_llm is None:
        usar_llm = db.obtener_config(conn, "copiloto_llm_activo", "1") == "1"
    ctx = dict(contexto or {})
    ctx.setdefault("margen_minimo_global_pct", float(db.obtener_config(conn, "margen_minimo_global_pct", "20")))
    llm = cliente_desde_config(conn) if usar_llm else None
    if llm is not None and not llm.disponible():
        rec = recomendar(producto, ctx, llm=None, usar_llm=False)
        rec.notas.insert(0, f"Modelo local '{llm.modelo}' no disponible en {llm.url}; se usó el motor de reglas.")
        rec.detalle["llm_intentado"] = True
        rec.detalle["motivo_fallback"] = "modelo local no instalado o servidor Ollama apagado"
        rec.detalle["modelo"] = llm.modelo
    else:
        rec = recomendar(producto, ctx, llm=llm, usar_llm=usar_llm)
    if producto.get("id") is not None:
        db.guardar_recomendacion(conn, producto["id"], {
            "fuente": rec.fuente, "precio_sugerido": rec.precio_sugerido, "precio_original": rec.precio_original,
            "ajustado_guardrail": rec.ajustado_guardrail, "precio_minimo_viable": rec.precio_minimo_viable,
            "margen_resultante_pct": rec.margen_resultante_pct, "justificacion": rec.justificacion,
            "riesgo": rec.riesgo, "detalle": rec.detalle,
        })
    return rec
