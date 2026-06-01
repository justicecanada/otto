import asyncio
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError

from attr import dataclass
from azure.identity import ClientSecretCredential
from kiota_abstractions.api_error import APIError
from msgraph import GraphServiceClient
from msgraph.generated.users.users_request_builder import UsersRequestBuilder
from structlog import get_logger

from otto.models import User

logger = get_logger(__name__)


def _get_graph_client() -> GraphServiceClient:
    if not (
        settings.ENTRA_AUTHORITY
        and settings.ENTRA_CLIENT_ID
        and settings.ENTRA_CLIENT_SECRET
    ):
        raise RuntimeError("Entra Graph client is not configured.")

    credential = ClientSecretCredential(
        tenant_id=settings.ENTRA_AUTHORITY.rstrip("/").split("/")[-1],
        client_id=settings.ENTRA_CLIENT_ID,
        client_secret=settings.ENTRA_CLIENT_SECRET,
    )
    return GraphServiceClient(credential)


@dataclass
class EntraUser:
    id: str
    upn: str
    email: str
    display_name: str
    first_name: str
    last_name: str
    status: str = User.EntraStatus.ACTIVE
    job_title: str = ""
    preferred_language: str = ""


@dataclass
class EntraSyncSnapshot:
    active_users: list[EntraUser]
    disabled_users: list[EntraUser]
    deleted_users: list[EntraUser]


ACTIVE_USER_SELECT_FIELDS = [
    "id",
    "mail",
    "givenName",
    "displayName",
    "accountEnabled",
    "surname",
    "userprincipalname",
    "jobTitle",
    "preferredLanguage",
]

DISABLED_USER_SELECT_FIELDS = ACTIVE_USER_SELECT_FIELDS

DELETED_USER_SELECT_FIELDS = [
    "id",
    "mail",
    "displayName",
    "userPrincipalName",
]


async def get_entra_users_async():
    return await _fetch_entra_users_async(
        select_fields=ACTIVE_USER_SELECT_FIELDS,
        filter_query="accountEnabled eq true",
        include_count=True,
        status=User.EntraStatus.ACTIVE,
    )


async def get_entra_disabled_users_async():
    return await _fetch_entra_users_async(
        select_fields=DISABLED_USER_SELECT_FIELDS,
        filter_query="accountEnabled eq false",
        include_count=True,
        status=User.EntraStatus.DISABLED,
    )


async def get_entra_deleted_users_async():
    deleted_items_query = urlencode(
        {
            "$select": ",".join(DELETED_USER_SELECT_FIELDS),
            "$top": 100,
        }
    )
    return await _fetch_entra_users_async(
        next_url=f"https://graph.microsoft.com/v1.0/directory/deletedItems/microsoft.graph.user?{deleted_items_query}",
        status=User.EntraStatus.DELETED,
    )


async def get_entra_sync_snapshot_async():
    active_users = await get_entra_users_async()
    disabled_users = await get_entra_disabled_users_async()
    deleted_users = await get_entra_deleted_users_async()
    return EntraSyncSnapshot(
        active_users=active_users,
        disabled_users=disabled_users,
        deleted_users=deleted_users,
    )


async def _fetch_entra_users_async(
    *,
    select_fields: list[str] | None = None,
    filter_query: str | None = None,
    include_count: bool = False,
    next_url: str | None = None,
    status: str,
):
    page_size = 100
    client = _get_graph_client()

    request_configuration = None
    if next_url is None:
        query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            select=select_fields,
            top=page_size,
            filter=filter_query,
            count=include_count,
        )
        request_configuration = (
            UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration(
                query_parameters=query_params
            )
        )
        if include_count:
            request_configuration.headers.add("ConsistencyLevel", "eventual")

    entra_users_list = []
    batch_number = 1

    try:
        logger.debug(f"Processing batch {batch_number}")
        if next_url is None:
            result = await client.users.get(request_configuration)
        else:
            result = await client.users.with_url(next_url).get()

        total_count = None
        if getattr(result, "odata_count", None):
            total_count = round(result.odata_count / page_size)
        batch_number += 1

        entra_users_list = __filter_users(result.value, status=status)

        next_iteration = result.odata_next_link

        while next_iteration:
            if total_count:
                logger.debug(f"Processing batch {batch_number} of {total_count}")
            else:
                logger.debug(f"Processing batch {batch_number}")
            result = await client.users.with_url(next_iteration).get()
            entra_users_list += __filter_users(result.value, status=status)
            next_iteration = result.odata_next_link
            batch_number += 1
    except APIError as e:
        logger.exception(
            f"Error trying to retrieve batch {batch_number} of entra users ({status}): {e}"
        )

    return entra_users_list


async def get_entra_user_async(user_id: str) -> EntraUser:
    client = _get_graph_client()
    result = await client.users.by_user_id(user_id).get()
    result = EntraUser(
        result.id,
        _normalize_upn_from_graph_user(result),
        (result.mail or _normalize_upn_from_graph_user(result) or "").lower(),
        result.display_name or "",
        result.given_name or "",
        result.surname or "",
        User.EntraStatus.ACTIVE
        if result.account_enabled
        else User.EntraStatus.DISABLED,
        result.job_title or "",
        result.preferred_language or "",
    )

    return result


def sync_users_with_entra():
    """Syncs Entra users with Otto and updates local Entra-derived status fields."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        snapshot = loop.run_until_complete(get_entra_sync_snapshot_async())
    except Exception as e:
        logger.exception(f"Error trying to retrieve entra users: {e}")
        return
    finally:
        loop.close()

    logger.info("Updating Otto...")
    update_or_create_users(snapshot.active_users)

    set_entra_status_for_inactive_users(
        snapshot.active_users,
        snapshot.disabled_users,
        snapshot.deleted_users,
    )


def update_or_create_users(users):
    from django.contrib.auth.models import Group

    # In Prod, auto-add all synced users to the "Otto user" group
    otto_user_group = None
    if settings.ENVIRONMENT.lower() == "prod":
        otto_user_group, _ = Group.objects.get_or_create(name=settings.OTTO_USER_GROUP)

    for user in users:
        if not user.email:
            logger.exception(f"Skipping user {user.upn} due to missing email")
            continue
        try:
            user_obj = User.objects.find_by_upn(user.upn, include_inactive=True)
            if user_obj:
                user_obj.oid = user.id
                user_obj.upn = user.upn
                user_obj.email = user.email
                user_obj.last_name = user.last_name
                user_obj.first_name = user.first_name
                user_obj.job_title = user.job_title or ""
                user_obj.preferred_language = user.preferred_language or ""
                user_obj.entra_status = User.EntraStatus.ACTIVE
                user_obj.is_active = True
                user_obj.save()
            else:
                user_obj = User.objects.create_user(
                    upn=user.upn,
                    oid=user.id,
                    email=user.email,
                    last_name=user.last_name,
                    first_name=user.first_name,
                    job_title=user.job_title or "",
                    preferred_language=user.preferred_language or "",
                    entra_status=User.EntraStatus.ACTIVE,
                    is_active=True,
                )
            if otto_user_group:
                otto_user_group.user_set.add(user_obj)
        except IntegrityError:
            logger.exception(f"Error updating or creating user {user.upn}")


def set_entra_status_for_inactive_users(active_users, disabled_users, deleted_users):
    logger.info("Updating non-active Entra users...")

    active_upns = {
        User.objects.normalize_upn(user.upn) for user in active_users if user.upn
    }
    active_oids = {user.id for user in active_users if user.id}
    disabled_by_upn = {
        User.objects.normalize_upn(user.upn): user
        for user in disabled_users
        if user.upn
    }
    disabled_by_oid = {user.id: user for user in disabled_users if user.id}
    deleted_by_upn = {
        User.objects.normalize_upn(user.upn): user for user in deleted_users if user.upn
    }
    deleted_by_oid = {user.id: user for user in deleted_users if user.id}

    updated_users = []
    for local_user in User.objects.all():
        local_upn = User.objects.normalize_upn(local_user.upn)
        if local_user.oid in active_oids or local_upn in active_upns:
            continue

        matched_disabled_user = disabled_by_oid.get(
            local_user.oid
        ) or disabled_by_upn.get(local_upn)
        matched_deleted_user = deleted_by_oid.get(local_user.oid) or deleted_by_upn.get(
            local_upn
        )

        if matched_disabled_user:
            local_user.oid = matched_disabled_user.id
            local_user.upn = matched_disabled_user.upn or local_user.upn
            local_user.email = matched_disabled_user.email or local_user.email
            if matched_disabled_user.first_name:
                local_user.first_name = matched_disabled_user.first_name
            if matched_disabled_user.last_name:
                local_user.last_name = matched_disabled_user.last_name
            local_user.job_title = matched_disabled_user.job_title or ""
            local_user.preferred_language = (
                matched_disabled_user.preferred_language or ""
            )
            local_user.entra_status = User.EntraStatus.DISABLED
        elif matched_deleted_user:
            local_user.oid = matched_deleted_user.id
            local_user.upn = matched_deleted_user.upn or local_user.upn
            local_user.email = matched_deleted_user.email or local_user.email
            local_user.entra_status = User.EntraStatus.DELETED
        else:
            local_user.entra_status = User.EntraStatus.UNKNOWN

        local_user.is_active = False
        updated_users.append(local_user)

    logger.info(f"Updating {len(updated_users)} non-active user(s) from Entra status")

    User.objects.bulk_update(
        updated_users,
        [
            "oid",
            "upn",
            "email",
            "first_name",
            "last_name",
            "job_title",
            "preferred_language",
            "entra_status",
            "is_active",
        ],
    )


def _normalize_upn_from_graph_user(user) -> str:
    user_principal_name = getattr(user, "user_principal_name", None)
    mail = getattr(user, "mail", None)
    return (user_principal_name or mail or "").lower()


def __filter_users(users, status) -> list[EntraUser]:
    users_list = []
    for user in users:
        upn = _normalize_upn_from_graph_user(user)
        if not upn:
            continue

        is_active_status = status == User.EntraStatus.ACTIVE
        if is_active_status and not getattr(user, "account_enabled", False):
            continue

        if is_active_status and (
            "disabled" in upn
            or ".ndr" in upn
            or "admin." in upn
            or "#" in upn
            or "," not in (user.display_name or "")
            or not user.given_name
            or not user.surname
        ):
            continue

        users_list.append(
            EntraUser(
                user.id,
                upn,
                (user.mail or upn).lower(),
                user.display_name or "",
                user.given_name or "",
                user.surname or "",
                status,
                getattr(user, "job_title", None) or "",
                getattr(user, "preferred_language", None) or "",
            )
        )
    return users_list
