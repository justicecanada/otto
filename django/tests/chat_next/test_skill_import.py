from io import BytesIO
from zipfile import ZipFile

import pytest
from chat_next._utils.skill_import import SkillImportError, parse_uploaded_skill_bytes


@pytest.mark.django_db
def test_parse_uploaded_skill_bytes_accepts_markdown_skill():
    parsed = parse_uploaded_skill_bytes(
        content=(
            b"---\n"
            b"name: imported-analysis\n"
            b"description: Analyze imported material\n"
            b"---\n\n"
            b"Review the source material carefully."
        ),
        filename="SKILL.md",
    )

    assert parsed.source_type == "markdown"
    assert parsed.raw_name == "imported-analysis"
    assert parsed.display_name == "Imported Analysis"
    assert parsed.description == "Analyze imported material"
    assert parsed.body == "Review the source material carefully."
    assert parsed.supporting_file_count == 0


@pytest.mark.django_db
def test_parse_uploaded_skill_bytes_rejects_multi_level_skill_bundle():
    bundle_bytes = BytesIO()
    with ZipFile(bundle_bytes, "w") as archive:
        archive.writestr(
            "skills/translator/SKILL.md",
            "---\nname: translator\ndescription: Translate\n---\n\nTranslate text.",
        )

    with pytest.raises(SkillImportError, match="multi-level skill bundle"):
        parse_uploaded_skill_bytes(
            content=bundle_bytes.getvalue(),
            filename="translator.zip",
        )


@pytest.mark.django_db
def test_parse_uploaded_skill_bytes_tracks_supporting_bundle_notes():
    bundle_bytes = BytesIO()
    with ZipFile(bundle_bytes, "w") as archive:
        archive.writestr(
            "translator/SKILL.md",
            "---\nname: translator\ndescription: Translate\n---\n\nTranslate text.",
        )
        archive.writestr("translator/references/glossary.md", "Glossary")
        archive.writestr("translator/scripts/tool.py", "print('tool')")
        archive.writestr("translator/.mcp.json", "{}")
        archive.writestr("translator/CLAUDE.md", "Shared instructions")

    parsed = parse_uploaded_skill_bytes(
        content=bundle_bytes.getvalue(),
        filename="translator.zip",
    )

    assert parsed.source_type == "zip"
    assert parsed.supporting_file_count == 4
    assert parsed.has_scripts is True
    assert parsed.has_mcp is True
    assert parsed.has_claude_md is True
    assert any("supporting files" in note.lower() for note in parsed.notes)
    assert any("will not execute" in note.lower() for note in parsed.notes)
    assert any("mcp" in note.lower() for note in parsed.notes)
    assert any("claude.md" in note.lower() for note in parsed.notes)
