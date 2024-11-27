import uuid

from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from .forms import EmailUploadForm
from .models import Email
from .utils import extract_email_details


def upload_emails(request):
    email_instances = []
    thread_id = uuid.uuid4()

    if request.method == "POST":
        form = EmailUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_files = request.FILES.getlist("email_files")

            for file in uploaded_files:
                email_instance = Email(file=file, thread_id=thread_id)
                email_details = extract_email_details(file)
                email_instance.sender = email_details.get("sender")
                email_instance.receiver = email_details.get("receiver")
                email_instance.sent_date = email_details.get("sent_date")
                email_instance.preview_text = email_details.get("preview_context")
                email_instance.attachment_count = email_details.get(
                    "attachment_count", 0
                )

                if email_instance.sent_date:
                    email_instance.save()
                    email_instances.append(email_instance)
    else:
        form = EmailUploadForm()

    threads = (
        Email.objects.values("thread_id")
        .annotate(
            total_emails=Count("id"),
            unique_participants=Count("sender", distinct=True)
            + Count("receiver", distinct=True),
            total_attachments=Sum("attachment_count"),
            last_email_date=Max("sent_date"),
        )
        .distinct()
    )

    emails_by_thread = {
        thread["thread_id"]: {
            "emails": Email.objects.filter(thread_id=thread["thread_id"]).order_by(
                "-sent_date"
            ),
            "total_emails": thread["total_emails"],
            "unique_participants": thread["unique_participants"],
            "total_attachments": thread["total_attachments"],
            "last_email_date": thread["last_email_date"],
        }
        for thread in threads
    }

    return render(
        request, "upload.html", {"form": form, "emails_by_thread": emails_by_thread}
    )


def add_emails_to_thread(request, thread_id):
    if request.method == "POST":
        form = EmailUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_files = request.FILES.getlist("msg_files")

            for file in uploaded_files:
                email_instance = Email(thread_id=thread_id, file=file)
                email_details = extract_email_details(file)
                email_instance.sender = email_details.get("sender")
                email_instance.receiver = email_details.get("receiver")
                email_instance.sent_date = email_details.get("sent_date")
                email_instance.preview_text = email_details.get("preview_context")
                email_instance.attachment_count = email_details.get(
                    "attachment_count", 0
                )
                email_instance.save()

    return redirect("chronology_email:index")


def delete_email(request, email_id):
    if request.method == "POST":
        email = get_object_or_404(Email, id=email_id)
        email.delete()
    return redirect("chronology_email:index")


def delete_thread(request, thread_id):
    if request.method == "POST":
        Email.objects.filter(thread_id=thread_id).delete()
    return redirect("chronology_email:index")
