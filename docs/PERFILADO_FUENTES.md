# Perfilado de fuentes — CaféNorte

Fecha del perfilado: 2026-09-23. Zip recibido: `datos_cafenorte.zip`.

## 0. Contenido del zip

| Archivo | Tamaño | Filas | Mencionado en el caso |
|---|---|---|---|
| `sales.csv` | 5.1 MB | 86,490 | Sí |
| `inventory.json` | 31.7 MB | 230,776 snapshots + catálogo + mapeo | Sí |
| `ecommerce_orders.parquet` | 0.7 MB | 9,947 | Sí |
| `exchange_rates.csv` | 16 KB | 730 | **No** |

`exchange_rates.csv` [Seguro]: tipo de cambio diario `USD→MXN` y `EUR→MXN`, 365 días (2025-04-01 a 2026-03-31) × 2 monedas, sin huecos ni duplicados. Sirve para convertir las órdenes Shopify en USD/EUR a MXN. Comprobé que la conversión es coherente: el precio unitario USD convertido queda a 0.99–1.05× del precio MXN del mismo handle.

`inventory.json` trae además tres bloques que el caso no menciona:
- `tiendas_info` (40 tiendas: ciudad, región, zona horaria) → dimensión tienda.
- `sku_mappings` (65 filas: `sku_pos` ↔ `sku_erp` ↔ `handle` de Shopify) → tabla puente entre las tres fuentes.
- `catalogo.productos` (70 productos con `cost_history` fechado) → dimensión producto y costo vigente para calcular margen.

## 1. Ventanas temporales: no coinciden

| Fuente | Desde | Hasta | Meses |
|---|---|---|---|
| POS `sales.csv` | 2024-10-01 | 2026-03-31 | 18 |
| Shopify | 2025-04-01 | 2026-03-31 | 12 |
| FX | 2025-04-01 | 2026-03-31 | 12 |
| Snapshots de inventario | 2025-10-01 | 2026-03-31 | 6 (182 días) |
| Costos (`cost_history`) | vigente desde 2024-10-01 | último cambio 2026-03-30 | 18 |

Los datos terminan el 2026-03-31 (el metadata dice `generado: 2026-04-23`). Hoy es septiembre de 2026: si "últimos 6 meses" se calcula contra la fecha de hoy, las preguntas 1–3 salen vacías.
**Decisión:** fecha de corte = último día con datos (2026-03-31). "Últimos 6 meses" = 2025-10-01..2026-03-31 (coincide exacto con los snapshots); "último trimestre" = Q1-2026 (ene–mar); "último año" = abr-2025..mar-2026 (coincide con Shopify y FX).

## 2. `sales.csv` (POS)

- Columnas: `venta_id` (único, secuencial sin huecos), `fecha_hora` (formato único `YYYY-MM-DD HH:MM:SS`, sin nulos), `tienda_id` (40), `sku` (70, `CN-000NN`), `cantidad` (int 1–16), `monto` (15.67–9,714.87, nunca ≤0), `moneda` (100% `MXN`), `tipo_comprobante`.
- Sin nulos, sin vacíos, sin duplicados exactos ni por (fecha_hora, tienda, sku).
- Precio unitario (`monto/cantidad`) por SKU: dispersión ±4.5% alrededor de la mediana en los 70 SKUs; sin outliers.
- Horario 07:00–21:59 en todas las zonas horarias → `fecha_hora` está en hora local de la tienda.
- La hora 07 tiene 9,070 filas contra 4,569 de las 08. Puede ser la hora pico del café o un corte artificial al abrir la tienda. No afecta ninguna de las 4 preguntas.

### `tipo_comprobante` — códigos
Son los tipos de CFDI del catálogo del SAT (`c_TipoDeComprobante`):

| Código | Significado SAT | Filas | Monto MXN | Tratamiento |
|---|---|---|---|---|
| I | Ingreso (venta) | 82,518 | 29,503,070 | Venta, suma |
| E | Egreso (devolución / nota de crédito) | 3,079 | 1,140,985 | Devolución, **resta** (el monto viene positivo) |
| P | Pago (complemento de pago de una venta ya facturada) | 451 | 160,501 | Excluir: sumarlo cuenta dos veces la venta |
| N | Nómina | 288 | 100,580 | Excluir: no es venta |
| T | Traslado (movimiento de mercancía) | 154 | 42,118 | Excluir de ventas; es movimiento de inventario |

Sumar todo el archivo da 30.95 M MXN; la venta neta (I − E) da 28.36 M. La diferencia es **+9.1%**. Esto explica que "cada área reporte un número distinto de ventas": depende de qué tipos incluya cada una.
Los E no traen referencia a la venta original: solo 27 de 3,079 coinciden con un I previo en tienda+sku+cantidad+monto, que es lo esperable por azar. Se restan en la fecha y tienda del egreso.

## 3. `inventory.json` (ERP)

### Snapshots
- 230,776 = 1,268 pares (tienda, sku) × 182 días, completo sin huecos, sin duplicados de llave.
- `cantidad_en_stock`: 226,359 enteros (0–153) y **4,417 strings `"N/A"` (1.9%)** repartidos en las 40 tiendas y los 70 SKUs; en rachas de 1 día (4,260), 2 días (74) y 3 días (3).
- Ceros: 11,726 filas; todos los pares tienen al menos un cero.
- **Solo se rastrean 1,268 de los 2,800 pares tienda×SKU que el POS vende (45%)** . En la ventana de snapshots, 15,483 de 28,622 líneas POS (54%) son de pares sin inventario.

### Catálogo y costos
- 70 productos, llave `sku_erp` única, 6 categorías: cafe_molido 17, mercancia 15, cafe_grano 12, comida_caliente 11, bebidas 8, panaderia 7.
- `cost_history`: 282 registros, costos 7.73–478.30, sin nulos ni ≤0, ordenados, todos arrancan el 2024-10-01; cambios entre −8% y +10%. Hay 4 cambios con ≤3 días de separación (p. ej., ERP-001-A: 84.00 → 85.77 el 17-mar → 92.65 el 18-mar-2026).
- `nombre` no es único (p. ej., 6 "Termo Mercancia" distintos); la identidad del producto es el número de SKU.

### Tiendas
- 40 tiendas; T001–T015 se repiten como T016–T030 y T031–T040 (15 ciudades).
- Errores de metadata: Monterrey (T003/T018/T033) y Chihuahua (T010/T025/T040) aparecen con `region = "centro"`; Ciudad Juárez (T013/T028) con zona `America/Ojinaga` (la correcta es `America/Ciudad_Juarez`, con 1 h de diferencia).
- El caso habla de CDMX, Bajío, Monterrey, Guadalajara y frontera; los datos incluyen también Puebla, Mérida, Cancún y Hermosillo.

### Mapeo de SKU
- 65 filas para 70 SKUs:
  - 5 SKU POS **sin fila**: CN-00001, 011, 021, 031, 041.
  - 5 filas con `sku_erp = null`: CN-00006, 016, 026, 036, 046.
  - 38 filas con `handle = null`, que es correcto para lo que no se vende online.
- En las 60 filas completas, el número del SKU POS (`CN-00NNN`) es igual al número del SKU ERP (`ERP-PROV-MX-NNN-X`). En los 27 handles, el handle es `slug(nombre ERP)-NNN` sin excepción. Los 70 números del catálogo cubren exactamente los 70 del POS.
- Los 10 huecos se resuelven por número. Lo confirma que los 4 handles de los SKUs con ERP nulo coinciden con el nombre del ERP de ese número (p. ej., `premium-cafe-molido-026` ↔ "Premium Cafe Molido").

## 4. `ecommerce_orders.parquet` (Shopify)

- 9,947 órdenes, una línea por orden; `order_id` único; sin nulos salvo `customer_rfc` (8,462 nulos, 85%).
- `fecha`: formato único, segundos siempre en 00, 2025-04-01 a 2026-03-31. Zona horaria no declarada.
- `currency`: MXN 6,942 · USD 2,535 · EUR 470. `amount` en la moneda de la orden (3.85–5,016.20).
- 33 handles, todos de café en grano, café molido o mercancía; 6 no están en `sku_mappings` (`americano-cafe-grano-032`, `americano-cafe-molido-052`, `gourmet-cafe-molido-041`, `molinillo-mercancia-030`, `selección-cafe-molido-013`, `termo-mercancia-031`: 1,849 órdenes, 18.6%), pero los 33 coinciden con `slug(nombre)-NNN` del catálogo.
- Sin duplicados por (email, fecha, handle). 2 pares con la misma fecha, handle, cantidad y monto tienen emails distintos → no son duplicados.
- **PII**: nombre, email, RFC y dirección completa. `shipping_city` son valores inventados ("Vieja Sudáfrica", "Nueva Serbia"; 1,414 distintos): no sirve para geografía.
- Precio EUR convertido ≈ 7% arriba del precio MXN del mismo handle en los 12 meses; USD ≈ 1.00.

## 5. `exchange_rates.csv`

- EUR vale exactamente `22.0` en 63 días (17%), entre 2025-05-20 y 2026-03-29; el resto trae 4 decimales. Es un tope o clip del proveedor: el valor real esos días era ≥22, así que el ingreso EUR sale ligeramente subestimado. El impacto es menor: EUR = 214,922 MXN, 5.1% del e-commerce.

## 6. ¿Cuentan la misma historia? — No

| Chequeo | Resultado | Certeza |
|---|---|---|
| Autocorrelación del stock día a día (t vs t-1) | 0.001 | [Seguro] |
| Correlación Δstock diario vs unidades netas vendidas ese día | 0.0004 | [Seguro] |
| Días con venta en que el stock **no** bajó | 5,874 de 11,558 (51%) | [Seguro] |
| Días con Δstock > 0 (reabasto) | 47% de los días; media +30 u | [Seguro] |
| Escala | Δstock típico ±25 u/día contra 0.06 líneas POS por par/día | [Seguro] |
| Días con venta y snapshot = 0 | 619 de 12,745 | [Seguro] |
| Precio unitario POS vs costo ERP | coherente (margen 17%–217%) salvo 3 SKUs | [Seguro] |
| Precio Shopify vs POS mismo SKU | Shopify ≈ +4% sobre POS | [Seguro] |
| Tiendas / SKUs cruzan | 40/40 tiendas; 70/70 SKUs por número | [Seguro] |

El stock del ERP se comporta como ruido independiente cada día (media ≈ 40 estable en los 6 meses, sin relación con las ventas). O el snapshot no refleja las ventas del POS o los datos son sintéticos sin ese vínculo. Para el pipeline, la rotación y los quiebres se calculan con los snapshots tal cual y se deja documentado que no se pueden conciliar contra el POS.

El volumen POS (~160 líneas/día para toda la cadena, ~4 por tienda) es de órdenes de magnitud menor que el de una cafetería real. Puede ser una muestra o una extracción parcial.

## 7. Márgenes (adelanto que condiciona la pregunta 4)

Con el precio mediano de venta contra el costo vigente, 3 SKUs se venden por debajo del costo en todo el periodo:

| SKU | Producto | Precio mediano | Costo (rango) | Precio/costo |
|---|---|---|---|---|
| CN-00015 / ERP-015-D | Especial Café Molido | 362.33 | 459.84–478.30 | 0.76–0.79 |
| CN-00002 / ERP-002-B | Sándwich Comida Caliente | 131.94 | 160.97–170.65 | 0.77–0.82 |
| CN-00001 / ERP-001-A | Sándwich Comida Caliente | 74.62 | 84.00–92.65 | 0.81–0.89 |

El siguiente peor está a 1.17×. Con ruido de precio ±4.5%, ninguna otra venta cruza a negativo. CN-00001 es uno de los SKUs sin mapeo: si el mapeo por número no se aplica, desaparece de la respuesta.

## 8. Sensibilidad de la pregunta 2 (quiebres >3 días, Q1-2026)

| Regla para `N/A` dentro de una racha de ceros | Rachas ≥4 días | Tiendas |
|---|---|---|
| `N/A` rompe la racha (desconocido) | 3 | T015 (ERP-014-D, 9–12 feb), T023 (ERP-046-A, 25–28 ene), T038 (ERP-040-A, 18–21 mar) |
| `N/A` entre ceros cuenta como cero | 4 | + T016 (ERP-020-C: 0, N/A, N/A, 0 del 7 al 10 feb) |

Con "≥3 días" en vez de ">3" salen 21–26 rachas. No hay rachas de 5+ días en los 6 meses.
