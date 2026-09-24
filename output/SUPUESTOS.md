# Supuestos: problema → supuesto → evidencia → impacto

Generado en cada corrida desde `audit.assumption_impact`.

| # | Problema | Supuesto | Evidencia | Impacto |
|---|---|---|---|---|
| A01 | Los datos terminan antes de hoy; una ventana contada desde hoy sale vacía. | Ancla = última fecha con datos. 6 meses, trimestre y año se cuentan desde ahí. | POS 2024-10-01..2026-03-31; Shopify 2025-04-01..2026-03-31; inventario 2025-10-01..2026-03-31. | Ancla 2026-03-31. P1 2025-10-01..2026-03-31; P2 2026-01-01..2026-03-31; P3 2025-04..2026-03. |
| A02 | tipo_comprobante mezcla ventas con pagos, nómina y traslados. | Códigos CFDI del SAT: I suma, E resta, P/N/T no son venta (quedan en fct_sales con signo 0). Un código nuevo detiene la corrida. | E: 3,079 filas / 1,140,985 MXN; I: 82,518 filas / 29,503,070 MXN; N: 288 filas / 100,580 MXN; P: 451 filas / 160,501 MXN; T: 154 filas / 42,118 MXN. | Venta neta 28,362,085 MXN vs suma ingenua 30,947,254 MXN (+9.1%). P3 físico: el crecimiento cambia hasta 2.50 pp sumando todo y 1.51 pp sin restar E; ambas variantes publicadas. |
| A03 | Las devoluciones (E) no referencian la venta original. | Se restan en la fecha y tienda del egreso, con el costo vigente ese día. | 27 de 3,079 E coinciden con un I previo en tienda+SKU+cantidad+monto. | P1 sin restar devoluciones comparte 9/10 SKUs del top 10. |
| A04 | Stock 'N/A' en snapshots. | N/A = desconocido: NULL, fuera del promedio de inventario. Nunca 0. | 4,417 de 230,776 snapshots (1.9%). | P1 con N/A como 0 comparte 9/10 SKUs del top 10. |
| A05 | Un N/A dentro de una racha de ceros decide si la racha pasa de 3 días. | Principal: N/A rompe la racha. Alternativa publicada: N/A entre ceros cuenta como 0. | Rachas de ≥4 días calculadas con ambas reglas. | P2: 3 tiendas confirmadas (T015, T023, T038); 1 más solo si N/A = 0 (T016). |
| A06 | El ERP solo rastrea parte de los pares tienda×SKU que se venden. | Rotación = unidades / inventario sobre los mismos pares rastreados. | 1,268 pares con inventario de 2,800 vendidos en la ventana; 54% de las líneas POS son de pares sin inventario. | P1 con ventas de todas las tiendas en el numerador comparte 2/10 SKUs del top 10: la definición cambia la respuesta. |
| A07 | El mapeo POS↔ERP↔Shopify está incompleto. | Llave = número de SKU (CN-00NNN = ERP-PROV-MX-NNN-X = handle-NNN). Vínculos marcados explícito/inferido. | La regla se cumple en 60/60 filas con sku_erp y 27/27 handles explícitos; los 33 handles vendidos coinciden con el nombre ERP. Inferidos POS→ERP: CN-00001, CN-00006, CN-00011, CN-00016, CN-00021, CN-00026, CN-00031, CN-00036, CN-00041, CN-00046. | POS inferido: 12,349 filas / 3,615,389 MXN. Shopify inferido: 1,849 órdenes / 573,591 MXN. Solo explícito: P1 comparte 9/10 del top 10; P4 pierde CN-00001. |
| A08 | No se sabe si el precio del POS incluye IVA. | Monto = precio cobrado, costo sin IVA; no se descuenta IVA. | Ningún campo lo indica. | P4 principal: CN-00001, CN-00002, CN-00015 (120 combinaciones producto×tienda en negativo). Con IVA 16% dentro del precio: CN-00001, CN-00002, CN-00015 (120 combinaciones). Pérdida conjunta -292,792 MXN vs -436,343 MXN con IVA. |
| A09 | EUR topado en un valor exacto repetido. | Se usa la tasa publicada; las órdenes quedan marcadas fx_diario_tope_sospechoso. | 63 días con EUR=22.0000. | 88 órdenes / 37,270 MXN (0.9% del e-commerce), posiblemente subestimadas. |
| A10 | Shopify no declara zona horaria. | Hora local CDMX. Alternativa UTC publicada (UTC-6). | El campo fecha no trae offset. | Si fuera UTC, 13 órdenes (4,558 MXN) cambian de mes; el crecimiento e-commerce cambia hasta 0.90 pp. |
| A11 | Shopify trae PII (nombre, email, RFC, dirección). | La PII se queda en raw. También se descarta el proveedor del ERP (puede ser persona física). | 9,947 órdenes con nombre, email y dirección; 1,485 con RFC. | Ninguna pregunta la usa. Dos checks detienen la corrida si aparece fuera de raw. |
| A12 | shipping_city no es una ciudad real. | Se descarta; no se infiere país de envío. | 1,414 valores distintos tipo 'Vieja Sudáfrica'. | No hay análisis geográfico del e-commerce. |
| A13 | La metadata de tiendas contradice la geografía. | Se conserva el valor del ERP y se agrega una propuesta marcada 'propuesta_inferida'. | 6 tiendas con región dudosa (Monterrey y Chihuahua como 'centro'); 2 con zona America/Ojinaga en Ciudad Juárez. | Ninguna pregunta usa región ni zona horaria (las horas del POS ya son locales). |
| A14 | Las ciudades no coinciden con el brief. | Se reportan las 40 tiendas del ERP. | Ciudades en datos: CDMX, Cancún, Chihuahua, Ciudad Juárez, Guadalajara, Hermosillo, León, Mexicali, Monterrey, Mérida, Nuevo Laredo, Puebla, Querétaro, Reynosa, Tijuana. | Ninguno en las respuestas. |
| A15 | Shopify no tiene inventario en el ERP. | P1 y P2 solo cubren tiendas físicas; P3 y P4 incluyen e-commerce (en P4 como la tienda ECOMMERCE). | No hay almacén online en tiendas_info ni en los snapshots. | La rotación no incluye unidades vendidas online. |
| A16 | E-commerce no tiene mes base para el primer mes del año. | Mes fuera de la cobertura de la fuente = NULL, no 0. | Shopify empieza el 2025-04-01. | Crecimiento e-commerce de 2025-04 = NULL (sin_cobertura_mes_anterior); el total de ese mes también. |
| A17 | El stock del ERP no se mueve con las ventas del POS. | Rotación y quiebres usan los snapshots tal cual; no se reconstruye el stock desde ventas. | Autocorrelación del stock día a día 0.001; correlación Δstock vs unidades vendidas 0.0004; en 5,874 de 11,558 días con venta el stock no bajó. | P1 y P2 describen al ERP, no necesariamente al anaquel. |
| A18 | El volumen del POS es muy bajo para una cafetería. | Se usa como viene; no se extrapola. | 4.0 líneas por tienda por día. | Rotación de 6 meses entre 0.31 y 0.47 (mediana 502 días de inventario). |

## Preguntas para el cliente

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
