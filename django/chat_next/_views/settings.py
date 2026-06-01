"""User-level settings modal views for chat_next."""

import json

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.utils.decorators import permission_required

from chat_next.forms import (
    ChatModelSelectorForm,
    ChatSettingsForm,
    get_chat_model_selector_summary,
)
from chat_next.models import Chat, ChatSettings

logger = get_logger(__name__)


def _render_chat_model_selector(request, *, chat_id, form):
    model_id = form["chat_model"].value() or form.instance.chat_model
    reasoning_effort = (
        form["chat_reasoning_effort"].value() or form.instance.chat_reasoning_effort
    )
    return render(
        request,
        "chat_next/components/model_selector.html",
        {
            "chat_id": chat_id,
            "model_selector_form": form,
            "model_selector_summary": get_chat_model_selector_summary(
                model_id,
                reasoning_effort,
            ),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def settings_modal(request, chat_id):
    """Render the settings modal body (GET shows form, POST saves)."""
    user_settings, __ = ChatSettings.objects.get_or_create_for_user(request.user)
    active_tab = request.POST.get("active_tab") or request.GET.get("active_tab")

    if request.method == "POST":
        form = ChatSettingsForm(request.POST, instance=user_settings)
        if form.is_valid():
            form.save()
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse("")
                response["HX-Reswap"] = "none"
                response["HX-Trigger"] = "settings-saved"
                return response
            messages.success(request, _("Settings saved."))
        else:
            logger.error("Settings form errors", errors=form.errors)
            if request.headers.get("HX-Request") == "true":
                response = render(
                    request,
                    "chat_next/modals/shared/settings_content.html",
                    {
                        "settings_form": form,
                        "chat_id": chat_id,
                        "active_tab": active_tab or "settings-tab-personalization",
                        "current_url": request.get_full_path(),
                    },
                )
                response["HX-Trigger"] = "settings-save-error"
                return response
    else:
        form = ChatSettingsForm(instance=user_settings)

    return render(
        request,
        "chat_next/modals/shared/settings_content.html",
        {
            "settings_form": form,
            "chat_id": chat_id,
            "active_tab": active_tab or "settings-tab-personalization",
            "current_url": reverse("chat_next:settings_modal", args=[chat_id]),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_model_selector(request, chat_id):
    user_settings, __ = ChatSettings.objects.get_or_create_for_user(request.user)

    if request.method == "POST":
        form = ChatModelSelectorForm(
            request.POST,
            instance=user_settings,
            prefix="chat-model-selector",
        )
        if form.is_valid():
            form.save()
            response = HttpResponse("")
            response["HX-Reswap"] = "none"
            response["HX-Trigger"] = json.dumps(
                {"chat-model-selector-saved": {"chatId": str(chat_id)}}
            )
            return response

        logger.error("Chat model selector form errors", errors=form.errors)
        response = _render_chat_model_selector(request, chat_id=chat_id, form=form)
        response["HX-Retarget"] = "#chat-model-selector"
        response["HX-Reswap"] = "outerHTML"
        return response

    form = ChatModelSelectorForm(instance=user_settings, prefix="chat-model-selector")
    return _render_chat_model_selector(request, chat_id=chat_id, form=form)
