# CaféNorte — pipeline de ventas e inventario

Ingiere el POS de tiendas (`sales.csv`), el ERP legacy (`inventory.json`), Shopify (`ecommerce_orders.parquet`) y el tipo de cambio (`exchange_rates.csv`). Los normaliza, los concilia y persiste un modelo analítico en DuckDB, con el que responde las 4 preguntas del brief. Cada supuesto se valida en cada corrida; si deja de cumplirse, la corrida se detiene.

## Cómo correrlo (Windows, macOS o Linux)

```powershell
py -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pipeline.run            # ~5 s
python -m pytest                  # 37 tests, ~12 s
```

Los archivos fuente viven en `data/raw/`. Opciones: `--data-dir` y `--output-dir`. La salida queda en `output/`:

| Archivo | Contenido |
|---|---|
| `output/RESPUESTAS.md` | Las 4 respuestas con sus variantes de sensibilidad |
| `output/SUPUESTOS.md` | La tabla de supuestos de abajo, regenerada con los números de la corrida |
| `output/marts/*.csv` | Cada mart, más `quality_results.csv` y `assumption_impact.csv` (UTF-8 con BOM para Excel) |
| `output/cafenorte.duckdb` | Modelo completo (raw → staging → core → mart → audit) |

Si un check falla, el proceso sale con código 1 e imprime qué check falló y cuántas violaciones encontró.

## Stack y por qué

| Decisión | Razón |
|---|---|
| **DuckDB** | Motor columnar embebido: un archivo, sin servidor, lee CSV, JSON y Parquet de forma nativa. 230 k snapshots + 96 k ventas se procesan en segundos en una laptop. Costo de infraestructura: $0. |
| **SQL por capas** (`pipeline/sql/*.sql`) | La lógica de negocio queda en SQL plano, legible por un analista y portable casi sin cambios a Athena/Trino, el destino propuesto en AWS (S3 + Glue + Athena). |
| **Python** solo para orquestar, leer el JSON anidado, calcular totales de control y escribir reportes | El JSON trae el stock mezclado (int y `"N/A"`). Aplanarlo en Python conserva el tipo original de cada valor para que staging distinga 0 de desconocido. |
| **pytest** + dataset sintético + recálculo con pandas | Los tests validan números calculados a mano, no solo que el código corra. |

Descartados: Spark/Glue jobs (sobrados para <1 M filas y con costo por DPU), dbt (agrega una dependencia y un perfil de conexión sin resolver un problema que hoy tengamos; el SQL ya está en capas y migrarlo a dbt es directo si el equipo crece) y Postgres/RDS (un servidor que pagar para un volumen que cabe en memoria).

## Arquitectura del modelo

```
data/raw/*  ──►  raw        copia fiel, todo como texto; PII incluida (solo aquí)
                   │         + raw.source_totals: totales calculados del archivo con código independiente
                   ▼
                 stg         tipos, nombres homogéneos, N/A → NULL, PII descartada
                   │         checks: formato, llaves, dominios, reglas de mapeo, cobertura FX
                   ▼
                 core        dim_product, dim_store, fct_sales (POS + Shopify), fct_inventory_daily, params
                   │         checks: totales vs archivo, costo y FX asignados, cobertura de ventanas
                   ▼
                 mart        q1_*, q2_*, q3_*, q4_* (respuesta principal + variantes)
                   │         checks: marts cuadran con fct; PII no salió de raw
                   ▼
                 audit       quality_results, assumption_impact
```

Llaves de conciliación:
- **Producto**: el número de SKU (`CN-00NNN` = `ERP-PROV-MX-NNN-X` = `handle-NNN`). La regla se valida en cada corrida contra todas las filas explícitas del mapeo y contra el nombre del producto en cada handle.
- **Tienda**: `tienda_id`.
- **Moneda**: todo se lleva a MXN con el tipo de cambio del día de la orden.

Cada vínculo lleva su marca de origen (`pos_erp_link_source`, `handle_link_source`, `fx_source`, `region_source`, `timezone_source`, `stock_status`, `sales_treatment`) para poder filtrar lo inferido.

`fct_sales` conserva **todas** las filas del POS y de Shopify. Los comprobantes que no son venta (P, N, T) quedan con signo 0 y su tratamiento explícito; ninguna fila se borra.

## Cómo interpreto cada pregunta

Todas las ventanas se cuentan desde la **última fecha con datos (2026-03-31)**, no desde hoy. Contadas desde hoy, las ventanas de las preguntas 1–3 caerían casi o totalmente fuera de los datos.

| Pregunta | Interpretación |
|---|---|
| **P1. Top 10 SKUs por rotación, últimos 6 meses** | Ventana 2025-10-01..2026-03-31 (182 días, igual a la cobertura de snapshots). Rotación = unidades netas vendidas (I − E) / inventario promedio diario, ambos sobre los **mismos** pares tienda×SKU que el ERP rastrea; se agrega por SKU sumando pares. En unidades, no en valor: dentro de un mismo SKU el costo unitario es casi constante, así que el orden no cambia y no depende de supuestos de costeo. Solo tiendas físicas (Shopify no tiene inventario). También se publica `days_of_inventory` = 182 / rotación. |
| **P2. Tiendas con quiebres de más de 3 días, último trimestre** | Último trimestre calendario completo = Q1-2026. Quiebre = snapshot con stock 0. "Más de 3 días" = **al menos 4 días consecutivos en 0 dentro del trimestre** para un mismo SKU (una racha que empieza antes se recorta al trimestre). Un N/A rompe la racha; la variante con N/A = 0 se publica aparte como `posible_si_na_es_cero`. |
| **P3. Crecimiento mes a mes por canal, último año** | Abr-2025..mar-2026. Físico = I − E en MXN; e-commerce = monto × tipo de cambio del día. MoM = (mes − mes anterior) / mes anterior. Un mes sin cobertura de la fuente da NULL, no 0. Abril de e-commerce queda NULL porque Shopify empieza ese mes. También se publica el total de ambos canales. |
| **P4. Productos con margen negativo y tiendas donde ocurre** | Todo el histórico (sin ventana en el brief). Margen = venta neta − unidades × costo vigente a la fecha de cada venta (as-of sobre `cost_history`). Un producto aparece si tiene margen negativo en al menos una tienda. Se reportan su margen total y la lista de tiendas negativas. Shopify cuenta como la tienda `ECOMMERCE`. |

## Supuestos: problema → supuesto → evidencia → impacto

Reglas de decisión, en orden:
1. Si los datos pueden confirmar la hipótesis, se confirma.
2. Si no, se elige la opción que no inventa información.
3. Lo afectado se marca con una columna de origen.
4. Se cuantifica el impacto, y si el supuesto cambia una respuesta se publican las dos versiones.
5. Lo que los datos no resuelven se convierte en pregunta para el cliente.

La tabla se regenera en `output/SUPUESTOS.md` en cada corrida; estos son los números de la corrida sobre los datos entregados.

| Problema | Supuesto | Evidencia | Impacto |
|---|---|---|---|
| **A01.** Los datos terminan antes de hoy; una ventana contada desde hoy sale vacía. | Ancla = última fecha con datos. 6 meses, trimestre y año se cuentan desde ahí. | POS 2024-10-01..2026-03-31; Shopify 2025-04-01..2026-03-31; inventario 2025-10-01..2026-03-31. | Ancla 2026-03-31. P1 2025-10-01..2026-03-31; P2 2026-01-01..2026-03-31; P3 2025-04..2026-03. |
| **A02.** tipo_comprobante mezcla ventas con pagos, nómina y traslados. | Códigos CFDI del SAT: I suma, E resta, P/N/T no son venta (quedan en fct_sales con signo 0). Un código nuevo detiene la corrida. | E: 3,079 filas / 1,140,985 MXN; I: 82,518 filas / 29,503,070 MXN; N: 288 filas / 100,580 MXN; P: 451 filas / 160,501 MXN; T: 154 filas / 42,118 MXN. | Venta neta 28,362,085 MXN vs suma ingenua 30,947,254 MXN (+9.1%). P3 físico: el crecimiento cambia hasta 2.50 pp sumando todo y 1.51 pp sin restar E; ambas variantes publicadas. |
| **A03.** Las devoluciones (E) no referencian la venta original. | Se restan en la fecha y tienda del egreso, con el costo vigente ese día. | 27 de 3,079 E coinciden con un I previo en tienda+SKU+cantidad+monto. | P1 sin restar devoluciones comparte 9/10 SKUs del top 10. |
| **A04.** Stock 'N/A' en snapshots. | N/A = desconocido: NULL, fuera del promedio de inventario. Nunca 0. | 4,417 de 230,776 snapshots (1.9%). | P1 con N/A como 0 comparte 9/10 SKUs del top 10. |
| **A05.** Un N/A dentro de una racha de ceros decide si la racha pasa de 3 días. | Principal: N/A rompe la racha. Alternativa publicada: N/A entre ceros cuenta como 0. | Rachas de ≥4 días calculadas con ambas reglas. | P2: 3 tiendas confirmadas (T015, T023, T038); 1 más solo si N/A = 0 (T016). |
| **A06.** El ERP solo rastrea parte de los pares tienda×SKU que se venden. | Rotación = unidades / inventario sobre los mismos pares rastreados. | 1,268 pares con inventario de 2,800 vendidos en la ventana; 54% de las líneas POS son de pares sin inventario. | P1 con ventas de todas las tiendas en el numerador comparte 2/10 SKUs del top 10: la definición cambia la respuesta. |
| **A07.** El mapeo POS↔ERP↔Shopify está incompleto. | Llave = número de SKU (CN-00NNN = ERP-PROV-MX-NNN-X = handle-NNN). Vínculos marcados explícito/inferido. | La regla se cumple en 60/60 filas con sku_erp y 27/27 handles explícitos; los 33 handles vendidos coinciden con el nombre ERP. Inferidos POS→ERP: CN-00001, CN-00006, CN-00011, CN-00016, CN-00021, CN-00026, CN-00031, CN-00036, CN-00041, CN-00046. | POS inferido: 12,349 filas / 3,615,389 MXN. Shopify inferido: 1,849 órdenes / 573,591 MXN. Solo explícito: P1 comparte 9/10 del top 10; P4 pierde CN-00001. |
| **A08.** No se sabe si el precio del POS incluye IVA. | Monto = precio cobrado, costo sin IVA; no se descuenta IVA. | Ningún campo lo indica. | P4 principal: CN-00001, CN-00002, CN-00015 (120 combinaciones producto×tienda en negativo). Con IVA 16% dentro del precio: CN-00001, CN-00002, CN-00015 (120 combinaciones). Pérdida conjunta -292,792 MXN vs -436,343 MXN con IVA. |
| **A09.** EUR topado en un valor exacto repetido. | Se usa la tasa publicada; las órdenes quedan marcadas fx_diario_tope_sospechoso. | 63 días con EUR=22.0000. | 88 órdenes / 37,270 MXN (0.9% del e-commerce), posiblemente subestimadas. |
| **A10.** Shopify no declara zona horaria. | Hora local CDMX. Alternativa UTC publicada (UTC-6). | El campo fecha no trae offset. | Si fuera UTC, 13 órdenes (4,558 MXN) cambian de mes; el crecimiento e-commerce cambia hasta 0.90 pp. |
| **A11.** Shopify trae PII (nombre, email, RFC, dirección). | La PII se queda en raw. También se descarta el proveedor del ERP (puede ser persona física). | 9,947 órdenes con nombre, email y dirección; 1,485 con RFC. | Ninguna pregunta la usa. Dos checks detienen la corrida si aparece fuera de raw. |
| **A12.** shipping_city no es una ciudad real. | Se descarta; no se infiere país de envío. | 1,414 valores distintos tipo 'Vieja Sudáfrica'. | No hay análisis geográfico del e-commerce. |
| **A13.** La metadata de tiendas contradice la geografía. | Se conserva el valor del ERP y se agrega una propuesta marcada 'propuesta_inferida'. | 6 tiendas con región dudosa (Monterrey y Chihuahua como 'centro'); 2 con zona America/Ojinaga en Ciudad Juárez. | Ninguna pregunta usa región ni zona horaria (las horas del POS ya son locales). |
| **A14.** Las ciudades no coinciden con el brief. | Se reportan las 40 tiendas del ERP. | Ciudades en datos: CDMX, Cancún, Chihuahua, Ciudad Juárez, Guadalajara, Hermosillo, León, Mexicali, Monterrey, Mérida, Nuevo Laredo, Puebla, Querétaro, Reynosa, Tijuana. | Ninguno en las respuestas. |
| **A15.** Shopify no tiene inventario en el ERP. | P1 y P2 solo cubren tiendas físicas; P3 y P4 incluyen e-commerce (en P4 como la tienda ECOMMERCE). | No hay almacén online en tiendas_info ni en los snapshots. | La rotación no incluye unidades vendidas online. |
| **A16.** E-commerce no tiene mes base para el primer mes del año. | Mes fuera de la cobertura de la fuente = NULL, no 0. | Shopify empieza el 2025-04-01. | Crecimiento e-commerce de 2025-04 = NULL (sin_cobertura_mes_anterior); el total de ese mes también. |
| **A17.** El stock del ERP no se mueve con las ventas del POS. | Rotación y quiebres usan los snapshots tal cual; no se reconstruye el stock desde ventas. | Autocorrelación del stock día a día 0.001; correlación Δstock vs unidades vendidas 0.0004; en 5,874 de 11,558 días con venta el stock no bajó. | P1 y P2 describen al ERP, no necesariamente al anaquel. |
| **A18.** El volumen del POS es muy bajo para una cafetería. | Se usa como viene; no se extrapola. | 4.0 líneas por tienda por día. | Rotación de 6 meses entre 0.31 y 0.47 (mediana 502 días de inventario). |

### Preguntas para el cliente

- **A02.** ¿Qué tipos de comprobante cuenta cada área como venta? ¿E siempre es devolución de mercancía?
- **A06.** ¿Por qué el ERP no rastrea todos los productos en todas las tiendas?
- **A07.** Confirmar que el número de SKU es la llave común entre sistemas.
- **A08.** ¿El monto del POS incluye IVA? ¿El costo del ERP es sin IVA?
- **A09.** ¿De qué proveedor sale el tipo de cambio y por qué topa en 22.0?
- **A10.** ¿La exportación de Shopify está en UTC o en hora de la tienda?
- **A12.** ¿Existe un campo de país de envío confiable?
- **A13.** ¿'centro' es región geográfica o comercial?
- **A14.** ¿Mérida, Cancún, Puebla y Hermosillo son tiendas propias?
- **A15.** ¿Desde qué almacén o tienda se surte Shopify?
- **A17.** ¿A qué hora se toma el snapshot y qué movimientos lo alimentan?
- **A18.** ¿sales.csv es el universo completo de tickets o una muestra?

## Respuestas y salvedades

Detalle completo, con cada variante, en `output/RESPUESTAS.md`. Los códigos A01–A18 remiten a la tabla de supuestos.

### P1. Top 10 SKUs por rotación (oct-2025 a mar-2026)

| # | SKU | Producto | Rotación 6 meses | Días de inventario |
|---|---|---|---|---|
| 1 | CN-00049 | Mocha Bebidas | 0.467 | 389 |
| 2 | CN-00068 | Croissant Panadería | 0.443 | 410 |
| 3 | CN-00015 | Especial Café Molido | 0.433 | 421 |
| 4 | CN-00067 | Premium Café Molido | 0.431 | 422 |
| 5 | CN-00036 | Gourmet Café Grano (mapeo inferido) | 0.427 | 427 |
| 6 | CN-00047 | Filtros Mercancía | 0.423 | 430 |
| 7 | CN-00069 | Estándar Café Grano | 0.414 | 439 |
| 8 | CN-00053 | Sándwich Comida Caliente | 0.409 | 445 |
| 9 | CN-00012 | Sándwich Comida Caliente | 0.407 | 447 |
| 10 | CN-00052 | Americano Café Molido | 0.406 | 448 |

Salvedades:
- **La definición manda (A06).** Con las ventas de todas las tiendas en el numerador, y no solo las de los pares que el ERP rastrea, el top 10 comparte solo 2 SKUs con este.
- **El #10 es frágil.** El #11 (CN-00005) está a 0.00002. Con N/A = 0 o con solo mapeo explícito, CN-00005 entra y sale CN-00052 o CN-00036 (A04, A07).
- **El stock del ERP no refleja las ventas (A17).** Además, el volumen del POS parece una muestra (A18). Por eso las rotaciones son bajísimas (más de un año de inventario). El orden sirve para comparar productos entre sí; el nivel absoluto no sirve para decidir compras.

### P2. Tiendas con quiebres de más de 3 días (Q1-2026)

| Tienda | Ciudad | SKU | Racha | Estado |
|---|---|---|---|---|
| T015 | Reynosa | ERP-PROV-MX-014-D Descafeinado Café Grano | 9–12 feb | Confirmado |
| T023 | Cancún | ERP-PROV-MX-046-A Termo | 25–28 ene | Confirmado |
| T038 | Cancún | ERP-PROV-MX-040-A Chocolate | 18–21 mar | Confirmado |
| T016 | CDMX | ERP-PROV-MX-020-C Taza | 7–10 feb (0, N/A, N/A, 0) | Solo si N/A = 0 |

Salvedades:
- Las cuatro rachas duran exactamente 4 días. No hay ninguna de 5 o más en los 6 meses, así que la frontera "más de 3" define la respuesta: con "3 o más días" salen 21 rachas confirmadas.
- Quiebre = snapshot en 0. El stock del ERP no se mueve con las ventas (A17), así que falta confirmar con la tienda si hubo anaquel vacío.

### P3. Crecimiento mes a mes por canal (abr-2025 a mar-2026, MXN)

| Mes | Físico MXN | Físico MoM | E-commerce MXN | E-commerce MoM | Total MoM |
|---|---|---|---|---|---|
| 2025-04 | 1,532,102 | −4.18% | 383,213 | sin base | sin base |
| 2025-05 | 1,639,658 | +7.02% | 365,088 | −4.73% | +4.67% |
| 2025-06 | 1,577,691 | −3.78% | 339,352 | −7.05% | −4.37% |
| 2025-07 | 1,644,239 | +4.22% | 350,578 | +3.31% | +4.06% |
| 2025-08 | 1,642,587 | −0.10% | 343,800 | −1.93% | −0.42% |
| 2025-09 | 1,563,129 | −4.84% | 345,454 | +0.48% | −3.92% |
| 2025-10 | 1,648,071 | +5.43% | 376,497 | +8.99% | +6.08% |
| 2025-11 | 1,544,897 | −6.26% | 359,338 | −4.56% | −5.94% |
| 2025-12 | 1,648,255 | +6.69% | 346,649 | −3.53% | +4.76% |
| 2026-01 | 1,553,377 | −5.76% | 344,899 | −0.50% | −4.84% |
| 2026-02 | 1,434,265 | −7.67% | 323,251 | −6.28% | −7.42% |
| 2026-03 | 1,543,956 | +7.65% | 350,763 | +8.51% | +7.81% |

Salvedades:
- Ningún canal tiene tendencia: los meses alternan. Febrero cae en ambos canales, y es el mes más corto.
- Abril de e-commerce no tiene base porque Shopify empieza ese mes (A16).
- Sumar todos los tipos de comprobante mueve el crecimiento físico hasta 2.5 pp; no restar devoluciones, hasta 1.5 pp (A02).
- Si Shopify viene en UTC, el crecimiento de e-commerce cambia hasta 0.9 pp (A10).
- 0.9% del e-commerce se convirtió con un tipo EUR topado en 22.0 (A09).

### P4. Productos con margen negativo (todo el histórico)

| SKU | Producto | Margen MXN | Margen % | Tiendas con margen negativo |
|---|---|---|---|---|
| CN-00015 | Especial Café Molido | −211,719 | −30.9% | las 40 (de −33.3% a −28.9%) |
| CN-00002 | Sándwich Comida Caliente | −64,723 | −28.3% | las 40 (de −30.5% a −26.4%) |
| CN-00001 | Sándwich Comida Caliente | −16,350 | −12.8% | las 40 (de −14.4% a −11.2%) |

Salvedades:
- El problema es de precio de lista contra costo, no de tiendas: cada línea de venta de estos 3 productos está bajo costo, y ningún otro producto tiene una sola línea bajo costo. Ninguno se vende en Shopify.
- CN-00001 depende del mapeo inferido: con mapeo solo explícito desaparece de la respuesta (A07).
- Si el precio del POS incluye IVA, la lista no cambia, pero la pérdida conjunta pasa de 293 mil a 436 mil MXN (A08).

## Checks de calidad

Son 54 checks por corrida (`pipeline/quality.py`): 36 en staging, 13 en core y 5 en marts. Todos detienen la corrida salvo uno de aviso. Resultado en `audit.quality_results`.

- **Totales contra el archivo**:
  - `ingest.py` suma montos, cantidades y filas directamente del CSV, del parquet y del JSON, con Python y `Decimal`.
  - Esos totales se comparan contra `fct_sales` y `fct_inventory_daily`, por tipo de comprobante y por moneda.
  - La venta en MXN de Shopify se recalcula desde raw con otra consulta.
- **Supuestos**:
  - Regla del número de SKU en el mapeo y en los handles.
  - Catálogo de tipos de comprobante.
  - Stock solo entero ≥ 0 o `"N/A"`.
  - Grilla diaria completa de snapshots.
  - Cobertura de FX y de costo.
  - Ventanas cubiertas por el inventario.
  - POS solo en MXN.
  - Llaves únicas.
- **PII**: ninguna columna fuera de raw con nombre de cliente, email, RFC o dirección, y ningún valor que parezca email o RFC.
- **Marts**: la P1, la P3 y la P4 cuadran contra `fct_sales`.
- **Aviso**: `fuentes_terminan_en_la_misma_fecha`.

## Tests

- `tests/synthetic.py` construye un dataset de 3 tiendas y 4 productos que incluye a propósito cada problema del perfilado: I/E/P/N/T, N/A aislado y entre ceros, racha de exactamente 3 días, racha que cruza el inicio del trimestre, racha que termina con los datos, SKU sin fila de mapeo, `sku_erp` nulo, handle fuera del mapeo, EUR topado, orden cerca de medianoche, cambio de costo a mitad de periodo, producto con margen solo negativo si hay IVA, par vendido sin inventario, venta un día antes de la ventana, PII y metadata de tienda errónea.
- `tests/test_synthetic.py`: 16 tests contra valores calculados a mano; la aritmética queda escrita en el test.
- `tests/test_quality_gates.py`: 16 tests. Rompe cada supuesto (14 mutaciones) y verifica que lo detenga el check correcto; además fuerza una fuga de PII y un total que no cuadra.
- `tests/test_real_data_crosscheck.py`: 5 tests. Recalcula las 4 respuestas y el ancla con pandas, leyendo los archivos originales y sin reutilizar SQL del pipeline (`merge_asof` para el costo, `diff` de fechas para las rachas).

Validé que los tests detectan errores: con el signo de las devoluciones puesto en 0, 23 de los 37 tests fallan o no arrancan, porque el check `fct_venta_fisica_neta_vs_archivo` detiene la corrida antes.

## Documentos

| Archivo | Contenido |
|---|---|
| `docs/PERFILADO_FUENTES.md` | Perfilado de las 4 fuentes con conteos, antes de escribir el pipeline |
| `docs/PROPUESTA_CAFENORTE.pdf` | Propuesta de 2 páginas para Dirección: arquitectura AWS, costo, fases, riesgos y preguntas. Se regenera con `python docs/propuesta/build_propuesta.py` (requiere `pip install reportlab matplotlib`) |
| `AI_LOG.md` | Registro del uso de IA en este caso |

## Limitaciones conocidas

- El stock del ERP no se puede conciliar con las ventas (A17): la rotación y los quiebres describen lo que el ERP reporta.
- Monterrey/Chihuahua (región) y Ciudad Juárez (zona horaria) tienen una propuesta de corrección, pero no se usan hasta que el cliente las confirme.
- Solo probado en Linux con Python 3.11. Las rutas usan `pathlib`, DuckDB recibe rutas con `/`, los archivos se leen y escriben en UTF-8 explícito y la consola se reconfigura a UTF-8 para Windows.
