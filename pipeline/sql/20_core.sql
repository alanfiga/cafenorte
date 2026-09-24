-- Core: dimensiones conciliadas, hechos unificados y parámetros de la corrida.
-- Cada vínculo inferido lleva su columna *_source para poder filtrarlo.

CREATE SCHEMA IF NOT EXISTS core;

CREATE OR REPLACE TABLE core.source_coverage AS
SELECT 'pos' AS source, min(sale_date) AS first_date, max(sale_date) AS last_date FROM stg.pos_sales
UNION ALL
SELECT 'ecommerce', min(order_date), max(order_date) FROM stg.ecommerce_orders
UNION ALL
SELECT 'inventario', min(snapshot_date), max(snapshot_date) FROM stg.inventory_snapshots;

-- Las ventanas se anclan en la última fecha con datos, no en la fecha de ejecución.
CREATE OR REPLACE TABLE core.params AS
WITH anchor AS (
    SELECT max(last_date) AS as_of_date FROM core.source_coverage
),
quarter AS (
    SELECT
        as_of_date,
        -- Último trimestre calendario completo: el del ancla si termina ese día, si no el anterior.
        CASE
            WHEN as_of_date = CAST(date_trunc('quarter', as_of_date) + INTERVAL 3 MONTH - INTERVAL 1 DAY AS DATE)
                THEN as_of_date
            ELSE CAST(date_trunc('quarter', as_of_date) - INTERVAL 1 DAY AS DATE)
        END AS quarter_end
    FROM anchor
)
SELECT
    as_of_date,
    CAST(as_of_date - INTERVAL 6 MONTH + INTERVAL 1 DAY AS DATE)    AS rotation_start,
    CAST(date_trunc('quarter', quarter_end) AS DATE)                AS quarter_start,
    quarter_end,
    CAST(date_trunc('month', as_of_date) - INTERVAL 11 MONTH AS DATE) AS growth_first_month,
    CAST(date_trunc('month', as_of_date) AS DATE)                   AS growth_last_month,
    as_of_date = last_day(as_of_date)                               AS last_month_complete
FROM quarter;

-- Región y zona horaria: se conserva el valor del ERP y se agrega una propuesta
-- marcada, solo donde el ERP contradice la geografía. Ninguna pregunta usa región.
CREATE OR REPLACE TABLE core.dim_store AS
WITH proposals AS (
    SELECT * FROM (VALUES
        ('Monterrey',     'centro', 'noreste', NULL,                    NULL),
        ('Chihuahua',     'centro', 'norte',   NULL,                    NULL),
        ('Ciudad Juárez', NULL,     NULL,      'America/Ojinaga',       'America/Ciudad_Juarez')
    ) AS t(city, region_observed, region_proposed, timezone_observed, timezone_proposed)
)
SELECT
    s.store_id,
    s.city,
    s.region_erp,
    coalesce(pr.region_proposed, s.region_erp)                       AS region,
    CASE WHEN pr.region_proposed IS NULL THEN 'erp' ELSE 'propuesta_inferida' END AS region_source,
    s.timezone_erp,
    coalesce(pt.timezone_proposed, s.timezone_erp)                   AS timezone,
    CASE WHEN pt.timezone_proposed IS NULL THEN 'erp' ELSE 'propuesta_inferida' END AS timezone_source
FROM stg.stores AS s
LEFT JOIN proposals AS pr
    ON pr.city = s.city AND pr.region_observed = s.region_erp
LEFT JOIN proposals AS pt
    ON pt.city = s.city AND pt.timezone_observed = s.timezone_erp;

-- Producto: el número de SKU es la llave común (CN-00NNN = ERP-PROV-MX-NNN-X = handle-NNN).
-- La regla se valida en cada corrida contra todas las filas explícitas del mapeo.
CREATE OR REPLACE TABLE core.dim_product AS
WITH pos_skus AS (
    SELECT DISTINCT sku_num, sku_pos FROM stg.pos_sales
),
web_handles AS (
    SELECT DISTINCT handle_num AS sku_num, handle FROM stg.ecommerce_orders
),
explicit_erp AS (
    SELECT sku_pos_num AS sku_num, sku_erp FROM stg.sku_mappings WHERE sku_erp IS NOT NULL
),
explicit_handle AS (
    SELECT sku_pos_num AS sku_num, handle FROM stg.sku_mappings WHERE handle IS NOT NULL
)
SELECT
    p.sku_num,
    pos.sku_pos,
    p.sku_erp,
    p.product_name,
    p.category,
    coalesce(eh.handle, w.handle)                                    AS handle,
    CASE
        WHEN pos.sku_pos IS NULL THEN 'sin_ventas_pos'
        WHEN ee.sku_erp IS NOT NULL THEN 'explicito'
        ELSE 'inferido'
    END                                                              AS pos_erp_link_source,
    CASE
        WHEN eh.handle IS NOT NULL THEN 'explicito'
        WHEN w.handle IS NOT NULL THEN 'inferido'
        ELSE 'sin_handle'
    END                                                              AS handle_link_source
FROM stg.products AS p
LEFT JOIN pos_skus AS pos USING (sku_num)
LEFT JOIN explicit_erp AS ee USING (sku_num)
LEFT JOIN explicit_handle AS eh USING (sku_num)
LEFT JOIN web_handles AS w USING (sku_num);

-- Hecho de ventas unificado. Todas las filas de POS y Shopify entran, incluidas las
-- excluidas de ventas (P/N/T), con signo 0 y su tratamiento explícito.
-- El costo es el vigente en la fecha de la venta.
CREATE OR REPLACE TABLE core.fct_sales AS
WITH pos AS (
    SELECT
        'fisico'                                     AS channel,
        s.sale_id                                    AS source_id,
        s.sale_ts,
        s.sale_date,
        s.sale_date                                  AS sale_date_if_utc,
        s.store_id,
        s.sku_num,
        s.doc_type,
        d.sales_treatment,
        d.sales_sign,
        s.qty,
        s.qty * d.sales_sign                         AS qty_net,
        s.currency                                   AS original_currency,
        s.amount                                     AS original_amount,
        CAST(1 AS DECIMAL(12, 4))                    AS fx_rate,
        'mxn_nativo'                                 AS fx_source,
        CAST(s.amount * d.sales_sign AS DECIMAL(24, 6)) AS revenue_mxn_net,
        dp.pos_erp_link_source                       AS product_link_source
    FROM stg.pos_sales AS s
    LEFT JOIN stg.ref_doc_types AS d USING (doc_type)
    LEFT JOIN core.dim_product AS dp USING (sku_num)
),
web AS (
    SELECT
        'ecommerce',
        o.order_id,
        o.order_ts,
        o.order_date,
        o.order_date_if_utc,
        NULL,
        o.handle_num,
        'shopify',
        'venta',
        1,
        o.qty,
        o.qty,
        o.currency,
        o.amount,
        CASE WHEN o.currency = 'MXN' THEN CAST(1 AS DECIMAL(12, 4)) ELSE fx.rate_to_mxn END,
        CASE
            WHEN o.currency = 'MXN' THEN 'mxn_nativo'
            WHEN fx.rate_to_mxn IS NULL THEN 'sin_fx'
            WHEN fx.is_suspected_cap THEN 'fx_diario_tope_sospechoso'
            ELSE 'fx_diario'
        END,
        CAST(o.amount * CASE WHEN o.currency = 'MXN' THEN 1 ELSE fx.rate_to_mxn END AS DECIMAL(24, 6)),
        dp.handle_link_source
    FROM stg.ecommerce_orders AS o
    LEFT JOIN stg.fx_rates AS fx
        ON fx.rate_date = o.order_date AND fx.currency = o.currency
    LEFT JOIN core.dim_product AS dp
        ON dp.sku_num = o.handle_num
),
costs AS (
    SELECT p.sku_num, c.valid_from, c.valid_to, c.unit_cost_mxn
    FROM stg.cost_history AS c
    JOIN stg.products AS p USING (sku_erp)
),
unified AS (
    SELECT * FROM pos
    UNION ALL
    SELECT * FROM web
)
SELECT
    u.*,
    c.unit_cost_mxn,
    CAST(u.qty_net * c.unit_cost_mxn AS DECIMAL(24, 6))                    AS cost_mxn_net,
    CAST(u.revenue_mxn_net - u.qty_net * c.unit_cost_mxn AS DECIMAL(24, 6)) AS margin_mxn_net
FROM unified AS u
LEFT JOIN costs AS c
    ON c.sku_num = u.sku_num
   AND u.sale_date BETWEEN c.valid_from AND c.valid_to;

CREATE OR REPLACE TABLE core.fct_inventory_daily AS
SELECT
    s.snapshot_date,
    s.store_id,
    p.sku_num,
    s.sku_erp,
    s.stock_qty,
    s.stock_status
FROM stg.inventory_snapshots AS s
LEFT JOIN stg.products AS p USING (sku_erp);
