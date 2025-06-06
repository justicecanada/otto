import datetime
import io
import json
import zipfile

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from rules.contrib.views import objectgetter

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
    mem_zip.seek(0)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"template_results_{session_id}_{timestamp}.zip"
    response = HttpResponse(mem_zip.read(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{zip_filename}"'
    return response
