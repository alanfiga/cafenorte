"""Cada supuesto roto a propósito debe detener la corrida con el check que lo vigila."""
import duckdb
import pytest

from pipeline.quality import QualityCheckError, run_checks
from pipeline.run import run_pipeline
from tests.synthetic import write_dataset


def _unknown_doc_type(src):
    src["pos"][0]["tipo_comprobante"] = "X"


def _stock_text(src):
    src["erp"]["snapshots"][0]["cantidad_en_stock"] = "desconocido"


def _negative_stock(src):
    src["erp"]["snapshots"][0]["cantidad_en_stock"] = -3


def _mapping_breaks_number_rule(src):
    src["erp"]["sku_mappings"][0]["sku_erp"] = "ERP-PROV-MX-004-D"


def _handle_not_matching_name(src):
    src["ecommerce"][0]["product_handle"] = "termo-grande-002"


def _missing_snapshot_day(src):
    src["erp"]["snapshots"].pop(10)


def _missing_fx(src):
    src["fx"] = [row for row in src["fx"] if not (row["fecha"] == "2025-05-06" and row["currency"] == "USD")]


def _pos_sku_outside_catalog(src):
    src["pos"][0]["sku"] = "CN-00099"


def _duplicate_sale_id(src):
    src["pos"][1]["venta_id"] = src["pos"][0]["venta_id"]


def _pos_in_dollars(src):
    src["pos"][0]["moneda"] = "USD"


def _unparseable_amount(src):
    src["pos"][0]["monto"] = "22,00"


def _new_currency(src):
    src["ecommerce"][0]["currency"] = "CAD"


def _store_outside_erp(src):
    src["pos"][0]["tienda_id"] = "T999"


def _duplicate_cost_date(src):
    history = src["erp"]["catalogo"]["productos"][0]["cost_history"]
    history.append(dict(history[0]))


@pytest.mark.parametrize(
    ("mutation", "expected_check"),
    [
        (_unknown_doc_type, "pos_tipo_comprobante_conocido"),
        (_stock_text, "stock_valores_validos"),
        (_negative_stock, "stock_valores_validos"),
        (_mapping_breaks_number_rule, "mapeo_explicito_mismo_numero"),
        (_handle_not_matching_name, "ecommerce_handle_coincide_con_nombre"),
        (_missing_snapshot_day, "snapshots_grilla_completa"),
        (_missing_fx, "fx_cobertura"),
        (_pos_sku_outside_catalog, "pos_sku_en_catalogo"),
        (_duplicate_sale_id, "pos_venta_id_unico"),
        (_pos_in_dollars, "pos_moneda_mxn"),
        (_unparseable_amount, "pos_campos_parseables"),
        (_new_currency, "ecommerce_moneda_conocida"),
        (_store_outside_erp, "tiendas_en_erp"),
        (_duplicate_cost_date, "costos_sin_fechas_repetidas"),
    ],
)
def test_supuesto_roto_detiene_la_corrida(tmp_path, mutation, expected_check):
    data_dir = write_dataset(tmp_path / "data", mutate=mutation)
    with pytest.raises(QualityCheckError) as error:
        run_pipeline(data_dir, tmp_path / "output")
    assert expected_check in [name for name, _, _ in error.value.failures]


def test_pii_fuera_de_raw_detiene_la_corrida(tmp_path):
    db_path = run_pipeline(write_dataset(tmp_path / "data"), tmp_path / "output")
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE mart.leak AS SELECT customer_email FROM raw.ecommerce_orders")
        with pytest.raises(QualityCheckError) as error:
            run_checks(con, "marts", "test")
    failed = [name for name, _, _ in error.value.failures]
    assert "pii_columnas_fuera_de_raw" in failed
    assert "pii_valores_fuera_de_raw" in failed


def test_total_que_no_cuadra_contra_el_archivo_detiene_la_corrida(tmp_path):
    db_path = run_pipeline(write_dataset(tmp_path / "data"), tmp_path / "output")
    with duckdb.connect(str(db_path)) as con:
        con.execute("DELETE FROM core.fct_sales WHERE source_id = 'V07'")
        with pytest.raises(QualityCheckError) as error:
            run_checks(con, "core", "test")
    failed = [name for name, _, _ in error.value.failures]
    assert {"fct_pos_filas_vs_archivo", "fct_pos_montos_vs_archivo", "fct_venta_fisica_neta_vs_archivo"} <= set(failed)
