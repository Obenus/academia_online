"""Generación de certificados PDF."""
import io
from datetime import datetime


def build_certificate_pdf(academy_name, user_name, course_title, code, issued_at):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    c.setTitle(f'Certificado - {course_title}')

    c.setFillColorRGB(0.48, 0.23, 0.93)
    c.rect(0, h - 3 * cm, w, 3 * cm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont('Helvetica-Bold', 22)
    c.drawCentredString(w / 2, h - 1.8 * cm, academy_name or 'Academia Online')

    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont('Helvetica', 14)
    c.drawCentredString(w / 2, h - 5 * cm, 'Certifica que')
    c.setFont('Helvetica-Bold', 28)
    c.drawCentredString(w / 2, h - 6.5 * cm, user_name)
    c.setFont('Helvetica', 14)
    c.drawCentredString(w / 2, h - 8 * cm, 'ha completado satisfactoriamente el curso')
    c.setFont('Helvetica-Bold', 20)
    c.drawCentredString(w / 2, h - 9.5 * cm, course_title)

    fecha = issued_at.strftime('%d/%m/%Y') if issued_at else datetime.utcnow().strftime('%d/%m/%Y')
    c.setFont('Helvetica', 11)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawCentredString(w / 2, 3.5 * cm, f'Fecha de emisión: {fecha}')
    c.drawCentredString(w / 2, 2.5 * cm, f'Código de verificación: {code}')

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
