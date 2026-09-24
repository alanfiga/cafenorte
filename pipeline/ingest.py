"""Carga fiel de los archivos fuente a la capa raw.

Raw no interpreta nada: el CSV entra como texto, el JSON se aplana sin convertir
tipos (el stock queda como texto junto con su tipo JSON original) y el parquet se
copia tal cual, con PII incluida. Aquí también se calculan totales directamente
del archivo, con código distinto al SQL, para que los checks de calidad
concilien contra la fuente y no contra una copia.
"""
import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

SOURCE_FILES = {
    "pos": "sales.csv",
    "erp": "inventory.json",
    "ecommerce": "ecommerce_orders.parquet",
    "fx": "exchange_rates.csv",
}


def _sql_path(path: Path) -> str:
    # DuckDB acepta "/" en Windows; las comillas simples se escapan para el literal SQL.
    return path.resolve().as_posix().replace("'", "''")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pos_file_totals(path: Path) -> list[tuple]:
    rows = 0
    unparseable = 0
    amount_by_type: dict[str, Decimal] = defaultdict(Decimal)
    qty_by_type: dict[str, int] = defaultdict(int)
    count_by_type: dict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8", newline="") as fh:
        for record in csv.DictReader(fh):
            rows += 1
            doc_type = record["tipo_comprobante"]
            count_by_type[doc_type] += 1
            try:
                amount_by_type[doc_type] += Decimal(record["monto"])
                qty_by_type[doc_type] += int(record["cantidad"])
            except (InvalidOperation, ValueError):
                # El check pos_campos_parseables detiene la corrida; aquí solo se cuenta.
                unparseable += 1
    totals = [("pos", "rows", "", Decimal(rows)), ("pos", "unparseable_rows", "", Decimal(unparseable))]
    for doc_type in sorted(count_by_type):
        totals.append(("pos", "rows_by_doc_type", doc_type, Decimal(count_by_type[doc_type])))
        totals.append(("pos", "amount_by_doc_type", doc_type, amount_by_type[doc_type]))
        totals.append(("pos", "qty_by_doc_type", doc_type, Decimal(qty_by_type[doc_type])))
    return totals


def _ecommerce_file_totals(path: Path) -> list[tuple]:
    table = pq.read_table(path, columns=["currency", "amount", "cantidad"]).to_pandas()
    totals = [("ecommerce", "rows", "", Decimal(len(table)))]
    for currency, group in table.groupby("currency"):
        amount = sum(Decimal(str(value)) for value in group["amount"])
        totals.append(("ecommerce", "rows_by_currency", currency, Decimal(len(group))))
        totals.append(("ecommerce", "amount_by_currency", currency, amount))
        totals.append(("ecommerce", "qty_by_currency", currency, Decimal(int(group["cantidad"].sum()))))
    return totals


def _fx_file_totals(path: Path) -> list[tuple]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = sum(1 for _ in csv.DictReader(fh))
    return [("fx", "rows", "", Decimal(rows))]


def _load_erp(con: duckdb.DuckDBPyConnection, path: Path) -> list[tuple]:
    with path.open(encoding="utf-8") as fh:
        erp = json.load(fh)

    stores = pd.DataFrame(erp["tiendas_info"], dtype="object")
    mappings = pd.DataFrame(erp["sku_mappings"], dtype="object")
    products = pd.DataFrame(
        [{k: v for k, v in p.items() if k != "cost_history"} for p in erp["catalogo"]["productos"]],
        dtype="object",
    )
    cost_history = pd.DataFrame(
        [
            {"sku_erp": p["sku_erp"], **entry}
            for p in erp["catalogo"]["productos"]
            for entry in p["cost_history"]
        ],
        dtype="object",
    )
    # Todo entra como texto. El stock llega mezclado (int o "N/A"): se guarda también
    # su tipo JSON para que staging distinga 0 de "N/A" y detecte cualquier texto nuevo.
    snapshots = pd.DataFrame(
        {
            "fecha": [s["fecha"] for s in erp["snapshots"]],
            "tienda_id": [s["tienda_id"] for s in erp["snapshots"]],
            "sku_erp": [s["sku_erp"] for s in erp["snapshots"]],
            "cantidad_en_stock": [
                None if s["cantidad_en_stock"] is None else str(s["cantidad_en_stock"])
                for s in erp["snapshots"]
            ],
            "cantidad_json_type": [type(s["cantidad_en_stock"]).__name__ for s in erp["snapshots"]],
        }
    )
    metadata = pd.DataFrame([{k: str(v) for k, v in erp["metadata"].items()}])

    for name, frame in {
        "erp_stores": stores,
        "erp_sku_mappings": mappings,
        "erp_products": products,
        "erp_cost_history": cost_history,
        "erp_snapshots": snapshots,
        "erp_metadata": metadata,
    }.items():
        con.register("frame_to_load", frame.astype("string"))
        con.execute(f"CREATE OR REPLACE TABLE raw.{name} AS SELECT * FROM frame_to_load")
        con.unregister("frame_to_load")

    na_strings = sum(1 for s in erp["snapshots"] if s["cantidad_en_stock"] == "N/A")
    stock_sum = sum(s["cantidad_en_stock"] for s in erp["snapshots"] if isinstance(s["cantidad_en_stock"], int))
    return [
        ("erp", "stores", "", Decimal(len(erp["tiendas_info"]))),
        ("erp", "sku_mappings", "", Decimal(len(erp["sku_mappings"]))),
        ("erp", "products", "", Decimal(len(erp["catalogo"]["productos"]))),
        ("erp", "cost_history", "", Decimal(len(cost_history))),
        ("erp", "snapshots", "", Decimal(len(erp["snapshots"]))),
        ("erp", "snapshots_na_string", "", Decimal(na_strings)),
        ("erp", "snapshots_stock_sum", "", Decimal(stock_sum)),
    ]


def load_raw(con: duckdb.DuckDBPyConnection, data_dir: Path) -> None:
    missing = [name for name in SOURCE_FILES.values() if not (data_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Faltan archivos fuente en {data_dir}: {', '.join(missing)}")

    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    pos_path = data_dir / SOURCE_FILES["pos"]
    fx_path = data_dir / SOURCE_FILES["fx"]
    ecommerce_path = data_dir / SOURCE_FILES["ecommerce"]

    con.execute(
        f"CREATE OR REPLACE TABLE raw.pos_sales AS "
        f"SELECT * FROM read_csv('{_sql_path(pos_path)}', header=true, all_varchar=true)"
    )
    con.execute(
        f"CREATE OR REPLACE TABLE raw.fx_rates AS "
        f"SELECT * FROM read_csv('{_sql_path(fx_path)}', header=true, all_varchar=true)"
    )
    con.execute(
        f"CREATE OR REPLACE TABLE raw.ecommerce_orders AS "
        f"SELECT * FROM read_parquet('{_sql_path(ecommerce_path)}')"
    )

    totals = _pos_file_totals(pos_path) + _ecommerce_file_totals(ecommerce_path) + _fx_file_totals(fx_path)
    totals += _load_erp(con, data_dir / SOURCE_FILES["erp"])

    con.execute(
        "CREATE OR REPLACE TABLE raw.source_totals ("
        "source VARCHAR, metric VARCHAR, dimension VARCHAR, value DECIMAL(18,4))"
    )
    con.executemany("INSERT INTO raw.source_totals VALUES (?, ?, ?, ?)", totals)

    con.execute(
        "CREATE OR REPLACE TABLE raw.source_files (source VARCHAR, file_name VARCHAR, sha256 VARCHAR, bytes BIGINT)"
    )
    con.executemany(
        "INSERT INTO raw.source_files VALUES (?, ?, ?, ?)",
        [
            (source, name, _sha256(data_dir / name), (data_dir / name).stat().st_size)
            for source, name in SOURCE_FILES.items()
        ],
    )
