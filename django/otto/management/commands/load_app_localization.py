import os
import sys

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from django_extensions.management.utils import signalcommand
from polib import pofile

from otto.management.commands import make_messages
from otto.utils.localization import LocaleTranslator


class Command(BaseCommand):
    help = "Translate .po file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-translation",
            help="Skips translation steps.",
            action="store_true",
        )
        parser.add_argument(
            "--no-po",
            help="Skips step to generate the localization Portable object (.po) file.",
            action="store_true",
        )
        parser.add_argument(
            "--no-mo",
            help="Skips step to generate the localization Machine object (.mo) file.",
            action="store_true",
        )
        parser.add_argument(
            "--clean",
            help="Removes all translations with no manual translations in .json file.",
            action="store_true",
        )

    @signalcommand
    def handle(self, *args, **options):
        no_po = options["no_po"]
        no_translation = options["no_translation"]
        no_mo = options["no_mo"]
        clean = options["clean"]

        if no_mo and no_po and no_translation:
            raise CommandError(
                "Type '%s help %s' for usage information."
                % (os.path.basename(sys.argv[0]), sys.argv[1])
            )

        if not no_po:
            call_command("make_messages", locale=["fr"], no_fuzzy_matching=True)
            self.stdout.write(self.style.SUCCESS(".po file generated successfully."))

        base_path = os.path.join(settings.BASE_DIR, "locale")

        if clean:
            try:
                translations_path = self.get_translation_file(base_path)
                self.clean_translations(translations_path)
                self.stdout.write(
                    self.style.SUCCESS("Translations cleaned successfully.")
                )
            except Exception as e:
                self.stderr.write(e)
                return

        if not no_translation:
            translations_path = self.get_translation_file(base_path)
            po_file_path = os.path.join(base_path, "fr", "LC_MESSAGES", "django.po")

            if not os.path.isfile(po_file_path):
                self.stderr.write(f"The file at {po_file_path} does not exist.")
                return

            translator_client = LocaleTranslator(
                settings.AZURE_COGNITIVE_SERVICE_KEY,
                settings.AZURE_COGNITIVE_SERVICE_REGION,
                settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
            )
            try:
                translator_client.update_translations(
                    os.path.join(settings.BASE_DIR, "locale"),
                )
            except Exception as e:
                self.stderr.write(e)

            self.stdout.write(self.style.SUCCESS("Files translated successfully."))

        if not no_mo:
            po_file_path = os.path.join(
                settings.BASE_DIR, "locale", "fr", "LC_MESSAGES", "django.po"
            )
            self.correct_po_file(po_file_path)
            call_command("compilemessages")
            self.stdout.write(self.style.SUCCESS(".mo file generated successfully."))

    def correct_po_file(self, po_file_path):
        po = pofile(po_file_path)
        for entry in po:
            if entry.msgid and entry.msgstr:
                try:
                    entry.msgid % {}
                    entry.msgstr % {}
                except (KeyError, ValueError):
                    entry.msgstr = (
                        entry.msgid
                    )  # Copy msgid to msgstr to avoid format issues
        po.save()
        self.stdout.write(self.style.SUCCESS(f"Corrected .po file at {po_file_path}."))

    def get_translation_file(self, base_path):
        translations_path = os.path.join(base_path, "translation", "translations.json")
        if not os.path.isfile(translations_path):
            raise CommandError(f"The file at {translations_path} does not exist.")
        return translations_path

    def clean_translations(self, file_path):
        import json

        # Read the JSON file
        with open(file_path, "r", encoding="utf-8") as file:
            translations = json.load(file)

        # Remove objects with empty 'fr' values
        cleaned_translations = {
            key: value for key, value in translations.items() if value.get("fr")
        }

        # Write the cleaned JSON back to the file
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(cleaned_translations, file, ensure_ascii=False, indent=4)
