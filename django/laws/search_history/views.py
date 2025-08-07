from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from otto.utils.decorators import app_access_required

from .models import LawSearch


@app_access_required("laws")
@login_required
def clear_search_history(request):
    """Clear all search history for the current user."""
    if request.method == "POST":
        deleted_count, _ = request.user.law_searches.all().delete()

        # Add success message
        from django.contrib import messages
        from django.utils.translation import gettext as _

        messages.success(
            request,
            _("Search history cleared successfully. {} searches removed.").format(
                deleted_count
            ),
        )

        if request.headers.get("HX-Request"):
            # Return empty content to remove the search history section
            from django.http import HttpResponse

            return HttpResponse(
                ""
            )  # This will be swapped with outerHTML, removing the section

    return redirect("laws:index")


@app_access_required("laws")
def view_search(request, search_id):
    """View a specific search from history by re-running the search."""
    search_obj = get_object_or_404(LawSearch, id=search_id, user=request.user)

    from django.http import QueryDict
    from django.shortcuts import render

    from laws.forms import LawSearchForm
    from laws.views import search as laws_search
    from otto.models import OttoStatus

    # If this is an HTMX request, it means we are replaying the search to get results
    if request.headers.get("HX-Request"):
        # Create a new request with the stored search parameters
        post_data = QueryDict(mutable=True)
        for key, value in search_obj.get_form_data().items():
            if isinstance(value, list):
                for v in value:
                    post_data.appendlist(key, v)
            else:
                post_data[key] = value

        # Modify the request to simulate the original search
        original_method = request.method
        request.method = "POST"
        request.POST = post_data
        # Set a flag to indicate this is a history replay
        request._from_history = True

        # Call the main search view to get the search result response
        search_response = laws_search(request, search_obj)

        # Restore original request method
        request.method = original_method

        return search_response

    # For the initial page load, just render the form and let HTMX fetch results
    form_data = search_obj.get_form_data()
    context = {
        "form": LawSearchForm(initial=form_data),
        "hide_breadcrumbs": True,
        "query": search_obj.query,
        "last_updated": OttoStatus.objects.singleton().laws_last_refreshed,
        "history_replay": True,
        "law_search": search_obj,
        "answer": search_obj.ai_answer,
        "advanced_search": search_obj.is_advanced_search,
    }

    return render(request, "laws/laws.html", context=context)


@app_access_required("laws")
@login_required
def delete_search(request, search_id):
    """Delete a single search history entry."""
    if request.method in ("DELETE", "POST"):
        search_obj = get_object_or_404(LawSearch, id=search_id, user=request.user)
        search_obj.delete()
        # Return empty response to remove the list item via HTMX
        return HttpResponse("", status=200)
    return JsonResponse({"error": "Invalid request method."}, status=400)
