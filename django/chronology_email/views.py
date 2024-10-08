from django.shortcuts import render

from .forms import EmailUploadForm
from .models import EmailUpload
from .utils import extract_email_date

app_name = "chronology_email"


@app_access_required(app_name)
def index(request):
    if request.method == "POST":
        form = EmailUploadForm(request.POST, request.FILES)
        if form.is_valid():
            email_instance = form.save()
            file_path = email_instance.email_file.path
            email_date = extract_email_date(file_path)

            return render(request, "email_date.html", {"email_date": email_date})
    else:
        form = EmailUploadForm()
    return render(request, "upload.html", {"form": form})
