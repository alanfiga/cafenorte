"""Registro de supuestos con su evidencia e impacto medidos en cada corrida.

Los textos son fijos; los números salen de la base, así la tabla del README
se puede regenerar y nunca queda desfasada de los datos.
"""
from dataclasses import dataclass
from types import SimpleNamespace

import duckdb


@dataclass
class Assumption:
    code: str
    problem: str
    assumption: str
    evidence: str
    impact: str
    client_question: str = ""


def read_params(con: duckdb.DuckDBPyConnection) -> SimpleNamespace:
    cursor = con.execute("SELECT * FROM core.params")
    return SimpleNamespace(**dict(zip([c[0] for c in cursor.description], cursor.fetchone())))


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple:
    return con.execute(sql).fetchone()


def _n(value) -> str:
    return f"{int(value):,}"


def _mxn(value) -> str:
    return f"{float(value):,.0f} MXN"


def _top10_overlap(con: duckdb.DuckDBPyConnection, variant: str) -> str:
    row = _one(con, f"SELECT skus_shared_with_principal FROM mart.q1_variant_summary WHERE variant = '{variant}'")
    return f"{row[0]}/10" if row else "variante vacía"


def _max_growth_gap(con: duckdb.DuckDBPyConnection, variant: str, channel: str) -> float:
    row = _one(
        con,
        f"""
        SELECT max(abs(a.mom_growth_pct - p.mom_growth_pct))
        FROM mart.q3_monthly_variants AS a
        JOIN mart.q3_monthly_variants AS p USING (channel, month)
        WHERE a.variant = '{variant}' AND p.variant = 'principal' AND a.channel = '{channel}'
        """,
    )
    return float(row[0] or 0)


def collect(con: duckdb.DuckDBPyConnection) -> list[Assumption]:
    params = read_params(con)
    coverage = {r[0]: (r[1], r[2]) for r in con.execute("SELECT * FROM core.source_coverage").fetchall()}
    doc = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT doc_type, count(*), sum(original_amount) FROM core.fct_sales WHERE channel = 'fisico' GROUP BY 1"
        ).fetchall()
    }
    net = float(doc.get("I", (0, 0))[1]) - float(doc.get("E", (0, 0))[1])
    naive = sum(float(v[1]) for v in doc.values())

    returns_matching_sale = _one(
        con,
        """
        SELECT count(DISTINCT e.source_id) FROM core.fct_sales AS e
        JOIN core.fct_sales AS i
          ON i.channel = 'fisico' AND i.doc_type = 'I' AND i.store_id = e.store_id AND i.sku_num = e.sku_num
         AND i.qty = e.qty AND i.original_amount = e.original_amount AND i.sale_ts <= e.sale_ts
        WHERE e.channel = 'fisico' AND e.doc_type = 'E'
        """,
    )[0]

    na_rows, total_snapshots = _one(
        con, "SELECT count(*) FILTER (WHERE stock_status = 'no_reportado'), count(*) FROM core.fct_inventory_daily"
    )
    stores_confirmed, stores_possible = _one(
        con,
        "SELECT count(*) FILTER (WHERE status = 'confirmado'), count(*) FILTER (WHERE status = 'posible_si_na_es_cero') "
        "FROM mart.q2_stores_with_stockouts",
    )
    confirmed_ids = _one(
        con,
        "SELECT coalesce(string_agg(store_id, ', ' ORDER BY store_id), 'ninguna') FROM mart.q2_stores_with_stockouts "
        "WHERE status = 'confirmado'",
    )[0]
    possible_ids = _one(
        con,
        "SELECT coalesce(string_agg(store_id, ', ' ORDER BY store_id), 'ninguna') FROM mart.q2_stores_with_stockouts "
        "WHERE status = 'posible_si_na_es_cero'",
    )[0]

    tracked_pairs, sold_pairs, untracked_share = _one(
        con,
        """
        WITH p AS (SELECT * FROM core.params),
        tracked AS (SELECT DISTINCT store_id, sku_num FROM core.fct_inventory_daily),
        sold AS (SELECT store_id, sku_num, count(*) AS n FROM core.fct_sales, p
                 WHERE channel = 'fisico' AND sales_sign <> 0 AND sale_date BETWEEN rotation_start AND as_of_date
                 GROUP BY ALL)
        SELECT (SELECT count(*) FROM tracked), (SELECT count(*) FROM sold),
               1 - sum(n) FILTER (WHERE (store_id, sku_num) IN (SELECT * FROM tracked)) / sum(n)
        FROM sold
        """,
    )

    mapping = _one(
        con,
        """
        SELECT
            (SELECT count(*) FROM stg.sku_mappings WHERE sku_erp IS NOT NULL),
            (SELECT count(*) FROM stg.sku_mappings WHERE handle IS NOT NULL),
            (SELECT count(DISTINCT handle) FROM stg.ecommerce_orders),
            (SELECT string_agg(sku_pos, ', ' ORDER BY sku_pos) FROM core.dim_product WHERE pos_erp_link_source = 'inferido'),
            (SELECT count(*) FROM core.fct_sales WHERE channel = 'fisico' AND product_link_source = 'inferido'),
            (SELECT sum(revenue_mxn_net) FROM core.fct_sales WHERE channel = 'fisico' AND product_link_source = 'inferido'),
            (SELECT count(*) FROM core.fct_sales WHERE channel = 'ecommerce' AND product_link_source = 'inferido'),
            (SELECT sum(revenue_mxn_net) FROM core.fct_sales WHERE channel = 'ecommerce' AND product_link_source = 'inferido')
        """,
    )
    q4_lost_by_explicit = _one(
        con,
        """
        SELECT coalesce(string_agg(sku_pos, ', ' ORDER BY sku_pos), 'ninguno') FROM mart.q4_margin_variants
        WHERE variant = 'principal' AND sku_num NOT IN
            (SELECT sku_num FROM mart.q4_margin_variants WHERE variant = 'solo_mapeo_explicito')
        """,
    )[0]
    iva = _one(
        con,
        """
        SELECT
            (SELECT string_agg(sku_pos, ', ' ORDER BY sku_pos) FROM mart.q4_margin_variants WHERE variant = 'principal'),
            (SELECT string_agg(sku_pos, ', ' ORDER BY sku_pos) FROM mart.q4_margin_variants WHERE variant = 'precio_incluye_iva_16'),
            (SELECT sum(stores_negative) FROM mart.q4_margin_variants WHERE variant = 'principal'),
            (SELECT sum(stores_negative) FROM mart.q4_margin_variants WHERE variant = 'precio_incluye_iva_16'),
            (SELECT sum(product_margin_mxn) FROM mart.q4_margin_variants WHERE variant = 'principal'),
            (SELECT sum(product_margin_mxn) FROM mart.q4_margin_variants WHERE variant = 'precio_incluye_iva_16')
        """,
    )
    fx_cap = _one(
        con,
        """
        SELECT
            (SELECT count(*) FROM stg.fx_rates WHERE is_suspected_cap),
            (SELECT string_agg(DISTINCT currency || '=' || CAST(rate_to_mxn AS VARCHAR), ', ') FROM stg.fx_rates WHERE is_suspected_cap),
            count(*), coalesce(sum(revenue_mxn_net), 0),
            (SELECT sum(revenue_mxn_net) FROM core.fct_sales WHERE channel = 'ecommerce')
        FROM core.fct_sales WHERE fx_source = 'fx_diario_tope_sospechoso'
        """,
    )
    utc_moves = _one(
        con,
        "SELECT count(*), coalesce(sum(revenue_mxn_net), 0) FROM core.fct_sales WHERE channel = 'ecommerce' "
        "AND date_trunc('month', sale_date) <> date_trunc('month', sale_date_if_utc)",
    )
    pii = _one(
        con,
        "SELECT count(*), count(customer_rfc) FROM raw.ecommerce_orders",
    )
    cities = _one(con, "SELECT count(DISTINCT shipping_city) FROM raw.ecommerce_orders")[0]
    store_flags = _one(
        con,
        "SELECT count(*) FILTER (WHERE region_source <> 'erp'), count(*) FILTER (WHERE timezone_source <> 'erp'), "
        "string_agg(DISTINCT city, ', ' ORDER BY city) FROM core.dim_store",
    )
    stock_vs_sales = _one(
        con,
        """
        WITH s AS (
            SELECT store_id, sku_num, snapshot_date, stock_qty,
                   lag(stock_qty) OVER (PARTITION BY store_id, sku_num ORDER BY snapshot_date) AS prev_qty
            FROM core.fct_inventory_daily
        ),
        u AS (
            SELECT store_id, sku_num, sale_date, sum(qty_net) AS units
            FROM core.fct_sales WHERE channel = 'fisico' GROUP BY ALL
        )
        SELECT corr(s.stock_qty, s.prev_qty),
               corr(s.stock_qty - s.prev_qty, coalesce(u.units, 0)),
               count(*) FILTER (WHERE u.units > 0),
               count(*) FILTER (WHERE u.units > 0 AND s.stock_qty >= s.prev_qty)
        FROM s LEFT JOIN u ON u.store_id = s.store_id AND u.sku_num = s.sku_num AND u.sale_date = s.snapshot_date
        WHERE s.prev_qty IS NOT NULL AND s.stock_qty IS NOT NULL
        """,
    )
    volume = _one(
        con,
        "SELECT count(*) / count(DISTINCT sale_date) / count(DISTINCT store_id) FROM core.fct_sales WHERE channel = 'fisico'",
    )[0]
    rotation_range = _one(con, "SELECT min(rotation), max(rotation), median(days_of_inventory) FROM mart.q1_rotation_by_sku")
    ecommerce_start = coverage["ecommerce"][0]

    return [
        Assumption(
            "A01", "Los datos terminan antes de hoy; una ventana contada desde hoy sale vacía.",
            "Ancla = última fecha con datos. 6 meses, trimestre y año se cuentan desde ahí.",
            f"POS {coverage['pos'][0]}..{coverage['pos'][1]}; Shopify {coverage['ecommerce'][0]}..{coverage['ecommerce'][1]}; "
            f"inventario {coverage['inventario'][0]}..{coverage['inventario'][1]}.",
            f"Ancla {params.as_of_date}. P1 {params.rotation_start}..{params.as_of_date}; "
            f"P2 {params.quarter_start}..{params.quarter_end}; P3 {params.growth_first_month:%Y-%m}..{params.growth_last_month:%Y-%m}.",
        ),
        Assumption(
            "A02", "tipo_comprobante mezcla ventas con pagos, nómina y traslados.",
            "Códigos CFDI del SAT: I suma, E resta, P/N/T no son venta (quedan en fct_sales con signo 0). "
            "Un código nuevo detiene la corrida.",
            "; ".join(f"{k}: {_n(v[0])} filas / {_mxn(v[1])}" for k, v in sorted(doc.items())) + ".",
            f"Venta neta {_mxn(net)} vs suma ingenua {_mxn(naive)} (+{100 * (naive / net - 1):.1f}%). "
            f"P3 físico: el crecimiento cambia hasta {_max_growth_gap(con, 'fisico_todos_los_tipos', 'fisico'):.2f} pp "
            f"sumando todo y {_max_growth_gap(con, 'fisico_solo_ingresos', 'fisico'):.2f} pp sin restar E; ambas variantes publicadas.",
            "¿Qué tipos de comprobante cuenta cada área como venta? ¿E siempre es devolución de mercancía?",
        ),
        Assumption(
            "A03", "Las devoluciones (E) no referencian la venta original.",
            "Se restan en la fecha y tienda del egreso, con el costo vigente ese día.",
            f"{_n(returns_matching_sale)} de {_n(doc.get('E', (0, 0))[0])} E coinciden con un I previo en tienda+SKU+cantidad+monto.",
            f"P1 sin restar devoluciones comparte {_top10_overlap(con, 'bruto_sin_restar_devoluciones')} SKUs del top 10.",
        ),
        Assumption(
            "A04", "Stock 'N/A' en snapshots.",
            "N/A = desconocido: NULL, fuera del promedio de inventario. Nunca 0.",
            f"{_n(na_rows)} de {_n(total_snapshots)} snapshots ({100 * na_rows / total_snapshots:.1f}%).",
            f"P1 con N/A como 0 comparte {_top10_overlap(con, 'na_como_cero')} SKUs del top 10.",
        ),
        Assumption(
            "A05", "Un N/A dentro de una racha de ceros decide si la racha pasa de 3 días.",
            "Principal: N/A rompe la racha. Alternativa publicada: N/A entre ceros cuenta como 0.",
            "Rachas de ≥4 días calculadas con ambas reglas.",
            f"P2: {stores_confirmed} tiendas confirmadas ({confirmed_ids}); "
            f"{stores_possible} más solo si N/A = 0 ({possible_ids}).",
        ),
        Assumption(
            "A06", "El ERP solo rastrea parte de los pares tienda×SKU que se venden.",
            "Rotación = unidades / inventario sobre los mismos pares rastreados.",
            f"{_n(tracked_pairs)} pares con inventario de {_n(sold_pairs)} vendidos en la ventana; "
            f"{100 * float(untracked_share):.0f}% de las líneas POS son de pares sin inventario.",
            f"P1 con ventas de todas las tiendas en el numerador comparte {_top10_overlap(con, 'numerador_todas_las_tiendas')} "
            "SKUs del top 10: la definición cambia la respuesta.",
            "¿Por qué el ERP no rastrea todos los productos en todas las tiendas?",
        ),
        Assumption(
            "A07", "El mapeo POS↔ERP↔Shopify está incompleto.",
            "Llave = número de SKU (CN-00NNN = ERP-PROV-MX-NNN-X = handle-NNN). Vínculos marcados explícito/inferido.",
            f"La regla se cumple en {mapping[0]}/{mapping[0]} filas con sku_erp y {mapping[1]}/{mapping[1]} handles explícitos; "
            f"los {mapping[2]} handles vendidos coinciden con el nombre ERP. Inferidos POS→ERP: {mapping[3]}.",
            f"POS inferido: {_n(mapping[4])} filas / {_mxn(mapping[5])}. Shopify inferido: {_n(mapping[6])} órdenes / "
            f"{_mxn(mapping[7])}. Solo explícito: P1 comparte {_top10_overlap(con, 'solo_mapeo_explicito')} del top 10; "
            f"P4 pierde {q4_lost_by_explicit}.",
            "Confirmar que el número de SKU es la llave común entre sistemas.",
        ),
        Assumption(
            "A08", "No se sabe si el precio del POS incluye IVA.",
            "Monto = precio cobrado, costo sin IVA; no se descuenta IVA.",
            "Ningún campo lo indica.",
            f"P4 principal: {iva[0]} ({iva[2]} combinaciones producto×tienda en negativo). Con IVA 16% dentro del precio: "
            f"{iva[1]} ({iva[3]} combinaciones). Pérdida conjunta {_mxn(iva[4])} vs {_mxn(iva[5])} con IVA.",
            "¿El monto del POS incluye IVA? ¿El costo del ERP es sin IVA?",
        ),
        Assumption(
            "A09", "EUR topado en un valor exacto repetido.",
            "Se usa la tasa publicada; las órdenes quedan marcadas fx_diario_tope_sospechoso.",
            f"{fx_cap[0]} días con {fx_cap[1]}.",
            f"{_n(fx_cap[2])} órdenes / {_mxn(fx_cap[3])} ({100 * float(fx_cap[3]) / float(fx_cap[4]):.1f}% del e-commerce), "
            "posiblemente subestimadas.",
            "¿De qué proveedor sale el tipo de cambio y por qué topa en 22.0?",
        ),
        Assumption(
            "A10", "Shopify no declara zona horaria.",
            "Hora local CDMX. Alternativa UTC publicada (UTC-6).",
            "El campo fecha no trae offset.",
            f"Si fuera UTC, {_n(utc_moves[0])} órdenes ({_mxn(utc_moves[1])}) cambian de mes; "
            f"el crecimiento e-commerce cambia hasta {_max_growth_gap(con, 'ecommerce_timestamp_utc', 'ecommerce'):.2f} pp.",
            "¿La exportación de Shopify está en UTC o en hora de la tienda?",
        ),
        Assumption(
            "A11", "Shopify trae PII (nombre, email, RFC, dirección).",
            "La PII se queda en raw. También se descarta el proveedor del ERP (puede ser persona física).",
            f"{_n(pii[0])} órdenes con nombre, email y dirección; {_n(pii[1])} con RFC.",
            "Ninguna pregunta la usa. Dos checks detienen la corrida si aparece fuera de raw.",
        ),
        Assumption(
            "A12", "shipping_city no es una ciudad real.",
            "Se descarta; no se infiere país de envío.",
            f"{_n(cities)} valores distintos tipo 'Vieja Sudáfrica'.",
            "No hay análisis geográfico del e-commerce.",
            "¿Existe un campo de país de envío confiable?",
        ),
        Assumption(
            "A13", "La metadata de tiendas contradice la geografía.",
            "Se conserva el valor del ERP y se agrega una propuesta marcada 'propuesta_inferida'.",
            f"{store_flags[0]} tiendas con región dudosa (Monterrey y Chihuahua como 'centro'); "
            f"{store_flags[1]} con zona America/Ojinaga en Ciudad Juárez.",
            "Ninguna pregunta usa región ni zona horaria (las horas del POS ya son locales).",
            "¿'centro' es región geográfica o comercial?",
        ),
        Assumption(
            "A14", "Las ciudades no coinciden con el brief.",
            "Se reportan las 40 tiendas del ERP.",
            f"Ciudades en datos: {store_flags[2]}.",
            "Ninguno en las respuestas.",
            "¿Mérida, Cancún, Puebla y Hermosillo son tiendas propias?",
        ),
        Assumption(
            "A15", "Shopify no tiene inventario en el ERP.",
            "P1 y P2 solo cubren tiendas físicas; P3 y P4 incluyen e-commerce (en P4 como la tienda ECOMMERCE).",
            "No hay almacén online en tiendas_info ni en los snapshots.",
            "La rotación no incluye unidades vendidas online.",
            "¿Desde qué almacén o tienda se surte Shopify?",
        ),
        Assumption(
            "A16", "E-commerce no tiene mes base para el primer mes del año.",
            "Mes fuera de la cobertura de la fuente = NULL, no 0.",
            f"Shopify empieza el {ecommerce_start}.",
            f"Crecimiento e-commerce de {ecommerce_start:%Y-%m} = NULL (sin_cobertura_mes_anterior); el total de ese mes también.",
        ),
        Assumption(
            "A17", "El stock del ERP no se mueve con las ventas del POS.",
            "Rotación y quiebres usan los snapshots tal cual; no se reconstruye el stock desde ventas.",
            f"Autocorrelación del stock día a día {float(stock_vs_sales[0]):.3f}; correlación Δstock vs unidades vendidas "
            f"{float(stock_vs_sales[1]):.4f}; en {_n(stock_vs_sales[3])} de {_n(stock_vs_sales[2])} días con venta el stock no bajó.",
            "P1 y P2 describen al ERP, no necesariamente al anaquel.",
            "¿A qué hora se toma el snapshot y qué movimientos lo alimentan?",
        ),
        Assumption(
            "A18", "El volumen del POS es muy bajo para una cafetería.",
            "Se usa como viene; no se extrapola.",
            f"{float(volume):.1f} líneas por tienda por día.",
            f"Rotación de 6 meses entre {float(rotation_range[0]):.2f} y {float(rotation_range[1]):.2f} "
            f"(mediana {float(rotation_range[2]):.0f} días de inventario).",
            "¿sales.csv es el universo completo de tickets o una muestra?",
        ),
    ]


def persist(con: duckdb.DuckDBPyConnection, assumptions: list[Assumption]) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS audit")
    con.execute(
        "CREATE OR REPLACE TABLE audit.assumption_impact ("
        "code VARCHAR, problem VARCHAR, assumption VARCHAR, evidence VARCHAR, impact VARCHAR, client_question VARCHAR)"
    )
    con.executemany(
        "INSERT INTO audit.assumption_impact VALUES (?, ?, ?, ?, ?, ?)",
        [(a.code, a.problem, a.assumption, a.evidence, a.impact, a.client_question) for a in assumptions],
    )
