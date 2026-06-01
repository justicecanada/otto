import hashlib
import json

from structlog import get_logger

logger = get_logger(__name__)

DERIVATION_DOC_TO_DOCX = "doc_to_docx"
DERIVATION_AZURE_OCR_PDF = "azure_ocr_pdf"
DERIVATION_IMAGE_TO_JPEG = "image_to_jpeg"
DEFAULT_DERIVATION_VERSION = "1"


def _normalize_params(params):
    return params or {}


def build_saved_file_derivative_cache_key(
    source_saved_file,
    derivation_type: str,
    derivation_version: str = DEFAULT_DERIVATION_VERSION,
    cache_params: dict | None = None,
) -> str:
    from librarian.models import SavedFile

    if not isinstance(source_saved_file, SavedFile):
        raise TypeError("source_saved_file must be a SavedFile instance")

    source_hash = source_saved_file.sha256_hash or source_saved_file.generate_hash()
    if not source_hash:
        raise ValueError("source_saved_file must have a sha256_hash")

    payload = {
        "source_sha256": source_hash,
        "derivation_type": derivation_type,
        "derivation_version": derivation_version,
        "cache_params": _normalize_params(cache_params),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_saved_file_derivative(
    source_saved_file,
    derivation_type: str,
    derivation_version: str = DEFAULT_DERIVATION_VERSION,
    cache_params: dict | None = None,
):
    from librarian.models import SavedFileDerivative

    cache_key = build_saved_file_derivative_cache_key(
        source_saved_file=source_saved_file,
        derivation_type=derivation_type,
        derivation_version=derivation_version,
        cache_params=cache_params,
    )
    return (
        SavedFileDerivative.objects.select_related("derived_saved_file")
        .filter(cache_key=cache_key)
        .first()
    )


def get_cached_derived_saved_file(
    source_saved_file,
    derivation_type: str,
    derivation_version: str = DEFAULT_DERIVATION_VERSION,
    cache_params: dict | None = None,
):
    derivative = get_saved_file_derivative(
        source_saved_file=source_saved_file,
        derivation_type=derivation_type,
        derivation_version=derivation_version,
        cache_params=cache_params,
    )
    if not derivative:
        return None

    derived_saved_file = derivative.derived_saved_file
    try:
        if derived_saved_file.file and derived_saved_file.file.storage.exists(
            derived_saved_file.file.name
        ):
            return derived_saved_file
    except Exception as exc:
        logger.warning(
            "Cached derivative file missing from storage",
            derivative_id=derivative.id,
            derived_saved_file_id=derived_saved_file.id,
            error=str(exc),
        )
    return None


def record_saved_file_derivative(
    source_saved_file,
    derived_saved_file,
    derivation_type: str,
    derivation_version: str = DEFAULT_DERIVATION_VERSION,
    derivation_params: dict | None = None,
    cache_params: dict | None = None,
):
    from librarian.models import SavedFileDerivative

    cache_key = build_saved_file_derivative_cache_key(
        source_saved_file=source_saved_file,
        derivation_type=derivation_type,
        derivation_version=derivation_version,
        cache_params=cache_params,
    )
    derivative, _created = SavedFileDerivative.objects.update_or_create(
        cache_key=cache_key,
        defaults={
            "source_saved_file": source_saved_file,
            "derived_saved_file": derived_saved_file,
            "derivation_type": derivation_type,
            "derivation_version": derivation_version,
            "derivation_params": _normalize_params(derivation_params),
        },
    )
    return derivative
