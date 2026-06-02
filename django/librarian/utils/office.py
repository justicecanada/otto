import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from structlog import get_logger

from otto.utils.common import get_temp_dir

logger = get_logger(__name__)

LEGACY_WORD_MIME_TYPES = frozenset({"application/msword"})
WORDPROCESSINGML_DOCUMENT_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class OfficeConversionError(Exception):
    """Raised when LibreOffice-based document conversion fails."""


def get_soffice_binary() -> str:
    soffice_binary = shutil.which("soffice")
    if soffice_binary:
        return soffice_binary

    raise OfficeConversionError(
        "LibreOffice conversion requires the `soffice` binary, but it is not installed in this runtime."
    )


def convert_office_bytes_with_soffice(
    content: bytes,
    *,
    source_suffix: str,
    target_extension: str,
    source_filename: str | None = None,
    target_filter: str | None = None,
    timeout_seconds: int = 120,
) -> bytes:
    """Convert office document bytes using headless LibreOffice."""
    if not content:
        raise OfficeConversionError("Cannot convert an empty office document.")

    normalized_source_suffix = _normalize_extension(source_suffix)
    normalized_target_extension = _normalize_extension(target_extension)
    source_name = Path(source_filename or f"document{normalized_source_suffix}")
    stem = source_name.stem or "document"
    convert_to_value = normalized_target_extension.lstrip(".")
    if target_filter:
        convert_to_value = f"{convert_to_value}:{target_filter}"

    temp_root = get_temp_dir()
    with (
        tempfile.TemporaryDirectory(
            prefix="soffice_profile_", dir=temp_root
        ) as user_profile_dir,
        tempfile.TemporaryDirectory(
            prefix="soffice_convert_", dir=temp_root
        ) as conversion_dir,
    ):
        conversion_dir_path = Path(conversion_dir)
        input_path = conversion_dir_path / f"{stem}{normalized_source_suffix}"
        input_path.write_bytes(content)

        env = os.environ.copy()
        env["HOME"] = user_profile_dir
        env["TMPDIR"] = conversion_dir
        env.setdefault("SAL_USE_VCLPLUGIN", "svp")

        soffice_command = [
            get_soffice_binary(),
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation={Path(user_profile_dir).as_uri()}",
            "--convert-to",
            convert_to_value,
            "--outdir",
            conversion_dir,
            str(input_path),
        ]

        try:
            completed = subprocess.run(
                soffice_command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise OfficeConversionError(
                "LibreOffice conversion requires the `soffice` binary, but it is not installed in this runtime."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OfficeConversionError(
                "LibreOffice conversion timed out before producing an output file."
            ) from exc

        if completed.returncode != 0:
            raise OfficeConversionError(
                "LibreOffice conversion failed"
                f" (exit code {completed.returncode}). "
                f"stdout: {completed.stdout.strip()} stderr: {completed.stderr.strip()}"
            )

        output_path = _resolve_converted_output(
            conversion_dir_path, stem, normalized_target_extension
        )
        if output_path is None or not output_path.exists():
            raise OfficeConversionError(
                "LibreOffice conversion completed without producing the expected output file. "
                f"stdout: {completed.stdout.strip()} stderr: {completed.stderr.strip()}"
            )

        converted_bytes = output_path.read_bytes()
        logger.info(
            "Converted office document with LibreOffice",
            source_filename=source_name.name,
            target_filename=output_path.name,
            source_suffix=normalized_source_suffix,
            target_extension=normalized_target_extension,
            output_size_bytes=len(converted_bytes),
        )
        return converted_bytes


def convert_legacy_word_to_docx(
    content: bytes,
    *,
    source_filename: str | None = None,
    timeout_seconds: int = 120,
) -> bytes:
    return convert_office_bytes_with_soffice(
        content,
        source_suffix=".doc",
        target_extension=".docx",
        source_filename=source_filename,
        timeout_seconds=timeout_seconds,
    )


def convert_docx_to_pdf(
    content: bytes,
    *,
    source_filename: str | None = None,
    timeout_seconds: int = 120,
) -> bytes:
    return convert_office_bytes_with_soffice(
        content,
        source_suffix=".docx",
        target_extension=".pdf",
        source_filename=source_filename,
        timeout_seconds=timeout_seconds,
    )


def _normalize_extension(value: str) -> str:
    if not value:
        raise OfficeConversionError("Office conversion requires a file extension.")
    return value if value.startswith(".") else f".{value}"


def _resolve_converted_output(
    conversion_dir: Path, stem: str, target_extension: str
) -> Path | None:
    expected_path = conversion_dir / f"{stem}{target_extension}"
    if expected_path.exists():
        return expected_path

    matches = sorted(conversion_dir.glob(f"*{target_extension}"))
    if len(matches) == 1:
        return matches[0]

    return None
