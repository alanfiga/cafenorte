# Respuestas — CaféNorte

Ancla de la corrida: **2026-03-31** (última fecha con datos). Checks de calidad: 54/54 pasaron.

## P1. Top 10 SKUs por rotación de inventario (2025-10-01 a 2026-03-31)

Rotación = unidades netas vendidas (I − E) / inventario promedio diario, ambos sobre los pares tienda×SKU que el ERP rastrea. Solo tiendas físicas.

| # | sku | producto | rotacion | unidades_netas | inventario_prom_suma_tiendas | dias_inventario | tiendas | mapeo |
|---|---|---|---|---|---|---|---|---|
| 1 | CN-00049 | Mocha Bebidas | 0.4674 | 347 | 742.5 | 389 | 19 | explicito |
| 2 | CN-00068 | Croissant Panaderia | 0.4435 | 315 | 710.3 | 410 | 18 | explicito |
| 3 | CN-00015 | Especial Cafe Molido | 0.4327 | 296 | 684.1 | 421 | 17 | explicito |
| 4 | CN-00067 | Premium Cafe Molido | 0.4314 | 311 | 720.8 | 422 | 18 | explicito |
| 5 | CN-00036 | Gourmet Cafe Grano | 0.4266 | 292 | 684.5 | 427 | 17 | inferido |
| 6 | CN-00047 | Filtros Mercancia | 0.4234 | 302 | 713.3 | 430 | 18 | explicito |
| 7 | CN-00069 | Estándar Cafe Grano | 0.4144 | 298 | 719.1 | 439 | 18 | explicito |
| 8 | CN-00053 | Sándwich Comida Caliente | 0.409 | 272 | 665 | 445 | 17 | explicito |
| 9 | CN-00012 | Sándwich Comida Caliente | 0.4069 | 214 | 525.9 | 447 | 13 | explicito |
| 10 | CN-00052 | Americano Cafe Molido | 0.4059 | 302 | 744 | 448 | 19 | explicito |

El #10 (CN-00052, 0.40592) y el #11 (CN-00005, 0.40590) están separados por 0.00002: el último lugar del top es frágil.

Variantes (si un supuesto cambia la respuesta, aquí se ve):

| variante | ranking_sku_num | comparte_con_principal | mismo_orden |
|---|---|---|---|
| principal | [49, 68, 15, 67, 36, 47, 69, 53, 12, 52] | 10 | True |
| bruto_sin_restar_devoluciones | [68, 49, 15, 47, 67, 36, 12, 69, 59, 53] | 9 | False |
| na_como_cero | [49, 68, 15, 67, 36, 47, 69, 53, 12, 5] | 9 | False |
| numerador_todas_las_tiendas | [57, 12, 41, 8, 30, 37, 62, 15, 34, 51] | 2 | False |
| solo_mapeo_explicito | [49, 68, 15, 67, 47, 69, 53, 12, 52, 5] | 9 | False |

## P2. Tiendas con quiebres de más de 3 días (2026-01-01 a 2026-03-31)

Quiebre = snapshot con stock 0. "Más de 3 días" = al menos 4 días consecutivos en 0 dentro del trimestre. Principal: un N/A rompe la racha.

| tienda | ciudad | estado | skus_confirmados | skus_si_na_es_cero | dias_max_confirmados | dias_max_si_na_es_cero |
|---|---|---|---|---|---|---|
| T015 | Reynosa | confirmado | ERP-PROV-MX-014-D | ERP-PROV-MX-014-D | 4 | 4 |
| T023 | Cancún | confirmado | ERP-PROV-MX-046-A | ERP-PROV-MX-046-A | 4 | 4 |
| T038 | Cancún | confirmado | ERP-PROV-MX-040-A | ERP-PROV-MX-040-A | 4 | 4 |
| T016 | CDMX | posible_si_na_es_cero |  | ERP-PROV-MX-020-C |  | 4 |

Rachas:

| variante | tienda | sku_erp | producto | inicio | fin | dias_en_trimestre | dias_na | corta_en_fin_de_datos |
|---|---|---|---|---|---|---|---|---|
| na_entre_ceros_como_cero | T015 | ERP-PROV-MX-014-D | Descafeinado Cafe Grano | 2026-02-09 | 2026-02-12 | 4 | 0 | False |
| na_entre_ceros_como_cero | T016 | ERP-PROV-MX-020-C | Taza Mercancia | 2026-02-07 | 2026-02-10 | 4 | 2 | False |
| na_entre_ceros_como_cero | T023 | ERP-PROV-MX-046-A | Termo Mercancia | 2026-01-25 | 2026-01-28 | 4 | 0 | False |
| na_entre_ceros_como_cero | T038 | ERP-PROV-MX-040-A | Chocolate Bebidas | 2026-03-18 | 2026-03-21 | 4 | 0 | False |
| na_rompe_racha | T015 | ERP-PROV-MX-014-D | Descafeinado Cafe Grano | 2026-02-09 | 2026-02-12 | 4 | 0 | False |
| na_rompe_racha | T023 | ERP-PROV-MX-046-A | Termo Mercancia | 2026-01-25 | 2026-01-28 | 4 | 0 | False |
| na_rompe_racha | T038 | ERP-PROV-MX-040-A | Chocolate Bebidas | 2026-03-18 | 2026-03-21 | 4 | 0 | False |

## P3. Crecimiento mes a mes por canal (2025-04 a 2026-03, MXN)

Físico = I − E. E-commerce convertido a MXN con el tipo de cambio del día. Un mes sin cobertura de la fuente es NULL, no 0.

| mes | fisico_mxn | fisico_mom_pct | ecommerce_mxn | ecommerce_mom_pct | nota_ecommerce | total_mom_pct |
|---|---|---|---|---|---|---|
| 2025-04 | 1,532,102.35 | -4.18 | 383,213.08 |  | sin_cobertura_mes_anterior |  |
| 2025-05 | 1,639,657.5 | 7.02 | 365,088.2 | -4.73 | ok | 4.67 |
| 2025-06 | 1,577,691 | -3.78 | 339,352.27 | -7.05 | ok | -4.37 |
| 2025-07 | 1,644,239.29 | 4.22 | 350,578.49 | 3.31 | ok | 4.06 |
| 2025-08 | 1,642,587.46 | -0.1 | 343,799.79 | -1.93 | ok | -0.42 |
| 2025-09 | 1,563,129.38 | -4.84 | 345,453.79 | 0.48 | ok | -3.92 |
| 2025-10 | 1,648,071.37 | 5.43 | 376,497.18 | 8.99 | ok | 6.08 |
| 2025-11 | 1,544,897.09 | -6.26 | 359,337.61 | -4.56 | ok | -5.94 |
| 2025-12 | 1,648,254.78 | 6.69 | 346,649.16 | -3.53 | ok | 4.76 |
| 2026-01 | 1,553,376.66 | -5.76 | 344,899.14 | -0.5 | ok | -4.84 |
| 2026-02 | 1,434,264.57 | -7.67 | 323,250.56 | -6.28 | ok | -7.42 |
| 2026-03 | 1,543,956.27 | 7.65 | 350,763.45 | 8.51 | ok | 7.81 |

Sensibilidad del crecimiento (%):

| mes | fisico_principal | fisico_todos_los_tipos | fisico_solo_ingresos | ecommerce_principal | ecommerce_si_utc |
|---|---|---|---|---|---|
| 2025-04 | -4.18 | -2.38 | -3.31 |  |  |
| 2025-05 | 7.02 | 7.4 | 7.14 | -4.73 | -4.55 |
| 2025-06 | -3.78 | -4.7 | -4.04 | -7.05 | -7.36 |
| 2025-07 | 4.22 | 4.54 | 3.99 | 3.31 | 3.55 |
| 2025-08 | -0.1 | -0.64 | -0.08 | -1.93 | -1.93 |
| 2025-09 | -4.84 | -5.33 | -5.38 | 0.48 | 0.53 |
| 2025-10 | 5.43 | 7.93 | 6.94 | 8.99 | 8.89 |
| 2025-11 | -6.26 | -7.53 | -6.74 | -4.56 | -4.38 |
| 2025-12 | 6.69 | 5.85 | 6.11 | -3.53 | -3.82 |
| 2026-01 | -5.76 | -3.7 | -5 | -0.5 | -0.24 |
| 2026-02 | -7.67 | -7.97 | -7.57 | -6.28 | -6.02 |
| 2026-03 | 7.65 | 8.97 | 8.53 | 8.51 | 7.61 |

## P4. Productos con margen negativo y tiendas donde ocurre (todo el histórico)

Margen = venta neta − unidades × costo vigente a la fecha de cada venta. Shopify cuenta como la tienda ECOMMERCE.

| sku | producto | margen_mxn | margen_pct | tiendas_negativas | tiendas_que_venden |
|---|---|---|---|---|---|
| CN-00015 | Especial Cafe Molido | -211,718.58 | -30.92 | 40 | 40 |
| CN-00002 | Sándwich Comida Caliente | -64,722.88 | -28.3 | 40 | 40 |
| CN-00001 | Sándwich Comida Caliente | -16,350.21 | -12.83 | 40 | 40 |

| sku | tiendas | margen_pct_min | margen_pct_max | mapeo |
|---|---|---|---|---|
| CN-00001 | T001, T002, T003, T004, T005, T006, T007, T008, T009, T010, T011, T012, T013, T014, T015, T016, T017, T018, T019, T020, T021, T022, T023, T024, T025, T026, T027, T028, T029, T030, T031, T032, T033, T034, T035, T036, T037, T038, T039, T040 | -14.4 | -11.2 | inferido |
| CN-00002 | T001, T002, T003, T004, T005, T006, T007, T008, T009, T010, T011, T012, T013, T014, T015, T016, T017, T018, T019, T020, T021, T022, T023, T024, T025, T026, T027, T028, T029, T030, T031, T032, T033, T034, T035, T036, T037, T038, T039, T040 | -30.5 | -26.4 | explicito |
| CN-00015 | T001, T002, T003, T004, T005, T006, T007, T008, T009, T010, T011, T012, T013, T014, T015, T016, T017, T018, T019, T020, T021, T022, T023, T024, T025, T026, T027, T028, T029, T030, T031, T032, T033, T034, T035, T036, T037, T038, T039, T040 | -33.3 | -28.9 | explicito |

Líneas de venta por debajo del costo en productos que no están en la lista: 0.

Variantes:

| variante | productos_negativos | combinaciones_negativas |
|---|---|---|
| precio_incluye_iva_16 | CN-00001, CN-00002, CN-00015 | 120 |
| principal | CN-00001, CN-00002, CN-00015 | 120 |
| solo_mapeo_explicito | CN-00002, CN-00015 | 80 |
