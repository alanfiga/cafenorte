"""Resultados esperados del dataset sintético, calculados a mano (ver tests/synthetic.py).

Las fracciones se dejan escritas como aritmética para que se pueda seguir el cálculo:
182 días en la ventana; un par con 4 días en 0 y el resto en 10 promedia 1780/182.
"""
from datetime import date

import duckdb
import pytest

from pipeline.run import run_pipeline
from tests.synthetic import write_dataset


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    folder = tmp_path_factory.mktemp("synthetic")
    data_dir = write_dataset(folder / "data")
    db_path = run_pipeline(data_dir, folder / "output")
    with duckdb.connect(str(db_path), read_only=True) as con:
        yield con


def rows(con, sql):
    return con.execute(sql).fetchall()


def test_ventanas_se_anclan_en_la_ultima_fecha_de_datos(db):
    as_of, rotation_start, quarter_start, quarter_end, first_month, last_month, complete = rows(
        db, "SELECT * FROM core.params"
    )[0]
    assert as_of == date(2026, 3, 31)
    assert rotation_start == date(2025, 10, 1)
    assert (quarter_start, quarter_end) == (date(2026, 1, 1), date(2026, 3, 31))
    assert (first_month, last_month) == (date(2025, 4, 1), date(2026, 3, 1))
    assert complete


def test_mapeo_inferido_por_numero_queda_marcado(db):
    links = dict(rows(db, "SELECT sku_num, pos_erp_link_source || '/' || handle_link_source FROM core.dim_product"))
    assert links == {
        1: "explicito/sin_handle",
        2: "inferido/inferido",      # sin fila de mapeo; handle vendido online
        3: "inferido/explicito",     # sku_erp nulo en el mapeo
        4: "explicito/sin_handle",
    }


def test_tipos_de_comprobante_p_n_t_no_suman_pero_no_se_borran(db):
    treatment = dict(rows(db, "SELECT doc_type, sum(revenue_mxn_net) FROM core.fct_sales WHERE channel = 'fisico' GROUP BY 1"))
    assert treatment["P"] == treatment["N"] == treatment["T"] == 0
    assert treatment["E"] == -111          # V03 11 + V12 100
    assert rows(db, "SELECT count(*) FROM core.fct_sales WHERE channel = 'fisico'")[0][0] == 15


def test_na_es_nulo_no_cero(db):
    status = dict(rows(db, "SELECT stock_status, count(*) FROM core.fct_inventory_daily GROUP BY 1"))
    assert status["no_reportado"] == 3
    assert rows(db, "SELECT count(*) FROM core.fct_inventory_daily WHERE stock_status = 'no_reportado' AND stock_qty IS NOT NULL")[0][0] == 0


def test_q1_rotacion_principal(db):
    rotation = dict(rows(db, "SELECT sku_num, rotation FROM mart.q1_rotation_by_sku"))
    assert rotation[4] == pytest.approx(10 / (178 * 5 / 182))
    assert rotation[2] == pytest.approx(3 / (1780 / 182))
    # T002: 180 días reportados, 5 en 0 (3 de feb + 2 de mar) -> 1750/180.
    assert rotation[1] == pytest.approx(4 / (1780 / 182 + 1750 / 180))
    # V11 5 − V12 1; V14 (T001, par sin inventario) no entra.
    assert rotation[3] == pytest.approx(4 / 20)
    ranking = rows(db, "SELECT ranking FROM mart.q1_variant_summary WHERE variant = 'principal'")[0][0]
    assert ranking == [4, 2, 1, 3]


def test_q1_variantes_que_cambian_la_respuesta(db):
    summary = {v: r for v, r in rows(db, "SELECT variant, ranking FROM mart.q1_variant_summary")}
    assert summary["na_como_cero"] == [4, 2, 1, 3]
    assert summary["numerador_todas_las_tiendas"] == [4, 2, 3, 1]    # SKU 3: 6/20 = 0.30
    assert summary["bruto_sin_restar_devoluciones"] == [4, 2, 3, 1]  # SKU 3: 5/20 = 0.25
    assert summary["solo_mapeo_explicito"] == [4, 1]
    rotation_na_zero = dict(rows(db, "SELECT sku_num, rotation_na_as_zero FROM mart.q1_rotation_by_sku"))
    assert rotation_na_zero[1] == pytest.approx(4 / (1780 / 182 + 1750 / 182))
    assert rotation_na_zero[3] == pytest.approx(4 / (20 * 181 / 182))


def test_q2_tiendas_con_quiebre(db):
    status = dict(rows(db, "SELECT store_id, status FROM mart.q2_stores_with_stockouts"))
    assert status == {"T001": "confirmado", "T002": "posible_si_na_es_cero", "T003": "confirmado"}


def test_q2_rachas_de_borde(db):
    crossing = rows(
        db,
        "SELECT run_days, days_in_quarter, starts_before_quarter FROM mart.q2_stockout_runs "
        "WHERE variant = 'na_rompe_racha' AND store_id = 'T001' AND sku_num = 1",
    )
    assert crossing == [(4, 2, True)]
    censored = rows(
        db,
        "SELECT days_in_quarter, censored_at_data_end FROM mart.q2_stockout_runs "
        "WHERE variant = 'na_rompe_racha' AND store_id = 'T003'",
    )
    assert censored == [(4, True)]
    bridged = rows(
        db,
        "SELECT run_start, run_end, na_days FROM mart.q2_stockout_runs "
        "WHERE variant = 'na_entre_ceros_como_cero' AND store_id = 'T002'",
    )
    assert bridged == [(date(2026, 3, 1), date(2026, 3, 4), 2)]
    # La racha de exactamente 3 días (T002, 10–12 feb) no aparece.
    assert rows(db, "SELECT count(*) FROM mart.q2_stockout_runs WHERE run_start = DATE '2026-02-10'")[0][0] == 0


def test_q3_fisico(db):
    growth = {
        m.isoformat()[:7]: (float(s), None if g is None else float(g), note)
        for m, s, g, note in rows(
            db, "SELECT month, sales_mxn, mom_growth_pct, growth_note FROM mart.q3_mom_growth WHERE channel = 'fisico'"
        )
    }
    assert growth["2025-04"] == (33.0, 50.0, "ok")            # (44 − 11) vs 22 de marzo
    assert growth["2025-05"] == (0.0, -100.0, "ok")
    assert growth["2025-06"] == (0.0, None, "base_cero")
    assert growth["2025-10"] == (310.0, -22.5, "ok")          # 110 + 200 vs 400 de septiembre
    assert growth["2025-11"] == (340.0, 9.68, "ok")
    assert growth["2025-12"] == (80.0, -76.47, "ok")
    assert growth["2026-03"] == (400.0, 100.0, "ok")          # 500 − 100 vs 200


def test_q3_ecommerce_fx_y_cobertura(db):
    growth = {
        m.isoformat()[:7]: (None if s is None else float(s), None if g is None else float(g), note)
        for m, s, g, note in rows(
            db, "SELECT month, sales_mxn, mom_growth_pct, growth_note FROM mart.q3_mom_growth WHERE channel = 'ecommerce'"
        )
    }
    assert growth["2025-04"] == (100.0, None, "sin_cobertura_mes_anterior")
    assert growth["2025-05"] == (290.0, 190.0, "ok")          # 10 USD × 18 + 5 EUR × 22
    assert growth["2025-06"] == (50.0, -82.76, "ok")
    assert growth["2025-07"] == (None, None, "sin_cobertura_mes")
    capped = rows(db, "SELECT fx_source FROM core.fct_sales WHERE source_id = 'SHOP-3'")[0][0]
    assert capped == "fx_diario_tope_sospechoso"


def test_q3_variantes(db):
    variants = {
        (v, m.isoformat()[:7]): float(s)
        for v, m, s in rows(db, "SELECT variant, month, sales_mxn FROM mart.q3_monthly_variants WHERE sales_mxn IS NOT NULL")
    }
    assert variants[("fisico_todos_los_tipos", "2025-04")] == 88.0   # 44 + 11 × 4
    assert variants[("fisico_solo_ingresos", "2026-03")] == 500.0
    assert variants[("ecommerce_timestamp_utc", "2025-05")] == 340.0
    assert variants[("ecommerce_timestamp_utc", "2025-06")] == 0.0


def test_q4_margen_negativo(db):
    products = rows(db, "SELECT sku_num, product_margin_mxn, product_margin_pct, negative_store_ids FROM mart.q4_negative_margin_products")
    assert products == [(1, -180, -25, "T001, T002")]           # 720 de venta − 900 de costo
    by_store = dict(rows(db, "SELECT store_id, margin_mxn FROM mart.q4_negative_margin_by_store WHERE sku_num = 1"))
    assert by_store == {"T001": -160, "T002": -20}
    as_of_cost = rows(db, "SELECT unit_cost_mxn FROM core.fct_sales WHERE source_id IN ('V09', 'V10') ORDER BY source_id")
    assert [c for (c,) in as_of_cost] == [60, 50]


def test_q4_variantes(db):
    variants = {}
    for variant, sku, stores in rows(db, "SELECT variant, sku_num, negative_store_ids FROM mart.q4_margin_variants"):
        variants.setdefault(variant, {})[sku] = stores
    assert variants["solo_mapeo_explicito"] == {1: "T001, T002"}
    # 55/1.16 − 50 = −2.59 en T001 y 110/1.16 − 100 = −5.17 en T003.
    assert variants["precio_incluye_iva_16"] == {1: "T001, T002", 4: "T001, T003"}


def test_pii_se_queda_en_raw(db):
    assert rows(db, "SELECT count(*) FROM raw.ecommerce_orders WHERE customer_email LIKE '%@%'")[0][0] == 4
    leaked = rows(
        db,
        "SELECT table_schema || '.' || table_name || '.' || column_name FROM information_schema.columns "
        "WHERE table_schema NOT IN ('raw', 'audit') AND (column_name ILIKE '%customer%' OR column_name ILIKE '%email%' "
        "OR column_name ILIKE '%rfc%' OR column_name ILIKE '%address%' OR column_name ILIKE '%shipping%' "
        "OR column_name ILIKE '%proveedor%')",
    )
    assert leaked == []


def test_region_y_zona_propuestas_quedan_marcadas(db):
    stores = {r[0]: r[1:] for r in rows(db, "SELECT store_id, region_erp, region, region_source, timezone, timezone_source FROM core.dim_store")}
    assert stores["T001"] == ("centro", "centro", "erp", "America/Mexico_City", "erp")
    assert stores["T002"] == ("centro", "noreste", "propuesta_inferida", "America/Monterrey", "erp")
    assert stores["T003"] == ("frontera", "frontera", "erp", "America/Ciudad_Juarez", "propuesta_inferida")


def test_todos_los_checks_de_error_pasan(db):
    failed = rows(db, "SELECT check_name FROM audit.quality_results WHERE NOT passed AND severity = 'error'")
    assert failed == []
    warned = [name for (name,) in rows(db, "SELECT check_name FROM audit.quality_results WHERE NOT passed")]
    assert warned == ["fuentes_terminan_en_la_misma_fecha"]   # Shopify sintético termina en junio
