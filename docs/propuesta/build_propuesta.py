"""Genera docs/PROPUESTA_CAFENORTE.pdf (2 páginas) y su diagrama de arquitectura.

Uso: python docs/propuesta/build_propuesta.py
Precios: lista pública de AWS para us-east-1, consultados el 23-sep-2026 (fuentes al pie del PDF).
"""
from pathlib import Path

import matplotlib
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
PDF_PATH = HERE.parent / "PROPUESTA_CAFENORTE.pdf"
DIAGRAM_PATH = HERE / "arquitectura.png"
# DejaVu viene dentro de matplotlib: mismo resultado en Windows, macOS y Linux.
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"

INK = "#1f2933"
ACCENT = "#0b6e4f"
MUTED = "#52606d"
LIGHT = "#e6f2ee"

# Supuestos de volumen del escenario de costo (ver tabla).
S3_GB = 50
S3_WRITE_THOUSANDS = 100
S3_READ_THOUSANDS = 1_000
LAMBDA_RUNS, LAMBDA_SECONDS, LAMBDA_GB = 30, 180, 4
ATHENA_TB = 0.05
SECRETS = 2
ECR_GB = 1
AUTHORS = 1
READERS_BASE, READERS_WIDE = 10, 40

PRICE = {
    "s3_gb": 0.023, "s3_write_k": 0.005, "s3_read_k": 0.0004, "lambda_gbs": 0.0000166667,
    "athena_tb": 5.0, "secret": 0.40, "ecr_gb": 0.10, "author": 24.0, "reader": 3.0,
}
IVA = 0.16


def draw_diagram() -> None:
    fig = Figure(figsize=(10.5, 3.3), dpi=200)
    ax = fig.add_subplot()
    ax.set_xlim(-1.5, 105.5)
    ax.set_ylim(0, 33)
    ax.axis("off")

    def box(x, y, w, h, title, detail, fill="white", edge=ACCENT):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                    linewidth=1.2, edgecolor=edge, facecolor=fill))
        ax.text(x + w / 2, y + h - 2.2, title, ha="center", va="top", fontsize=8.2, weight="bold", color=INK)
        ax.text(x + w / 2, y + h - 5.6, detail, ha="center", va="top", fontsize=6.6, color=MUTED, linespacing=1.35)

    def arrow(x1, y1, x2, y2, label=""):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=10, color=INK, lw=1))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 1.1, label, ha="center", fontsize=6, color=MUTED)

    box(0, 12, 15, 17, "Fuentes", "POS 40 tiendas\n(export diario)\nERP legacy (JSON)\nShopify (API)\nTipo de cambio\nBanxico FIX")
    box(20, 16, 15, 13, "S3 · zona raw", "archivos tal cual\ncifrado por defecto\nPII: acceso\nrestringido", fill=LIGHT)
    box(40, 16, 18, 13, "Lambda · pipeline", "el código de este repo\n(DuckDB + SQL)\n54 checks: si uno falla\nno publica")
    box(63, 16, 17, 13, "S3 · modelo", "Parquet: core + marts\nsin PII\nGlue Data Catalog", fill=LIGHT)
    box(85, 16, 19, 13, "QuickSight", "tablero único\nventas · rotación\nquiebres · margen\nvía Athena → SPICE")

    arrow(15.8, 22.5, 19.2, 22.5)
    arrow(35.8, 22.5, 39.2, 22.5)
    arrow(58.8, 22.5, 62.2, 22.5)
    arrow(80.8, 22.5, 84.2, 22.5, "Athena")

    box(40, 1.5, 18, 9, "EventBridge Scheduler", "corre el pipeline\ncada madrugada", edge=MUTED)
    box(63, 1.5, 17, 9, "CloudWatch + SNS", "correo si falla un\ncheck o no llega archivo", edge=MUTED)
    box(20, 1.5, 15, 9, "Secrets Manager", "token Shopify\ny Banxico", edge=MUTED)
    box(85, 1.5, 19, 9, "AWS Budgets", "alerta al 75% de\nUSD 200", edge=MUTED)
    arrow(49, 11.3, 49, 15.2)
    arrow(33, 11.3, 41.5, 15.2, "credenciales")
    arrow(56.5, 15.2, 66, 11.3, "logs y alertas")

    ax.text(0, 31.5, "Región us-east-1 (N. Virginia) · todo serverless: sin servidores encendidos 24/7",
            fontsize=7, color=MUTED)
    fig.savefig(DIAGRAM_PATH, bbox_inches="tight", facecolor="white")


def cost_rows() -> tuple[list[list[str]], float, float]:
    items = [
        ("S3 almacenamiento", f"{S3_GB} GB al cierre del año 1", S3_GB * PRICE["s3_gb"]),
        ("S3 solicitudes", f"{S3_WRITE_THOUSANDS}k escrituras, {S3_READ_THOUSANDS // 1000}M lecturas", S3_WRITE_THOUSANDS * PRICE["s3_write_k"] + S3_READ_THOUSANDS * PRICE["s3_read_k"]),
        ("Lambda", f"{LAMBDA_RUNS} corridas × {LAMBDA_SECONDS} s × {LAMBDA_GB} GB (sin free tier)", LAMBDA_RUNS * LAMBDA_SECONDS * LAMBDA_GB * PRICE["lambda_gbs"]),
        ("Athena", f"{int(ATHENA_TB * 1000)} GB escaneados (refresco SPICE + consultas)", ATHENA_TB * PRICE["athena_tb"]),
        ("Secrets Manager", f"{SECRETS} secretos", SECRETS * PRICE["secret"]),
        ("ECR", f"imagen del pipeline, {ECR_GB} GB", ECR_GB * PRICE["ecr_gb"]),
        ("Glue Catalog, EventBridge, SNS, CloudWatch, Budgets", "dentro de sus niveles gratuitos permanentes", 0.0),
    ]
    infra = sum(cost for _, _, cost in items)
    rows = [[name, basis, f"{cost:,.2f}"] for name, basis, cost in items]
    return rows, infra, AUTHORS * PRICE["author"]


def build_pdf() -> None:
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")

    body = ParagraphStyle("body", fontName="DejaVu", fontSize=8.4, leading=10.6, textColor=colors.HexColor(INK), alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=6.6, leading=8.2, textColor=colors.HexColor(MUTED))
    cell = ParagraphStyle("cell", parent=body, fontSize=7.4, leading=9)
    h1 = ParagraphStyle("h1", parent=body, fontName="DejaVu-Bold", fontSize=14, leading=17, textColor=colors.HexColor(ACCENT))
    h2 = ParagraphStyle("h2", parent=body, fontName="DejaVu-Bold", fontSize=9.6, leading=12, spaceBefore=5, spaceAfter=2,
                        textColor=colors.HexColor(ACCENT))

    def p(text, style=body):
        return Paragraph(text, style)

    def table(data, widths, header=True):
        wrapped = [[c if not isinstance(c, str) else Paragraph(c, cell) for c in row] for row in data]
        t = Table(wrapped, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd2d9")),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(LIGHT))]
        t.setStyle(TableStyle(style))
        return t

    rows, infra, authors_cost = cost_rows()
    base_total = infra + authors_cost + READERS_BASE * PRICE["reader"]
    wide_total = infra + authors_cost + READERS_WIDE * PRICE["reader"]
    max_readers = int((200 / (1 + IVA) - infra - authors_cost) // PRICE["reader"])

    story = [
        p("Propuesta: un solo tablero de ventas e inventario para CaféNorte", h1),
        p("Para: Dirección de CaféNorte · De: equipo de ingeniería de datos · 23 de septiembre de 2026", small),
        Spacer(1, 4),
        p("<b>Qué resolvemos.</b> Hoy cada área reporta una cifra de venta distinta porque cada una suma documentos "
          "distintos. En los datos de prueba, sumar todos los comprobantes del POS da 9.1% más que la venta neta. "
          "Proponemos un solo lugar, actualizado cada madrugada, con una definición de venta firmada por Dirección y con "
          "controles automáticos que impiden publicar cifras que no cuadren con la fuente. El prototipo ya existe y ya "
          "encontró algo: <b>3 productos se venden por debajo del costo en las 40 tiendas</b> (pérdida de ~293 mil MXN "
          "en 18 meses de datos de prueba; se confirma con datos reales en la fase 1)."),
        p("Arquitectura en AWS", h2),
        Image(str(DIAGRAM_PATH), width=18.2 * cm, height=18.2 * cm * 3.3 / 10.5 * 0.97),
        table(
            [
                ["Pieza", "Por qué esta y no otra"],
                ["S3 + Parquet", "Almacenamiento más barato de AWS; guarda la historia completa y cada archivo original para auditoría."],
                ["Lambda + DuckDB", "Reutiliza tal cual el pipeline ya probado (54 controles, 37 pruebas). Cobra solo los segundos que corre; "
                                    "no hay servidor encendido. Si el volumen crece, la misma imagen corre en ECS Fargate sin reescribir."],
                ["Athena + Glue Catalog", "SQL sobre S3 sin base de datos que administrar; se paga por dato leído (USD 5 por TB)."],
                ["QuickSight", "BI nativo de AWS con precio por usuario (lector USD 3, autor USD 24/mes); cabe en el presupuesto con decenas de lectores."],
                ["Descartado", "Redshift o RDS (servidor fijo), Glue ETL (sobrado para este volumen), AWS Transfer SFTP "
                               "(USD 0.30/h ≈ USD 219/mes solo por existir), NAT Gateway (USD 0.045/h ≈ USD 33/mes: la Lambda corre fuera de VPC)."],
            ],
            [3.2 * cm, 15 * cm],
        ),
        p("<b>Región:</b> us-east-1. QuickSight no está disponible en la región México (mx-central-1); si la ley o la "
          "política interna exige datos en México, cambia el diseño (ver preguntas).", small),
        p("Costo mensual estimado (USD, precios de lista us-east-1)", h2),
        table([["Servicio", "Supuesto de uso", "USD/mes"], *rows,
               ["Subtotal infraestructura", "", f"{infra:,.2f}"],
               ["QuickSight · escenario A", f"{AUTHORS} autor + {READERS_BASE} lectores (Dirección y regionales)", f"{authors_cost + READERS_BASE * PRICE['reader']:,.2f}"],
               ["QuickSight · escenario B", f"{AUTHORS} autor + {READERS_WIDE} lectores (+ gerentes de tienda)", f"{authors_cost + READERS_WIDE * PRICE['reader']:,.2f}"],
               ["<b>Total A / B sin IVA</b>", "", f"<b>{base_total:,.2f} / {wide_total:,.2f}</b>"],
               ["<b>Total A / B con IVA 16%</b>", "AWS México factura IVA desde 2026", f"<b>{base_total * (1 + IVA):,.2f} / {wide_total * (1 + IVA):,.2f}</b>"]],
              [5.2 * cm, 10.6 * cm, 2.4 * cm]),
        p(f"El costo depende casi solo del número de usuarios del tablero: con IVA caben hasta <b>{max_readers} lectores</b> "
          "y 1 autor dentro de USD 200. Aunque el volumen real de ventas fuera 100 veces el de la muestra, la infraestructura "
          "seguiría por debajo de USD 20. <b>Línea roja:</b> activar un usuario \"Pro\" o las preguntas en lenguaje natural de "
          "QuickSight agrega una cuota fija de USD 250/mes a la cuenta; queda bloqueado por política. No incluye horas de "
          "personas, plan de soporte de pago ni costos de extracción del POS/ERP.", body),
        p("Plan por fases", h2),
        table(
            [
                ["Fase", "Entrega", "Riesgo principal y cómo lo mitigamos"],
                ["0 · Semana 1<br/>Decisiones", "Respuestas a las preguntas de abajo; definición de venta firmada.",
                 "Sin definición única el tablero repite el problema actual → no se inicia la fase 1 sin firma."],
                ["1 · Semanas 2–4<br/>Piloto", "Cuenta configurada, carga histórica a S3, pipeline en Lambda, tablero con las 4 "
                 "preguntas. Éxito: venta de un mes cerrado cuadra con contabilidad ±0.5%.",
                 "El inventario del ERP no se mueve con las ventas (en la muestra, correlación ≈ 0) → conteo físico en 3 tiendas "
                 "para validar antes de usar rotación y quiebres para decidir."],
                ["2 · Semanas 5–8<br/>Automatización", "Extracción diaria del POS y ERP, API de Shopify, tipo de cambio Banxico, "
                 "alertas por correo, 40 tiendas.",
                 "POS/ERP legacy sin API → depender de exportaciones manuales. Mitigación: script de carga en cada origen y "
                 "alerta si el archivo no llega antes de las 6:00."],
                ["3 · Semanas 9–10<br/>Adopción", "Accesos por rol, capacitación, glosario de métricas, revisión de costo real vs estimado.",
                 "Datos personales de clientes (LFPDPPP) → nunca salen de la zona raw; acceso solo a 1 rol técnico."],
            ],
            [2.6 * cm, 7.3 * cm, 8.3 * cm],
        ),
        p("Riesgos transversales", h2),
        table(
            [
                ["Riesgo", "Mitigación"],
                ["Un control de calidad falla de madrugada.", "El tablero conserva la última versión válida y marca la fecha; llega un correo con el control que falló. Nunca se publica una cifra que no cuadra."],
                ["El costo se sale del presupuesto.", "Alerta de AWS Budgets al 75% (USD 150); roles Pro y Q&amp;A bloqueados; revisión mensual en la fase 3."],
                ["Depender de una sola persona.", "Todo el código, SQL y controles vive en un repositorio con pruebas automáticas y documentación de cada supuesto."],
                ["Las cifras de prueba no representan la operación.", "El piloto se valida contra contabilidad y conteo físico antes de usarse para decidir."],
            ],
            [5.2 * cm, 13 * cm],
        ),
        p("Preguntas antes de firmar", h2),
        table(
            [
                ["#", "Pregunta", "Qué cambia con la respuesta"],
                ["1", "¿Qué es \"venta\" para Dirección: neta de devoluciones, con o sin IVA?", "La cifra oficial; hoy hay hasta 9.1% de diferencia."],
                ["2", "¿Cómo sale hoy la información del POS y del ERP (archivo, API, base de datos) y quién la opera?", "Esfuerzo y fecha de la fase 2."],
                ["3", "El archivo de ventas de prueba trae unas 4 líneas por tienda al día: ¿es una muestra o el total?", "Validez de todas las cifras del piloto."],
                ["4", "¿Por qué el ERP solo registra inventario de 45% de las combinaciones producto×tienda y a qué hora toma la foto?", "Si la rotación y los quiebres son confiables."],
                ["5", "¿Quién confirma que el número de SKU es el mismo producto en POS, ERP y Shopify?", "10 productos hoy se ligan por inferencia."],
                ["6", "¿Cuántas personas verán el tablero y cuántas lo editarán?", "El costo mensual (escenario A o B)."],
                ["7", "¿Hay obligación de guardar datos en México? ¿Los USD 200 incluyen IVA?", "Región y margen de presupuesto."],
                ["8", "¿Desde qué almacén o tienda se surte Shopify?", "Si la rotación puede incluir la venta en línea."],
            ],
            [0.6 * cm, 10.4 * cm, 7.2 * cm],
        ),
        Spacer(1, 3),
        p("Precios de lista consultados el 23-sep-2026 en aws.amazon.com (páginas de precios de Lambda, Athena, Glue, "
          "EventBridge, Secrets Manager, ECR, Transfer Family, VPC, Budgets, QuickSight y tax-help/mexico); región de "
          "QuickSight en docs.aws.amazon.com/quick. La tabla oficial de S3 no se pudo leer automáticamente: USD 0.023/GB y "
          "solicitudes tomados de CloudZero (actualizado 30-jul-2026) y de SNS de CloudZero (29-jul-2026); a validar en la "
          "calculadora de AWS antes de firmar.", small),
    ]

    doc = SimpleDocTemplate(str(PDF_PATH), pagesize=letter, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            topMargin=1.3 * cm, bottomMargin=1.2 * cm,
                            title="Propuesta CaféNorte — tablero de ventas e inventario", author="Equipo de datos")
    doc.build(story)


if __name__ == "__main__":
    draw_diagram()
    build_pdf()
    print(PDF_PATH)
