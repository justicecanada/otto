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

    @signalcommand
    def handle(self, *args, **options):
        no_po = options["no_po"]
        no_translation = options["no_translation"]
        no_mo = options["no_mo"]

        if no_mo and no_po and no_translation:
            raise CommandError(
                "Type '%s help %s' for usage information."
                % (os.path.basename(sys.argv[0]), sys.argv[1])
            )

        if not no_po:
            call_command("make_messages", locale=["fr"], no_fuzzy_matching=True)
            self.stdout.write(self.style.SUCCESS(".po file generated successfully."))

        if not no_translation:
            base_path = os.path.join(settings.BASE_DIR, "locale")
            translations_path = os.path.join(
                base_path, "translation", "translations.json"
            )
            po_file_path = os.path.join(base_path, "fr", "LC_MESSAGES", "django.po")

            if not os.path.isfile(translations_path):
                self.stderr.write(f"The file at {translations_path} does not exist.")
                return
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
