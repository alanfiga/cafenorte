# AI_LOG 


## 1. Herramientas

| Herramienta | Uso |
|---|---|
| Claude (app Claude, sesión de agente con contenedor Linux en la nube) | Perfilado, diseño y código del pipeline, tests, documentación y propuesta. Modelo configurado en la sesión: `claude-opus-5-5` (dato de la configuración; el modelo que atiende cada turno puede variar). |
| Python 3.11, DuckDB 1.5.5, pandas 3.0.2, pyarrow 25.0.1, pytest 9.1.1, pyflakes | Dentro del contenedor de la IA: ejecución del pipeline, de los tests y del lint. |
| reportlab 4.4.10, matplotlib 3.10.9, poppler (pdftoppm, pdftotext) | Generar la propuesta en PDF y revisarla como imagen. |
| Búsqueda y lectura web de la IA | Precios de AWS y disponibilidad de servicios por región (23-sep-2026). |
| VSCode y Anaconda Prompt | Para poder modificar y validar código propuesto. |


## 2. Flujo de trabajo

1. **Perfilado antes de programar (prompt 1).** La IA descomprimió el zip y perfiló las 4 fuentes con scripts desechables de pandas. Entregó `docs/PERFILADO_FUENTES.md` y una tabla de 18 problemas con conteos.
2. **Reglas de decisión y pipeline (prompt 2).** Tú fijaste el criterio (confirmar con datos → no inventar → marcar origen → cuantificar → preguntar al cliente). La IA construyó en este orden:
   - ingesta a raw con totales de control;
   - SQL de staging, core y marts;
   - 54 checks;
   - registro de supuestos A01–A18 con impacto medido;
   - dataset sintético con resultados calculados a mano;
   - pruebas de mutación y recálculo con pandas.
   Después corrió todo sobre los datos reales.
3. **Documentación (prompt 3).** La IA verificó precios en la web, escribió la propuesta de 2 páginas, completó el README y escribió este borrador.



## 3. Prompts clave

### Prompt 1 — Perfilado

> "Antes de escribir código de pipeline, perfila cada archivo del zip: tipos, nulos, duplicados, rangos, valores raros y qué significa cada código que aparezca. Verifica que las llaves crucen entre fuentes […] y que las fuentes cuenten la misma historia; por ejemplo, que el stock baje cuando hay ventas. Si el zip trae archivos que no menciono, dime para qué sirven. Entrégame una tabla de problemas con evidencia (conteos) […]. No asumas nada en silencio."

**Qué devolvió la IA:**
- `exchange_rates.csv`, que el brief no menciona, sirve para convertir Shopify a MXN.
- `tipo_comprobante` son tipos de CFDI del SAT. Sumar todo infla la venta 9.1%.
- 4,417 valores de stock `"N/A"`.
- El mapeo de SKUs está incompleto, pero sigue una regla por número.
- El ERP solo rastrea 1,268 de 2,800 combinaciones tienda×SKU.
- El hallazgo central: **el stock del ERP no se mueve con las ventas** (autocorrelación 0.001, correlación con ventas 0.0004).
- Una tabla de 18 problemas con el tratamiento propuesto para cada uno.

**Autocrítica**: se realizó las pruebas y comprobación de los hallazgos encontrados por la IA, por lo que se validó la propuesta de supuestos para darle solución. Hay inconsistencia con los datos, pero por temas de tiempo y entrega, considero que es valido en este caso considerar la propuesta de supeustos que está realizando la IA. No obstante, en un caso más real, este punto se resolvería con preguntas directas al cliente, a los stakesholder y al negocio, para poder revisar estas inconsistencias y darles una solución más apegada a la realidad.

### Prompt 2 — Reglas de decisión y pipeline

> "Con el diagnóstico anterior, no me hagas preguntas: propón una solución para cada problema de la tabla y aplícala […] 1. Si los datos pueden confirmar la hipótesis, confírmala primero […] 2. Si no se puede confirmar, elige la opción que no invente información […] 3. Marca los registros afectados con una columna de origen […] 4. Cuantifica […] Si un supuesto cambia la respuesta de alguna pregunta, calcula la alternativa y muestra las dos. 5. Si algo no se puede resolver con datos, dilo explícitamente y conviértelo en una pregunta para el cliente."

**Qué devolvió la IA:**
- Pipeline Python + DuckDB en capas raw → stg → core → mart → audit.
- 54 checks que detienen la corrida.
- 37 tests: 16 con valores calculados a mano sobre datos sintéticos, 16 de mutación y 5 de recálculo independiente con pandas.
- Variantes publicadas donde un supuesto cambia la respuesta:
  - P1: con otro numerador, el top 10 comparte solo 2 de 10 SKUs.
  - P2: 3 tiendas confirmadas, más 1 que solo cuenta si N/A = 0.
  - P3: sensibilidad al tipo de comprobante y a la zona horaria.
  - P4: CN-00001 depende del mapeo inferido.

Dos cambios de criterio frente a la tabla del prompt 1, forzados por tu regla 2:
- La tabla proponía **corregir** la región y la zona horaria de las tiendas. El pipeline conserva el valor del ERP y agrega una propuesta marcada `propuesta_inferida`.
- La tabla proponía guardar un hash del email para contar clientes recurrentes. El pipeline no guarda ningún identificador de cliente fuera de raw.

**Autocrítica**: Se realizó la corrida del código python `-m pipeline.run` en anaconda prompt y se validó el funcionamiento del pipeline. Posteriormente, se realizaron los  test propuestos por la IA mediante correr el código `python -m pytest -q` para la validación de la calidad, el crosscheck de los datos y las pruebas sintéticas, validando el funcionamiento del código.

### Prompt 3 — Documentación y propuesta

> "README […]. Propuesta para el director de CaféNorte (máximo 2 páginas): arquitectura AWS por debajo de USD 200/mes, con precios actuales verificados en la web y no de memoria […]. Borrador de AI_LOG: solo con lo que pasó realmente en esta sesión, dejando marcado lo que yo tengo que escribir."

**Qué devolvió la IA:**
- Propuesta en PDF de 2 páginas (verificado con pypdf y revisado como imagen): S3 + Lambda/DuckDB + Athena + QuickSight en us-east-1.
- Costo: USD 57.56/mes sin IVA con 1 autor y 10 lectores; USD 171.17 con IVA y 40 lectores.
- Tope: 48 lectores dentro de USD 200 con IVA.
- README con respuestas y salvedades.
- Este borrador.

**Hallazgos de la verificación web que cambiaron el diseño:**
- QuickSight no está en la región México (mx-central-1).
- AWS México cobra IVA 16% desde 2026.
- Un usuario "Pro" o Q&A en QuickSight agrega USD 250/mes a la cuenta.
- SFTP de Transfer Family cuesta USD 0.30/h.

**Autocrítica**: Se hizo la revisión de la documentación entregada y me gustó que la IA desarrolló un código python para la elaboración de la propuesta, por lo cual puede  adaptarse mediante código y automatizarse en dado casos de cambios. No obstante, es importante considerar que la IA asumió un servidor en Virginia, el costo podría ser ligeramente menor o mayor. Por ejemplo, en el caso que se eligiera una servidor en México, el costo aumentaría aproximadamente 15 centavos de dólar. Si es importante, considerar que podría haber estas ligeras diferencias, pero para ello sería necesario resolver esas dudas personalmente con los interesados y el cliente. 

Finalmente, la  documentación entregada por la IA es bastante enriquecedora para el negocio y para mejorar la toma de decisiones, además de ser muy completa. Pero si habrá que hacer consideraciones previas más apegadas a la realidad para definir de mejor manera los puntos, el alcance y reglas de negocio que no están definidas. En mi opinión personal y autocrítica, acepto la propuesta de la IA, pero honestamente, no es algo que entregaría al final en un trabajo, si yo de primera mano no puedo tener comunicación con los interesados para poder afinar inconsistencias o reglas del negocio que se encuentran en los datos y el caso. En esta ocasión, por ser una prueba y el corto tiempo, valido y comparto los hallazgosy propuestas encontradas por la IA, pero habría consideraciones que yo haría de forma personal antes de presentar la propuesta relacionadas a las reglas de negocio y objetivos particulares de los interesados.

## 4. Errores de la IA en esta sesión

| # | Error | Cómo se detectó | Corrección |
|---|---|---|---|
| 1 | Etiquetó como **[Seguro]** que "Shopify cobra ≈ +4% sobre el POS" con evidencia de un solo SKU. | Al preparar este log se recalculó para los 33 SKUs que se venden en ambos canales. | La afirmación resultó cierta (1.039 a 1.044), pero la etiqueta no estaba justificada cuando se escribió. |
| 2 | Los reportes generados mostraban las fechas como `2026-03-31 00:00:00`. | La IA lo vio al leer `RESPUESTAS.md` después de la primera corrida. | Se leen los parámetros como fechas de Python, no como Timestamps de pandas. |
| 3 | Los totales de control de la ingesta convertían el monto con `Decimal()` sin manejar errores: un monto como `"22,00"` habría tirado la corrida con un traceback en vez de detenerla con el check `pos_campos_parseables`. | Al diseñar la prueba de mutación de un monto no parseable, antes de correrla. | Las filas no parseables se cuentan y el check las detiene. |
| 4 | Primera versión del check `q1_unidades_cuadran_con_fct` con una subconsulta redundante y difícil de leer. | Revisión de la IA antes de la primera corrida. | Se reescribió como una comparación directa. |
| 5 | En el diagrama de la propuesta, flechas entre las cajas inferiores sugerían flujos que no existen (Secrets Manager → EventBridge → CloudWatch). Además, "Q&A" salía como "Q&A;" en el PDF. | Al renderizar las páginas del PDF como imagen. | Flechas redibujadas hacia la Lambda y `&` escapado. |

**[TÚ: errores que encontraste tú y la IA no. Si no encontraste ninguno, dilo y explica cómo revisaste.]**

## 5. Límites de lo verificado

- **Precios.** La tabla oficial de S3 no se pudo leer: la página se arma con JavaScript y el proxy del contenedor bloqueó la API de precios de AWS. Los precios de S3 (USD 0.023/GB) y de SNS por correo vienen de CloudZero (actualizado en jul-2026). El resto viene de las páginas oficiales de aws.amazon.com y docs.aws.amazon.com.
- **Windows.** El pipeline solo se ejecutó en Linux (Python 3.11).
- **Independencia de las pruebas.** Los tests sintéticos y el recálculo con pandas los escribió la misma IA que escribió el pipeline. Un error de interpretación compartido pasaría por ambos.
- **Prueba de que los tests detectan errores.** La IA puso en 0 el signo de las devoluciones: 23 de 37 tests fallaron o no arrancaron, y el check `fct_venta_fisica_neta_vs_archivo` detuvo la corrida.

## 6. Autocrítica

En general, considero que la propuesta de la IA es bastante buena. No obstante, existen gaps o temas con los datos que se adoptaron los supuestos propuestos por la IA para darle solución. Por cuestiones de tiempo y de las pruebas, son necesario responder estos gaps o inconsistencias con los datos con los stakeholders, algo que haría al momento de desempeñar un proyecto. Por ejemplo, temas como las ciudades no coinciden con la descripción del negocio, el tipo de cambio de EUR/MXN, inventario de Shopify faltante, no esta completo el mapeo de Shopify, inconsistencias con las fechas de los datos entre las fuentes. Estos temas, definitivamente deben resolverse en una sesión con stakesholders y técnicos para poder asumir soluciones más concretas y no basadas en puestos. Sin  embargo, por temas de tiempo y la prueba, se tomaron los supuestos de la IA. 

Por otra parte, en cuanto al código, considero que la IA es muy buena, no obstante, siempre es importante considerar la operación del negocio en la vida real. Para ello, habría que ver y  hacer un analísis exploratorio de como se están comportando el negocio, las ventas y la carga de datos desde de las diferentes ubicaciones, desde un parte más cualitativa para hacer consideraciones en la arquitectura y data pipeline de los datos. 

La propuesta en costo por parte de la IA, es acertada. Sin embargo, si hace una consideración importante, que considero también importante para mi, el hecho que es necesario definir los usuarios a los reportes y a la arquitectura. Se consideró Quicksight como herramienta de reportería, pero quizás la empresa cuente con otro sistema de BI que no se considere dentro del presupuesto como Looker, Power BI o Tableau, que podrían reducir costos si ya se cuentan con licencias y si el número de ususarios es mayor. 

Finalmente, utilize la IA como comúnmente la uso para revisar y analizar para obtener insights relevantes para el desarrollo de propuestas. En primera instancia, propuse un prompt que analizará los datos de la fuente con el fin de ver la consistencia y veracidad de la información. Posteriormente, desarrollar con base en una necesidad y objetivo que es el cual se describe en el Brief y, finalmente, desarrollar documentación acorde a dichas necesidades. Esta propuesta por parte de la IA la consideraría como punto de partida para discutir y no como propuesta final de un proyecto, creo que hay insights muy relevantes y útiles a considerar, pero habría que definir y afinar detalles imporatantes antes de una propuesta final. Sin embargo, a manera de reto y por los tiempos se comparten resultados.

Material que la IA identifica sobre su propio trabajo, para que decidas qué usar:
- **La definición de rotación decide la P1** (2 de 10 SKUs en común entre variantes). La IA eligió una y publicó la otra, pero no la discutió contigo antes de construir.
- **Sin historial de git.** El repo se entrega sin commits, así que no se ve cómo evolucionó.
- **`report.py` y `assumptions.py` no tienen tests propios.** Los textos de la tabla de supuestos se generan con SQL embebido en Python que ningún test revisa.
- **Las etiquetas [Seguro]/[Probable]/[Suposición] las puso la IA** sobre sus propias afirmaciones. El error 1 muestra que no son infalibles.
- **Supuestos de usuarios sin validar.** El costo de la propuesta depende del número de usuarios de QuickSight (1 autor, 10–40 lectores), un supuesto que nadie de CaféNorte ha validado.
