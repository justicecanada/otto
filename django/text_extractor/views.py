import os
from datetime import datetime
from io import BytesIO

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from PyPDF2 import PdfMerger

from otto.secure_models import AccessKey
from otto.utils.common import file_size_to_string
from otto.utils.decorators import app_access_required, budget_required
from text_extractor.models import OutputFile, UserRequest

from .utils import (
    calculate_start_pages,
    create_searchable_pdf,
    create_toc_pdf,
    format_merged_file_name,
    shorten_input_name,
)

app_name = "text_extractor"


@app_access_required(app_name)
def index(request):
    from text_extractor.utils import img_extensions

    extensions = ", ".join(list(img_extensions) + [".pdf"])
    return render(request, "text_extractor/ocr.html", {"extensions": extensions})


@budget_required
def submit_document(request):

    if request.method == "POST":
        files = request.FILES.getlist("file_upload")
        print(f"Received {len(files)} files")
        access_key = AccessKey(user=request.user)

        UserRequest.grant_create_to(access_key)
        OutputFile.grant_create_to(access_key)
        user_request = UserRequest.objects.create(access_key)

        user_name = request.user.username
        user_request.name = user_name

        completed_documents = []
        all_texts = []

        merged = request.POST.get("merge_docs_checkbox", False)
        merger = PdfMerger() if merged else None
        file_names_to_merge = []

        try:
            if merged:
                start_pages = calculate_start_pages(files)
                toc_pdf_bytes = create_toc_pdf(files, start_pages)
                toc_file = InMemoryUploadedFile(
                    toc_pdf_bytes,
                    "file",
                    "toc.pdf",
                    "application/pdf",
                    toc_pdf_bytes.getbuffer().nbytes,
                    None,
                )
                files.insert(0, toc_file)

            current_time = datetime.now().strftime("%Y%m%d_%H%M%S")

            for idx, file in enumerate(files):
                file.name = shorten_input_name(file.name)
                ocr_file, txt_file = create_searchable_pdf(file, merged and idx > 0)
                all_texts.append(txt_file)

                input_name, _ = os.path.splitext(file.name)
                pdf_bytes = BytesIO()
                ocr_file.write(pdf_bytes)

                if merged:
                    file_name = input_name
                    pdf_bytes.seek(0)
                    merger.append(pdf_bytes)
                    if idx > 0:  # Exclude TOC from file names to merge
                        file_names_to_merge.append(file_name)
                else:

                    # input_name_short = shorten_input_name(input_name, max_length)

                    file_id = f"{user_name}_{current_time}_OCR_{input_name}.pdf"
                    text_id = f"{user_name}_{current_time}_{input_name}.txt"
                    file_name = f"OCR_{input_name}.pdf"
                    text_name = f"OCR_{input_name}.txt"

                    content_file = ContentFile(pdf_bytes.getvalue(), name=file_id)
                    content_text = ContentFile(txt_file, name=text_id)

                    # Ensure the filename is within the allowed length
                    if len(file_id) > 1024 or len(text_id) > 1024:
                        raise SuspiciousFileOperation(
                            "Maximum character reached: Generated filename is too long for Azure storage."
                        )

                    output_file = OutputFile.objects.create(
                        access_key,
                        file=content_file,
                        file_id=file_id,
                        file_name=file_name,
                        user_request=user_request,
                    )

                    output_text = OutputFile.objects.create(
                        access_key,
                        file=content_text,
                        file_id=text_id,
                        file_name=text_name,
                        user_request=user_request,
                    )

                    output_file.save(access_key)
                    output_text.save(access_key)
                    completed_documents.append(
                        {
                            "pdf": {
                                "file": output_file,
                                "size": file_size_to_string(output_file.file.size),
                            },
                            "txt": {
                                "file": output_text,
                                "size": file_size_to_string(output_text.file.size),
                            },
                        }
                    )

            if merged:

                formatted_merged_name = format_merged_file_name(
                    file_names_to_merge, max_length=40
                )
                merged_file_id = (
                    f"{user_name}_{current_time}_{formatted_merged_name}.pdf"
                )
                merged_text_id = (
                    f"{user_name}_{current_time}_{formatted_merged_name}.txt"
                )
                merge_file_name = f"{formatted_merged_name}.pdf"
                merged_text_name = f"{formatted_merged_name}.txt"

                merged_pdf_bytes = BytesIO()
                merger.write(merged_pdf_bytes)
                merged_pdf_file = ContentFile(
                    merged_pdf_bytes.getvalue(), name=merged_file_id
                )

                all_texts_bytes = BytesIO()
                for text in all_texts:
                    all_texts_bytes.write(text.encode())
                    all_texts_bytes.write(b"\n")
                all_texts_bytes.seek(0)
                all_texts_file = ContentFile(
                    all_texts_bytes.getvalue(), name=merged_text_id
                )

                output_file = OutputFile.objects.create(
                    access_key,
                    file=merged_pdf_file,
                    file_id=merged_file_id,
                    file_name=merge_file_name,
                    user_request=user_request,
                )

                output_text = OutputFile.objects.create(
                    access_key,
                    file=all_texts_file,
                    file_id=merged_text_id,
                    file_name=merged_text_name,
                    user_request=user_request,
                )

                output_file.save(access_key)
                output_text.save(access_key)
                completed_documents.append(
                    {
                        "pdf": {
                            "file": output_file,
                            "size": file_size_to_string(output_file.file.size),
                        },
                        "txt": {
                            "file": output_text,
                            "size": file_size_to_string(output_text.file.size),
                        },
                    }
                )

            context = {
                "ocr_docs": completed_documents,
                "user_request_id": user_request.id,
            }
            user_request.save(access_key)

            return render(request, "text_extractor/completed_documents.html", context)

        except Exception as e:
            # Improve error logging
            import traceback

            print(f"ERROR: {str(e)}")
            print(traceback.format_exc())
            return render(
                request, "text_extractor/error_message.html", {"error_message": str(e)}
            )
    else:
        return JsonResponse({"error": "Invalid request method."}, status=400)


def download_document(request, file_id, user_request_id):
    access_key = AccessKey(user=request.user)
    user_request = UserRequest.objects.get(access_key, id=user_request_id)

    try:
        output_file = user_request.output_files.get(
            access_key=access_key, file_id=file_id
        )
    except OutputFile.DoesNotExist:
        return render(request, "text_extractor/error_message.html")

    with output_file.file.open("rb") as file:
        response = HttpResponse(file.read(), content_type="application/octet-stream")
        response["Content-Disposition"] = (
            f'attachment; filename="{output_file.file_name}"'
        )
        return response
