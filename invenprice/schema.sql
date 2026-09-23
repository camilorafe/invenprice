-- InvenPrice — esquema SQLite (Fase 1)
-- Convenciones:
--   * Todo importe se guarda como REAL en la MONEDA DEL PRODUCTO (moneda base elegida por el usuario).
--   * Las fechas se guardan como texto ISO-8601 (UTC) para ordenar y comparar sin ambigüedad.
--   * Los movimientos y ventas guardan SNAPSHOTS (costo, precio, stock resultante) para que la
--     auditoría no cambie si el producto se edita después.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS productos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sku               TEXT UNIQUE,
    nombre            TEXT NOT NULL,
    categoria         TEXT,
    costo             REAL NOT NULL CHECK (costo >= 0),
    precio_venta      REAL NOT NULL CHECK (precio_venta >= 0),
    moneda            TEXT NOT NULL CHECK (moneda IN ('USD', 'COP', 'CNY')),
    stock_actual      INTEGER NOT NULL DEFAULT 0,
    -- gastos variables por unidad (comisiones, empaque, impuestos sobre venta); afectan el margen neto
    gastos_variables  REAL NOT NULL DEFAULT 0 CHECK (gastos_variables >= 0),
    -- restricción del dueño: nunca vender por debajo de este margen (NULL = usar el global)
    margen_minimo_pct REAL CHECK (margen_minimo_pct IS NULL OR margen_minimo_pct < 100),
    -- precio observado de la competencia (opcional, puede no existir)
    precio_competencia REAL CHECK (precio_competencia IS NULL OR precio_competencia >= 0),
    activo            INTEGER NOT NULL DEFAULT 1,
    creado_en         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    actualizado_en    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS ventas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id     INTEGER NOT NULL REFERENCES productos(id),
    cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario REAL NOT NULL CHECK (precio_unitario >= 0),
    costo_unitario  REAL NOT NULL CHECK (costo_unitario >= 0),   -- snapshot
    moneda          TEXT NOT NULL,
    fecha           TEXT NOT NULL,
    usuario         TEXT,
    nota            TEXT
);
CREATE INDEX IF NOT EXISTS idx_ventas_producto_fecha ON ventas(producto_id, fecha);

-- tipo:
--   entrada  -> compra/recepción (delta > 0)
--   salida   -> venta o despacho (delta < 0)
--   merma    -> pérdida, rotura, vencimiento, robo (delta < 0)
--   ajuste   -> corrección por conteo físico; guarda stock_esperado y stock_contado
CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id      INTEGER NOT NULL REFERENCES productos(id),
    tipo             TEXT NOT NULL CHECK (tipo IN ('entrada', 'salida', 'merma', 'ajuste')),
    delta            INTEGER NOT NULL,                  -- cambio con signo aplicado al stock
    stock_resultante INTEGER NOT NULL,                  -- snapshot del stock después del movimiento
    stock_esperado   INTEGER,                           -- solo ajustes: lo que decía el sistema
    stock_contado    INTEGER,                           -- solo ajustes: lo que se contó físicamente
    fecha            TEXT NOT NULL,
    usuario          TEXT,
    motivo           TEXT,
    venta_id         INTEGER REFERENCES ventas(id)
);
CREATE INDEX IF NOT EXISTS idx_mov_producto_fecha ON movimientos_inventario(producto_id, fecha);
CREATE INDEX IF NOT EXISTS idx_mov_usuario ON movimientos_inventario(usuario);

CREATE TABLE IF NOT EXISTS objetivos_ingreso (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    anio   INTEGER NOT NULL,
    mes    INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    monto  REAL NOT NULL,
    moneda TEXT NOT NULL CHECK (moneda IN ('USD', 'COP', 'CNY')),
    UNIQUE (anio, mes)
);

-- costos fijos mensuales del negocio (arriendo, nómina, servicios) para break-even y margen neto
CREATE TABLE IF NOT EXISTS costos_fijos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT NOT NULL,
    monto_mensual REAL NOT NULL CHECK (monto_mensual >= 0),
    moneda        TEXT NOT NULL DEFAULT 'COP' CHECK (moneda IN ('USD', 'COP', 'CNY'))
);

-- tasas expresadas como "unidades de la moneda por 1 USD" (USD siempre = 1.0)
CREATE TABLE IF NOT EXISTS tasas_cambio (
    moneda         TEXT PRIMARY KEY CHECK (moneda IN ('USD', 'COP', 'CNY')),
    tasa_por_usd   REAL NOT NULL CHECK (tasa_por_usd > 0),
    fuente         TEXT NOT NULL DEFAULT 'manual',      -- manual | api
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- bitácora de recomendaciones del copiloto (auditoría y explicabilidad)
CREATE TABLE IF NOT EXISTS recomendaciones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id       INTEGER NOT NULL REFERENCES productos(id),
    fecha             TEXT NOT NULL,
    fuente            TEXT NOT NULL,        -- motor_reglas | llm_local
    precio_sugerido   REAL NOT NULL,
    precio_original   REAL NOT NULL,        -- antes del guardrail
    ajustado_guardrail INTEGER NOT NULL DEFAULT 0,
    precio_minimo_viable REAL,
    margen_resultante_pct REAL,
    justificacion     TEXT,
    riesgo            TEXT,
    detalle_json      TEXT                  -- números de sustento (auditoría)
);
