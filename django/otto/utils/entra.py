import asyncio
import uuid

from django.conf import settings

from attr import dataclass
from azure.identity import ClientSecretCredential
from kiota_abstractions.api_error import APIError
from msgraph import GraphServiceClient
from msgraph.generated.users.users_request_builder import UsersRequestBuilder
from structlog import get_logger

from otto.models import User

credential = ClientSecretCredential(
    tenant_id=settings.ENTRA_AUTHORITY.split("/")[-1],
    client_id=settings.ENTRA_CLIENT_ID,
    client_secret=settings.ENTRA_CLIENT_SECRET,
)

client = GraphServiceClient(credential)
logger = get_logger(__name__)


@dataclass
class EntraUser:
    id: str
    upn: str
    email: str
    display_name: str
    first_name: str
    last_name: str


async def get_entra_users_async():

    page_size = 100

    query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
        select=[
            "id",
            "mail",
            "givenName",
            "displayName",
            "accountEnabled",
            "surname",
            "userprincipalname",
        ],
        top=page_size,
        filter="accountEnabled eq true",
        count=True,
    )
    request_configuration = (
        UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration(
            query_parameters=query_params
        )
    )
    request_configuration.headers.add("ConsistencyLevel", "eventual")

    entra_users_list = []
    batch_number = 1

    try:
        logger.debug(f"Processing batch {batch_number}")
        result = await client.users.get(request_configuration)
        total_count = round(result.odata_count / page_size)
        batch_number += 1

        entra_users_list = __filter_users(result.value)

        next_iteration = result.odata_next_link

        while next_iteration:
            logger.debug(f"Processing batch {batch_number} of {total_count}")
            result = await client.users.with_url(next_iteration).get()
            entra_users_list += __filter_users(result.value)
            next_iteration = result.odata_next_link
            batch_number += 1
    except APIError as e:
        logger.exception(
            f"Error trying to retrieve batch {batch_number} of entra users: {e}"
        )

    return entra_users_list


async def get_entra_user_async(user_id: str) -> EntraUser:
    result = await client.users.by_user_id(user_id).get()
    result = EntraUser(
        result.id,
        result.user_principal_name,
        result.mail,
        result.display_name,
        result.given_name,
        result.surname,
    )

    return result


def sync_users_with_entra():
    """Syncs entra users with Otto. Users in Otto not present in entra are flagged as inactive."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        users = loop.run_until_complete(get_entra_users_async())
    except Exception as e:
        logger.exception(f"Error trying to retrieve entra users: {e}")
    finally:
        loop.close()

    logger.info("Updating Otto...")
    update_or_create_users(users)

    set_inactive_users(users)


def update_or_create_users(users):
    for user in users:
        User.objects.update_or_create(
            upn=user.upn,
            defaults={
                "oid": user.id,
                "upn": user.upn,
                "email": user.email,
                "last_name": user.last_name,
                "first_name": user.first_name,
                "is_active": True,
            },
        )


# AC-2(3): Inactive Accounts
def set_inactive_users(users):
    logger.info("Setting Inactive Users...")
    users_upn = [user.upn for user in users]
    inactive_users = User.objects.exclude(upn__in=users_upn)

    logger.info(f"Deactivating {len(inactive_users)} inactive user(s)")

    for inactive_user in inactive_users:
        inactive_user.is_active = False

    User.objects.bulk_update(inactive_users, ["is_active"])


def __filter_users(users) -> list[EntraUser]:
    users_list = []
    for user in users:
        if user.user_principal_name and user.account_enabled:
            upn = user.user_principal_name.lower()

            if (
                "disabled" not in upn
                and ".ndr" not in upn
                and "admin." not in upn
                and "#" not in upn
                and "," in user.display_name
                and user.given_name
                and user.surname
            ):
                users_list.append(
                    EntraUser(
                        user.id,
                        user.user_principal_name,
                        user.mail,
                        user.display_name,
                        user.given_name,
                        user.surname,
                    )
                )
    return users_list
