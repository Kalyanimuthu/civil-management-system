from django.template.loader import render_to_string
from django.http import HttpResponse
from weasyprint import HTML
from django.conf import settings
import os


def render_to_pdf_weasy(template_src, context_dict={}):
    html_string = render_to_string(template_src, context_dict)

    # Base URL is IMPORTANT for static files
    base_url = settings.BASE_DIR

    pdf_file = HTML(string=html_string, base_url=base_url).write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="report.pdf"'
    return response