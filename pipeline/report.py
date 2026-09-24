"""Exporta los marts a CSV y escribe RESPUESTAS.md y SUPUESTOS.md."""
from pathlib import Path

import duckdb
import pandas as pd

from pipeline.assumptions import Assumption, read_params

EXPORTED_TABLES = [
    "mart.q1_rotation_by_sku",
    "mart.q1_top10_variants",
    "mart.q1_variant_summary",
    "mart.q2_stockout_runs",
    "mart.q2_stores_with_stockouts",
    "mart.q3_mom_growth",
    "mart.q3_monthly_variants",
    "mart.q4_margin_by_product_store",
    "mart.q4_margin_variants",
    "mart.q4_negative_margin_products",
    "mart.q4_negative_margin_by_store",
    "audit.assumption_impact",
    "audit.quality_results",
]


def markdown_table(frame: pd.DataFrame) -> str:
    def cell(value) -> str:
        if value is None or (not isinstance(value, (list, tuple)) and pd.isna(value)):
            return ""
        if isinstance(value, float):
            return f"{value:,.4f}".rstrip("0").rstrip(".")
        return str(value).replace("|", "\\|")

    header = "| " + " | ".join(frame.columns) + " |"
    divider = "|" + "|".join("---" for _ in frame.columns) + "|"
    rows = ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *rows])


def export_csvs(con: duckdb.DuckDBPyConnection, output_dir: Path) -> None:
    csv_dir = output_dir / "marts"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for table in EXPORTED_TABLES:
        frame = con.execute(f"SELECT * FROM {table}").fetchdf()
        # utf-8-sig para que Excel en Windows abra los acentos bien.
        frame.to_csv(csv_dir / f"{table.split('.')[1]}.csv", index=False, encoding="utf-8-sig")


def write_assumptions(assumptions: list[Assumption], output_dir: Path) -> None:
    table = pd.DataFrame(
        [(a.code, a.problem, a.assumption, a.evidence, a.impact) for a in assumptions],
        columns=["#", "Problema", "Supuesto", "Evidencia", "Impacto"],
    )
    questions = [f"- **{a.code}.** {a.client_question}" for a in assumptions if a.client_question]
    text = (
        "# Supuestos: problema → supuesto → evidencia → impacto\n\n"
        "Generado en cada corrida desde `audit.assumption_impact`.\n\n"
        + markdown_table(table)
        + "\n\n## Preguntas para el cliente\n\n"
        + "\n".join(questions)
        + "\n"
    )
    (output_dir / "SUPUESTOS.md").write_text(text, encoding="utf-8")


def write_answers(con: duckdb.DuckDBPyConnection, output_dir: Path) -> None:
    def query(sql: str) -> pd.DataFrame:
        return con.execute(sql).fetchdf()

    params = read_params(con)
    checks = query("SELECT count(*) AS total, count(*) FILTER (WHERE passed) AS passed FROM audit.quality_results "
                   "WHERE run_id = (SELECT max(run_id) FROM audit.quality_results)").iloc[0]

    top10 = query(
        """
        SELECT v.rank AS "#", r.sku_pos AS sku, r.product_name AS producto, round(r.rotation, 4) AS rotacion,
               r.units_net_tracked AS unidades_netas, round(r.avg_stock, 1) AS inventario_prom_suma_tiendas,
               round(r.days_of_inventory, 0) AS dias_inventario, r.stores_tracked AS tiendas,
               r.pos_erp_link_source AS mapeo
        FROM mart.q1_top10_variants AS v JOIN mart.q1_rotation_by_sku AS r USING (sku_num)
        WHERE v.variant = 'principal' ORDER BY v.rank
        """
    )
    boundary = query(
        "SELECT sku_pos, rotation FROM mart.q1_rotation_by_sku ORDER BY rotation DESC, sku_num LIMIT 2 OFFSET 9"
    )
    q1_variants = query(
        "SELECT variant AS variante, CAST(ranking AS VARCHAR) AS ranking_sku_num, "
        "skus_shared_with_principal AS comparte_con_principal, same_ranking_as_principal AS mismo_orden "
        "FROM mart.q1_variant_summary ORDER BY variant <> 'principal', variant"
    )
    q2_stores = query(
        "SELECT store_id AS tienda, city AS ciudad, status AS estado, skus_confirmed AS skus_confirmados, "
        "skus_if_na_is_zero AS skus_si_na_es_cero, max_days_confirmed AS dias_max_confirmados, "
        "max_days_if_na_is_zero AS dias_max_si_na_es_cero FROM mart.q2_stores_with_stockouts ORDER BY status, store_id"
    )
    q2_runs = query(
        "SELECT variant AS variante, store_id AS tienda, sku_erp, product_name AS producto, CAST(run_start AS VARCHAR) AS inicio, "
        "CAST(run_end AS VARCHAR) AS fin, days_in_quarter AS dias_en_trimestre, na_days AS dias_na, "
        "censored_at_data_end AS corta_en_fin_de_datos FROM mart.q2_stockout_runs ORDER BY variant, store_id"
    )
    q3 = query(
        """
        SELECT strftime(month, '%Y-%m') AS mes,
               max(sales_mxn) FILTER (WHERE channel = 'fisico') AS fisico_mxn,
               max(mom_growth_pct) FILTER (WHERE channel = 'fisico') AS fisico_mom_pct,
               max(sales_mxn) FILTER (WHERE channel = 'ecommerce') AS ecommerce_mxn,
               max(mom_growth_pct) FILTER (WHERE channel = 'ecommerce') AS ecommerce_mom_pct,
               max(growth_note) FILTER (WHERE channel = 'ecommerce') AS nota_ecommerce,
               max(mom_growth_pct) FILTER (WHERE channel = 'total') AS total_mom_pct
        FROM mart.q3_mom_growth GROUP BY month ORDER BY month
        """
    )
    q3_variants = query(
        """
        SELECT strftime(month, '%Y-%m') AS mes,
               max(mom_growth_pct) FILTER (WHERE variant = 'principal' AND channel = 'fisico') AS fisico_principal,
               max(mom_growth_pct) FILTER (WHERE variant = 'fisico_todos_los_tipos') AS fisico_todos_los_tipos,
               max(mom_growth_pct) FILTER (WHERE variant = 'fisico_solo_ingresos') AS fisico_solo_ingresos,
               max(mom_growth_pct) FILTER (WHERE variant = 'principal' AND channel = 'ecommerce') AS ecommerce_principal,
               max(mom_growth_pct) FILTER (WHERE variant = 'ecommerce_timestamp_utc') AS ecommerce_si_utc
        FROM mart.q3_monthly_variants GROUP BY month ORDER BY month
        """
    )
    q4_products = query(
        "SELECT sku_pos AS sku, product_name AS producto, product_margin_mxn AS margen_mxn, "
        "product_margin_pct AS margen_pct, stores_negative AS tiendas_negativas, stores_selling AS tiendas_que_venden "
        "FROM mart.q4_negative_margin_products ORDER BY product_margin_mxn"
    )
    q4_stores = query(
        "SELECT sku_pos AS sku, string_agg(store_id, ', ' ORDER BY store_id) AS tiendas, "
        "round(min(margin_pct), 1) AS margen_pct_min, round(max(margin_pct), 1) AS margen_pct_max, "
        "max(pos_erp_link_source) AS mapeo FROM mart.q4_negative_margin_by_store GROUP BY sku_pos ORDER BY sku_pos"
    )
    q4_variants = query(
        "SELECT variant AS variante, string_agg(sku_pos, ', ' ORDER BY sku_pos) AS productos_negativos, "
        "sum(stores_negative) AS combinaciones_negativas FROM mart.q4_margin_variants GROUP BY variant ORDER BY variant"
    )
    lines_below_cost_elsewhere = con.execute(
        "SELECT coalesce(sum(sale_lines_below_cost), 0) FROM mart.q4_margin_by_product_store "
        "WHERE sku_num NOT IN (SELECT sku_num FROM mart.q4_negative_margin_products)"
    ).fetchone()[0]

    boundary_note = ""
    if len(boundary) == 2:
        tenth, eleventh = boundary.itertuples(index=False)
        boundary_note = (
            f"El #10 ({tenth.sku_pos}, {tenth.rotation:.5f}) y el #11 ({eleventh.sku_pos}, {eleventh.rotation:.5f}) "
            f"están separados por {tenth.rotation - eleventh.rotation:.5f}: el último lugar del top es frágil.\n\n"
        )

    text = f"""# Respuestas — CaféNorte

Ancla de la corrida: **{params.as_of_date}** (última fecha con datos). Checks de calidad: {checks.passed}/{checks.total} pasaron.

## P1. Top 10 SKUs por rotación de inventario ({params.rotation_start} a {params.as_of_date})

Rotación = unidades netas vendidas (I − E) / inventario promedio diario, ambos sobre los pares tienda×SKU que el ERP rastrea. Solo tiendas físicas.

{markdown_table(top10)}

{boundary_note}Variantes (si un supuesto cambia la respuesta, aquí se ve):

{markdown_table(q1_variants)}

## P2. Tiendas con quiebres de más de 3 días ({params.quarter_start} a {params.quarter_end})

Quiebre = snapshot con stock 0. "Más de 3 días" = al menos 4 días consecutivos en 0 dentro del trimestre. Principal: un N/A rompe la racha.

{markdown_table(q2_stores)}

Rachas:

{markdown_table(q2_runs)}

## P3. Crecimiento mes a mes por canal ({params.growth_first_month:%Y-%m} a {params.growth_last_month:%Y-%m}, MXN)

Físico = I − E. E-commerce convertido a MXN con el tipo de cambio del día. Un mes sin cobertura de la fuente es NULL, no 0.

{markdown_table(q3)}

Sensibilidad del crecimiento (%):

{markdown_table(q3_variants)}

## P4. Productos con margen negativo y tiendas donde ocurre (todo el histórico)

Margen = venta neta − unidades × costo vigente a la fecha de cada venta. Shopify cuenta como la tienda ECOMMERCE.

{markdown_table(q4_products)}

{markdown_table(q4_stores)}

Líneas de venta por debajo del costo en productos que no están en la lista: {lines_below_cost_elsewhere}.

Variantes:

{markdown_table(q4_variants)}
"""
    (output_dir / "RESPUESTAS.md").write_text(text, encoding="utf-8")
