import os
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone

import requests
from bs4 import BeautifulSoup

from otto.secure_models import AccessKey
from otto.utils.common import file_size_to_string
from otto.utils.decorators import budget_required
from template_wizard.models import Report

from .utils import (
    convert_to_text,
    generate_atip_case_report,
    generate_general_case_report,
    generate_immigration_case_report,
)

TEMPLATE_CHOICES = {
    "general_case_report_en": {
        "title": "General case report",
        "template_file": "general_case_report_en.docx",
        "output_file": "General_Case_Report_%TIMESTAMP%.docx",
    },
    "atip_case_report_en": {
        "title": "ATIP case report",
        "template_file": "atip_case_report_en.docx",
        "output_file": "ATIP_Case_Report_%TIMESTAMP%.docx",
    },
    "immigration_case_report_en": {
        "title": "Immigration case report",
        "template_file": "immigration_case_report_en.docx",
        "output_file": "Immigration_Case_Report_%TIMESTAMP%.docx",
    },
}


def sanitize_filename(filename):
    """
    A simple function to remove or replace potentially problematic characters
    from filenames. Expand this function based on your requirements.
    """
    return filename.replace(" ", "_").replace("%", "_")


def add_report_data(request, report_id):
    import os
    from urllib.parse import unquote

    access_key = AccessKey(bypass=True)

    report = Report.objects.get(access_key, id=report_id)  # More robust error handling

    report_data = report.data if report.data else {}

    data_source = request.POST.get("data_source")
    data_items = report_data.get("submitted_documents", [])

    if data_source == "canlii":
        url_input = request.POST.get("url_input")
        if url_input:
            data_items.append({"type": "url", "value": url_input, "name": url_input})
    elif data_source == "file":
        files = request.FILES.getlist("file_upload")
        for file in files:
            # Sanitize and decode the file name
            sanitized_filename = sanitize_filename(file.name)
            decoded_filename = unquote(
                sanitized_filename
            )  # Decode URL-encoded characters

            # Construct a safe, relative file path
            file_path = os.path.join("uploads", str(report_id), decoded_filename)

            # Save the file using Django's storage system
            saved_path = default_storage.save(file_path, ContentFile(file.read()))

            data_items.append(
                {
                    "type": "file",
                    "value": saved_path,
                    "name": file.name,
                    "content_type": file.content_type,
                    "generated_report": False,
                    "index": "0",
                }
            )
    report.data = {}
    report.data["submitted_documents"] = data_items
    report.save(access_key)

    return JsonResponse({"message": "Report data updated successfully."})


def select_data(request, report):
    selected_cases_count = (
        len(report.data.get("selected_cases", [])) if report.data else 0
    )
    return {
        "selected_cases_count": selected_cases_count,
    }


def delete_report_data_item(request, report_id, item_index):
    report = Report.objects.get(id=report_id)
    if report.data and 0 <= item_index < len(report.data):
        del report.data[item_index]
        report.save()
    # Redirect back to the page where the user can see the updated list of items
    return JsonResponse({"message": "Report data updated successfully."})


# def handle_immigration_data(request):

#     # Call your sum_bcro_report function with the request
#     file_path = sum_bcro_report(request)
#     # Assuming response_data contains the path to the generated .docx file
#     file_url = default_storage.url(file_path)
#     return JsonResponse({"success": True, "file_url": file_url})


@budget_required
def generate_report(request, report_id):

    template_key = request.POST.get("template_key")
    language_dropdown = request.POST.get("language_select")
    languages_selected = (
        ["en", "fr"] if language_dropdown == "both" else [language_dropdown]
    )

    access_key = AccessKey(user=request.user)
    report = Report.objects.get(access_key, id=report_id)

    # Generate the report content for each submitted document in desired languages
    for case in report.data["submitted_documents"]:
        for language in languages_selected:
            generated_report = _generate_report_content(case, template_key, language)
            report.data["generated_reports"].append(generated_report)

    report.save(access_key)

    html_content = render_to_string(
        "template_wizard/canlii_wizard/generated_reports_results.html",
        {
            "report": report,
        },
    )

    return HttpResponse(html_content)


def _generate_report_content(case, template_key, language):

    template = TEMPLATE_CHOICES.get(template_key)

    generated_report_id = str(uuid.uuid4())

    # Get the current date
    current_date = timezone.now()

    # Generate UUID for the filename
    output_file = template["output_file"].replace(
        "%TIMESTAMP%", current_date.strftime("%Y%m%d_%H%M%S")
    )

    title = template["title"] + " " + current_date.strftime("%Y-%m-%d")

    # Define the directory path
    directory = os.path.join(
        settings.MEDIA_ROOT,
        "generated_reports",
        str(current_date.year),
        str(current_date.month),
        str(current_date.day),
    )

    # Create the directory if it doesn't exist
    if not os.path.exists(directory):
        os.makedirs(directory)

    if case["type"] == "file":
        with default_storage.open(case["value"], "rb") as file:
            text = convert_to_text(file, content_type=case["content_type"])
    elif case["type"] == "url":
        html = requests.get(case["value"]).content
        soup = BeautifulSoup(html, "html.parser")
        text = soup.find(id="originalDocument").text

    # Generate the report content as io.BytesIO
    if template_key == "immigration_case_report_en":
        report_content, citation = generate_immigration_case_report(text, language)
    elif template_key == "atip_case_report_en":
        report_content, citation = generate_atip_case_report(text, language)
    elif template_key == "general_case_report_en":
        report_content, citation = generate_general_case_report(text, language)

    title = f"{title} - {citation} - {language}"

    # Define the file path
    file_path = os.path.join(directory, generated_report_id)

    # Write content to the file
    with open(file_path, "wb") as file:
        file.write(report_content.getvalue())

    return {
        "id": generated_report_id,
        "template_key": template_key,
        "title": title,
        "file_path": file_path,
        "output_file": output_file,
        "size": file_size_to_string(os.path.getsize(file_path)),
    }


def download_generated_report(request, report_id, generated_report_id):

    access_key = AccessKey(user=request.user)
    report = Report.objects.get(access_key, id=report_id)

    # Get the generated report data
    generated_reports = report.data.get("generated_reports", [])

    # Find the generated report data by generated_report_id
    generated_report = next(
        (item for item in generated_reports if item["id"] == generated_report_id), None
    )

    if not generated_report:
        return HttpResponseNotAllowed("Invalid generated report ID")

    # Get the file path
    file_path = generated_report["file_path"]

    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()

    # Get the filename
    output_file = generated_report["output_file"]

    # Prepare the response
    response = HttpResponse(file_content, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{output_file}"'

    return response


def delete_generated_report(request, report_id, generated_report_id):

    access_key = AccessKey(user=request.user)
    report = Report.objects.get(access_key, id=report_id)

    # Get the generated report data
    generated_reports = report.data.get("generated_reports", [])

    # Find the generated report data by output_file
    generated_report = next(
        (item for item in generated_reports if item["id"] == generated_report_id), None
    )

    if not generated_report:
        return HttpResponseNotAllowed("Invalid")

    # Delete the file
    try:
        os.remove(generated_report["file_path"])
    except:
        pass

    # Remove the generated report data from the report's data
    report.data["generated_reports"] = [
        item for item in generated_reports if item["id"] != generated_report_id
    ]
    report.save(access_key)

    html_content = render_to_string(
        "template_wizard/canlii_wizard/generated_reports_results.html",
        {
            "report": report,
        },
    )

    return HttpResponse(html_content)


def pick_template(request, report):

    access_key = AccessKey(user=request.user)

    if not report.data:
        report.data = {}

    report.data.setdefault("generated_reports", [])
    report.save(access_key)

    return {"templates": TEMPLATE_CHOICES}
