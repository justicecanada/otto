from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from django.utils.translation import gettext as _

import yaml

SUPPORTED_SKILL_FILENAMES = {"skill.md"}
IN_PROGRESS_STATUSES = {"PENDING", "INIT", "PROCESSING", "TEXT_EXTRACTED"}


class SkillImportError(ValueError):
    """Raised when an uploaded skill file or bundle cannot be imported."""


@dataclass(slots=True)
class ParsedSkillImport:
    source_type: str
    original_filename: str
    raw_name: str
    display_name: str
    description: str
    body: str
    notes: list[str] = field(default_factory=list)
    supporting_file_count: int = 0
    has_scripts: bool = False
    has_mcp: bool = False
    has_claude_md: bool = False


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<frontmatter>.*?)\r?\n---\s*(?:\r?\n(?P<body>.*))?\Z",
    re.DOTALL,
)


def parse_uploaded_skill_bytes(*, content: bytes, filename: str) -> ParsedSkillImport:
    lowered_name = (filename or "").lower()
    if lowered_name.endswith(".zip"):
        return _parse_zip_bundle(content=content, filename=filename)
    return _parse_markdown_skill(
        content=content, filename=filename, source_type="markdown"
    )


def build_display_name(raw_name: str) -> str:
    normalized = (raw_name or "").strip()
    if not normalized:
        return str(_("Imported skill"))
    if any(char.isspace() for char in normalized) or any(
        char.isupper() for char in normalized
    ):
        return normalized
    return normalized.replace("-", " ").strip().title()


def _parse_markdown_skill(
    *, content: bytes, filename: str, source_type: str
) -> ParsedSkillImport:
    text = _decode_text(content=content, filename=filename)
    metadata, body = _parse_frontmatter(text=text, filename=filename)

    raw_name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    cleaned_body = (body or "").strip()

    if not raw_name:
        raise SkillImportError(
            _("The imported skill is missing the required 'name' field.")
        )
    if not description:
        raise SkillImportError(
            _("The imported skill is missing the required 'description' field.")
        )
    if not cleaned_body:
        raise SkillImportError(
            _(
                "The imported skill is missing instruction content below the frontmatter."
            )
        )

    return ParsedSkillImport(
        source_type=source_type,
        original_filename=filename,
        raw_name=raw_name,
        display_name=build_display_name(raw_name),
        description=description,
        body=cleaned_body,
    )


def _parse_zip_bundle(*, content: bytes, filename: str) -> ParsedSkillImport:
    try:
        archive = ZipFile(io.BytesIO(content))
    except BadZipFile as exc:
        raise SkillImportError(
            _("The uploaded file is not a valid ZIP archive.")
        ) from exc

    with archive:
        file_names = [
            name
            for name in archive.namelist()
            if name and not name.endswith("/") and not name.startswith("__MACOSX/")
        ]
        skill_paths = [
            name
            for name in file_names
            if PurePosixPath(name).name.lower() in SUPPORTED_SKILL_FILENAMES
        ]

        if not skill_paths:
            raise SkillImportError(
                _(
                    "This ZIP does not contain a supported SKILL.md file. Expected one SKILL.md, Skill.md, or skill.md file."
                )
            )

        if len(skill_paths) > 1:
            raise SkillImportError(
                _(
                    "This ZIP contains more than one SKILL.md file. Otto round 1 only supports importing a single skill per upload."
                )
            )

        skill_path = skill_paths[0]
        skill_parts = PurePosixPath(skill_path).parts
        if len(skill_parts) > 2:
            raise SkillImportError(
                _(
                    "This ZIP appears to contain a multi-level skill bundle. Otto round 1 only supports a single skill at the ZIP root or in one top-level folder."
                )
            )

        skill_root = skill_parts[0] if len(skill_parts) == 2 else ""
        skill_bytes = archive.read(skill_path)
        parsed = _parse_markdown_skill(
            content=skill_bytes,
            filename=skill_path,
            source_type="zip",
        )

        supporting_files = [
            name
            for name in file_names
            if name != skill_path
            and (not skill_root or name.startswith(f"{skill_root}/"))
        ]

        normalized_supporting_paths = [PurePosixPath(name) for name in supporting_files]
        has_scripts = any(
            len(path.parts) >= 2 and path.parts[1] == "scripts"
            for path in normalized_supporting_paths
            if skill_root
        ) or any(
            path.parts and path.parts[0] == "scripts"
            for path in normalized_supporting_paths
            if not skill_root
        )
        has_mcp = any(
            path.name.lower() in {".mcp.json", "mcp.json"}
            for path in normalized_supporting_paths
        )
        has_claude_md = any(
            path.name.lower() == "claude.md" for path in normalized_supporting_paths
        )

        notes = list(parsed.notes)
        if supporting_files:
            notes.append(
                _(
                    "Supporting files from the bundle will be added to the skill's files folder and should be treated as context for adaptation."
                )
            )
        if has_scripts:
            notes.append(
                _(
                    "Bundled scripts can be kept as supporting files, but Otto will not execute them automatically. Any usable logic would need explicit adaptation to OpenAI Code Interpreter."
                )
            )
        if has_mcp:
            notes.append(
                _(
                    "This bundle includes MCP connector configuration, which Otto does not support for imported skills."
                )
            )
        if has_claude_md:
            notes.append(
                _(
                    "This bundle includes CLAUDE.md guidance. Otto will import it only as a supporting file; it is not applied automatically."
                )
            )

        parsed.notes = notes
        parsed.supporting_file_count = len(supporting_files)
        parsed.has_scripts = has_scripts
        parsed.has_mcp = has_mcp
        parsed.has_claude_md = has_claude_md
        parsed.original_filename = filename
        return parsed


def _decode_text(*, content: bytes, filename: str) -> str:
    encodings = ["utf-8-sig", "utf-8", "utf-16", "cp1252"]
    for encoding in encodings:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SkillImportError(
        _("Could not decode '{filename}' as a text skill file.").format(
            filename=filename
        )
    )


def _parse_frontmatter(*, text: str, filename: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillImportError(
            _(
                "'{filename}' must start with YAML frontmatter delimited by --- markers."
            ).format(filename=filename)
        )

    frontmatter_text = match.group("frontmatter") or ""
    body = match.group("body") or ""

    try:
        metadata = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillImportError(
            _("The YAML frontmatter in '{filename}' could not be parsed.").format(
                filename=filename
            )
        ) from exc

    if not isinstance(metadata, dict):
        raise SkillImportError(
            _(
                "The YAML frontmatter in '{filename}' must be a mapping of keys and values."
            ).format(filename=filename)
        )

    return metadata, body
