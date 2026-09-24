"""Dataset sintético mínimo que reproduce a propósito cada problema del perfilado.

Ancla esperada: 2026-03-31 (último snapshot). Ventana P1: 2025-10-01..2026-03-31.
Trimestre P2: 2026-01-01..2026-03-31. Año P3: 2025-04..2026-03.

Productos (número = llave común):
  1 Sándwich Comida Caliente  costo 100, precio 80  -> margen negativo. Mapeo explícito.
  2 Termo Mercancia           costo 50 y 60 desde 2026-01-01 (costo as-of). Sin fila de mapeo (inferido);
                              su handle se vende en Shopify sin estar en el mapeo (handle inferido).
  3 Estándar Cafe Grano       costo 40, precio 100. Fila de mapeo con sku_erp nulo pero handle explícito.
  4 Latte Bebidas             costo 10, precio 11 -> margen positivo, negativo si el precio trae IVA.

Tiendas: T001 CDMX, T002 Monterrey (región 'centro', error), T003 Ciudad Juárez (zona Ojinaga, error).
"""
import csv
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

AS_OF = date(2026, 3, 31)
SNAPSHOT_START = date(2025, 10, 1)

STORES = [
    {"tienda_id": "T001", "ciudad": "CDMX", "region": "centro", "timezone": "America/Mexico_City"},
    {"tienda_id": "T002", "ciudad": "Monterrey", "region": "centro", "timezone": "America/Monterrey"},
    {"tienda_id": "T003", "ciudad": "Ciudad Juárez", "region": "frontera", "timezone": "America/Ojinaga"},
]

PRODUCTS = [
    {"sku_erp": "ERP-PROV-MX-001-A", "nombre": "Sándwich Comida Caliente", "categoria": "comida_caliente",
     "cost_history": [{"fecha_vigencia": "2024-10-01", "costo_mxn": 100.0, "proveedor": "Proveedor Uno"}]},
    {"sku_erp": "ERP-PROV-MX-002-B", "nombre": "Termo Mercancia", "categoria": "mercancia",
     "cost_history": [{"fecha_vigencia": "2024-10-01", "costo_mxn": 50.0, "proveedor": "Proveedor Dos"},
                      {"fecha_vigencia": "2026-01-01", "costo_mxn": 60.0, "proveedor": "Proveedor Tres"}]},
    {"sku_erp": "ERP-PROV-MX-003-C", "nombre": "Estándar Cafe Grano", "categoria": "cafe_grano",
     "cost_history": [{"fecha_vigencia": "2024-10-01", "costo_mxn": 40.0, "proveedor": "Proveedor Cuatro"}]},
    {"sku_erp": "ERP-PROV-MX-004-D", "nombre": "Latte Bebidas", "categoria": "bebidas",
     "cost_history": [{"fecha_vigencia": "2024-10-01", "costo_mxn": 10.0, "proveedor": "Proveedor Cinco"}]},
]

SKU_MAPPINGS = [
    {"sku_pos": "CN-00001", "sku_erp": "ERP-PROV-MX-001-A", "handle": None},
    {"sku_pos": "CN-00003", "sku_erp": None, "handle": "estándar-cafe-grano-003"},
    {"sku_pos": "CN-00004", "sku_erp": "ERP-PROV-MX-004-D", "handle": None},
]

POS_COLUMNS = ["venta_id", "fecha_hora", "tienda_id", "sku", "cantidad", "monto", "moneda", "tipo_comprobante"]
POS_ROWS = [
    ("V01", "2025-03-15 10:00:00", "T001", "CN-00004", 2, "22.00", "MXN", "I"),   # mes base de P3
    ("V02", "2025-04-10 09:00:00", "T001", "CN-00004", 4, "44.00", "MXN", "I"),
    ("V03", "2025-04-11 09:00:00", "T001", "CN-00004", 1, "11.00", "MXN", "E"),   # devolución
    ("V04", "2025-04-12 09:00:00", "T001", "CN-00004", 1, "11.00", "MXN", "P"),   # pago: no es venta
    ("V05", "2025-04-13 09:00:00", "T001", "CN-00004", 1, "11.00", "MXN", "N"),   # nómina
    ("V06", "2025-04-14 09:00:00", "T001", "CN-00004", 1, "11.00", "MXN", "T"),   # traslado
    ("V07", "2025-11-05 12:00:00", "T001", "CN-00001", 3, "240.00", "MXN", "I"),
    ("V08", "2025-12-01 12:00:00", "T002", "CN-00001", 1, "80.00", "MXN", "I"),
    ("V09", "2026-02-10 12:00:00", "T001", "CN-00002", 2, "200.00", "MXN", "I"),  # costo 60 (as-of)
    ("V10", "2025-11-20 12:00:00", "T001", "CN-00002", 1, "100.00", "MXN", "I"),  # costo 50
    ("V11", "2026-03-10 08:00:00", "T002", "CN-00003", 5, "500.00", "MXN", "I"),
    ("V12", "2026-03-11 08:00:00", "T002", "CN-00003", 1, "100.00", "MXN", "E"),
    ("V13", "2025-10-15 08:00:00", "T003", "CN-00004", 10, "110.00", "MXN", "I"),
    ("V14", "2025-10-16 08:00:00", "T001", "CN-00003", 2, "200.00", "MXN", "I"),  # par sin inventario
    ("V15", "2025-09-30 23:00:00", "T001", "CN-00001", 5, "400.00", "MXN", "I"),  # un día antes de la ventana P1
]

ECOMMERCE_ROWS = [
    {"order_id": "SHOP-1", "fecha": "2025-04-05 12:00:00", "product_handle": "termo-mercancia-002",
     "cantidad": 1, "amount": 100.0, "currency": "MXN"},
    {"order_id": "SHOP-2", "fecha": "2025-05-06 12:00:00", "product_handle": "estándar-cafe-grano-003",
     "cantidad": 1, "amount": 10.0, "currency": "USD"},
    {"order_id": "SHOP-3", "fecha": "2025-05-07 12:00:00", "product_handle": "estándar-cafe-grano-003",
     "cantidad": 1, "amount": 5.0, "currency": "EUR"},
    # 03:00 local; si el timestamp fuera UTC sería 2025-05-31 21:00 y cambiaría de mes.
    {"order_id": "SHOP-4", "fecha": "2025-06-01 03:00:00", "product_handle": "termo-mercancia-002",
     "cantidad": 1, "amount": 50.0, "currency": "MXN"},
]

FX_ROWS = [
    ("2025-05-06", "USD", "18.0000"),
    ("2025-05-06", "EUR", "20.0000"),
    ("2025-05-07", "USD", "18.1000"),
    ("2025-05-07", "EUR", "22.0"),    # tope repetido
    ("2025-05-08", "EUR", "22.0"),
    ("2025-05-09", "EUR", "22.0"),
    ("2025-05-10", "EUR", "22.0"),
]

BASE_STOCK = {
    ("T001", "ERP-PROV-MX-001-A"): 10,
    ("T001", "ERP-PROV-MX-002-B"): 10,
    ("T002", "ERP-PROV-MX-001-A"): 10,
    ("T002", "ERP-PROV-MX-003-C"): 20,
    ("T003", "ERP-PROV-MX-004-D"): 5,
}


def _days(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


STOCK_OVERRIDES: dict[tuple[str, str, str], object] = {}
# 4 días en 0 que cruzan el inicio del trimestre: solo 2 caen dentro.
STOCK_OVERRIDES.update({("T001", "ERP-PROV-MX-001-A", d): 0 for d in _days("2025-12-30", "2026-01-02")})
# 4 días en 0 dentro del trimestre: quiebre confirmado de T001.
STOCK_OVERRIDES.update({("T001", "ERP-PROV-MX-002-B", d): 0 for d in _days("2026-02-01", "2026-02-04")})
# Exactamente 3 días en 0: no califica.
STOCK_OVERRIDES.update({("T002", "ERP-PROV-MX-001-A", d): 0 for d in _days("2026-02-10", "2026-02-12")})
# 0, N/A, N/A, 0: 4 días solo si N/A cuenta como 0.
STOCK_OVERRIDES.update({
    ("T002", "ERP-PROV-MX-001-A", "2026-03-01"): 0,
    ("T002", "ERP-PROV-MX-001-A", "2026-03-02"): "N/A",
    ("T002", "ERP-PROV-MX-001-A", "2026-03-03"): "N/A",
    ("T002", "ERP-PROV-MX-001-A", "2026-03-04"): 0,
})
# N/A aislado: cambia el promedio solo si se trata como 0.
STOCK_OVERRIDES[("T002", "ERP-PROV-MX-003-C", "2025-11-01")] = "N/A"
# 4 días en 0 al final de los datos: racha censurada.
STOCK_OVERRIDES.update({("T003", "ERP-PROV-MX-004-D", d): 0 for d in _days("2026-03-28", "2026-03-31")})


def build_sources() -> dict:
    snapshots = [
        {
            "fecha": day,
            "tienda_id": store,
            "sku_erp": sku,
            "cantidad_en_stock": STOCK_OVERRIDES.get((store, sku, day), stock),
        }
        for (store, sku), stock in BASE_STOCK.items()
        for day in _days(SNAPSHOT_START.isoformat(), AS_OF.isoformat())
    ]
    ecommerce = [
        {
            **row,
            "customer_name": f"Cliente {i}",
            "customer_email": f"cliente{i}@example.com",
            "customer_rfc": "ABCD800101XY1" if i == 0 else None,
            "shipping_city": "Nueva Serbia",
            "shipping_address": f"Calle Falsa {i}",
        }
        for i, row in enumerate(ECOMMERCE_ROWS)
    ]
    return {
        "pos": [dict(zip(POS_COLUMNS, row)) for row in POS_ROWS],
        "erp": {
            "metadata": {"generado": "2026-04-01T00:00:00", "fuente": "sintético", "descripcion": "tests"},
            "tiendas_info": [dict(s) for s in STORES],
            "sku_mappings": [dict(m) for m in SKU_MAPPINGS],
            "catalogo": {"productos": json.loads(json.dumps(PRODUCTS))},
            "snapshots": snapshots,
        },
        "ecommerce": ecommerce,
        "fx": [dict(zip(["fecha", "currency", "rate_to_mxn"], row)) for row in FX_ROWS],
    }


def write_dataset(folder: Path, mutate: Callable[[dict], None] | None = None) -> Path:
    sources = build_sources()
    if mutate is not None:
        mutate(sources)
    folder.mkdir(parents=True, exist_ok=True)

    with (folder / "sales.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=POS_COLUMNS)
        writer.writeheader()
        writer.writerows(sources["pos"])
    with (folder / "exchange_rates.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["fecha", "currency", "rate_to_mxn"])
        writer.writeheader()
        writer.writerows(sources["fx"])
    (folder / "inventory.json").write_text(json.dumps(sources["erp"], ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(sources["ecommerce"]).astype({"customer_rfc": "object"}).to_parquet(
        folder / "ecommerce_orders.parquet", index=False
    )
    return folder
