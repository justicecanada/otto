from django.core.management.commands import makemessages

from django_extensions.management.utils import signalcommand


class Command(makemessages.Command):
    msgmerge_options = makemessages.Command.msgmerge_options + ["--no-fuzzy-matching"]
    # We only want to look into certain directories; the rest are not relevant for translation
    msgmerge_options += [
        "-D",
        "librarian",
        "-D",
        "otto",
        "-D",
        "chat",
    ]

    @signalcommand
    def add_arguments(self, parser):
        super().add_arguments(parser=parser)
        parser.add_argument(
            "--no-fuzzy-matching",
            action="store_true",
            help="Do not use fuzzy matching in msgmerge.",
        )
