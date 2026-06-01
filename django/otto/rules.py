"""
Permissions-related rules for Otto apps
See https://github.com/dfunckt/django-rules
"""

from django.conf import settings

from data_fetcher import cache_within_request
from rules import add_perm, is_group_member, predicate

from otto.models import TeamMembership

from chat.models import Chat
from librarian.models import LibraryTeamRole, LibraryUserRole

# AC-16 & AC-16(2): Real-time enforcement of modified security attributes
# AC-3(7): Custom permission predicates and rules for role-based access control

ADMINISTRATIVE_PERMISSIONS = {
    "otto.manage_users",
    "otto.manage_feedback",
    "otto.manage_cost_dashboard",
    "otto.load_laws",
    "librarian.manage_public_libraries",
}

GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN = "Skill files (Otto defaults)"


def is_global_skill_defaults_library(library):
    return getattr(library, "name_en", None) == GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN


@predicate
def accepted_terms(user):
    return user.accepted_terms_date is not None


# AC-16(2): Security Attribute Modification
# "is_group_member" returns a predicate
is_admin = is_group_member(settings.OTTO_ADMIN_GROUP)
is_operations_admin = is_group_member(settings.OTTO_OPERATIONS_ADMIN_GROUP)
is_bulk_uploader = is_group_member(settings.OTTO_BULK_UPLOADER_GROUP)
# Backward-compatible alias; prefer is_bulk_uploader in new code.
is_data_steward = is_bulk_uploader
# Public sharing admin: members can share libraries, presets, and skills org-wide
is_public_sharing_admin = is_group_member(settings.OTTO_PUBLIC_SHARING_ADMIN_GROUP)
is_beta_tester = is_group_member(settings.OTTO_BETA_TESTER_GROUP)

add_perm("otto.manage_users", is_admin)
add_perm("otto.manage_banner", is_admin)
add_perm("otto.load_laws", is_admin)
add_perm("otto.access_otto", accepted_terms)


@predicate
def can_switch_cost_groups(user):
    """Check if user has access to any cost groups (used to show/hide cost group switcher)"""
    from otto.models import CostGroup

    return CostGroup.get_available_cost_groups(user).exists()


add_perm("otto.can_switch_cost_groups", can_switch_cost_groups)


@predicate
def can_enable_load_testing(user):
    if settings.IS_PROD:
        return False
    return is_admin(user)


@predicate
def can_access_feedback(user):
    return is_admin(user) or is_operations_admin(user)


@predicate
def can_access_cost_dashboard(user):
    return is_admin(user) or is_operations_admin(user)


@predicate
def can_access_chat_next(user):
    return is_admin(user) or is_beta_tester(user)


add_perm("otto.enable_load_testing", can_enable_load_testing)
add_perm("otto.manage_feedback", can_access_feedback)
add_perm("otto.manage_cost_dashboard", can_access_cost_dashboard)
add_perm("otto.can_access_chat_next", can_access_chat_next)


# AI Assistant
@predicate
def can_access_chat(user, chat):
    return chat.user == user


@predicate
def can_access_message(user, message):
    return message.chat.user == user


@predicate
def can_access_file(user, file):
    return file.message.chat.user == user


@cache_within_request
def get_user_team_ids(user):
    """Return set of team IDs the user belongs to."""
    return set(
        TeamMembership.objects.filter(user=user).values_list("team_id", flat=True)
    )


def user_in_teams(user, teams_qs):
    """Check if user is a member of any team in the queryset."""
    team_ids = get_user_team_ids(user)
    if not team_ids:
        return False
    return teams_qs.filter(pk__in=team_ids).exists()


@predicate
def can_access_preset(user, preset):
    return (
        user == preset.owner
        or preset.accessible_to.filter(pk=user.pk).exists()
        or preset.editable_by.filter(pk=user.pk).exists()
        or user_in_teams(user, preset.accessible_to_teams)
        or user_in_teams(user, preset.editable_by_teams)
        or preset.sharing_option == "everyone"
    )


@predicate
def can_edit_preset(user, preset):
    if preset.owner is None:
        return is_admin(user)
    if user == preset.owner:
        return True
    if preset.sharing_option == "everyone" and is_admin(user):
        return True
    return preset.editable_by.filter(pk=user.pk).exists() or user_in_teams(
        user, preset.editable_by_teams
    )


@predicate
def can_delete_preset(user, preset):
    if preset.global_default:
        return False
    return can_edit_preset(user, preset)


@predicate
def can_edit_preset_sharing(user, preset):
    if preset.global_default:
        return False
    return can_edit_preset(user, preset)


@predicate
def can_upload_large_files(user):
    return is_admin(user) or is_bulk_uploader(user)


@predicate
def can_share_skill_with_everyone(user):
    # Beta: all users can share skills publicly
    return True


@predicate
def can_manage_featured_skills(user):
    return is_admin(user)


@predicate
def can_admin_edit_skill(user, skill):
    return is_admin(user) and (
        skill.sharing_option == "everyone" or skill.is_featured or skill.is_system
    )


def can_edit_skill(user, skill) -> bool:
    """Return True when the user may edit a skill.

    This is intentionally a plain helper rather than a registered object
    permission because skill editing is currently enforced inside chat_next views.
    """
    if not user or not getattr(user, "is_authenticated", False) or not skill:
        return False
    if skill.owner_id == user.id:
        return True
    if can_admin_edit_skill(user, skill):
        return True
    if skill.editable_by.filter(pk=user.pk).exists():
        return True
    return user_in_teams(user, skill.editable_by_teams)


add_perm("chat.access_chat", can_access_chat)
add_perm("chat.access_message", can_access_message)
add_perm("chat.access_file", can_access_file)
add_perm("chat.access_preset", can_access_preset)
add_perm("chat.edit_preset", can_edit_preset)
add_perm("chat.delete_preset", can_delete_preset)
add_perm("chat.edit_preset_sharing", can_edit_preset_sharing)
add_perm("chat.upload_large_files", can_upload_large_files)
add_perm("chat_next.share_skill_with_everyone", can_share_skill_with_everyone)
add_perm("chat_next.manage_featured_skills", can_manage_featured_skills)
add_perm("chat_next.admin_edit_skill", can_admin_edit_skill)


# Team management
@predicate
def can_manage_team(user, team):
    """Team admins and site admins can manage a team."""
    if is_admin(user):
        return True
    return TeamMembership.objects.filter(team=team, user=user, role="admin").exists()


add_perm("otto.manage_team", can_manage_team)


# Librarian
# Ensures a simple query is used to get the roles for a user
@cache_within_request
def get_library_roles_for_user(user):
    # Direct user roles
    roles = list(LibraryUserRole.objects.filter(user=user))
    # Team-based roles: if the user is a member of a team that has a role on a library,
    # include those as equivalent LibraryUserRole-like objects.
    team_ids = get_user_team_ids(user)
    if team_ids:
        team_roles = LibraryTeamRole.objects.filter(team_id__in=team_ids)
        for tr in team_roles:
            # Avoid duplicating a role that already exists for this user
            if not any(
                r.library_id == tr.library_id and r.role == tr.role for r in roles
            ):
                # Create a lightweight object that quacks like LibraryUserRole
                synthetic = LibraryUserRole(
                    library_id=tr.library_id, user=user, role=tr.role
                )
                roles.append(synthetic)
    return roles


# Do all subsequent filtering on Python objects (in memory) instead of in the database
@predicate
def is_library_viewer(user, library):
    return any(
        role.library_id == library.id and role.role == "viewer"
        for role in get_library_roles_for_user(user)
    )


@predicate
def is_library_contributor(user, library):
    return any(
        role.library_id == library.id and role.role == "contributor"
        for role in get_library_roles_for_user(user)
    )


@predicate
def is_library_admin(user, library):
    return any(
        role.library_id == library.id and role.role == "admin"
        for role in get_library_roles_for_user(user)
    )


@predicate
def is_library_user(user, library):
    return any(
        role.library_id == library.id for role in get_library_roles_for_user(user)
    )


@predicate
def can_manage_public_libraries(user):
    # Only Otto admins and Public sharing admins can manage public libraries.
    return is_admin(user) or is_public_sharing_admin(user)


@predicate
def can_change_publicity(user, library):
    if library.is_personal_library or library.is_skill_library:
        return False
    if not library.id:
        return can_manage_public_libraries(user)
    return can_manage_public_libraries(user) and (
        is_library_admin(user, library) or is_admin(user)
    )


@cache_within_request
def _get_skill_hint_ids_by_type(user):
    """Return (library_ids, folder_ids, document_ids) from skills accessible to user.

    Parses context_hints from all accessible skills and groups hint IDs by type.
    Each set contains integer IDs. Cached per request.
    """
    from chat_next._utils.context_hints import sanitize_runtime_context_hints
    from chat_next.models import Skill

    accessible_skills = Skill.objects.get_accessible(user)

    library_ids = set()
    folder_ids = set()
    document_ids = set()

    for hints in accessible_skills.values_list("context_hints", flat=True):
        if not hints:
            continue
        for hint in sanitize_runtime_context_hints(hints):
            hint_type = hint.get("type")
            hint_id = hint.get("id")
            if not hint_id:
                continue
            try:
                hint_id_int = int(hint_id)
            except (ValueError, TypeError):
                continue
            if hint_type == "library":
                library_ids.add(hint_id_int)
            elif hint_type == "document":
                document_ids.add(hint_id_int)
            elif hint_type == "folder":
                folder_ids.add(hint_id_int)

    return library_ids, folder_ids, document_ids


def get_skill_referenced_library_ids(user):
    """Return set of library IDs directly referenced (type=library) in skills."""
    library_ids, _, _ = _get_skill_hint_ids_by_type(user)
    return library_ids


@cache_within_request
def get_skill_referenced_folder_ids(user):
    """Return set of data_source IDs referenced (type=folder) in skills."""
    _, folder_ids, _ = _get_skill_hint_ids_by_type(user)
    return folder_ids


@cache_within_request
def get_skill_referenced_document_ids(user):
    """Return set of document IDs referenced (type=document) in skills."""
    _, _, document_ids = _get_skill_hint_ids_by_type(user)
    return document_ids


def get_skills_referencing_item(item_type, item_id):
    """Return queryset of Skills whose context_hints reference the given item.

    Args:
        item_type: "library", "document", or "folder"
        item_id: the ID of the item
    """
    from chat_next.models import Skill

    # JSONField contains filter: look for hints with matching type and id.
    # context_hints is a list of dicts, so we use __contains with the target dict.
    return Skill.objects.filter(
        context_hints__contains=[{"type": item_type, "id": item_id}],
    ) | Skill.objects.filter(
        context_hints__contains=[{"type": item_type, "id": str(item_id)}],
    )


def can_access_library_via_skills(user, library) -> bool:
    """Return True when a skill available to the user references the library."""
    return library.id in get_skill_referenced_library_ids(user)


def can_access_data_source_via_skills(user, data_source) -> bool:
    """Return True when a skill available to the user references the folder or its library."""
    return data_source.library_id in get_skill_referenced_library_ids(
        user
    ) or data_source.id in get_skill_referenced_folder_ids(user)


def can_access_document_via_skills(user, document) -> bool:
    """Return True when a skill available to the user references the document or its scope."""
    return can_access_data_source_via_skills(
        user, document.data_source
    ) or document.id in get_skill_referenced_document_ids(user)


def can_access_library_in_chat(user, library) -> bool:
    """Assistant/runtime access path: explicit access OR skill-derived access."""
    if not user or not getattr(user, "is_authenticated", False) or not library:
        return False
    if is_global_skill_defaults_library(library):
        return True
    return can_view_library(user, library) or can_access_library_via_skills(
        user, library
    )


def can_access_data_source_in_chat(user, data_source) -> bool:
    """Assistant/runtime access path: explicit access OR skill-derived access."""
    if can_access_library_in_chat(user, data_source.library):
        return True
    return can_view_data_source(user, data_source) or can_access_data_source_via_skills(
        user, data_source
    )


def can_access_document_in_chat(user, document) -> bool:
    """Assistant/runtime access path: explicit access OR skill-derived access."""
    if can_access_data_source_in_chat(user, document.data_source):
        return True
    return can_view_document(user, document) or can_access_document_via_skills(
        user, document
    )


def _can_view_library_explicit(user, library) -> bool:
    if is_global_skill_defaults_library(library):
        return is_admin(user)
    if library.is_personal_library or library.is_skill_library:
        return library.created_by_id == getattr(user, "id", None)
    return (
        library.is_public
        or library.created_by_id == getattr(user, "id", None)
        or is_library_user(user, library)
    )


def _can_view_skill_linked_data_source(user, data_source) -> bool:
    return bool(
        data_source.library.is_skill_library
        and data_source.skill_id
        and can_edit_skill(user, data_source.skill)
    )


def _can_edit_skill_linked_data_source(user, data_source) -> bool:
    return _can_view_skill_linked_data_source(user, data_source)


@predicate
def can_view_library(user, library):
    return _can_view_library_explicit(user, library)


@predicate
def can_edit_library(user, library):
    if getattr(library, "temp", False):
        return True
    if is_global_skill_defaults_library(library):
        return is_admin(user)
    if library.is_personal_library or library.is_skill_library:
        return library.created_by_id == getattr(user, "id", None)
    if library.is_public and is_admin(user):
        return True
    return (
        library.created_by_id == getattr(user, "id", None)
        or is_library_admin(user, library)
        or is_library_contributor(user, library)
    )


@predicate
def can_delete_library(user, library):
    if (
        library.is_default_library
        or library.is_personal_library
        or library.is_skill_library
        or is_global_skill_defaults_library(library)
    ):
        return False
    if library.is_public:
        if is_admin(user):
            return True
        return is_library_admin(user, library)
    return is_library_admin(user, library)


@predicate
def can_edit_data_source(user, data_source):
    if _can_edit_skill_linked_data_source(user, data_source):
        return True
    # If they can edit the library, they can edit a data_source
    return can_edit_library(user, data_source.library)


@predicate
def can_view_data_source(user, data_source):
    if can_view_library(user, data_source.library):
        return True
    return _can_view_skill_linked_data_source(user, data_source)


@predicate
def can_delete_data_source(user, data_source):
    if Chat.objects.filter(data_source=data_source).exists():
        return False
    if data_source.library.is_default_library:
        return is_admin(user)
    return can_edit_library(user, data_source.library)


@predicate
def can_edit_document(user, document):
    return can_edit_data_source(user, document.data_source)


@predicate
def can_view_document(user, document):
    if can_view_data_source(user, document.data_source):
        return True
    return False


@predicate
def can_delete_document(user, document):
    return can_edit_library(user, document.data_source.library)


@predicate
def can_manage_library_users(user, library):
    if library.is_personal_library or library.is_skill_library:
        return False
    if is_global_skill_defaults_library(library):
        return is_admin(user)
    if library.is_public:
        if is_admin(user):
            return True
        return is_library_admin(user, library)
    return is_library_admin(user, library)


@predicate
def can_download_document(user, document):
    return can_view_document(user, document)


add_perm("librarian.manage_public_libraries", can_manage_public_libraries)
add_perm("librarian.change_publicity", can_change_publicity)
add_perm("librarian.view_library", can_view_library)
add_perm("librarian.edit_library", can_edit_library)
add_perm("librarian.delete_library", can_delete_library)
add_perm("librarian.edit_data_source", can_edit_data_source)
add_perm("librarian.view_data_source", can_view_data_source)
add_perm("librarian.delete_data_source", can_delete_data_source)
add_perm("librarian.edit_document", can_edit_document)
add_perm("librarian.view_document", can_view_document)
add_perm("librarian.delete_document", can_delete_document)
add_perm("librarian.manage_library_users", can_manage_library_users)
add_perm("librarian.download_document", can_download_document)
