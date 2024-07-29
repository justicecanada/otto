import json
import os
from uuid import uuid4

import polib
import requests


class LocaleTranslator:

    def __init__(self, key: str, region: str, endpoint: str) -> None:
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
        engine = "azure"

        # Build the request
        params = {"api-version": "3.0", "to": "fr-ca"}

        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Ocp-Apim-Subscription-Region": self.region,
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid4().hex),
            # Visual Studio Enterprise
            "X-MS-CLIENT-PRINCIPAL-NAME": "41ede1ad-d5e6-4b6f-bd8e-979eb3813b47",
        }
        body = [{"Text": text}]

        # Send the request and get response
        url = f"{self.endpoint}/translator/text/v3.0/translate"
        response = requests.post(url, params=params, headers=headers, json=body)

        # Get translation
        translation = response.json()[0]["translations"][0]["text"]
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
        print(f"Loaded {len(valid_entries)} entries.")

        for entry in valid_entries:
            translation_id = entry.msgid
            fr = ""
            fr_auto = ""
            if translation_id in translations_reference:
                fr = translations_reference[translation_id].get("fr")
                fr_auto = translations_reference[translation_id].get("fr_auto")

                if fr:
                    fr = fr
                    fr_auto = fr_auto
                    print(f'Using manual translation for "{translation_id}."')
                elif entry.msgstr:
                    fr = ""
                    fr_auto = entry.msgstr
                    print(
                        f'Machine translation entry for "{translation_id}" already exists.'
                    )
                elif fr_auto:
                    fr = ""
                    fr_auto = fr_auto
                    print(
                        f'Machine translation entry for "{translation_id}" already exists.'
                    )
                else:
                    fr = ""
                    fr_auto = self.translate_text(translation_id)
                    print(f'Translating "{translation_id}."')
            else:
                fr = ""
                fr_auto = (
                    entry.msgstr
                    if entry.msgstr
                    else self.translate_text(translation_id)
                )
                print(f'Creating and translating entry "{translation_id}".')

            translations_reference[translation_id] = {"fr": fr, "fr_auto": fr_auto}
            entry.msgstr = fr if fr else fr_auto

        print(f"Updating file at path: {po_file_path}.")
        po_file.save(po_file_path)
