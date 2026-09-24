"""Recálculo independiente con pandas sobre los datos reales.

No reutiliza SQL ni código del pipeline: lee los archivos originales y vuelve a
derivar las 4 respuestas con otra implementación (merge_asof, diff de fechas,
groupby). Si el pipeline y este recálculo difieren, uno de los dos está mal.
"""
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from pipeline.run import run_pipeline

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
pytestmark = pytest.mark.skipif(not (DATA_DIR / "sales.csv").exists(), reason="datos reales no disponibles")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    db_path = run_pipeline(DATA_DIR, tmp_path_factory.mktemp("real") / "output")
    with duckdb.connect(str(db_path), read_only=True) as con:
        yield con


@pytest.fixture(scope="module")
def sources():
    sales = pd.read_csv(DATA_DIR / "sales.csv", dtype={"venta_id": str})
    sales["day"] = pd.to_datetime(sales["fecha_hora"]).dt.normalize()
    sales["num"] = sales["sku"].str[-3:].astype(int)
    sales["sign"] = sales["tipo_comprobante"].map({"I": 1, "E": -1}).fillna(0).astype(int)

    erp = json.loads((DATA_DIR / "inventory.json").read_text(encoding="utf-8"))
    snaps = pd.DataFrame(erp["snapshots"])
    snaps["day"] = pd.to_datetime(snaps["fecha"])
    snaps["num"] = snaps["sku_erp"].str.extract(r"-(\d{3})-[A-Z]$")[0].astype(int)
    snaps["stock"] = pd.to_numeric(snaps["cantidad_en_stock"].where(snaps["cantidad_en_stock"] != "N/A"))

    costs = pd.DataFrame(
        [
            {"num": int(p["sku_erp"][-5:-2]), "since": pd.Timestamp(h["fecha_vigencia"]), "cost": h["costo_mxn"]}
            for p in erp["catalogo"]["productos"]
            for h in p["cost_history"]
        ]
    )

    orders = pd.read_parquet(DATA_DIR / "ecommerce_orders.parquet", columns=["order_id", "fecha", "product_handle", "cantidad", "amount", "currency"])
    orders["day"] = pd.to_datetime(orders["fecha"]).dt.normalize()
    orders["num"] = orders["product_handle"].str[-3:].astype(int)
    fx = pd.read_csv(DATA_DIR / "exchange_rates.csv", parse_dates=["fecha"])
    orders = orders.merge(fx, left_on=["day", "currency"], right_on=["fecha", "currency"], how="left", suffixes=("", "_fx"))
    orders["rate"] = orders["rate_to_mxn"].where(orders["currency"] != "MXN", 1.0)
    orders["mxn"] = orders["amount"] * orders["rate"]

    as_of = max(sales["day"].max(), orders["day"].max(), snaps["day"].max())
    return {"sales": sales, "snaps": snaps, "costs": costs, "orders": orders, "as_of": as_of}


def test_ancla(db, sources):
    assert pd.Timestamp(db.execute("SELECT as_of_date FROM core.params").fetchone()[0]) == sources["as_of"]


def test_q1_top10(db, sources):
    as_of = sources["as_of"]
    start = as_of - pd.DateOffset(months=6) + pd.Timedelta(days=1)
    snaps = sources["snaps"][sources["snaps"]["day"].between(start, as_of)]
    avg_stock = snaps.groupby(["tienda_id", "num"])["stock"].mean().rename("avg")
    sales = sources["sales"][sources["sales"]["day"].between(start, as_of)]
    units = (sales["cantidad"] * sales["sign"]).groupby([sales["tienda_id"], sales["num"]]).sum().rename("units")
    pairs = avg_stock.to_frame().join(units, how="left").fillna({"units": 0})
    by_sku = pairs.groupby(level="num").sum()
    rotation = by_sku["units"] / by_sku["avg"]
    # Desempate por número de SKU, igual que el pipeline.
    expected = rotation.sort_index().sort_values(ascending=False, kind="stable").head(10)

    got = db.execute(
        "SELECT v.sku_num, r.rotation FROM mart.q1_top10_variants AS v JOIN mart.q1_rotation_by_sku AS r USING (sku_num) "
        "WHERE v.variant = 'principal' ORDER BY v.rank"
    ).fetchall()
    assert [sku for sku, _ in got] == list(expected.index)
    assert [float(value) for _, value in got] == pytest.approx(list(expected.values), rel=1e-9)


def test_q2_tiendas(db, sources):
    as_of = sources["as_of"]
    quarter = as_of.to_period("Q")
    if as_of != quarter.end_time.normalize():
        quarter -= 1
    in_quarter = sources["snaps"]["day"].between(quarter.start_time, quarter.end_time)
    quarter = sources["snaps"][in_quarter].sort_values(["tienda_id", "num", "day"])

    zeros = quarter[quarter["stock"] == 0].copy()
    new_run = zeros.groupby(["tienda_id", "num"])["day"].diff() != pd.Timedelta(days=1)
    zeros["run"] = new_run.cumsum()
    run_lengths = zeros.groupby(["tienda_id", "num", "run"]).size()
    expected = sorted(set(run_lengths[run_lengths > 3].index.get_level_values("tienda_id")))

    got = [s for (s,) in db.execute(
        "SELECT store_id FROM mart.q2_stores_with_stockouts WHERE status = 'confirmado' ORDER BY store_id"
    ).fetchall()]
    assert got == expected


def test_q3_crecimiento(db, sources):
    as_of = sources["as_of"]
    sales = sources["sales"]
    physical = (sales["monto"] * sales["sign"]).groupby(sales["day"].dt.to_period("M")).sum()
    orders = sources["orders"]
    online = orders.groupby(orders["day"].dt.to_period("M"))["mxn"].sum()
    months = pd.period_range(as_of.to_period("M") - 11, as_of.to_period("M"), freq="M")

    for channel, series in (("fisico", physical), ("ecommerce", online)):
        got = {
            pd.Period(m, "M"): (None if g is None else float(g))
            for m, g in db.execute(
                "SELECT month, mom_growth_pct FROM mart.q3_mom_growth WHERE channel = ?", [channel]
            ).fetchall()
        }
        for month in months:
            previous = month - 1
            if previous not in series.index:
                assert got[month] is None, (channel, month)
                continue
            expected = round(100 * (series[month] - series[previous]) / series[previous], 2)
            assert got[month] == pytest.approx(expected, abs=0.011), (channel, month)


def test_q4_margen_negativo(db, sources):
    costs = sources["costs"].sort_values("since")
    sales = sources["sales"][sources["sales"]["sign"] != 0].sort_values("day")
    sales = pd.merge_asof(sales, costs, left_on="day", right_on="since", by="num")
    sales["margin"] = sales["sign"] * (sales["monto"] - sales["cantidad"] * sales["cost"])

    orders = pd.merge_asof(sources["orders"].sort_values("day"), costs, left_on="day", right_on="since", by="num")
    orders["margin"] = orders["mxn"] - orders["cantidad"] * orders["cost"]
    orders["tienda_id"] = "ECOMMERCE"

    margins = pd.concat([sales[["num", "tienda_id", "margin"]], orders[["num", "tienda_id", "margin"]]])
    by_store = margins.groupby(["num", "tienda_id"])["margin"].sum()
    negative = by_store[by_store < 0]
    expected = {num: sorted(group.index.get_level_values("tienda_id")) for num, group in negative.groupby(level="num")}

    got = {
        num: stores.split(", ")
        for num, stores in db.execute("SELECT sku_num, negative_store_ids FROM mart.q4_negative_margin_products").fetchall()
    }
    assert got == expected

    product_margin = margins.groupby("num")["margin"].sum()
    for num, value in db.execute("SELECT sku_num, product_margin_mxn FROM mart.q4_negative_margin_products").fetchall():
        assert float(value) == pytest.approx(product_margin[num], abs=0.5)
