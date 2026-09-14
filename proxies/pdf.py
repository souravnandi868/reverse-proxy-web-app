"""Printable reports of saved reverse proxy settings."""

from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def build_proxy_pdf(proxy):
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=42, leftMargin=42,
        topMargin=42, bottomMargin=42, title=f"Reverse proxy: {proxy.domain_name}",
    )
    styles = getSampleStyleSheet()

    def paragraph(value, style="BodyText"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    def timestamp(value):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M %Z") if value else "Never"

    certificate = proxy.certificate_bundle
    rows = [
        ("Domain", proxy.domain_name),
        ("Public IP", proxy.public_ip or "Not set"),
        ("Incoming protocol", proxy.get_incoming_protocol_display()),
        ("Backend IP", proxy.backend_private_ip),
        ("Backend port", proxy.backend_port),
        ("Backend protocol", proxy.get_backend_protocol_display()),
        ("Configured status", "Enabled" if proxy.enabled else "Disabled"),
        ("Certificate", certificate.name if certificate else "No certificate"),
        ("SSL valid until", str(certificate.valid_until) if certificate and certificate.valid_until else "Not set"),
        ("Created", timestamp(proxy.created_at)),
        ("Updated", timestamp(proxy.updated_at)),
        ("Last applied", timestamp(proxy.last_applied_at)),
    ]
    table = Table([[paragraph(label), paragraph(value)] for label, value in rows], colWidths=[145, document.width - 145])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f6")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce2e8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story = [paragraph("Reverse proxy entry", "Title"),
             paragraph(f"Exported {timestamp(timezone.now())}", "Normal"), Spacer(1, 18), table]
    for label, value in [("NAT notes", proxy.nat_notes), ("Firewall notes", proxy.firewall_notes)]:
        story.extend([Spacer(1, 14), paragraph(label, "Heading2"), paragraph(value or "Not provided")])
    document.build(story)
    return output.getvalue()
