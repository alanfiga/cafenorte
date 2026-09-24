"""Checks de calidad que corren en cada ejecución.

Cada check devuelve el número de violaciones. Un check con severidad "error" y
violaciones > 0 detiene la corrida: significa que un supuesto documentado dejó de
cumplirse o que un total no cuadra contra el archivo fuente.
"""
from dataclasses import dataclass
from typing import Callable

import duckdb

# Nombres de columna que delatan PII de Shopify si aparecen fuera de raw.
PII_COLUMN_PATTERNS = ("customer", "email", "rfc", "address", "shipping", "cliente", "direccion")
# RFC mexicano (persona física o moral) y correo electrónico.
PII_VALUE_REGEX = r"([A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3})|([^@\s]+@[^@\s]+\.[a-z]{2,})"
AMOUNT_TOLERANCE = "0.005"


class QualityCheckError(RuntimeError):
    def __init__(self, layer: str, failures: list[tuple[str, int, str]]):
        self.layer = layer
        self.failures = failures
        detail = "\n".join(f"  - {name}: {violations} violaciones. {description}" for name, violations, description in failures)
        super().__init__(f"La corrida se detuvo en la capa '{layer}':\n{detail}")


@dataclass(frozen=True)
class Check:
    name: str
    layer: str
    description: str
    sql: str | None = None
    func: Callable[[duckdb.DuckDBPyConnection], int] | None = None
    severity: str = "error"

    def violations(self, con: duckdb.DuckDBPyConnection) -> int:
        if self.func is not None:
            return self.func(con)
        return int(con.execute(self.sql).fetchone()[0] or 0)


def _source_total(source: str, metric: str, dimension: str = "") -> str:
    return (
        f"(SELECT value FROM raw.source_totals "
        f"WHERE source = '{source}' AND metric = '{metric}' AND dimension = '{dimension}')"
    )


def _raw_count_matches_file(name: str, table: str, source: str, metric: str) -> Check:
    return Check(
        name=name,
        layer="staging",
        description=f"Filas en {table} = filas leídas del archivo fuente.",
        sql=f"SELECT abs((SELECT count(*) FROM {table}) - {_source_total(source, metric)})",
    )


def _pii_outside_raw_columns(con: duckdb.DuckDBPyConnection) -> int:
    columns = con.execute(
        "SELECT lower(column_name) FROM information_schema.columns WHERE table_schema NOT IN ('raw', 'audit')"
    ).fetchall()
    return sum(1 for (column,) in columns if any(pattern in column for pattern in PII_COLUMN_PATTERNS))


def _pii_outside_raw_values(con: duckdb.DuckDBPyConnection) -> int:
    text_columns = con.execute(
        "SELECT table_schema, table_name, column_name FROM information_schema.columns "
        "WHERE table_schema NOT IN ('raw', 'audit', 'information_schema', 'pg_catalog') AND data_type = 'VARCHAR'"
    ).fetchall()
    hits = 0
    for schema, table, column in text_columns:
        hits += con.execute(
            f'SELECT count(*) FROM "{schema}"."{table}" WHERE regexp_matches("{column}", ?)',
            [PII_VALUE_REGEX],
        ).fetchone()[0]
    return hits


CHECKS: list[Check] = [
    # --- Staging: la fuente se leyó completa y respeta los supuestos de formato ---
    _raw_count_matches_file("raw_pos_filas_vs_archivo", "raw.pos_sales", "pos", "rows"),
    _raw_count_matches_file("raw_ecommerce_filas_vs_archivo", "raw.ecommerce_orders", "ecommerce", "rows"),
    _raw_count_matches_file("raw_fx_filas_vs_archivo", "raw.fx_rates", "fx", "rows"),
    _raw_count_matches_file("raw_snapshots_filas_vs_archivo", "raw.erp_snapshots", "erp", "snapshots"),
    _raw_count_matches_file("raw_catalogo_filas_vs_archivo", "raw.erp_products", "erp", "products"),
    _raw_count_matches_file("raw_mapeo_filas_vs_archivo", "raw.erp_sku_mappings", "erp", "sku_mappings"),
    _raw_count_matches_file("raw_costos_filas_vs_archivo", "raw.erp_cost_history", "erp", "cost_history"),
    _raw_count_matches_file("raw_tiendas_filas_vs_archivo", "raw.erp_stores", "erp", "stores"),
    Check(
        "pos_campos_parseables", "staging",
        "Fecha, cantidad, monto, SKU (formato CN-NNNNN) y tienda parsean en todas las filas POS.",
        "SELECT count(*) FROM stg.pos_sales WHERE sale_ts IS NULL OR qty IS NULL OR amount IS NULL "
        "OR sku_num IS NULL OR store_id IS NULL",
    ),
    Check("pos_venta_id_unico", "staging", "venta_id no se repite.",
          "SELECT count(*) - count(DISTINCT sale_id) FROM stg.pos_sales"),
    Check("pos_moneda_mxn", "staging", "El POS solo trae MXN; no se aplica tipo de cambio a tiendas.",
          "SELECT count(*) FROM stg.pos_sales WHERE currency IS DISTINCT FROM 'MXN'"),
    Check(
        "pos_tipo_comprobante_conocido", "staging",
        "Todo tipo_comprobante está en el catálogo SAT soportado (I, E, P, N, T). Un código nuevo no se suma.",
        "SELECT count(*) FROM stg.pos_sales WHERE doc_type NOT IN (SELECT doc_type FROM stg.ref_doc_types) "
        "OR doc_type IS NULL",
    ),
    Check(
        "pos_cantidad_y_monto_positivos", "staging",
        "Cantidad y monto > 0; el signo lo da el tipo de comprobante (E viene en positivo).",
        "SELECT count(*) FROM stg.pos_sales WHERE qty <= 0 OR amount <= 0",
    ),
    Check(
        "ecommerce_campos_parseables", "staging",
        "Fecha, handle con número de 3 dígitos, cantidad y monto parsean en todas las órdenes.",
        "SELECT count(*) FROM stg.ecommerce_orders WHERE order_ts IS NULL OR handle_num IS NULL "
        "OR qty IS NULL OR amount IS NULL",
    ),
    Check("ecommerce_order_id_unico", "staging", "order_id no se repite.",
          "SELECT count(*) - count(DISTINCT order_id) FROM stg.ecommerce_orders"),
    Check("ecommerce_moneda_conocida", "staging", "Monedas Shopify soportadas: MXN, USD, EUR.",
          "SELECT count(*) FROM stg.ecommerce_orders WHERE currency NOT IN ('MXN', 'USD', 'EUR') OR currency IS NULL"),
    Check("ecommerce_cantidad_y_monto_positivos", "staging", "Cantidad y monto > 0 en Shopify.",
          "SELECT count(*) FROM stg.ecommerce_orders WHERE qty <= 0 OR amount <= 0"),
    Check("fx_parseable_y_positivo", "staging", "Tipo de cambio con fecha válida y tasa > 0.",
          "SELECT count(*) FROM stg.fx_rates WHERE rate_date IS NULL OR rate_to_mxn IS NULL OR rate_to_mxn <= 0"),
    Check("fx_llave_unica", "staging", "Un tipo de cambio por (fecha, moneda).",
          "SELECT count(*) - count(DISTINCT (rate_date, currency)) FROM stg.fx_rates"),
    Check(
        "fx_cobertura", "staging",
        "Toda orden en USD/EUR tiene tipo de cambio del mismo día.",
        "SELECT count(*) FROM stg.ecommerce_orders AS o LEFT JOIN stg.fx_rates AS fx "
        "ON fx.rate_date = o.order_date AND fx.currency = o.currency "
        "WHERE o.currency <> 'MXN' AND fx.rate_to_mxn IS NULL",
    ),
    Check(
        "stock_valores_validos", "staging",
        "El stock es entero >= 0 o el texto 'N/A'; cualquier otro valor detiene la corrida.",
        "SELECT count(*) FROM stg.inventory_snapshots WHERE stock_status = 'valor_no_reconocido' "
        "OR (stock_status = 'reportado' AND (stock_qty IS NULL OR stock_qty < 0))",
    ),
    Check("snapshots_parseables", "staging", "Fecha de snapshot válida.",
          "SELECT count(*) FROM stg.inventory_snapshots WHERE snapshot_date IS NULL"),
    Check("snapshots_llave_unica", "staging", "Un snapshot por (fecha, tienda, SKU ERP).",
          "SELECT count(*) - count(DISTINCT (snapshot_date, store_id, sku_erp)) FROM stg.inventory_snapshots"),
    Check(
        "snapshots_grilla_completa", "staging",
        "Cada par tienda×SKU tiene un snapshot por día entre la primera y la última fecha del inventario "
        "(las rachas de quiebre asumen días consecutivos).",
        "WITH b AS (SELECT min(snapshot_date) AS lo, max(snapshot_date) AS hi FROM stg.inventory_snapshots) "
        "SELECT count(*) FROM (SELECT store_id, sku_erp, count(*) AS n FROM stg.inventory_snapshots "
        "GROUP BY ALL) AS p, b WHERE p.n <> b.hi - b.lo + 1",
    ),
    Check("catalogo_sku_formato", "staging", "Todo sku_erp tiene formato ERP-PROV-MX-NNN-X.",
          "SELECT count(*) FROM stg.products WHERE sku_num IS NULL"),
    Check("catalogo_numero_unico", "staging", "Un producto por número de SKU (la llave de conciliación).",
          "SELECT count(*) - count(DISTINCT sku_num) FROM stg.products"),
    Check("costos_parseables_y_positivos", "staging", "Costo con fecha válida y valor > 0.",
          "SELECT count(*) FROM stg.cost_history WHERE valid_from IS NULL OR unit_cost_mxn IS NULL OR unit_cost_mxn <= 0"),
    Check("costos_sin_fechas_repetidas", "staging", "Un costo por (SKU, fecha de vigencia).",
          "SELECT count(*) - count(DISTINCT (sku_erp, valid_from)) FROM stg.cost_history"),
    Check(
        "mapeo_explicito_mismo_numero", "staging",
        "Supuesto de mapeo: en toda fila explícita, número de SKU POS = número de SKU ERP.",
        "SELECT count(*) FROM stg.sku_mappings WHERE sku_erp IS NOT NULL AND sku_pos_num IS DISTINCT FROM sku_erp_num",
    ),
    Check(
        "mapeo_sku_pos_unico", "staging", "Un SKU POS aparece una sola vez en el mapeo.",
        "SELECT count(*) - count(DISTINCT sku_pos) FROM stg.sku_mappings",
    ),
    Check(
        "mapeo_handle_coincide_con_nombre", "staging",
        "Supuesto de mapeo: todo handle explícito = slug(nombre ERP)-NNN del producto con su número.",
        "SELECT count(*) FROM stg.sku_mappings AS m LEFT JOIN stg.products AS p ON p.sku_num = m.sku_pos_num "
        "WHERE m.handle IS NOT NULL AND (m.handle_num IS DISTINCT FROM m.sku_pos_num "
        "OR m.handle IS DISTINCT FROM p.name_slug || '-' || lpad(CAST(p.sku_num AS VARCHAR), 3, '0'))",
    ),
    Check(
        "ecommerce_handle_coincide_con_nombre", "staging",
        "Todo handle vendido (explícito o no) = slug(nombre ERP)-NNN; confirma los handles inferidos.",
        "SELECT count(*) FROM stg.ecommerce_orders AS o LEFT JOIN stg.products AS p ON p.sku_num = o.handle_num "
        "WHERE o.handle IS DISTINCT FROM p.name_slug || '-' || lpad(CAST(p.sku_num AS VARCHAR), 3, '0')",
    ),
    Check(
        "pos_sku_en_catalogo", "staging", "Todo SKU del POS tiene producto en el catálogo ERP con su número.",
        "SELECT count(DISTINCT sku_num) FROM stg.pos_sales WHERE sku_num NOT IN (SELECT sku_num FROM stg.products)",
    ),
    Check(
        "pos_sku_texto_unico_por_numero", "staging", "Un número de SKU corresponde a un solo código POS.",
        "SELECT count(*) FROM (SELECT sku_num FROM stg.pos_sales GROUP BY sku_num HAVING count(DISTINCT sku_pos) > 1)",
    ),
    Check("snapshots_sku_en_catalogo", "staging", "Todo SKU de snapshots existe en el catálogo.",
          "SELECT count(DISTINCT sku_erp) FROM stg.inventory_snapshots WHERE sku_erp NOT IN (SELECT sku_erp FROM stg.products)"),
    Check(
        "tiendas_en_erp", "staging", "Toda tienda del POS y de los snapshots existe en tiendas_info.",
        "SELECT count(*) FROM (SELECT store_id FROM stg.pos_sales UNION SELECT store_id FROM stg.inventory_snapshots) "
        "WHERE store_id NOT IN (SELECT store_id FROM stg.stores)",
    ),
    # --- Core: conciliación de totales contra el archivo y cobertura de supuestos ---
    Check(
        "fct_pos_filas_vs_archivo", "core", "Cada fila del POS está en fct_sales (ninguna se borra).",
        f"SELECT abs((SELECT count(*) FROM core.fct_sales WHERE channel = 'fisico') - {_source_total('pos', 'rows')})",
    ),
    Check(
        "fct_pos_montos_vs_archivo", "core",
        "Monto, cantidad y filas por tipo de comprobante en fct_sales = totales calculados del CSV.",
        "WITH fct AS (SELECT doc_type, sum(original_amount) AS amount, sum(qty) AS qty, count(*) AS n "
        "FROM core.fct_sales WHERE channel = 'fisico' GROUP BY doc_type), "
        "src AS (SELECT dimension AS doc_type, "
        "max(value) FILTER (WHERE metric = 'amount_by_doc_type') AS amount, "
        "max(value) FILTER (WHERE metric = 'qty_by_doc_type') AS qty, "
        "max(value) FILTER (WHERE metric = 'rows_by_doc_type') AS n "
        "FROM raw.source_totals WHERE source = 'pos' AND dimension <> '' GROUP BY dimension) "
        "SELECT count(*) FROM fct FULL JOIN src USING (doc_type) "
        f"WHERE abs(coalesce(fct.amount, 0) - coalesce(src.amount, 0)) > {AMOUNT_TOLERANCE} "
        "OR coalesce(fct.qty, 0) <> coalesce(src.qty, 0) OR coalesce(fct.n, 0) <> coalesce(src.n, 0)",
    ),
    Check(
        "fct_venta_fisica_neta_vs_archivo", "core",
        "Venta física neta del modelo = monto I − monto E calculados del CSV.",
        "SELECT CASE WHEN abs((SELECT sum(revenue_mxn_net) FROM core.fct_sales WHERE channel = 'fisico') - ("
        f"coalesce({_source_total('pos', 'amount_by_doc_type', 'I')}, 0) - "
        f"coalesce({_source_total('pos', 'amount_by_doc_type', 'E')}, 0))) > {AMOUNT_TOLERANCE} THEN 1 ELSE 0 END",
    ),
    Check(
        "fct_ecommerce_montos_vs_archivo", "core",
        "Filas, monto original y cantidad por moneda en fct_sales = totales calculados del parquet.",
        "WITH fct AS (SELECT original_currency AS currency, sum(original_amount) AS amount, sum(qty) AS qty, "
        "count(*) AS n FROM core.fct_sales WHERE channel = 'ecommerce' GROUP BY ALL), "
        "src AS (SELECT dimension AS currency, "
        "max(value) FILTER (WHERE metric = 'amount_by_currency') AS amount, "
        "max(value) FILTER (WHERE metric = 'qty_by_currency') AS qty, "
        "max(value) FILTER (WHERE metric = 'rows_by_currency') AS n "
        "FROM raw.source_totals WHERE source = 'ecommerce' AND dimension <> '' GROUP BY dimension) "
        "SELECT count(*) FROM fct FULL JOIN src USING (currency) "
        f"WHERE abs(coalesce(fct.amount, 0) - coalesce(src.amount, 0)) > {AMOUNT_TOLERANCE} "
        "OR coalesce(fct.qty, 0) <> coalesce(src.qty, 0) OR coalesce(fct.n, 0) <> coalesce(src.n, 0)",
    ),
    Check(
        "fct_ecommerce_mxn_recalculado_desde_raw", "core",
        "Venta e-commerce en MXN = Σ monto × tipo de cambio del día, recalculado directo de raw.",
        "WITH recalculated AS (SELECT sum(CAST(o.amount AS DECIMAL(14, 2)) * CASE WHEN o.currency = 'MXN' THEN 1 "
        "ELSE CAST(fx.rate_to_mxn AS DECIMAL(12, 4)) END) AS mxn FROM raw.ecommerce_orders AS o "
        "LEFT JOIN raw.fx_rates AS fx ON CAST(fx.fecha AS DATE) = CAST(CAST(o.fecha AS TIMESTAMP) AS DATE) "
        "AND fx.currency = o.currency) "
        "SELECT CASE WHEN abs((SELECT sum(revenue_mxn_net) FROM core.fct_sales WHERE channel = 'ecommerce') "
        f"- (SELECT mxn FROM recalculated)) > {AMOUNT_TOLERANCE} THEN 1 ELSE 0 END",
    ),
    Check("fct_producto_vinculado", "core", "Toda línea de venta quedó ligada a un producto del catálogo.",
          "SELECT count(*) FROM core.fct_sales WHERE product_link_source IS NULL"),
    Check("fct_costo_vigente_asignado", "core", "Toda venta o devolución tiene costo vigente a su fecha.",
          "SELECT count(*) FROM core.fct_sales WHERE sales_sign <> 0 AND unit_cost_mxn IS NULL"),
    Check("fct_fx_asignado", "core", "Toda orden en moneda extranjera tiene tipo de cambio.",
          "SELECT count(*) FROM core.fct_sales WHERE fx_source = 'sin_fx'"),
    Check(
        "inventario_vs_archivo", "core",
        "Filas, conteo de N/A y suma de stock en fct_inventory_daily = lo leído del JSON.",
        "SELECT (abs((SELECT count(*) FROM core.fct_inventory_daily) - " + _source_total("erp", "snapshots") + ") > 0)::INT"
        " + (abs((SELECT count(*) FROM core.fct_inventory_daily WHERE stock_status = 'no_reportado') - "
        + _source_total("erp", "snapshots_na_string") + ") > 0)::INT"
        " + (abs((SELECT coalesce(sum(stock_qty), 0) FROM core.fct_inventory_daily) - "
        + _source_total("erp", "snapshots_stock_sum") + ") > 0)::INT",
    ),
    Check("dim_producto_numero_unico", "core", "dim_product tiene un renglón por número de SKU.",
          "SELECT count(*) - count(DISTINCT sku_num) FROM core.dim_product"),
    Check(
        "inventario_cubre_ventana_rotacion", "core",
        "Los snapshots cubren completa la ventana de 6 meses de la pregunta 1.",
        "SELECT count(*) FROM core.params, core.source_coverage AS c WHERE c.source = 'inventario' "
        "AND (c.first_date > rotation_start OR c.last_date < as_of_date)",
    ),
    Check(
        "inventario_cubre_trimestre", "core", "Los snapshots cubren completo el trimestre de la pregunta 2.",
        "SELECT count(*) FROM core.params, core.source_coverage AS c WHERE c.source = 'inventario' "
        "AND (c.first_date > quarter_start OR c.last_date < quarter_end)",
    ),
    Check(
        "fuentes_terminan_en_la_misma_fecha", "core",
        "Aviso: una fuente que termina antes del ancla deja meses sin cobertura (se reportan NULL, no 0).",
        "SELECT count(*) FROM core.source_coverage, core.params WHERE last_date < as_of_date",
        severity="warn",
    ),
    # --- Marts: las respuestas cuadran con el hecho y la PII no salió de raw ---
    Check(
        "q1_unidades_cuadran_con_fct", "marts",
        "Unidades rastreadas + no rastreadas de la P1 = unidades netas físicas de la ventana en fct_sales.",
        "SELECT CASE WHEN (SELECT sum(units_net_tracked + units_net_untracked_pairs) FROM mart.q1_rotation_by_sku) "
        "<> (SELECT coalesce(sum(qty_net), 0) FROM core.fct_sales, core.params WHERE channel = 'fisico' "
        "AND sale_date BETWEEN rotation_start AND as_of_date "
        "AND sku_num IN (SELECT sku_num FROM mart.q1_rotation_by_sku)) THEN 1 ELSE 0 END",
    ),
    Check(
        "q3_meses_cuadran_con_fct", "marts",
        "Venta principal de la P3 por canal (meses con cobertura) = suma de fct_sales en esos meses.",
        "WITH m AS (SELECT channel, sum(sales_mxn) AS s FROM mart.q3_monthly_variants "
        "WHERE variant = 'principal' GROUP BY channel), "
        "f AS (SELECT channel, round(sum(revenue_mxn_net), 2) AS s FROM core.fct_sales, core.params "
        "WHERE sale_date >= growth_first_month AND sale_date < growth_last_month + INTERVAL 1 MONTH GROUP BY channel) "
        "SELECT count(*) FROM m JOIN f USING (channel) WHERE abs(m.s - f.s) > 0.06",
    ),
    Check(
        "q4_margen_cuadra_con_fct", "marts",
        "Σ margen por producto×tienda de la P4 = Σ margen de fct_sales.",
        "SELECT CASE WHEN abs((SELECT sum(margin_mxn) FROM mart.q4_margin_by_product_store) - "
        "(SELECT sum(margin_mxn_net) FROM core.fct_sales)) > 0.01 * (SELECT count(*) FROM mart.q4_margin_by_product_store) "
        "THEN 1 ELSE 0 END",
    ),
    Check("pii_columnas_fuera_de_raw", "marts",
          "Ninguna tabla fuera de raw tiene columnas de cliente, email, RFC o dirección.",
          func=_pii_outside_raw_columns),
    Check("pii_valores_fuera_de_raw", "marts",
          "Ningún texto fuera de raw parece email o RFC.",
          func=_pii_outside_raw_values),
]


def run_checks(con: duckdb.DuckDBPyConnection, layer: str, run_id: str) -> list[tuple[str, str, int]]:
    con.execute("CREATE SCHEMA IF NOT EXISTS audit")
    con.execute(
        "CREATE TABLE IF NOT EXISTS audit.quality_results ("
        "run_id VARCHAR, layer VARCHAR, check_name VARCHAR, severity VARCHAR, violations BIGINT, "
        "passed BOOLEAN, description VARCHAR, checked_at TIMESTAMP DEFAULT current_timestamp)"
    )
    results = []
    failures = []
    for check in (c for c in CHECKS if c.layer == layer):
        violations = check.violations(con)
        passed = violations == 0
        con.execute(
            "INSERT INTO audit.quality_results (run_id, layer, check_name, severity, violations, passed, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [run_id, layer, check.name, check.severity, violations, passed, check.description],
        )
        results.append((check.name, check.severity, violations))
        if not passed and check.severity == "error":
            failures.append((check.name, violations, check.description))
    if failures:
        raise QualityCheckError(layer, failures)
    return results
