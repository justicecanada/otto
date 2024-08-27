from django.core.exceptions import ObjectDoesNotExist
from django.core.management import call_command
from django.core.management.base import BaseCommand

from otto.models import Group, User


class Command(BaseCommand):
    help = "Sync users and set a user as Otto admin based on their UPN"

    def add_arguments(self, parser):
        parser.add_argument(
            "upn",
            type=str,
            help="The User Principal Name (UPN) of the user to be set as admin",
        )

    def handle(self, *args, **options):
        upn = options["upn"]

        # Find the user and make them an admin
        try:
            user = User.objects.get(upn__iexact=upn)
            user.make_otto_admin()
            self.stdout.write(
                self.style.SUCCESS(
                    f"User '{user.full_name}' ({user.upn}) has been set as Otto admin."
                )
            )
        except ObjectDoesNotExist:
            self.stdout.write(self.style.ERROR(f"No user found with UPN: {upn}"))
        except Group.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    "Otto admin group does not exist. Please create it first."
                )
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"An error occurred: {str(e)}"))
