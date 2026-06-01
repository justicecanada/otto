import json
import os
from string import Formatter
from uuid import uuid4

import polib
import requests
from structlog import get_logger

logger = get_logger(__name__)


class LocaleTranslator:
    def __init__(self, key: str, region: str | None, endpoint: str) -> None:
        self.key = key
        self.region = region
        self.endpoint = endpoint

    def update_translations(self, locale_dir) -> None:
        translations_file = os.path.join(locale_dir, "translation", "translations.json")
        translations = self.__load_translations(translations_file)

        self.__update_po_file(locale_dir, translations)

        self.__save_translations(translations_file, translations)

    # Might be better to move this in a general translation class with all other translation methods.
    def translate_text(self, text: str) -> str:
        # Build the request
        params = {"api-version": "3.0", "to": "fr-ca"}

        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid4().hex),
            # Visual Studio Enterprise
            "X-MS-CLIENT-PRINCIPAL-NAME": "41ede1ad-d5e6-4b6f-bd8e-979eb3813b47",
        }
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        body = [{"Text": text}]

        # Send the request and get response
        url = f"{self.endpoint.rstrip('/')}/translator/text/v3.0/translate"
        response = requests.post(url, params=params, headers=headers, json=body)

        if not response.ok:
            raise RuntimeError(
                "Translator request failed "
                f"(status={response.status_code}): {response.text}"
            )

        try:
            payload = response.json()
            translation = payload[0]["translations"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected Translator response format: {response.text}"
            ) from exc

        return translation

    def __load_translations(self, translations_file):
        with open(translations_file, "r", encoding="utf-8") as json_file:
            translations = json.load(json_file)
        return translations

    def __save_translations(self, translations_file, translations):
        with open(translations_file, "w", encoding="utf-8") as json_file:
            json.dump(translations, json_file, ensure_ascii=False, indent=4)

    def __update_po_file(self, dir, translations_reference):
        po_file_path = os.path.join(dir, "fr", "LC_MESSAGES", "django.po")
        po_file = polib.pofile(po_file_path)

        valid_entries = [entry for entry in po_file if not entry.obsolete]
        logger.debug(f"Loaded {len(valid_entries)} entries.")

        for entry in valid_entries:
            translation_id = entry.msgid
            translation_entry = translations_reference.get(translation_id, {})
            fr = translation_entry.get("fr", "")
            fr_auto = translation_entry.get("fr_auto", "")

            if not fr:
                if entry.msgstr:
                    fr_auto = entry.msgstr
                elif not fr_auto:
                    fr_auto = self.__translate_text_safe(translation_id)
                else:
                    logger.debug(
                        f'Machine translation entry for "{translation_id}" already exists.'
                    )
            else:
                logger.debug(f'Using manual translation for "{translation_id}."')

            candidate_msgstr = fr if fr else fr_auto
            if self.__has_brace_placeholder_mismatch(translation_id, candidate_msgstr):
                logger.warning(
                    "Brace-format placeholder mismatch detected; falling back to source string",
                    translation_id=translation_id,
                    candidate_msgstr=candidate_msgstr,
                )
                candidate_msgstr = translation_id

            translations_reference[translation_id] = {"fr": fr, "fr_auto": fr_auto}
            entry.msgstr = candidate_msgstr

        logger.debug(f"Updating file at path: {po_file_path}.")
        po_file.save(po_file_path)

    def __translate_text_safe(self, translation_id: str) -> str:
        try:
            translated_text = self.translate_text(translation_id)
            logger.debug(f'Translating "{translation_id}."')
            return translated_text
        except Exception as exc:
            logger.warning(
                "Failed to translate localization entry; leaving msgstr empty",
                translation_id=translation_id,
                error=str(exc),
            )
            return ""

    def __has_brace_placeholder_mismatch(self, source: str, translated: str) -> bool:
        source_fields = self.__extract_brace_fields(source)
        return bool(source_fields) and source_fields != self.__extract_brace_fields(
            translated
        )

    def __extract_brace_fields(self, text: str) -> set[str]:
        try:
            parsed_fields = list(Formatter().parse(text))
        except ValueError as exc:
            logger.warning(
                "Invalid brace-format string detected during localization placeholder validation",
                text=text,
                error=str(exc),
            )
            return set()

        return {
            normalized
            for _, field_name, _, _ in parsed_fields
            if field_name
            for normalized in [field_name.split(".", 1)[0].split("[", 1)[0]]
            if normalized
        }
