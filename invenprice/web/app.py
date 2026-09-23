"""Dashboard web local (Fase 8). Flask + HTML renderizado en servidor, sin JavaScript obligatorio.

Arranque:  python -m invenprice.web.app   (abre http://127.0.0.1:5000)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import anomalies, copilot, currency, db, finance as f, rules

MONEDAS = ("USD", "COP", "CNY")
VELOCIDADES = ("lenta", "media", "rapida", "muy_rapida")


clasificar_velocidad = rules.clasificar_velocidad  # compatibilidad


def _edad(fecha_iso: str) -> str:
    """'hace 3 h', 'hace 2 días' para mostrar qué tan vieja es una recomendación guardada."""
    try:
        dt = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    seg = (datetime.now(timezone.utc) - dt).total_seconds()
    if seg < 3600:
        return f"hace {int(seg // 60)} min"
    if seg < 86400:
        return f"hace {int(seg // 3600)} h"
    return f"hace {int(seg // 86400)} días"


def crear_app(ruta_db: str | Path = db.DB_PATH_DEFAULT, testing: bool = False) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).with_name("templates")))
    app.config.update(SECRET_KEY="invenprice-local", TESTING=testing, RUTA_DB=str(ruta_db))

    # una sola conexión compartida cuando la BD es en memoria (tests); por petición si es archivo
    conn_memoria = db.abrir(":memory:") if str(ruta_db) == ":memory:" else None
    app.config["CONN_TEST"] = conn_memoria  # solo para tests: acceso directo a la BD en memoria

    def conexion() -> sqlite3.Connection:
        if conn_memoria is not None:
            return conn_memoria
        if "conn" not in g:
            g.conn = db.abrir(app.config["RUTA_DB"])
        return g.conn

    @app.teardown_appcontext
    def cerrar(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    # ------------------------------------------------------------------ filtros de plantilla
    @app.template_filter("dinero")
    def _dinero(v, moneda="COP"):
        return "—" if v is None else currency.formatear(v, moneda)

    @app.template_filter("pct")
    def _pct(v):
        return "—" if v is None else f"{v:.1f}%".replace(".", ",")

    @app.template_filter("num")
    def _num(v):
        if v is None:
            return "—"
        return f"{v:,.0f}".replace(",", ".") if float(v).is_integer() or abs(v) >= 1000 else f"{v:.2f}"

    # ------------------------------------------------------------------ helpers
    def cfg(clave, default=None):
        return db.obtener_config(conexion(), clave, default)

    def contexto_producto(p: dict, velocidad: Optional[str] = None, restricciones: Optional[list[str]] = None) -> dict:
        # misma lógica que usa el proceso por lotes (invenprice.batch_pricing)
        return copilot.contexto_desde_bd(conexion(), p, velocidad=velocidad, restricciones=restricciones)

    def analisis(p: dict, ctx: Optional[dict] = None) -> dict:
        conn = conexion()
        ctx = ctx or contexto_producto(p)
        n = max(1, len(db.listar_productos(conn)))
        costos_fijos_base = db.total_costos_fijos(conn)
        costos_fijos_prod = currency.convertir_con_bd(conn, costos_fijos_base, cfg("moneda_base", "COP"), p["moneda"]) / n
        a = f.analisis_producto(p, costos_fijos_asignados=costos_fijos_prod, objetivo_mensual=ctx["objetivo_ingreso_mensual"],
                                margen_minimo_global_pct=ctx["margen_minimo_global_pct"])
        tasas = currency.tasas_actuales(conn)
        a["precios"] = currency.en_todas_las_monedas(p["precio_venta"], p["moneda"], tasas)
        a["costos"] = currency.en_todas_las_monedas(p["costo"], p["moneda"], tasas)
        a["minimos"] = currency.en_todas_las_monedas(a["precio_minimo_viable"] or 0, p["moneda"], tasas)
        a["costos_fijos_asignados"] = costos_fijos_prod
        return a

    def leer_float(nombre, default=None, obligatorio=False):
        v = (request.form.get(nombre) or "").strip().replace(",", ".")
        if v == "":
            if obligatorio:
                raise ValueError(f"El campo '{nombre}' es obligatorio")
            return default
        return float(v)

    # ------------------------------------------------------------------ rutas
    @app.route("/")
    def dashboard():
        conn = conexion()
        productos = db.listar_productos(conn)
        desde = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        moneda_base = cfg("moneda_base", "COP")
        filas = []
        for p in productos:
            ventas = db.listar_ventas(conn, producto_id=p["id"], desde=desde)
            u = sum(v["cantidad"] for v in ventas)
            filas.append({"id": p["id"], "nombre": p["nombre"], "precio_venta": currency.convertir_con_bd(conn, p["precio_venta"], p["moneda"], moneda_base),
                          "costo": currency.convertir_con_bd(conn, p["costo"], p["moneda"], moneda_base),
                          "gastos_variables": currency.convertir_con_bd(conn, p["gastos_variables"], p["moneda"], moneda_base),
                          "unidades_vendidas": u, "stock": p["stock_actual"], "moneda": p["moneda"], "mix": max(u, 1)})
        ranking = f.rentabilidad_marginal(filas)
        pe = f.punto_equilibrio_agregado(db.total_costos_fijos(conn), filas)
        hoy = datetime.now(timezone.utc)
        objetivo = db.obtener_objetivo(conn, hoy.year, hoy.month)
        contribucion_mes = sum(r.contribucion_total for r in ranking)
        valor_stock = sum(fi["costo"] * fi["stock"] for fi in filas)
        alertas = anomalies.detectar_en_bd(conn)
        ultimas = db.ultimas_recomendaciones_por_producto(conn)
        filas_rec = []
        for p in productos:
            u = ultimas.get(p["id"])
            filas_rec.append({"p": p, "rec": u, "edad": _edad(u["fecha"]) if u else None})
        return render_template("dashboard.html", productos=productos, ranking=ranking, pe=pe, objetivo=objetivo,
                               contribucion_mes=contribucion_mes, valor_stock=valor_stock, alertas=alertas[:5],
                               n_alertas=len(alertas), moneda_base=moneda_base, filas_rec=filas_rec,
                               sin_rec=sum(1 for fr in filas_rec if fr["rec"] is None), Estado=f.Estado)

    @app.route("/productos")
    def productos():
        conn = conexion()
        lista = db.listar_productos(conn)
        tasas = currency.tasas_actuales(conn)
        for p in lista:
            p["margen_pct"] = f.margen_bruto_pct(p["precio_venta"], p["costo"])
            p["precios"] = currency.en_todas_las_monedas(p["precio_venta"], p["moneda"], tasas)
        return render_template("productos.html", productos=lista, monedas=MONEDAS)

    @app.route("/productos/nuevo", methods=["POST"])
    def producto_nuevo():
        try:
            pid = db.crear_producto(
                conexion(), nombre=request.form["nombre"].strip(), categoria=request.form.get("categoria") or None,
                sku=request.form.get("sku") or None, costo=leer_float("costo", obligatorio=True),
                precio_venta=leer_float("precio_venta", obligatorio=True), moneda=request.form.get("moneda", "COP"),
                stock_actual=int(leer_float("stock_actual", 0)), gastos_variables=leer_float("gastos_variables", 0.0),
                margen_minimo_pct=leer_float("margen_minimo_pct"), precio_competencia=leer_float("precio_competencia"),
            )
            flash(f"Producto creado (#{pid}).", "ok")
            return redirect(url_for("producto_detalle", pid=pid))
        except (ValueError, KeyError, sqlite3.IntegrityError) as e:
            flash(f"No se pudo crear el producto: {e}", "error")
            return redirect(url_for("productos"))

    @app.route("/productos/<int:pid>")
    def producto_detalle(pid):
        conn = conexion()
        p = db.obtener_producto(conn, pid) or abort(404)
        ctx = contexto_producto(p)
        a = analisis(p, ctx)
        movs = db.listar_movimientos(conn, producto_id=pid)[-25:][::-1]
        recs = db.listar_recomendaciones(conn, producto_id=pid, limite=5)
        ultima = db.ultima_recomendacion(conn, pid)
        return render_template("producto.html", p=p, a=a, ctx=ctx, movs=movs, recs=recs, monedas=MONEDAS,
                               velocidades=VELOCIDADES, Estado=f.Estado, ultima=ultima,
                               edad_ultima=_edad(ultima["fecha"]) if ultima else None)

    @app.route("/productos/<int:pid>/editar", methods=["POST"])
    def producto_editar(pid):
        try:
            db.actualizar_producto(
                conexion(), pid, nombre=request.form["nombre"].strip(), categoria=request.form.get("categoria") or None,
                sku=request.form.get("sku") or None, costo=leer_float("costo", obligatorio=True),
                precio_venta=leer_float("precio_venta", obligatorio=True), moneda=request.form.get("moneda", "COP"),
                gastos_variables=leer_float("gastos_variables", 0.0), margen_minimo_pct=leer_float("margen_minimo_pct"),
                precio_competencia=leer_float("precio_competencia"),
            )
            flash("Producto actualizado.", "ok")
        except (ValueError, KeyError, sqlite3.IntegrityError) as e:
            flash(f"No se pudo actualizar: {e}", "error")
        return redirect(url_for("producto_detalle", pid=pid))

    @app.route("/productos/<int:pid>/movimiento", methods=["POST"])
    def producto_movimiento(pid):
        try:
            db.registrar_movimiento(conexion(), pid, tipo=request.form["tipo"], cantidad=int(leer_float("cantidad", obligatorio=True)),
                                    usuario=request.form.get("usuario") or None, motivo=request.form.get("motivo") or None)
            flash("Movimiento registrado.", "ok")
        except (ValueError, KeyError) as e:
            flash(f"Movimiento rechazado: {e}", "error")
        return redirect(url_for("producto_detalle", pid=pid))

    @app.route("/productos/<int:pid>/venta", methods=["POST"])
    def producto_venta(pid):
        try:
            db.registrar_venta(conexion(), pid, cantidad=int(leer_float("cantidad", obligatorio=True)),
                               precio_unitario=leer_float("precio_unitario"), usuario=request.form.get("usuario") or None)
            flash("Venta registrada.", "ok")
        except (ValueError, KeyError) as e:
            flash(f"Venta rechazada: {e}", "error")
        return redirect(url_for("producto_detalle", pid=pid))

    @app.route("/productos/<int:pid>/recomendar", methods=["POST"])
    def producto_recomendar(pid):
        conn = conexion()
        p = db.obtener_producto(conn, pid) or abort(404)
        restr = [r.strip() for r in (request.form.get("restricciones") or "").replace("\n", ";").split(";") if r.strip()]
        vel = request.form.get("velocidad_venta") or None
        ctx = contexto_producto(p, velocidad=vel if vel in VELOCIDADES else None, restricciones=restr)
        rec = copilot.recomendar_para_producto(conn, p, ctx)
        a = analisis(p, ctx)
        tasas = currency.tasas_actuales(conn)
        sugerido_monedas = currency.en_todas_las_monedas(rec.precio_sugerido, p["moneda"], tasas)
        return render_template("recomendacion.html", p=p, rec=rec, ctx=ctx, a=a, sugerido_monedas=sugerido_monedas)

    @app.route("/alertas")
    def alertas():
        return render_template("alertas.html", alertas=anomalies.detectar_en_bd(conexion()))

    @app.route("/reglas")
    def reglas_view():
        return render_template("reglas.html", reglas=rules.catalogo_reglas(), unidades=rules.UNIDADES_POR_VELOCIDAD)

    @app.route("/tasas", methods=["GET", "POST"])
    def tasas():
        conn = conexion()
        if request.method == "POST":
            try:
                for m in ("COP", "CNY"):
                    v = leer_float(m)
                    if v is not None:
                        currency.fijar_tasa_manual(conn, m, v)
                flash("Tasas guardadas.", "ok")
            except ValueError as e:
                flash(f"Tasa inválida: {e}", "error")
            return redirect(url_for("tasas"))
        return render_template("tasas.html", tasas=db.obtener_tasas(conn), modo_b=currency.modo_online_activo(conn),
                               api_url=currency.API_URL_DEFAULT)

    @app.route("/tasas/modo", methods=["POST"])
    def tasas_modo():
        activo = request.form.get("modo_tasas_online") == "1"
        currency.activar_modo_online(conexion(), activo)
        flash(f"Modo B (tasas en línea) {'activado' if activo else 'desactivado'}.", "ok")
        return redirect(url_for("tasas"))

    @app.route("/tasas/actualizar", methods=["POST"])
    def tasas_actualizar():
        r = currency.actualizar_tasas_online(conexion(), timeout=5)
        if r.exito:
            flash("Tasas actualizadas desde internet.", "ok")
        elif r.motivo == "modo_b_desactivado":
            flash("El Modo B está desactivado; se usan las tasas manuales.", "error")
        else:
            flash(f"No se pudo actualizar ({r.motivo}). Se mantiene la última tasa guardada.", "error")
        return redirect(url_for("tasas"))

    @app.route("/configuracion", methods=["GET", "POST"])
    def configuracion():
        conn = conexion()
        hoy = datetime.now(timezone.utc)
        if request.method == "POST":
            try:
                for clave in ("margen_minimo_global_pct", "copiloto_llm_activo", "modelo_local", "ollama_url",
                              "hora_apertura", "hora_cierre", "zona_horaria_offset", "moneda_base", "timeout_llm_seg"):
                    if clave in request.form:
                        db.fijar_config(conn, clave, request.form[clave].strip())
                obj = leer_float("objetivo_mes")
                if obj is not None:
                    db.fijar_objetivo(conn, hoy.year, hoy.month, obj, moneda=cfg("moneda_base", "COP"))
                if request.form.get("costo_fijo_nombre") and leer_float("costo_fijo_monto") is not None:
                    db.agregar_costo_fijo(conn, request.form["costo_fijo_nombre"].strip(), leer_float("costo_fijo_monto"), moneda=cfg("moneda_base", "COP"))
                flash("Configuración guardada.", "ok")
            except ValueError as e:
                flash(f"Valor inválido: {e}", "error")
            return redirect(url_for("configuracion"))
        llm = copilot.cliente_desde_config(conn)
        return render_template("configuracion.html", cfg=lambda k, d=None: cfg(k, d), objetivo=db.obtener_objetivo(conn, hoy.year, hoy.month),
                               costos_fijos=db.listar_costos_fijos(conn), total_fijos=db.total_costos_fijos(conn),
                               llm_disponible=llm.disponible(timeout=1.0), monedas=MONEDAS)

    @app.route("/configuracion/costo-fijo/<int:cid>/eliminar", methods=["POST"])
    def costo_fijo_eliminar(cid):
        db.eliminar_costo_fijo(conexion(), cid)
        return redirect(url_for("configuracion"))

    @app.route("/api/productos/<int:pid>/analisis")
    def api_analisis(pid):
        p = db.obtener_producto(conexion(), pid) or abort(404)
        a = analisis(p)
        a["punto_equilibrio"] = {"estado": a["punto_equilibrio"].estado.value, "unidades": a["punto_equilibrio"].unidades, "ingreso": a["punto_equilibrio"].ingreso}
        uo = a["unidades_objetivo"]
        a["unidades_objetivo"] = None if uo is None else {"estado": uo.estado.value, "unidades": uo.unidades}
        a["markup_estado"] = a["markup_estado"].value
        a["precio_minimo_estado"] = a["precio_minimo_estado"].value
        return jsonify(a)

    return app


def main():
    app = crear_app()
    print("InvenPrice en http://127.0.0.1:5000  (Ctrl+C para salir)")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
