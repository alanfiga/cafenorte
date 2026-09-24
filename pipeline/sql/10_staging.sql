-- Staging: tipos, nombres homogéneos y banderas. Una fila de raw = una fila de staging.
-- Los casts usan TRY_ para que un valor no parseable quede NULL y lo detenga un check,
-- en vez de romper la corrida sin decir qué fila fue.
-- La PII de Shopify (nombre, email, RFC, dirección, ciudad) no pasa de aquí.

CREATE SCHEMA IF NOT EXISTS stg;

-- Catálogo c_TipoDeComprobante del SAT. Solo I suma y E resta; P duplicaría una venta
-- ya facturada y N/T no son ventas. Un código fuera de esta lista no se suma a nada:
-- el check pos_tipo_comprobante_conocido detiene la corrida.
CREATE OR REPLACE TABLE stg.ref_doc_types AS
SELECT * FROM (VALUES
    ('I', 'Ingreso',                               'venta',             1),
    ('E', 'Egreso (devolución / nota de crédito)', 'devolucion',       -1),
    ('P', 'Pago (complemento de pago)',            'excluido_pago',     0),
    ('N', 'Nómina',                                'excluido_nomina',   0),
    ('T', 'Traslado',                              'excluido_traslado', 0)
) AS t(doc_type, sat_description, sales_treatment, sales_sign);

CREATE OR REPLACE TABLE stg.pos_sales AS
SELECT
    venta_id                                                           AS sale_id,
    try_strptime(fecha_hora, '%Y-%m-%d %H:%M:%S')                      AS sale_ts,
    CAST(try_strptime(fecha_hora, '%Y-%m-%d %H:%M:%S') AS DATE)        AS sale_date,
    tienda_id                                                          AS store_id,
    sku                                                                AS sku_pos,
    TRY_CAST(regexp_extract(sku, '^CN-(\d{5})$', 1) AS INTEGER)        AS sku_num,
    TRY_CAST(cantidad AS INTEGER)                                      AS qty,
    TRY_CAST(monto AS DECIMAL(14, 2))                                  AS amount,
    moneda                                                             AS currency,
    tipo_comprobante                                                   AS doc_type
FROM raw.pos_sales;

CREATE OR REPLACE TABLE stg.ecommerce_orders AS
SELECT
    order_id,
    try_strptime(fecha, '%Y-%m-%d %H:%M:%S')                           AS order_ts,
    CAST(try_strptime(fecha, '%Y-%m-%d %H:%M:%S') AS DATE)             AS order_date,
    -- Shopify no declara zona horaria. Se asume hora local CDMX; esta columna es la
    -- alternativa si fuera UTC (CDMX es UTC-6 fijo desde oct-2022).
    CAST(try_strptime(fecha, '%Y-%m-%d %H:%M:%S') - INTERVAL 6 HOUR AS DATE) AS order_date_if_utc,
    product_handle                                                     AS handle,
    TRY_CAST(regexp_extract(product_handle, '-(\d{3})$', 1) AS INTEGER) AS handle_num,
    CAST(cantidad AS INTEGER)                                          AS qty,
    CAST(amount AS DECIMAL(14, 2))                                     AS amount,
    currency
FROM raw.ecommerce_orders;

CREATE OR REPLACE TABLE stg.fx_rates AS
WITH typed AS (
    SELECT
        TRY_CAST(fecha AS DATE)                     AS rate_date,
        currency,
        TRY_CAST(rate_to_mxn AS DECIMAL(12, 4))     AS rate_to_mxn
    FROM raw.fx_rates
)
SELECT
    *,
    -- Un máximo exacto que se repite muchos días es un tope del proveedor, no un
    -- precio de mercado (EUR = 22.0 en 63 días). Se usa, pero queda marcado.
    rate_to_mxn = max(rate_to_mxn) OVER (PARTITION BY currency)
        AND count(*) OVER (PARTITION BY currency, rate_to_mxn) > 3   AS is_suspected_cap
FROM typed;

CREATE OR REPLACE TABLE stg.stores AS
SELECT
    tienda_id  AS store_id,
    ciudad     AS city,
    region     AS region_erp,
    timezone   AS timezone_erp
FROM raw.erp_stores;

CREATE OR REPLACE TABLE stg.products AS
SELECT
    sku_erp,
    TRY_CAST(regexp_extract(sku_erp, '^ERP-PROV-MX-(\d{3})-[A-Z]$', 1) AS INTEGER) AS sku_num,
    nombre                                  AS product_name,
    categoria                               AS category,
    lower(replace(nombre, ' ', '-'))        AS name_slug
FROM raw.erp_products;

-- Proveedor se descarta: puede ser persona física y no lo usa ninguna pregunta.
CREATE OR REPLACE TABLE stg.cost_history AS
WITH typed AS (
    SELECT
        sku_erp,
        TRY_CAST(fecha_vigencia AS DATE)        AS valid_from,
        TRY_CAST(costo_mxn AS DECIMAL(12, 2))   AS unit_cost_mxn
    FROM raw.erp_cost_history
)
SELECT
    sku_erp,
    valid_from,
    coalesce(
        lead(valid_from) OVER (PARTITION BY sku_erp ORDER BY valid_from) - 1,
        DATE '9999-12-31'
    )                                           AS valid_to,
    unit_cost_mxn
FROM typed;

CREATE OR REPLACE TABLE stg.sku_mappings AS
SELECT
    sku_pos,
    sku_erp,
    handle,
    TRY_CAST(regexp_extract(sku_pos, '^CN-(\d{5})$', 1) AS INTEGER)               AS sku_pos_num,
    TRY_CAST(regexp_extract(sku_erp, '^ERP-PROV-MX-(\d{3})-[A-Z]$', 1) AS INTEGER) AS sku_erp_num,
    TRY_CAST(regexp_extract(handle, '-(\d{3})$', 1) AS INTEGER)                    AS handle_num
FROM raw.erp_sku_mappings;

-- "N/A" es desconocido, no cero: stock_qty queda NULL y stock_status lo distingue.
CREATE OR REPLACE TABLE stg.inventory_snapshots AS
SELECT
    TRY_CAST(fecha AS DATE)     AS snapshot_date,
    tienda_id                   AS store_id,
    sku_erp,
    CASE WHEN cantidad_json_type = 'int' THEN TRY_CAST(cantidad_en_stock AS INTEGER) END AS stock_qty,
    CASE
        WHEN cantidad_json_type = 'int' THEN 'reportado'
        WHEN cantidad_json_type = 'str' AND cantidad_en_stock = 'N/A' THEN 'no_reportado'
        ELSE 'valor_no_reconocido'
    END                         AS stock_status,
    cantidad_en_stock           AS stock_raw
FROM raw.erp_snapshots;
