import os
import traceback
import uuid
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.translation import get_language
from django.utils.translation import gettext as _

import tldextract
from structlog import get_logger

logger = get_logger(__name__)


# Exact host rewrites for allowlisted domains whose apex hostname is known to be
# unreliable or misleading for Otto's non-browser fetch path. Keep this list
# intentionally small and auditable.
CONTENT_INGESTION_CANONICAL_HOSTS = {
    "fca-caf.ca": "www.fca-caf.ca",
    "fct-cf.ca": "www.fct-cf.ca",
    "tcc-cci.ca": "www.tcc-cci.ca",
    "canada.ca": "www.canada.ca",
    "cmac-cacm.ca": "www.cmac-cacm.ca",
    "manitobacourts.mb.ca": "www.manitobacourts.mb.ca",
    "nwtcourts.ca": "www.nwtcourts.ca",
}


def _build_netloc(parsed_url, hostname: str) -> str:
    netloc = hostname
    if parsed_url.port:
        netloc = f"{netloc}:{parsed_url.port}"
    if parsed_url.username:
        auth = parsed_url.username
        if parsed_url.password:
            auth = f"{auth}:{parsed_url.password}"
        netloc = f"{auth}@{netloc}"
    return netloc


def normalize_content_ingestion_url(url: str) -> str:
    """
    Normalize a content-ingestion URL according to explicit policy.

    Current policy:
    - upgrade http:// to https://
    - rewrite exact host matches using CONTENT_INGESTION_CANONICAL_HOSTS

    This function is intentionally conservative: it only mutates the scheme or
    host when policy explicitly allows it.
    """
    if not url:
        return url

    parsed_url = urlsplit(url)
    if not parsed_url.netloc:
        return url

    scheme = parsed_url.scheme.lower()
    hostname = (parsed_url.hostname or "").lower()
    canonical_host = CONTENT_INGESTION_CANONICAL_HOSTS.get(hostname, hostname)

    normalized_scheme = "https" if scheme == "http" else parsed_url.scheme
    normalized_netloc = _build_netloc(parsed_url, canonical_host)
    normalized_url = urlunsplit(
        (
            normalized_scheme,
            normalized_netloc,
            parsed_url.path,
            parsed_url.query,
            parsed_url.fragment,
        )
    )

    if normalized_url != url:
        logger.info(
            "Normalized content-ingestion URL",
            original_url=url,
            normalized_url=normalized_url,
            original_host=hostname,
            normalized_host=canonical_host,
            scheme_changed=scheme == "http",
            host_changed=canonical_host != hostname,
        )

    return normalized_url


def _is_url_allowed(url: str) -> bool:
    normalized_url = normalize_content_ingestion_url(url)

    if not normalized_url.startswith("https://"):
        return False

    hostname = urlparse(normalized_url).hostname or ""
    if not hostname:
        return False

    extracted = get_tld_extractor()(hostname)
    domain = f"{extracted.domain}.{extracted.suffix}"

    return any(
        domain == allowed_domain or domain.endswith(f".{allowed_domain}")
        for allowed_domain in settings.ALLOWED_FETCH_URLS
    )


def file_size_to_string(filesize):
    from django.utils.translation import gettext_lazy as _

    if filesize >= 1024 * 1024:
        return f"{filesize / 1024 / 1024:.2f} {_('MB')}"
    elif filesize >= 1024:
        return f"{filesize / 1024:.2f} {_('KB')}"
    else:
        return f"{filesize} {_('bytes')}"


def display_cad_cost(usd_cost):
    from django.utils.translation import get_language

    from otto.models import OttoStatus  # Need to do it here to avoid circular imports

    """
    Converts a USD cost to CAD and returns a formatted string
    """
    approx_cost_cad = float(usd_cost) * OttoStatus.objects.singleton().exchange_rate
    is_fr = (get_language() or "").lower().startswith("fr")
    if approx_cost_cad < 0.01:
        # For tiny amounts, keep the threshold text but position the $ per locale
        return "< 0.01$" if is_fr else "< $0.01"
    # Position the $ symbol per locale
    return f"{approx_cost_cad:.2f}$" if is_fr else f"${approx_cost_cad:.2f}"


def cad_cost(usd_cost):
    from otto.models import OttoStatus  # Need to do it here to avoid circular imports

    """
    Converts a USD cost to CAD and returns a float
    """
    approx_cost_cad = float(usd_cost) * OttoStatus.objects.singleton().exchange_rate
    return approx_cost_cad


def set_costs(object):
    """
    Sums cost.usd_cost from the object's cost_set and assigns total to object.usd_cost
    """
    object.usd_cost = sum([cost.usd_cost for cost in object.cost_set.all()])
    object.save()


def get_app_from_path(path):
    """
    Returns the app name from a path
    """
    from urllib.parse import urlparse

    parsed_url = urlparse(path)
    path = parsed_url.path.strip("/").split("/")
    # If the path is empty or the result is empty, return "otto"
    if not path or not path[0]:
        return "Otto"
    return path[0]


def check_url_allowed(url):
    from otto.models import BlockedURL

    normalized_url = normalize_content_ingestion_url(url)
    if not _is_url_allowed(normalized_url):
        BlockedURL.objects.create(url=normalized_url)
        return False

    return True


def generate_mailto(to, cc=None, subject="Otto", body=None):
    """
    Generates a mailto link with the provided parameters
    """
    if isinstance(to, list):
        to = ",".join(to)
    if isinstance(cc, list):
        cc = ",".join(cc)
    subject = quote(subject)
    body = quote(body)

    mailto = f"mailto:{to}?subject={subject}"
    if cc:
        mailto += f"&cc={cc}"
    if body:
        mailto += f"&body={body}"
    return mailto


def get_tld_extractor():
    """
    Returns a tldextract.TLDExtract instance with the default suffix list
    """
    return tldextract.TLDExtract(
        suffix_list_urls=[
            "file://" + os.path.join(settings.BASE_DIR, "effective_tld_names.dat")
        ],
        cache_dir=os.path.join(settings.BASE_DIR, "tld_cache"),
    )


def robust_redirect(request, redirect_url):
    """
    Checks if HTMX request and redirects accordingly
    """
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = redirect_url
        return response
    return redirect(redirect_url)


def get_temp_dir():
    """Get or create a temp directory within MEDIA_ROOT for shared access across workers."""
    temp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def generate_ai_error_summary(
    exception: Exception,
    error_id: str = None,
    include_trace: bool = None,
    plain_text: bool = False,
) -> str:
    """
    Generate a user-friendly, AI-powered error summary for non-technical users.

    Uses GPT-4.1 to create a concise, understandable explanation of the error
    with potential workarounds when possible.

    Args:
        exception: The exception that was raised
        error_id: Optional error ID for reference (will be generated if not provided)
        include_trace: Whether to include the full traceback (defaults to settings.DEBUG)
        plain_text: If True, formats for plain text (no markdown, no emoji)

    Returns:
        A formatted string with the AI-generated error summary and error ID
    """
    if error_id is None:
        error_id = str(uuid.uuid4())[:7]

    if include_trace is None:
        include_trace = settings.DEBUG

    # Get the traceback
    tb = traceback.format_exc()

    # Detect language preference using Django's get_language
    current_lang = get_language()
    is_french = current_lang and current_lang.lower().startswith("fr")
    lang = "French" if is_french else "English"

    # Adjust prompt based on plain_text flag
    formatting_instruction = (
        "- Do NOT use any markdown formatting (no bold, italic, asterisks, etc.)"
        if plain_text
        else "- You MAY use markdown formatting for emphasis (bold, italic, lists)"
    )

    # Construct the prompt for the AI
    prompt = f"""You are explaining an error to a NON-TECHNICAL END USER of a web application. Generate a brief, friendly error summary.

CRITICAL: Your response MUST be in {lang}. This is mandatory.

Context: The user is using an AI assistant web application called "Otto". They CANNOT modify backend settings, quotas, or infrastructure.

Requirements:
- Use simple, non-technical language that an end user can understand
- Be concise (2-3 sentences)
{formatting_instruction}
- You can indicate briefly what component of the system is causing the error (e.g., "AI service", "file upload", "database"), but do NOT use technical jargon
- Focus on what the USER can do themselves
- If relevant, suggest user-actionable workarounds such as:
  * Refresh the page or try again later
  * Check file format or file size
  * Try a different AI model (if rate limit or quota errors)
  * Create a new chat conversation or use a GPT-4.1 series model (if context/token limit errors)
  * For Q&A/summarization: use smaller documents; use "top excerpts" mode instead of "full documents"; "separate" full documents rather than "combine"
  * Clear browser cache or try a different browser
  * (If a database error) Try using a different library or uploading files again.
- Do NOT suggest things users cannot do (e.g., increase Azure quotas, modify server settings, contact cloud providers)
- Do NOT include technical details like stack traces, error codes, or function names
- Respond ONLY in {lang}

Error type: {type(exception).__name__}
Error message: {str(exception)}

Provide ONLY the user-friendly summary in {lang}, no preamble."""

    try:
        # Import here to avoid circular imports
        from chat.llm import OttoLLM

        llm = OttoLLM(deployment="gpt-4.1")

        # Generate the summary
        ai_summary = llm.complete(prompt)

        # Clean up the response
        ai_summary = ai_summary.strip()

        # Translatable strings (extracted outside f-strings for xgettext compatibility)
        error_occurred_msg = _("An error occurred.")
        error_id_label = _("Error ID:")

        # Format the final message based on plain_text flag
        if plain_text:
            result = error_occurred_msg + "\n\n" + ai_summary
        else:
            result = f"⚠️ {ai_summary}"

        # Add traceback if in debug mode
        if include_trace:
            result += f"\n\n```\n{tb}\n```"

        # Add error ID
        if plain_text:
            result += f"\n\n({error_id_label} {error_id})"
        else:
            result += f"\n\n_({error_id_label} {error_id})_"

        return result

    except Exception as e:
        # If AI generation fails, fall back to a simple message
        logger.warning(
            "Failed to generate AI error summary", error_id=error_id, ai_error=str(e)
        )
        # Translatable strings (extracted outside f-strings for xgettext compatibility)
        error_occurred_msg = _("An error occurred.")
        error_id_label = _("Error ID:")

        if plain_text:
            fallback = error_occurred_msg
            if include_trace:
                fallback += f"\n\n{tb}"
            fallback += f"\n\n({error_id_label} {error_id})"
        else:
            fallback = f"⚠️ {error_occurred_msg}"
            if include_trace:
                fallback += f"\n\n```\n{tb}\n```"
            fallback += f"\n\n_({error_id_label} {error_id})_"
        return fallback
