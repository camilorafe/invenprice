"""Genera data/pricing_reasoning_dataset.jsonl (Fase 4).

Cada caso fue autorado a mano con criterio de consultoría de pricing para pequeña empresa.
Los números derivados (márgenes, precio mínimo viable, unidades necesarias) se calculan con el
motor financiero determinista para que la justificación cite cifras EXACTAS y coherentes.

Uso:  python scripts/generar_dataset.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from invenprice import finance as f  # noqa: E402

SALIDA = Path(__file__).resolve().parent.parent / "data" / "pricing_reasoning_dataset.jsonl"

# unidades/mes aproximadas que implica cada velocidad (parámetro calibrable, ver rules.py)
UNIDADES_POR_VELOCIDAD = {"lenta": 40, "media": 120, "rapida": 300, "muy_rapida": 600}


def fmt(v) -> str:
    v = float(v)
    if abs(v) >= 1000 or v.is_integer():
        return f"{v:,.0f}".replace(",", ".")
    return f"{v:.2f}"


def pct(v) -> str:
    return f"{v:.1f}%"


# (id, P, C, m_min, velocidad, G, restricciones, Pc, P_rec, justificacion, riesgo)
CASOS = [
    # ------------------------------------------------------------ A. competencia más barata
    dict(
        P=35000, C=20300, m_min=30, vel="lenta", G=5_000_000, R=["nunca bajar de 30% de margen"], Pc=31000, P_rec=31000,
        J="El producto se vende lento a {P} mientras la competencia está en {Pc}, un {gap} por encima del mercado. Con costo "
          "{C} el margen actual es {m}, y el piso del dueño ({m_min}) fija un precio mínimo viable de {P_min}. Igualar el precio "
          "de la competencia en {P_rec} respeta el piso con holgura (margen resultante {m_rec}, {mu_rec} por unidad) y elimina la "
          "razón más probable de la baja rotación. Para alcanzar el objetivo de {G} de margen mensual harían falta {u_req} unidades "
          "al nuevo precio; con la velocidad actual (~{u_est}/mes) el objetivo no depende del precio sino del volumen, así que "
          "recuperar rotación es la prioridad.",
        Rk="Si la rotación no mejora tras igualar a la competencia, el problema no era el precio (visibilidad, surtido o "
           "producto) y se habrá cedido {delta} de precio sin ganar volumen. Revisar a 30 días.",
    ),
    dict(
        P=48000, C=36000, m_min=22, vel="lenta", G=3_000_000, R=["nunca bajar de 22% de margen"], Pc=42000, P_rec=46200,
        J="La competencia vende a {Pc}, pero con costo {C} y margen mínimo de {m_min} el precio mínimo viable es {P_min}: "
          "igualar a la competencia obligaría a vender por debajo del piso. La recomendación es bajar hasta el mínimo viable "
          "redondeado, {P_rec} (margen {m_rec}), y aceptar que seguiremos ~{gap_rec} por encima del competidor. Como no se puede "
          "competir por precio, el resto de la brecha debe cerrarse por servicio, garantía o presentación, o renegociando el "
          "costo de {C} con el proveedor. Con {mu_rec} de margen unitario el objetivo de {G} exige {u_req} unidades/mes, muy por "
          "encima de una venta lenta (~{u_est}); el objetivo es inalcanzable con este producto solo.",
        Rk="Quedar 10% por encima de la competencia con ventas lentas puede seguir sin mover inventario; si en 45 días no hay "
           "mejora, evaluar descontinuar la línea o renegociar el costo antes que romper el piso de margen.",
    ),
    dict(
        P=28000, C=15000, m_min=35, vel="media", G=4_000_000, R=[], Pc=25000, P_rec=26500,
        J="Vendemos a {P} contra {Pc} de la competencia ({gap} más caros) con rotación media: la demanda aguanta un sobreprecio, "
          "pero no conviene dejar crecer la brecha. Reducir a la mitad la distancia, a {P_rec}, mantiene un margen de {m_rec} "
          "(costo {C}, {mu_rec} por unidad) lejos del piso de {m_min} (mínimo viable {P_min}) y prueba si un precio más cercano "
          "al mercado acelera la venta sin regalar todo el diferencial. Para {G} de margen mensual se requieren {u_req} unidades; "
          "con ~{u_est}/mes actuales, ganar volumen pesa más que sostener los {mu} de margen actuales.",
        Rk="Una bajada parcial puede no ser suficiente para cambiar la decisión del cliente y sí reducir margen; medir "
           "unidades semanales y, si no hay respuesta en 3-4 semanas, decidir entre igualar a {Pc} o volver a {P}.",
    ),
    dict(
        P=12000, C=6500, m_min=30, vel="rapida", G=3_000_000, R=[], Pc=10500, P_rec=12000,
        J="Aunque la competencia está en {Pc} ({gap} por debajo), el producto rota rápido a {P}: el mercado ya validó el "
          "sobreprecio. Bajar sería regalar margen sin necesidad. Se recomienda mantener {P_rec}, con margen {m_rec} sobre costo "
          "{C} ({mu_rec} por unidad) y amplia distancia al mínimo viable de {P_min}. Para el objetivo de {G} hacen falta {u_req} "
          "unidades/mes frente a ~{u_est} de ritmo actual: el objetivo exige casi duplicar volumen, y eso no se logra bajando "
          "el precio sino ampliando canales.",
        Rk="La competencia puede estar ganando clientes nuevos que no vemos en nuestras ventas; vigilar si la rotación cae "
           "dos semanas seguidas, que sería la señal para acercarse a {Pc}.",
    ),
    dict(
        P=52000, C=30000, m_min=30, vel="media", G=4_000_000, R=[], Pc=50500, P_rec=52000,
        J="La diferencia con la competencia ({Pc}) es solo de {gap}, dentro del ruido de percepción del cliente, y la rotación "
          "es normal. Cambiar el precio por una brecha tan pequeña no altera la decisión de compra y sí introduce inestabilidad. "
          "Mantener {P_rec} conserva un margen de {m_rec} sobre costo {C} ({mu_rec} por unidad) con holgura frente al mínimo "
          "viable de {P_min}. El objetivo de {G} requiere {u_req} unidades/mes frente a ~{u_est} actuales: el foco debe ser "
          "volumen vía promoción o surtido, no precio.",
        Rk="Si la competencia baja más y la brecha supera 5-8%, la recomendación cambia; revisar el precio del competidor "
           "cada dos semanas.",
    ),
    dict(
        P=90000, C=45000, m_min=35, vel="lenta", G=6_000_000, R=[], Pc=63000, P_rec=76500,
        J="La competencia vende a {Pc}, {gap} por debajo de nuestro {P}, y el producto no rota. Igualar es imposible: con "
          "costo {C} y piso de {m_min} el mínimo viable es {P_min}. Además, una caída de un 30% de golpe destruye la referencia "
          "de valor. Se recomienda un primer paso de -15%, a {P_rec} (margen {m_rec}, {mu_rec} por unidad), medir 30 días y "
          "solo entonces decidir un segundo ajuste hacia {P_min}. El objetivo de {G} exige {u_req} unidades/mes; a ~{u_est} "
          "actuales el producto no es el vehículo para ese objetivo.",
        Rk="Tras el recorte seguiremos ~21% por encima del competidor; si el cliente compara solo precio, la bajada parcial "
           "no moverá inventario y habremos perdido {delta} de margen. Considerar salir de la línea si no reacciona.",
    ),
    dict(
        P=60000, C=33000, m_min=30, vel="lenta", G=4_000_000, R=["temporada baja hasta marzo"], Pc=54000, P_rec=51300,
        J="Estamos en temporada baja, vendemos lento y la competencia está en {Pc} ({gap} por debajo de {P}). Igualar no basta "
          "en temporada baja: conviene quedar ligeramente por debajo del competidor, en {P_rec} (5% bajo su precio), para ser la "
          "opción barata mientras dura la caída de demanda. Con costo {C} el margen resultante es {m_rec} ({mu_rec} por unidad), "
          "cómodo frente al mínimo viable de {P_min}. El objetivo de {G} pide {u_req} unidades/mes; en temporada baja (~{u_est}) "
          "es prioritario no acumular stock.",
        Rk="Bajar por debajo de la competencia puede desatar una respuesta de precio y fijar una referencia difícil de revertir "
           "al llegar la temporada alta; comunicar el precio como promoción de temporada con fecha de fin.",
    ),
    # ------------------------------------------------------------ B. competencia más cara
    dict(
        P=18000, C=11000, m_min=30, vel="muy_rapida", G=8_000_000, R=[], Pc=22000, P_rec=20700,
        J="El producto se agota (venta muy rápida) a {P} y la competencia cobra {Pc}, un {gap_abs} más: estamos dejando margen "
          "en la mesa. Igualar de golpe (+22%) puede generar rechazo, así que se recomienda un primer aumento del 15% a {P_rec}, "
          "que sube el margen de {m} a {m_rec} sobre costo {C} ({mu_rec} por unidad frente a {mu} hoy). Con ese margen el "
          "objetivo de {G} requiere {u_req} unidades/mes frente a ~{u_est} actuales (hoy exigiría {u_req_act}): el aumento "
          "acerca el objetivo sin alcanzarlo del todo. Si la rotación se mantiene, un segundo paso hasta {Pc} es razonable.",
        Rk="Un aumento del 15% puede frenar la rotación si parte de la demanda venía justamente del precio bajo; monitorear "
           "unidades diarias las dos primeras semanas y revertir a {P} si caen más de un 25%.",
    ),
    dict(
        P=40000, C=24000, m_min=30, vel="rapida", G=10_000_000, R=[], Pc=44000, P_rec=43700,
        J="Con venta rápida a {P} y la competencia en {Pc}, hay espacio para subir sin perder la ventaja de precio. Se "
          "recomienda {P_rec}, algo por debajo del competidor, con margen {m_rec} sobre costo {C} ({mu_rec} por unidad frente "
          "a {mu} actuales). El objetivo de {G} es exigente: incluso al nuevo precio requiere {u_req} unidades/mes frente a "
          "~{u_est} de ritmo actual, por eso conviene capturar más margen por unidad ahora que la demanda lo permite.",
        Rk="Acercarse tanto al precio de la competencia reduce el argumento de ser más baratos; si la rotación baja de forma "
           "notable, retroceder a un punto intermedio (~{P_mid}).",
    ),
    dict(
        P=25000, C=16000, m_min=25, vel="media", G=3_000_000, R=[], Pc=30000, P_rec=26500,
        J="Vendemos a {P} con la competencia en {Pc} ({gap_abs} más cara) y rotación media. Estar tan por debajo del mercado sin "
          "una rotación alta sugiere que el precio bajo no está comprando volumen: se recomienda recuperar parte de la brecha, "
          "subiendo a {P_rec} (margen {m_rec} sobre costo {C}, {mu_rec} por unidad frente a {mu}). El objetivo de {G} requiere "
          "{u_req} unidades/mes frente a ~{u_est} actuales, así que cada peso de margen unitario cuenta.",
        Rk="Si la rotación media dependía de ser claramente los más baratos, el aumento puede reducir ventas; mantener el "
           "precio por debajo del competidor y comunicar el valor, no el descuento.",
    ),
    dict(
        P=15000, C=9000, m_min=30, vel="lenta", G=2_000_000, R=[], Pc=17500, P_rec=15000,
        J="Ya somos {gap_abs} más baratos que la competencia ({Pc}) y aun así la venta es lenta: el precio no es la palanca. "
          "Bajar más solo sacrificaría margen sin evidencia de elasticidad, y subir con rotación lenta es arriesgado. Se "
          "recomienda mantener {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}, mínimo viable {P_min}) y actuar "
          "sobre visibilidad, ubicación en tienda o surtido. El objetivo de {G} requiere {u_req} unidades/mes; a ~{u_est} "
          "el problema es comercial, no de precio.",
        Rk="Mantener puede prolongar la baja rotación y el capital inmovilizado; fijar un plazo (60 días) para acciones "
           "comerciales y, si no funcionan, considerar liquidar o descontinuar.",
    ),
    dict(
        P=100000, C=70000, m_min=25, vel="media", G=5_000_000, R=[], Pc=104000, P_rec=100000,
        J="La competencia está apenas {gap_abs} por encima ({Pc}) y la rotación es normal: la brecha es demasiado pequeña "
          "para justificar un cambio. Mantener {P_rec} conserva un margen de {m_rec} sobre costo {C} ({mu_rec} por unidad) por "
          "encima del mínimo viable de {P_min}. El objetivo de {G} exige {u_req} unidades/mes frente a ~{u_est} actuales; el "
          "crecimiento vendrá de volumen, no de mover el precio un 4%.",
        Rk="Con margen unitario de {mu_rec} sobre un costo alto de {C}, cualquier aumento del proveedor comprime el margen "
           "rápidamente; revisar el costo antes que el precio.",
    ),
    dict(
        P=8000, C=5000, m_min=30, vel="muy_rapida", G=4_000_000, R=["no subir precio este trimestre (compromiso con clientes)"], Pc=9500, P_rec=8000,
        J="Todo indica que el precio podría subir: venta muy rápida y competencia en {Pc} ({gap_abs} más cara). Sin embargo, "
          "el dueño se comprometió a no subir este trimestre, y romper esa promesa cuesta más confianza que el margen que se "
          "ganaría. Se recomienda mantener {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}) y preparar el aumento "
          "hacia {Pc} para el inicio del próximo trimestre, comunicándolo con antelación. El objetivo de {G} requiere {u_req} "
          "unidades/mes frente a ~{u_est} actuales: con el precio congelado no se alcanzará este trimestre, y conviene "
          "decírselo al dueño con claridad.",
        Rk="Costo de oportunidad: cada unidad vendida deja ~{dif_comp} menos que al precio de la competencia; además puede "
           "haber desabastecimiento si la demanda sigue creciendo al precio actual.",
    ),
    # ------------------------------------------------------------ C. sin datos de competencia
    dict(
        P=45000, C=20000, m_min=35, vel="lenta", G=3_000_000, R=[], Pc=None, P_rec=40000,
        J="Sin referencia de competencia, la señal dominante es la venta lenta con un margen alto ({m}) que deja espacio para "
          "probar elasticidad: el mínimo viable con piso de {m_min} es {P_min}. Se recomienda un recorte del 11% a {P_rec}, que "
          "mantiene {m_rec} de margen sobre costo {C} ({mu_rec} por unidad). El objetivo de {G} requiere {u_req} unidades/mes "
          "frente a ~{u_est} actuales, por eso se prioriza volumen: el producto solo aportará al objetivo si rota.",
        Rk="Sin datos de competencia no sabemos si el precio es la causa de la lentitud; si tras 30 días las unidades no "
           "suben al menos un 15%, revertir y buscar la causa en otro lado.",
    ),
    dict(
        P=30000, C=22000, m_min=25, vel="lenta", G=2_000_000, R=[], Pc=None, P_rec=30000,
        J="La venta es lenta, pero el margen actual ({m}) está a menos de 2 puntos del piso de {m_min}: el mínimo viable es "
          "{P_min}, apenas por debajo del precio actual. No hay espacio real para una rebaja que el cliente perciba. Se "
          "recomienda mantener {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}) y atacar el costo: cada 1.000 de "
          "reducción en el costo de {C} abre más espacio que cualquier ajuste de precio. Para {G} de margen se necesitan {u_req} "
          "unidades/mes, inviable a ~{u_est}.",
        Rk="Sostener un producto lento con margen al límite inmoviliza capital; si no se logra bajar el costo en 60 días, "
           "evaluar descontinuarlo.",
    ),
    dict(
        P=22000, C=13000, m_min=30, vel="rapida", G=5_000_000, R=[], Pc=None, P_rec=24000,
        J="Rotación rápida sin datos de competencia: la demanda absorbe el precio actual, y el objetivo de {G} es exigente "
          "(al precio actual requiere {u_req_act} unidades/mes frente a ~{u_est}). Se recomienda subir un 9% a {P_rec}, llevando "
          "el margen de {m} a {m_rec} sobre costo {C} ({mu_rec} por unidad). Con ese margen el objetivo baja a {u_req} "
          "unidades/mes. El mínimo viable ({P_min}) queda muy lejos, así que el riesgo de margen es nulo.",
        Rk="Sin referencia de mercado, un aumento del 9% podría dejarnos por encima de un competidor que no vemos; observar "
           "la rotación dos semanas y revertir si cae más de un 20%.",
    ),
    dict(
        P=65000, C=40000, m_min=30, vel="muy_rapida", G=12_000_000, R=["temporada alta: diciembre"], Pc=None, P_rec=74800,
        J="Venta muy rápida y temporada alta en diciembre: es el momento de máxima disposición a pagar. Se recomienda subir "
          "un 15% a {P_rec} (tope prudente por paso), lo que eleva el margen de {m} a {m_rec} sobre costo {C} ({mu_rec} por "
          "unidad frente a {mu}). El objetivo de {G} requiere {u_req} unidades/mes al nuevo precio, muy alcanzable a ~{u_est}/mes; "
          "sin la subida harían falta {u_req_act}. El mínimo viable ({P_min}) no es restricción.",
        Rk="Subir en temporada alta puede percibirse como oportunismo si el cliente habitual lo nota; comunicar el precio de "
           "temporada y prever volver a un precio regular en enero cuando caiga la demanda.",
    ),
    dict(
        P=12500, C=8000, m_min=30, vel="media", G=1_500_000, R=[], Pc=None, P_rec=12100,
        J="Rotación media sin datos de competencia y un objetivo de {G} que al precio actual exige {u_req_act} unidades/mes "
          "frente a ~{u_est}: hace falta volumen. Con margen {m} hay 6 puntos sobre el piso de {m_min} (mínimo viable {P_min}). "
          "Se recomienda un recorte pequeño, a {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}), como prueba de "
          "elasticidad de bajo costo: si el volumen sube más de un 4%, la rebaja se paga sola.",
        Rk="Un 3% de rebaja puede ser invisible para el cliente y solo recortar margen; definir de antemano la métrica "
           "(unidades semanales) y el plazo (4 semanas) para revertir.",
    ),
    dict(
        P=12500, C=8000, m_min=30, vel="media", G=300_000, R=[], Pc=None, P_rec=12500,
        J="Mismo producto pero con un objetivo modesto de {G}: al precio actual bastan {u_req} unidades/mes, muy por debajo de "
          "la rotación media (~{u_est}). No hay presión de objetivo ni señal de mercado que justifique tocar el precio. "
          "Mantener {P_rec} conserva un margen de {m_rec} sobre costo {C} ({mu_rec} por unidad), con el mínimo viable en "
          "{P_min}. Cambiar precios sin motivo erosiona la confianza del cliente.",
        Rk="Un objetivo tan bajo puede ocultar que el producto podría rendir más; revisar el objetivo antes que el precio.",
    ),
    dict(
        P=20000, C=16000, m_min=30, vel="media", G=2_000_000, R=["nunca bajar de 30% de margen"], Pc=None, P_rec=22900,
        J="El producto está violando la regla del dueño: margen actual {m} frente a un mínimo de {m_min}. Con costo {C} el "
          "precio mínimo viable es {P_min}; se recomienda subir a {P_rec} (margen {m_rec}, {mu_rec} por unidad frente a {mu} "
          "hoy). Es un aumento del {delta_abs}, en el límite de lo prudente por paso, pero necesario porque hoy cada venta aporta "
          "menos de lo que el negocio necesita. Para {G} de margen mensual se requieren {u_req} unidades al nuevo precio "
          "(al precio actual serían {u_req_act}).",
        Rk="Un aumento del 15% con rotación media puede reducir el volumen; si el cliente no lo acepta, la alternativa no es "
           "bajar el precio sino bajar el costo de {C} o retirar el producto.",
    ),
    dict(
        P=9000, C=10000, m_min=20, vel="lenta", G=1_000_000, R=[], Pc=None, P_rec=12500,
        J="Se está vendiendo por debajo del costo: precio {P} contra costo {C}, margen {m}. Cada unidad vendida destruye "
          "{mu_abs}. Con piso de {m_min} el mínimo viable es {P_rec}, y esa es la recomendación aunque implique un aumento del "
          "{delta_abs}: no hay ajuste gradual posible cuando se pierde dinero por unidad. Al nuevo precio el margen es {m_rec} "
          "({mu_rec} por unidad). Para {G} de margen mensual se requerirían {u_req} unidades, poco realista con venta lenta.",
        Rk="Un salto del 39% con ventas lentas puede frenar por completo la venta; si ocurre, es preferible descontinuar el "
           "producto o liquidar el stock restante que seguir vendiendo a pérdida.",
    ),
    dict(
        P=25000, C=0, m_min=50, vel="media", G=2_000_000, R=[], Pc=30000, P_rec=26500,
        J="Producto sin costo unitario (costo {C}: servicio digital o inventario ya amortizado): el margen es 100% a cualquier "
          "precio, el markup es indefinido y el mínimo viable es 0, así que la referencia relevante es el mercado. La competencia "
          "cobra {Pc} ({gap_abs} más) con rotación media; se recomienda recuperar parte de la brecha subiendo a {P_rec}, que sigue "
          "12% por debajo del competidor. El objetivo de {G} requiere {u_req} unidades/mes frente a ~{u_est} actuales.",
        Rk="Con costo cero la tentación es competir bajando precio; el riesgo real es lo contrario, subvalorar el producto. "
           "Vigilar que la brecha con la competencia no supere el 20%.",
    ),
    dict(
        P=5000, C=3200, m_min=25, vel="muy_rapida", G=3_000_000, R=["stock limitado, reposición en 45 días"], Pc=None, P_rec=5750,
        J="Venta muy rápida con stock limitado y reposición a 45 días: el inventario se agotará antes de reponer, así que "
          "vender más barato solo adelanta el desabastecimiento. Se recomienda subir un 15% a {P_rec}, elevando el margen de {m} "
          "a {m_rec} sobre costo {C} ({mu_rec} por unidad frente a {mu}). El precio mayor ralentiza el agotamiento y maximiza el "
          "margen del stock existente. El objetivo de {G} requiere {u_req} unidades/mes al nuevo precio.",
        Rk="Si el cliente percibe el aumento como abuso por escasez puede migrar a otro proveedor de forma permanente; limitar "
           "el aumento al periodo de escasez y volver a {P} al reponer.",
    ),
    dict(
        P=8000, C=4500, m_min=20, vel="lenta", G=800_000, R=["producto perecedero, vence en 15 días"], Pc=None, P_rec=6800,
        J="Producto perecedero con vencimiento a 15 días y venta lenta: el costo real de no vender es perder el 100% del "
          "costo ({C} por unidad). Se recomienda bajar un 15% a {P_rec}, manteniendo un margen de {m_rec} ({mu_rec} por unidad) "
          "sobre el piso de {m_min} (mínimo viable {P_min}). Es mejor asegurar {mu_rec} por unidad que arriesgar perder {C}. "
          "El objetivo de {G} requiere {u_req} unidades; con la fecha encima, el objetivo es secundario frente a evitar la merma.",
        Rk="Si el 15% no acelera la venta, será necesario un segundo recorte hasta el mínimo viable ({P_min}) a los 7 días; "
           "el dueño debe decidir explícitamente si autoriza vender bajo el piso antes de perder el producto.",
    ),
    dict(
        P=30000, C=24000, m_min=15, vel="lenta", G=1_000_000, R=["liquidar inventario de fin de temporada"], Pc=None, P_rec=28300,
        J="El dueño quiere liquidar, pero el margen actual ({m}) está a solo 5 puntos del piso de {m_min}: con costo {C} el "
          "mínimo viable es {P_min}. El precio recomendado es {P_rec}, el mínimo viable redondeado, con margen {m_rec} ({mu_rec} "
          "por unidad). Es una rebaja de apenas {delta_abs}, probablemente insuficiente como liquidación: si el dueño quiere un "
          "descuento visible debe bajar explícitamente el piso de margen para esta línea, aceptando la pérdida de margen a cambio "
          "de liberar caja. El objetivo de {G} requiere {u_req} unidades.",
        Rk="Una liquidación del 6% no atrae compradores y el stock puede quedarse; la decisión de vender bajo el piso es del "
           "dueño y debe tomarse pronto, porque cada semana de stock parado cuesta espacio y capital.",
    ),
    # ------------------------------------------------------------ D. restricciones del dueño
    dict(
        P=120000, C=60000, m_min=40, vel="lenta", G=6_000_000, R=["posicionamiento premium: no bajar del precio actual"], Pc=95000, P_rec=120000,
        J="La competencia vende a {Pc} ({gap} por debajo) y la rotación es lenta, lo que normalmente aconsejaría bajar. Pero el "
          "dueño definió un posicionamiento premium y prohíbe bajar de {P}: una marca premium que baja el precio pierde justamente "
          "lo que vende. Se recomienda mantener {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}, mínimo viable "
          "{P_min}) y trabajar la rotación con exclusividad, servicio y presentación. Para {G} de margen se requieren {u_req} "
          "unidades/mes frente a ~{u_est} actuales: en premium el objetivo se cumple con más rotación a igual precio, no con "
          "descuentos.",
        Rk="Si la lentitud persiste, el posicionamiento premium no está siendo comunicado o el mercado no lo reconoce; revisar "
           "la propuesta de valor antes que el precio, con un plazo de 90 días.",
    ),
    dict(
        P=14000, C=9000, m_min=30, vel="rapida", G=3_000_000, R=["no subir: contrato de precio con cliente institucional"], Pc=None, P_rec=14000,
        J="Con venta rápida y margen {m} habría espacio para subir, pero existe un contrato de precio con un cliente "
          "institucional que lo impide. Se recomienda mantener {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}) "
          "y, si se quiere capturar más margen, hacerlo en el canal minorista con una lista de precios separada o en la próxima "
          "renegociación del contrato. Para {G} de margen mensual se requieren {u_req} unidades frente a ~{u_est}, alcanzable.",
        Rk="El contrato fija el precio pero no el costo: si el costo de {C} sube, el margen se comprime sin poder reaccionar; "
           "incluir una cláusula de indexación en la renovación.",
    ),
    dict(
        P=40000, C=22000, m_min=30, vel="media", G=2_000_000, R=["lanzamiento: ganar cuota los primeros 3 meses"], Pc=38000, P_rec=37000,
        J="Producto en lanzamiento con la competencia en {Pc}: el objetivo de los primeros meses es cuota, no margen. Se "
          "recomienda entrar ligeramente por debajo del competidor, en {P_rec}, con margen {m_rec} ({mu_rec} por unidad sobre "
          "costo {C}) y buena distancia al mínimo viable de {P_min}. Es un precio de penetración prudente: visible frente al "
          "competidor sin regalar margen. El objetivo de {G} requiere {u_req} unidades/mes, apenas por encima de la rotación media (~{u_est}): "
          "alcanzable si la penetración funciona.",
        Rk="Los precios de lanzamiento fijan expectativas: subir después a {P} puede sentirse como aumento. Comunicar desde el "
           "inicio que es precio de lanzamiento con fecha de fin.",
    ),
    dict(
        P=50000, C=30000, m_min=30, vel="rapida", G=8_000_000, R=["temporada alta (regreso a clases)"], Pc=46000, P_rec=52500,
        J="La competencia está en {Pc} ({gap} por debajo), pero el producto rota rápido y entramos en temporada alta: la demanda "
          "supera la sensibilidad al precio. Se recomienda un aumento moderado del 5% a {P_rec}, llevando el margen de {m} a "
          "{m_rec} sobre costo {C} ({mu_rec} por unidad). El objetivo de {G} requiere {u_req} unidades/mes al nuevo precio "
          "frente a ~{u_est} de ritmo actual, lo que hace valioso cada punto de margen en temporada.",
        Rk="Subir estando ya por encima del competidor amplía la brecha a ~14%; si la rotación cae al terminar la temporada, "
           "volver de inmediato a {P} o por debajo.",
    ),
    dict(
        P=36000, C=21000, m_min=30, vel="media", G=3_000_000, R=["temporada baja"], Pc=None, P_rec=33100,
        J="Temporada baja con rotación media y sin datos de competencia. El margen actual ({m}) deja 12 puntos sobre el piso de "
          "{m_min} (mínimo viable {P_min}), así que hay espacio para estimular demanda. Se recomienda una rebaja del 8% a {P_rec} "
          "(margen {m_rec}, {mu_rec} por unidad sobre costo {C}) presentada como precio de temporada. El objetivo de {G} requiere "
          "{u_req} unidades/mes frente a ~{u_est}: sin volumen adicional no se alcanza, y el precio es la única palanca inmediata.",
        Rk="Si la caída de demanda es estacional y no de precio, la rebaja no traerá volumen; fijar fecha de fin de la "
           "promoción para restaurar {P} al iniciar la temporada normal.",
    ),
    # ------------------------------------------------------------ E. presión del objetivo y casos límite
    dict(
        P=30000, C=21000, m_min=25, vel="rapida", G=15_000_000, R=[], Pc=None, P_rec=32700,
        J="El objetivo de {G} es desproporcionado para este producto: al precio actual requiere {u_req_act} unidades/mes frente "
          "a ~{u_est} de una venta rápida. Como la demanda es fuerte, se recomienda subir un 9% a {P_rec} (margen {m_rec} sobre "
          "costo {C}, {mu_rec} por unidad frente a {mu}), lo que reduce la necesidad a {u_req} unidades/mes: sigue siendo "
          "inalcanzable, pero cada unidad aporta más. El mínimo viable ({P_min}) no es restricción. El mensaje al dueño es que "
          "el objetivo debe repartirse entre varios productos.",
        Rk="Subir precio persiguiendo un objetivo irreal puede frenar una demanda que hoy es sana; el riesgo mayor es "
           "seguir subiendo en pasos sucesivos. Revisar el objetivo, no solo el precio.",
    ),
    dict(
        P=30000, C=21000, m_min=25, vel="lenta", G=200_000, R=[], Pc=None, P_rec=28000,
        J="Objetivo modesto ({G}) que requiere pocas unidades, pero la venta es lenta y hay 5 puntos de margen sobre el piso de "
          "{m_min}. Se recomienda bajar hasta el mínimo viable, {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}), "
          "para probar si un precio más bajo reactiva la rotación. Al nuevo precio el objetivo exige {u_req} unidades/mes, "
          "compatible con ~{u_est} de venta lenta. El recorte del 7% agota todo el espacio de precio: no hay más margen para bajar.",
        Rk="Quedar exactamente en el piso deja cero margen de maniobra ante cualquier aumento de costo; si el costo de {C} sube, "
           "habrá que subir el precio de inmediato o descontinuar.",
    ),
    dict(
        P=20000, C=12000, m_min=30, vel="media", G=2_000_000, R=[], Pc=20000, P_rec=20000,
        J="Precio en paridad exacta con la competencia ({Pc}) y rotación media: el mercado está equilibrado. Mantener {P_rec} "
          "(margen {m_rec}, {mu_rec} por unidad sobre costo {C}, mínimo viable {P_min}) evita iniciar una guerra de precios sin "
          "necesidad. El objetivo de {G} requiere {u_req} unidades/mes frente a ~{u_est}: la diferencia debe cubrirse con más "
          "productos o más tráfico, no con precio.",
        Rk="La paridad hace que cualquier movimiento del competidor nos afecte de inmediato; revisar su precio con frecuencia "
           "y tener definida de antemano la respuesta.",
    ),
    dict(
        P=20000, C=12000, m_min=30, vel="muy_rapida", G=4_000_000, R=[], Pc=20000, P_rec=21000,
        J="Precio en paridad con la competencia ({Pc}) pero venta muy rápida: la demanda indica que el cliente nos prefiere a "
          "igual precio, y ese diferencial se puede monetizar con prudencia. Se recomienda un aumento del 5% a {P_rec}, con margen "
          "{m_rec} ({mu_rec} por unidad sobre costo {C}). Es un paso pequeño que prueba disposición a pagar sin alejarse del "
          "mercado. El objetivo de {G} requiere {u_req} unidades/mes al nuevo precio, alcanzable a ~{u_est}.",
        Rk="Romper la paridad puede desviar a los clientes que comparan precio; si la rotación cae por debajo de la de la "
           "competencia, volver a {P}.",
    ),
    dict(
        P=15000, C=9000, m_min=30, vel="media", G=0, R=[], Pc=None, P_rec=15000,
        J="No hay objetivo de ingreso definido (0) ni datos de competencia, y la rotación es media: no existe ninguna señal que "
          "justifique mover el precio. Se recomienda mantener {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}, "
          "mínimo viable {P_min}) y, antes que nada, fijar un objetivo mensual: sin él no es posible saber si el producto "
          "necesita más margen o más volumen.",
        Rk="Operar sin objetivo impide detectar a tiempo si el producto rinde por debajo de lo necesario; el riesgo es "
           "estratégico, no de precio.",
    ),
    dict(
        P=4.5, C=2.7, m_min=30, vel="lenta", G=3000, R=[], Pc=3.99, P_rec=3.99,
        J="Producto de ticket bajo con precio {P} frente a {Pc} de la competencia ({gap} más caros) y venta lenta. Con costo "
          "{C} y piso de {m_min} el mínimo viable es {P_min}, así que igualar a {P_rec} es viable con margen {m_rec} ({mu_rec} por "
          "unidad). En tickets bajos el cliente compara el precio de forma directa y una diferencia de 0,51 pesa mucho. Para el "
          "objetivo de {G} se requieren {u_req} unidades/mes frente a ~{u_est}.",
        Rk="Con margen unitario de {mu_rec}, cualquier costo oculto (empaque, comisión de pago) puede llevar el margen real "
           "bajo el piso; verificar los gastos variables antes de aplicar.",
    ),
    dict(
        P=2_500_000, C=1_900_000, m_min=20, vel="lenta", G=20_000_000, R=[], Pc=2_350_000, P_rec=2_380_000,
        J="Producto de alto valor con venta lenta y competencia en {Pc} ({gap} por debajo). Con costo {C} y piso de {m_min} "
          "el mínimo viable es {P_min}, así que no se puede igualar; se recomienda bajar al mínimo redondeado, {P_rec} (margen "
          "{m_rec}, {mu_rec} por unidad), quedando apenas 1,3% por encima del competidor, una brecha que un buen servicio de venta "
          "cierra. Para {G} de margen mensual se requieren {u_req} unidades; en un ticket alto eso significa cerrar {u_req} ventas "
          "al mes frente a ~{u_est} de ritmo actual.",
        Rk="En tickets altos el cliente negocia; quedar exactamente en el piso elimina cualquier espacio para descuento en la "
           "negociación final. Considerar bajar el costo con el proveedor antes que el precio.",
    ),
    dict(
        P=10000, C=6000, m_min=30, vel="rapida", G=5_000_000, R=[], Pc=14000, P_rec=11500,
        J="Vendemos a {P} con la competencia en {Pc}, un {gap_abs} más cara, y aun así la rotación es rápida: estamos claramente "
          "subvalorados. Igualar de golpe no es prudente; se recomienda un primer aumento del 15% a {P_rec}, con margen {m_rec} "
          "sobre costo {C} ({mu_rec} por unidad frente a {mu} actuales). El objetivo de {G} pasa de requerir {u_req_act} unidades "
          "a {u_req} unidades/mes. Si la rotación se mantiene, seguir subiendo en pasos hasta acercarse a {Pc}.",
        Rk="Parte de la demanda rápida puede venir de compradores que solo eligen por precio; el aumento revelará cuánta. "
           "Monitorear la rotación semanal y detener la escalada si cae más de un 20%.",
    ),
    dict(
        P=100000, C=30000, m_min=40, vel="lenta", G=5_000_000, R=[], Pc=None, P_rec=89000,
        J="Margen muy alto ({m}) con venta lenta y sin referencia de competencia: la señal es que el precio está frenando la "
          "rotación y hay mucho espacio (mínimo viable {P_min} con piso de {m_min}). Se recomienda una rebaja del 11% a {P_rec}, "
          "manteniendo {m_rec} de margen ({mu_rec} por unidad sobre costo {C}). El objetivo de {G} requiere {u_req} unidades/mes "
          "frente a ~{u_est} actuales: el producto necesita duplicar rotación, y el precio es la palanca más directa.",
        Rk="Un margen del 70% puede reflejar valor percibido alto (exclusividad); bajar podría dañar esa percepción sin ganar "
           "volumen. Probar primero con un segmento o canal antes de generalizar.",
    ),
    dict(
        P=30000, C=18000, m_min=30, vel="muy_rapida", G=6_000_000, R=[], Pc=27000, P_rec=30000,
        J="La competencia vende a {Pc} ({gap} por debajo), pero el producto rota muy rápido a {P}: los clientes ya pagan el "
          "diferencial. Bajar sería regalar margen y subir sin mirar al competidor sería excesivo. Se recomienda mantener {P_rec} "
          "(margen {m_rec}, {mu_rec} por unidad sobre costo {C}, mínimo viable {P_min}) y asegurar disponibilidad de stock, que es "
          "el verdadero cuello de botella. El objetivo de {G} requiere {u_req} unidades/mes, alcanzable a ~{u_est}.",
        Rk="Con venta muy rápida el riesgo principal es el desabastecimiento, no el precio; si hay quiebres de stock frecuentes, "
           "la recomendación cambia a subir el precio.",
    ),
    dict(
        P=70000, C=35000, m_min=45, vel="lenta", G=4_000_000, R=["nunca bajar de 45% de margen"], Pc=60000, P_rec=63700,
        J="La competencia está en {Pc} ({gap} por debajo) y la venta es lenta, pero el dueño fija un piso alto de {m_min}: con "
          "costo {C} el mínimo viable es {P_min}. Se recomienda bajar hasta ese mínimo redondeado, {P_rec} (margen {m_rec}, "
          "{mu_rec} por unidad), quedando ~6% por encima del competidor. Es todo el espacio que la restricción permite; la brecha "
          "restante debe justificarse con producto o servicio. El objetivo de {G} requiere {u_req} unidades/mes frente a ~{u_est}.",
        Rk="Un piso del 45% puede ser incompatible con este mercado; si tras el ajuste no rota, el dueño debe elegir entre "
           "relajar el piso para esta línea o retirar el producto.",
    ),
    dict(
        P=18000, C=13500, m_min=30, vel="media", G=2_500_000, R=[], Pc=18500, P_rec=19300,
        J="El costo subió a {C} y el margen actual ({m}) quedó bajo el piso de {m_min}: el mínimo viable es {P_min}. Es "
          "obligatorio subir a {P_rec} (margen {m_rec}, {mu_rec} por unidad), aunque eso nos deje ~4% por encima de la competencia "
          "({Pc}). Con rotación media el aumento del {delta_abs} es asumible si se comunica como consecuencia del costo. Para {G} "
          "de margen mensual se requieren {u_req} unidades al nuevo precio frente a ~{u_est} actuales.",
        Rk="Pasar de estar por debajo a estar por encima de la competencia puede costar clientes sensibles al precio; "
           "buscar en paralelo un proveedor alternativo que devuelva el costo a niveles previos.",
    ),
    dict(
        P=22000, C=14000, m_min=30, vel="rapida", G=-100, R=[], Pc=None, P_rec=23300,
        J="El objetivo registrado es negativo ({G}), es decir, inválido; se trata como inexistente y se decide solo con las "
          "señales de mercado: venta rápida y sin datos de competencia. Se recomienda un aumento moderado del 6% a {P_rec}, "
          "llevando el margen de {m} a {m_rec} sobre costo {C} ({mu_rec} por unidad frente a {mu}). El mínimo viable ({P_min}) "
          "queda lejos. Corregir el objetivo mensual es prioritario para poder medir si el producto rinde.",
        Rk="Sin objetivo válido no hay criterio para saber cuánto margen adicional se necesita; el aumento del 6% es una "
           "apuesta conservadora que debe revisarse cuando exista un objetivo real.",
    ),
    dict(
        P=55000, C=33000, m_min=30, vel="lenta", G=4_000_000, R=["temporada alta: día de la madre"], Pc=52000, P_rec=54600,
        J="Señales contradictorias: venta lenta y competencia {gap} por debajo ({Pc}) sugieren bajar, pero se acerca la "
          "temporada alta del día de la madre, cuando la demanda sube sola. No conviene ceder margen justo antes del pico. Se "
          "recomienda un ajuste mínimo a {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}), prácticamente mantener, "
          "y reevaluar después de la temporada. El objetivo de {G} requiere {u_req} unidades/mes; la temporada debería acercar "
          "la rotación a esa cifra.",
        Rk="Si la temporada alta no compensa la lentitud, se habrá perdido un mes sin corregir el precio; fijar la revisión "
           "para la semana siguiente al pico con una regla clara: si no rota, igualar a {Pc}.",
    ),
    dict(
        P=20000, C=12000, m_min=30, vel="muy_rapida", G=3_000_000, R=["liquidar: cambio de colección en 3 semanas"], Pc=None, P_rec=20400,
        J="El dueño pide liquidar por cambio de colección, pero el producto ya se vende muy rápido a {P}: el stock se agotará "
          "solo antes de las 3 semanas. Descontar sería regalar margen a clientes que ya compran. Se recomienda mantener o subir "
          "apenas un 2%, a {P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}), y reservar el descuento para las últimas "
          "unidades si en la semana 2 aún queda stock. El objetivo de {G} requiere {u_req} unidades/mes, alcanzable a ~{u_est}.",
        Rk="Si la rotación se frena antes de agotar el stock, quedará inventario obsoleto con la nueva colección en tienda; "
           "definir un umbral (stock restante en la semana 2) que active la liquidación.",
    ),
    dict(
        P=26000, C=18000, m_min=30, vel="media", G=3_000_000, R=[], Pc=24000, P_rec=26000,
        J="La competencia está en {Pc} ({gap} por debajo), pero el margen actual ({m}) está prácticamente en el piso de {m_min}: "
          "el mínimo viable es {P_min}. No se puede seguir al competidor sin violar la regla del dueño. Se recomienda mantener "
          "{P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}) y trabajar el costo: bajar el costo a 17.000 permitiría "
          "igualar a {Pc} cumpliendo el piso. El objetivo de {G} requiere {u_req} unidades/mes frente a ~{u_est}.",
        Rk="Ser 8% más caros con rotación media es sostenible a corto plazo, pero si la competencia sostiene su precio la "
           "rotación caerá; la solución está en el costo de {C}, no en el precio.",
    ),
    dict(
        P=40000, C=16000, m_min=35, vel="rapida", G=5_000_000, R=[], Pc=32000, P_rec=40000,
        J="Estamos {gap} por encima de la competencia ({Pc}) con margen {m}, y aun así la venta es rápida: el producto tiene un "
          "valor percibido claramente superior. Bajar hacia el competidor destruiría margen sin necesidad. Se recomienda mantener "
          "{P_rec} (margen {m_rec}, {mu_rec} por unidad sobre costo {C}, mínimo viable {P_min}). El objetivo de {G} requiere "
          "{u_req} unidades/mes, cómodamente dentro de ~{u_est} de ritmo actual.",
        Rk="Un diferencial del 25% invita a nuevos competidores o a que el actual mejore su oferta; vigilar la rotación "
           "mensual y no dar por permanente la ventaja.",
    ),
    dict(
        P=80000, C=40000, m_min=40, vel="lenta", G=8_000_000, R=["nunca bajar de 40% de margen en esta línea"], Pc=None, P_rec=71200,
        J="Venta lenta con margen {m} y sin datos de competencia; el dueño fija un piso de {m_min}, es decir un mínimo viable "
          "de {P_min}. Hay 10 puntos de espacio. Se recomienda una rebaja del 11% a {P_rec} (margen {m_rec}, {mu_rec} por unidad "
          "sobre costo {C}), que estimula demanda sin acercarse al piso. El objetivo de {G} requiere {u_req} unidades/mes frente "
          "a ~{u_est}: sin un salto de volumen el objetivo es inalcanzable, por eso se apuesta por precio antes que por margen.",
        Rk="Si la elasticidad es baja, la rebaja reduce el margen sin volumen adicional; establecer un umbral de éxito "
           "(unidades +20% en 30 días) para decidir entre volver a {P} o seguir hasta {P_min}.",
    ),
]


def construir(i: int, c: dict) -> dict:
    P, C, m_min, G, Pc, P_rec = c["P"], c["C"], c["m_min"], c["G"], c["Pc"], c["P_rec"]
    m = f.margen_bruto_pct(P, C)
    m_rec = f.margen_bruto_pct(P_rec, C)
    P_min = f.precio_minimo_viable(C, m_min).precio
    if P_rec < P_min - 1e-9:
        raise ValueError(f"caso {i}: P_rec {P_rec} < P_min {P_min}")
    mu, mu_rec = P - C, P_rec - C
    u_est = UNIDADES_POR_VELOCIDAD[c["vel"]]

    def unidades(margen_unit):
        r = f.unidades_para_objetivo(G, margen_unit, P_rec)
        return fmt(r.unidades) if r.unidades is not None else "n/a"

    ctx = dict(
        P=fmt(P), C=fmt(C), m=pct(m), m_min=pct(m_min), P_min=fmt(P_min), G=fmt(G), P_rec=fmt(P_rec),
        m_rec=pct(m_rec), mu=fmt(mu), mu_rec=fmt(mu_rec), mu_abs=fmt(abs(mu)), u_est=u_est,
        u_req=unidades(mu_rec), u_req_act=unidades(mu),
        delta=pct(abs((P_rec - P) / P * 100)), delta_abs=pct(abs((P_rec - P) / P * 100)),
        Pc=fmt(Pc) if Pc else "n/a",
        gap=pct((P - Pc) / Pc * 100) if Pc else "n/a",            # cuánto más caros somos que la competencia
        gap_abs=pct(abs(P - Pc) / P * 100) if Pc else "n/a",     # brecha relativa a nuestro precio
        gap_rec=pct((P_rec - Pc) / Pc * 100) if Pc else "n/a",
        dif_comp=fmt(Pc - P) if Pc else "n/a",
        P_mid=fmt(round((P + Pc) / 2, -2)) if Pc else "n/a",
    )
    return {
        "id": f"caso_{i:03d}",
        "input": {
            "precio_actual": P,
            "costo": C,
            "margen_actual_pct": round(m, 1),
            "margen_minimo_pct": m_min,
            "velocidad_venta": c["vel"],
            "objetivo_ingreso_mensual": G,
            "restricciones": c["R"],
            "precio_competencia": Pc,
        },
        "output": {
            "precio_recomendado": P_rec,
            "margen_resultante_pct": round(m_rec, 1),
            "justificacion": c["J"].format(**ctx),
            "riesgo": c["Rk"].format(**ctx),
        },
    }


def main() -> None:
    casos = [construir(i + 1, c) for i, c in enumerate(CASOS)]
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    with SALIDA.open("w", encoding="utf-8", newline="\n") as fh:  # LF siempre: el CI regenera en Linux y compara
        for c in casos:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"{len(casos)} casos escritos en {SALIDA}")


if __name__ == "__main__":
    main()
