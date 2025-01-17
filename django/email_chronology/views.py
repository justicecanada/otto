import uuid

from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from .forms import EmailUploadForm
from .models import Attachment, Email, Participant, Thread
from .utils import clean_subject, extract_email_details, summary


def upload_emails(request):

    if request.method == "POST":
        form = EmailUploadForm(request.POST, request.FILES)
        if form.is_valid():
            thread = Thread()
            uploaded_files = request.FILES.getlist("email_files")

            for file in uploaded_files:
                email_instance = Email(file=file)
                email_details = extract_email_details(file)
                email_instance.sender = email_details.get("sender")
                email_instance.sent_date = email_details.get("sent_date")
                email_instance.preview_text = email_details.get("preview_context")
                email_instance.subject = email_details.get("subject")
                email_instance.thread = thread

                if email_instance.sent_date:
                    thread.save()
                    email_instance.save()

                attachments = email_details.get("attachments")
                participants = email_details.get("participants")

                for attachment in attachments:
                    a = Attachment(
                        name=attachment.get("name"),
                        url=attachment.get("url"),
                        mime=attachment.get("mime"),
                    )
                    a.email = email_instance
                    a.save()

                for participant in participants:
                    p = Participant(email_address=participant)
                    p.email = email_instance
                    p.save()

        form = EmailUploadForm()
    else:
        form = EmailUploadForm()

    threads = Thread.objects.all()

    result = []
    for thread in threads:
        data = {
            "id": thread.id,
            "created_at": thread.created_at,
            "subject": "",
            "summary": "",
            "emails": [],
        }
        emails = thread.emails.all().order_by("-sent_date")
        e = []
        total_attachments = 0
        total_participants = 0
        for email in emails:
            item = {
                "id": email.id,
                "sent_date": email.sent_date,
                "preview_text": email.preview_text,
                "sender": email.sender,
                "attachments": [],
                "participants": [],
            }
            attachments = email.attachments.all().values()
            participants = email.participants.all().values()
            item["attachments"] = list(attachments)
            item["participants"] = list(participants)
            total_attachments += len(attachments)
            total_participants += len(participants)
            e.append(item)
        data["emails"] = e
        data["subject"] = clean_subject(emails.earliest("sent_date").subject)
        data["summary"] = summarize_thread(emails=emails)
        data["total_attachments"] = total_attachments
        data["total_participants"] = total_participants
        data["last_sent_date"] = emails.aggregate(Max("sent_date"))
        result.append(data)

    print(result)

    return render(request, "upload.html", {"form": form, "emails_by_thread": result})


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
                email_instance.unique_participants = email_details.get(
                    "unique_participants"
                )
                email_instance.save()

    return redirect("email_chronology:index")


def delete_email(request, email_id):
    if request.method == "POST":
        email = get_object_or_404(Email, id=email_id)
        email.delete()
    return redirect("email_chronology:index")


def delete_thread(request, thread_id):
    if request.method == "POST":
        Email.objects.filter(thread_id=thread_id).delete()
    return redirect("email_chronology:index")


def summarize_thread(emails):
    combined_text = " ".join(
        email.preview_text for email in emails if email.preview_text
    )
    return summary(combined_text)
