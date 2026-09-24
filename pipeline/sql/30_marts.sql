-- Marts: una tabla principal por pregunta y, cuando un supuesto puede cambiar la
-- respuesta, la variante alternativa calculada al lado.

CREATE SCHEMA IF NOT EXISTS mart;

-- ---------------------------------------------------------------------------
-- P1. Top 10 SKUs por rotación, últimos 6 meses.
-- rotación = unidades netas vendidas / inventario promedio, ambos sobre los mismos
-- pares tienda×SKU que el ERP rastrea. Tiendas físicas únicamente: Shopify no tiene
-- inventario en el ERP.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.q1_rotation_by_sku AS
WITH params AS (
    SELECT * FROM core.params
),
stock_by_pair AS (
    SELECT
        i.store_id,
        i.sku_num,
        avg(i.stock_qty)                 AS avg_stock_na_excluded,
        avg(coalesce(i.stock_qty, 0))    AS avg_stock_na_as_zero,
        count(i.stock_qty)               AS days_reported,
        count(*)                         AS days_in_window
    FROM core.fct_inventory_daily AS i, params
    WHERE i.snapshot_date BETWEEN params.rotation_start AND params.as_of_date
    GROUP BY i.store_id, i.sku_num
),
units_by_pair AS (
    SELECT
        f.store_id,
        f.sku_num,
        sum(f.qty_net)                                         AS units_net,
        sum(CASE WHEN f.sales_sign = 1 THEN f.qty ELSE 0 END)  AS units_gross
    FROM core.fct_sales AS f, params
    WHERE f.channel = 'fisico'
      AND f.sale_date BETWEEN params.rotation_start AND params.as_of_date
    GROUP BY f.store_id, f.sku_num
),
tracked AS (
    SELECT
        s.sku_num,
        count(*)                              AS stores_tracked,
        sum(s.days_in_window - s.days_reported) AS store_days_not_reported,
        sum(s.avg_stock_na_excluded)          AS avg_stock,
        sum(s.avg_stock_na_as_zero)           AS avg_stock_na_as_zero,
        sum(coalesce(u.units_net, 0))         AS units_net_tracked,
        sum(coalesce(u.units_gross, 0))       AS units_gross_tracked
    FROM stock_by_pair AS s
    LEFT JOIN units_by_pair AS u USING (store_id, sku_num)
    GROUP BY s.sku_num
),
all_stores AS (
    SELECT sku_num, sum(units_net) AS units_net_all_stores
    FROM units_by_pair
    GROUP BY sku_num
)
SELECT
    d.sku_num,
    d.sku_pos,
    d.sku_erp,
    d.product_name,
    d.category,
    d.pos_erp_link_source,
    t.stores_tracked,
    t.store_days_not_reported,
    t.units_net_tracked,
    t.avg_stock,
    t.units_net_tracked / nullif(t.avg_stock, 0)                        AS rotation,
    (SELECT as_of_date - rotation_start + 1 FROM params)
        / nullif(t.units_net_tracked / nullif(t.avg_stock, 0), 0)      AS days_of_inventory,
    coalesce(a.units_net_all_stores, 0) - t.units_net_tracked           AS units_net_untracked_pairs,
    t.units_net_tracked / nullif(t.avg_stock_na_as_zero, 0)             AS rotation_na_as_zero,
    coalesce(a.units_net_all_stores, 0) / nullif(t.avg_stock, 0)        AS rotation_all_store_sales,
    t.units_gross_tracked / nullif(t.avg_stock, 0)                      AS rotation_gross
FROM tracked AS t
JOIN core.dim_product AS d USING (sku_num)
LEFT JOIN all_stores AS a USING (sku_num);

CREATE OR REPLACE TABLE mart.q1_top10_variants AS
WITH candidates AS (
    SELECT 'principal' AS variant, sku_num, rotation AS rotation_value FROM mart.q1_rotation_by_sku
    UNION ALL
    SELECT 'na_como_cero', sku_num, rotation_na_as_zero FROM mart.q1_rotation_by_sku
    UNION ALL
    SELECT 'numerador_todas_las_tiendas', sku_num, rotation_all_store_sales FROM mart.q1_rotation_by_sku
    UNION ALL
    SELECT 'bruto_sin_restar_devoluciones', sku_num, rotation_gross FROM mart.q1_rotation_by_sku
    UNION ALL
    SELECT 'solo_mapeo_explicito', sku_num, rotation FROM mart.q1_rotation_by_sku
    WHERE pos_erp_link_source = 'explicito'
),
ranked AS (
    SELECT
        *,
        row_number() OVER (PARTITION BY variant ORDER BY rotation_value DESC, sku_num) AS rank
    FROM candidates
    WHERE rotation_value IS NOT NULL
)
SELECT r.variant, r.rank, r.sku_num, d.sku_pos, d.product_name, r.rotation_value
FROM ranked AS r
JOIN core.dim_product AS d USING (sku_num)
WHERE r.rank <= 10;

CREATE OR REPLACE TABLE mart.q1_variant_summary AS
WITH lists AS (
    SELECT variant, list(sku_num ORDER BY rank) AS ranking
    FROM mart.q1_top10_variants
    GROUP BY variant
),
principal AS (
    SELECT ranking FROM lists WHERE variant = 'principal'
)
SELECT
    l.variant,
    l.ranking,
    len(list_intersect(l.ranking, p.ranking))  AS skus_shared_with_principal,
    l.ranking = p.ranking                       AS same_ranking_as_principal
FROM lists AS l, principal AS p;

-- ---------------------------------------------------------------------------
-- P2. Tiendas con quiebres de stock de más de 3 días en el último trimestre.
-- Quiebre = snapshot con stock 0. "Más de 3 días" = al menos 4 días consecutivos
-- en 0 dentro del trimestre (la racha se recorta a los límites del trimestre).
-- Variante principal: un N/A rompe la racha (desconocido no es cero).
-- Variante alternativa: N/A rodeado de ceros se cuenta como cero.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.q2_stockout_runs AS
WITH params AS (
    SELECT * FROM core.params
),
inventory_bounds AS (
    SELECT first_date, last_date FROM core.source_coverage WHERE source = 'inventario'
),
zero_days AS (
    SELECT
        store_id,
        sku_num,
        snapshot_date,
        snapshot_date - CAST(row_number() OVER (PARTITION BY store_id, sku_num ORDER BY snapshot_date) AS INTEGER) AS island
    FROM core.fct_inventory_daily
    WHERE stock_qty = 0
),
strict_runs AS (
    SELECT
        'na_rompe_racha'        AS variant,
        store_id,
        sku_num,
        min(snapshot_date)      AS run_start,
        max(snapshot_date)      AS run_end,
        0                       AS na_days
    FROM zero_days
    GROUP BY store_id, sku_num, island
),
zero_or_unknown_days AS (
    SELECT
        store_id,
        sku_num,
        snapshot_date,
        stock_qty,
        snapshot_date - CAST(row_number() OVER (PARTITION BY store_id, sku_num ORDER BY snapshot_date) AS INTEGER) AS island
    FROM core.fct_inventory_daily
    WHERE stock_qty = 0 OR stock_qty IS NULL
),
bridged_bounds AS (
    SELECT
        store_id,
        sku_num,
        island,
        min(snapshot_date) FILTER (WHERE stock_qty = 0) AS run_start,
        max(snapshot_date) FILTER (WHERE stock_qty = 0) AS run_end
    FROM zero_or_unknown_days
    GROUP BY store_id, sku_num, island
    HAVING count(*) FILTER (WHERE stock_qty = 0) > 0
),
bridged_runs AS (
    SELECT
        'na_entre_ceros_como_cero' AS variant,
        b.store_id,
        b.sku_num,
        b.run_start,
        b.run_end,
        count(*) FILTER (WHERE z.stock_qty IS NULL) AS na_days
    FROM bridged_bounds AS b
    JOIN zero_or_unknown_days AS z
        ON z.store_id = b.store_id AND z.sku_num = b.sku_num AND z.island = b.island
       AND z.snapshot_date BETWEEN b.run_start AND b.run_end
    GROUP BY ALL
),
runs AS (
    SELECT * FROM strict_runs
    UNION ALL
    SELECT * FROM bridged_runs
)
SELECT
    r.variant,
    r.store_id,
    r.sku_num,
    d.sku_erp,
    d.product_name,
    r.run_start,
    r.run_end,
    r.run_end - r.run_start + 1                                           AS run_days,
    greatest(0, least(r.run_end, p.quarter_end) - greatest(r.run_start, p.quarter_start) + 1) AS days_in_quarter,
    r.na_days,
    r.run_start < p.quarter_start                                         AS starts_before_quarter,
    r.run_end = b.last_date                                               AS censored_at_data_end
FROM runs AS r
CROSS JOIN params AS p
CROSS JOIN inventory_bounds AS b
JOIN core.dim_product AS d USING (sku_num)
WHERE r.run_end >= p.quarter_start
  AND r.run_start <= p.quarter_end
  AND r.run_end - r.run_start + 1 > 3;

CREATE OR REPLACE TABLE mart.q2_stores_with_stockouts AS
WITH per_store AS (
    SELECT
        store_id,
        count(*) FILTER (WHERE variant = 'na_rompe_racha' AND days_in_quarter > 3)            AS runs_confirmed,
        count(*) FILTER (WHERE variant = 'na_entre_ceros_como_cero' AND days_in_quarter > 3)  AS runs_if_na_is_zero,
        count(*) FILTER (WHERE variant = 'na_rompe_racha')                                    AS runs_counting_days_outside_quarter,
        max(days_in_quarter) FILTER (WHERE variant = 'na_rompe_racha')                        AS max_days_confirmed,
        max(days_in_quarter) FILTER (WHERE variant = 'na_entre_ceros_como_cero')              AS max_days_if_na_is_zero,
        string_agg(DISTINCT sku_erp, ', ' ORDER BY sku_erp)
            FILTER (WHERE variant = 'na_rompe_racha' AND days_in_quarter > 3)                 AS skus_confirmed,
        string_agg(DISTINCT sku_erp, ', ' ORDER BY sku_erp)
            FILTER (WHERE variant = 'na_entre_ceros_como_cero' AND days_in_quarter > 3)       AS skus_if_na_is_zero,
        bool_or(censored_at_data_end AND days_in_quarter > 3)                                 AS has_run_censored_at_data_end
    FROM mart.q2_stockout_runs
    GROUP BY store_id
)
SELECT
    s.store_id,
    st.city,
    CASE
        WHEN s.runs_confirmed > 0 THEN 'confirmado'
        WHEN s.runs_if_na_is_zero > 0 THEN 'posible_si_na_es_cero'
        ELSE 'solo_contando_dias_fuera_del_trimestre'
    END AS status,
    s.* EXCLUDE (store_id)
FROM per_store AS s
JOIN core.dim_store AS st USING (store_id);

-- ---------------------------------------------------------------------------
-- P3. Crecimiento mes a mes por canal, último año (12 meses que terminan en el
-- mes del ancla). Venta física = I − E en MXN; e-commerce convertido a MXN al tipo
-- del día. Un mes fuera de la cobertura de la fuente es NULL, no 0.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.q3_monthly_variants AS
WITH params AS (
    SELECT * FROM core.params
),
months AS (
    SELECT CAST(unnest(generate_series(
        CAST(growth_first_month - INTERVAL 1 MONTH AS TIMESTAMP),
        CAST(growth_last_month AS TIMESTAMP),
        INTERVAL 1 MONTH)) AS DATE) AS month
    FROM params
),
series AS (
    SELECT * FROM (VALUES
        ('principal',                  'fisico',    'pos'),
        ('principal',                  'ecommerce', 'ecommerce'),
        ('fisico_todos_los_tipos',     'fisico',    'pos'),
        ('fisico_solo_ingresos',       'fisico',    'pos'),
        ('ecommerce_timestamp_utc',    'ecommerce', 'ecommerce')
    ) AS t(variant, channel, source)
),
monthly AS (
    SELECT
        channel,
        CAST(date_trunc('month', sale_date) AS DATE) AS month,
        sum(revenue_mxn_net)                                         AS net,
        sum(original_amount)                                         AS all_doc_types,
        sum(CASE WHEN sales_sign = 1 THEN revenue_mxn_net ELSE 0 END) AS gross
    FROM core.fct_sales
    GROUP BY ALL
),
monthly_utc AS (
    SELECT CAST(date_trunc('month', sale_date_if_utc) AS DATE) AS month, sum(revenue_mxn_net) AS net
    FROM core.fct_sales
    WHERE channel = 'ecommerce'
    GROUP BY ALL
),
cells AS (
    SELECT
        s.variant,
        s.channel,
        m.month,
        m.month BETWEEN date_trunc('month', c.first_date) AND date_trunc('month', c.last_date) AS covered,
        CASE s.variant
            WHEN 'fisico_todos_los_tipos' THEN coalesce(mo.all_doc_types, 0)
            WHEN 'fisico_solo_ingresos' THEN coalesce(mo.gross, 0)
            WHEN 'ecommerce_timestamp_utc' THEN coalesce(mu.net, 0)
            ELSE coalesce(mo.net, 0)
        END AS sales_value
    FROM series AS s
    CROSS JOIN months AS m
    JOIN core.source_coverage AS c ON c.source = s.source
    LEFT JOIN monthly AS mo ON mo.channel = s.channel AND mo.month = m.month
    LEFT JOIN monthly_utc AS mu ON mu.month = m.month
),
with_prev AS (
    SELECT
        variant,
        channel,
        month,
        CASE WHEN covered THEN sales_value END                                                   AS sales_mxn,
        lag(CASE WHEN covered THEN sales_value END) OVER (PARTITION BY variant, channel ORDER BY month) AS prev_sales_mxn,
        covered,
        lag(covered) OVER (PARTITION BY variant, channel ORDER BY month)                         AS prev_covered
    FROM cells
)
SELECT
    w.variant,
    w.channel,
    w.month,
    round(w.sales_mxn, 2)                                                        AS sales_mxn,
    round(w.prev_sales_mxn, 2)                                                   AS prev_sales_mxn,
    round(100 * (w.sales_mxn - w.prev_sales_mxn) / nullif(w.prev_sales_mxn, 0), 2) AS mom_growth_pct,
    CASE
        WHEN NOT w.covered THEN 'sin_cobertura_mes'
        WHEN NOT w.prev_covered THEN 'sin_cobertura_mes_anterior'
        WHEN w.prev_sales_mxn = 0 THEN 'base_cero'
        WHEN w.month = p.growth_last_month AND NOT p.last_month_complete THEN 'mes_incompleto'
        ELSE 'ok'
    END                                                                          AS growth_note
FROM with_prev AS w
CROSS JOIN params AS p
WHERE w.month >= p.growth_first_month;

CREATE OR REPLACE TABLE mart.q3_mom_growth AS
WITH principal AS (
    SELECT * FROM mart.q3_monthly_variants WHERE variant = 'principal'
),
total AS (
    SELECT
        month,
        -- Si un canal no tiene cobertura en el mes o el anterior, el total no es comparable.
        CASE WHEN count(sales_mxn) = 2 THEN sum(sales_mxn) END           AS sales_mxn,
        CASE WHEN count(prev_sales_mxn) = 2 THEN sum(prev_sales_mxn) END AS prev_sales_mxn
    FROM principal
    GROUP BY month
)
SELECT channel, month, sales_mxn, prev_sales_mxn, mom_growth_pct, growth_note
FROM principal
UNION ALL
SELECT
    'total',
    month,
    sales_mxn,
    prev_sales_mxn,
    round(100 * (sales_mxn - prev_sales_mxn) / nullif(prev_sales_mxn, 0), 2),
    CASE
        WHEN sales_mxn IS NULL OR prev_sales_mxn IS NULL THEN 'algun_canal_sin_cobertura'
        WHEN prev_sales_mxn = 0 THEN 'base_cero'
        ELSE 'ok'
    END
FROM total;

-- ---------------------------------------------------------------------------
-- P4. Productos con margen negativo y tiendas donde ocurre.
-- Todo el histórico disponible; margen = venta neta − costo vigente a la fecha
-- de cada venta. Shopify aparece como la "tienda" ECOMMERCE.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.q4_margin_by_product_store AS
SELECT
    f.sku_num,
    d.sku_pos,
    d.sku_erp,
    d.product_name,
    d.pos_erp_link_source,
    coalesce(f.store_id, 'ECOMMERCE')                                          AS store_id,
    f.channel,
    sum(f.qty_net)                                                             AS units_net,
    round(sum(f.revenue_mxn_net), 2)                                           AS revenue_mxn,
    round(sum(f.cost_mxn_net), 2)                                              AS cost_mxn,
    round(sum(f.margin_mxn_net), 2)                                            AS margin_mxn,
    round(100 * sum(f.margin_mxn_net) / nullif(sum(f.revenue_mxn_net), 0), 2)  AS margin_pct,
    round(sum(f.revenue_mxn_net) / 1.16 - sum(f.cost_mxn_net), 2)              AS margin_mxn_if_price_includes_iva,
    count(*) FILTER (WHERE f.sales_sign = 1 AND f.original_amount * f.fx_rate < f.qty * f.unit_cost_mxn) AS sale_lines_below_cost,
    count(*) FILTER (WHERE f.sales_sign = 1)                                   AS sale_lines
FROM core.fct_sales AS f
JOIN core.dim_product AS d USING (sku_num)
WHERE f.sales_sign <> 0
GROUP BY ALL;

CREATE OR REPLACE TABLE mart.q4_margin_variants AS
WITH store_level AS (
    SELECT 'principal' AS variant, sku_num, store_id, margin_mxn AS margin_value, revenue_mxn
    FROM mart.q4_margin_by_product_store
    UNION ALL
    SELECT 'solo_mapeo_explicito', sku_num, store_id, margin_mxn, revenue_mxn
    FROM mart.q4_margin_by_product_store
    WHERE (channel = 'fisico' AND pos_erp_link_source = 'explicito')
       OR (channel = 'ecommerce' AND sku_num IN (SELECT sku_num FROM core.dim_product WHERE handle_link_source = 'explicito'))
    UNION ALL
    SELECT 'precio_incluye_iva_16', sku_num, store_id, margin_mxn_if_price_includes_iva, round(revenue_mxn / 1.16, 2)
    FROM mart.q4_margin_by_product_store
),
product_level AS (
    SELECT
        variant,
        sku_num,
        sum(margin_value)                                        AS product_margin_mxn,
        sum(revenue_mxn)                                         AS product_revenue_mxn,
        count(*) FILTER (WHERE margin_value < 0)                 AS stores_negative,
        count(*)                                                 AS stores_selling,
        string_agg(store_id, ', ' ORDER BY store_id) FILTER (WHERE margin_value < 0) AS negative_store_ids
    FROM store_level
    GROUP BY variant, sku_num
)
SELECT
    pl.variant,
    pl.sku_num,
    d.sku_pos,
    d.product_name,
    round(pl.product_margin_mxn, 2)                                          AS product_margin_mxn,
    round(100 * pl.product_margin_mxn / nullif(pl.product_revenue_mxn, 0), 2) AS product_margin_pct,
    pl.stores_negative,
    pl.stores_selling,
    pl.negative_store_ids
FROM product_level AS pl
JOIN core.dim_product AS d USING (sku_num)
WHERE pl.stores_negative > 0;

CREATE OR REPLACE TABLE mart.q4_negative_margin_products AS
SELECT sku_num, sku_pos, product_name, product_margin_mxn, product_margin_pct,
       stores_negative, stores_selling, negative_store_ids
FROM mart.q4_margin_variants
WHERE variant = 'principal';

CREATE OR REPLACE TABLE mart.q4_negative_margin_by_store AS
SELECT m.sku_num, m.sku_pos, m.product_name, m.pos_erp_link_source, m.store_id, s.city,
       m.units_net, m.revenue_mxn, m.cost_mxn, m.margin_mxn, m.margin_pct,
       m.sale_lines_below_cost, m.sale_lines
FROM mart.q4_margin_by_product_store AS m
LEFT JOIN core.dim_store AS s USING (store_id)
WHERE m.margin_mxn < 0;
