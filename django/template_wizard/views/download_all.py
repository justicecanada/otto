import datetime
import io
import json
import re
import zipfile

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from docx import Document
from html4docx import HtmlToDocx
from rules.contrib.views import objectgetter
from xhtml2pdf import pisa

from otto.utils.decorators import permission_required
from template_wizard.models import Source, TemplateSession


def _unique_filename(base, ext, existing):
    """Generate a unique filename not in existing set."""
    i = 1
    candidate = f"{base}{ext}"
    while candidate in existing:
        candidate = f"{base}_{i}{ext}"
        i += 1
    existing.add(candidate)
    return candidate


def _remove_id_attributes(html):
    """Remove all id attributes from HTML string."""
    return re.sub(r'\s*id="[^"]*"', "", html)


@require_GET
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def download_all_results(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    completed_sources = session.sources.filter(status="completed")
    if not completed_sources.exists():
        raise Http404(_("No completed sources to download."))

    mem_zip = io.BytesIO()
    filenames = set()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source in completed_sources:
            base = (
                slugify(source.filename or source.url or f"source_{source.id}")
                or f"source_{source.id}"
            )
            # HTML file
            html_content = source.template_result or ""
            html_filename = _unique_filename(base, ".html", filenames)
            zf.writestr(html_filename, html_content)
            # JSON file
            json_content = json.dumps(
                source.extracted_json, ensure_ascii=False, indent=2
            )
            json_filename = _unique_filename(base, ".json", filenames)
            zf.writestr(json_filename, json_content)
            try:
                # PDF file (now using xhtml2pdf)
                pdf_filename = _unique_filename(base, ".pdf", filenames)
                pdf_bytesio = io.BytesIO()
                pisa_status = pisa.CreatePDF(html_content, dest=pdf_bytesio)
                if pisa_status.err:
                    raise Exception(f"xhtml2pdf error: {pisa_status.err}")
                zf.writestr(pdf_filename, pdf_bytesio.getvalue())
            except Exception as e:
                # If PDF generation fails, log the error but continue
                print(f"Error generating PDF for {source.id}: {e}")
            try:
                # DOCX file (updated to use html-for-docx, stripping id attributes)
                docx_filename = _unique_filename(base, ".docx", filenames)
                docx_bytesio = io.BytesIO()
                document = Document()
                parser = HtmlToDocx()
                safe_html_content = _remove_id_attributes(html_content)
                parser.add_html_to_document(safe_html_content, document)
                document.save(docx_bytesio)
                zf.writestr(docx_filename, docx_bytesio.getvalue())
            except Exception as e:
                # If DOCX generation fails, log the error but continue
                print(f"Error generating DOCX for {source.id}: {e}")
    mem_zip.seek(0)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"template_results_{session_id}_{timestamp}.zip"
    response = HttpResponse(mem_zip.read(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{zip_filename}"'
    return response
