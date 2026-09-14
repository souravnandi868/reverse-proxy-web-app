"""Printable reports of saved reverse proxy settings."""

from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def build_proxy_pdf(proxies):
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=landscape(A4), rightMargin=42, leftMargin=42,
        topMargin=42, bottomMargin=42, title="Reverse proxy entries",
    )
    styles = getSampleStyleSheet()

    def paragraph(value, style="BodyText"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    def timestamp(value):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M %Z") if value else "Never"

    rows = [[paragraph(label) for label in ["Domain", "Public IP", "Backend", "SSL valid until", "Status"]]]
    count = 0
    for proxy in proxies:
        certificate = proxy.certificate_bundle
        address = proxy.backend_private_ip
        if ":" in address:
            address = f"[{address}]"
        rows.append([paragraph(value) for value in [
            proxy.domain_name,
            proxy.public_ip or "Not set",
            f"{proxy.backend_protocol}://{address}:{proxy.backend_port}",
            (str(certificate.valid_until) if certificate.valid_until else "Date not set") if certificate else "No certificate",
            "Enabled" if proxy.enabled else "Disabled",
        ]])
        count += 1
    table = Table(rows, colWidths=[document.width * fraction for fraction in [.27, .19, .27, .15, .12]], repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce2e8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story = [paragraph("Reverse proxy entries", "Title"),
             paragraph(f"Exported {timestamp(timezone.now())} | Total entries: {count}", "Normal"), Spacer(1, 18)]
    story.append(table if count else paragraph("No proxy configurations yet."))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(landscape(A4)[0] - 42, 24, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
