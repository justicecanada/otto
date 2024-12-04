"""
Permissions-related rules for Otto apps
See https://github.com/dfunckt/django-rules
"""

from django.conf import settings

from data_fetcher import cache_within_request
from rules import add_perm, is_group_member, predicate

from chat.models import Chat
from librarian.models import LibraryUserRole

# AC-16 & AC-16(2): Real-time enforcement of modified security attributes
# AC-3(7): Custom permission predicates and rules for role-based access control

ADMINISTRATIVE_PERMISSIONS = {
    "otto.manage_users",
    "librarian.manage_public_libraries",
    "librarian.manage_library_users",
}


@predicate
def accepted_terms(user):
    return user.accepted_terms_date is not None


# AC-16(2): Security Attribute Modification
# "is_group_member" returns a predicate
is_admin = is_group_member("Otto admin")
is_data_steward = is_group_member("Data steward")

add_perm("otto.manage_users", is_admin)
add_perm("otto.access_otto", accepted_terms)
add_perm("otto.view_github_link", is_admin)


@predicate
def can_enable_load_testing(user):
    if settings.IS_PROD:
        return False
    return is_admin(user)


@predicate
def can_view_app(user, app):
    if is_admin(user):
        return True
    if settings.IS_PROD:
        return app.visible_to_all or can_access_app(user, app)
    return app.visible_to_all or can_access_app(user, app)


@predicate
def can_access_app(user, app):
    if is_admin(user):
        return True
    return is_group_member(app.user_group.name)(user)


add_perm("otto.view_app", can_view_app)
add_perm("otto.access_app", can_access_app)
add_perm("otto.enable_load_testing", can_enable_load_testing)


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


@predicate
def can_access_preset(user, preset):
    return (
        user == preset.owner
        or user in preset.accessible_to.all()
        or preset.shared_with == "Shared with everyone"
    )


@predicate
def can_edit_preset(user, preset):
    return user == preset.owner


add_perm("chat.access_chat", can_access_chat)
add_perm("chat.access_message", can_access_message)
add_perm("chat.access_file", can_access_file)
add_perm("chat.access_preset", can_access_preset)
add_perm("chat.edit_preset", can_edit_preset)

# Template Wizard
add_perm(
    "template_wizard.access_lex_wizard",
    is_group_member("Litigation briefing user") | is_admin,
)


# Librarian
@cache_within_request
def get_library_roles_for_user(user):
    return list(LibraryUserRole.objects.filter(user=user))


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
    return is_admin(user) or is_data_steward(user)


@predicate
def can_change_publicity(user, library):
    if library.is_personal_library:
        return False
    if not library.id:
        return can_manage_public_libraries(user)
    return can_manage_public_libraries(user) and (
        is_library_admin(user, library) or is_admin(user)
    )


@predicate
def can_view_library(user, library):
    return library.is_public or is_library_user(user, library)


@predicate
def can_edit_library(user, library):
    if library.is_public:
        if is_admin(user):
            return True
        return can_manage_public_libraries(user) and (
            is_library_admin(user, library) or is_library_contributor(user, library)
        )
    return is_library_admin(user, library) or is_library_contributor(user, library)


@predicate
def can_delete_library(user, library):
    if library.is_default_library or library.is_personal_library:
        return False
    if library.is_public:
        if is_admin(user):
            return True
        return can_manage_public_libraries(user) and is_library_admin(user, library)
    return is_library_admin(user, library)


@predicate
def can_edit_data_source(user, data_source):
    # If they can edit the library, they can edit a data_source
    return can_edit_library(user, data_source.library)


@predicate
def can_delete_data_source(user, data_source):
    if Chat.objects.filter(data_source=data_source).exists():
        return False
    if data_source.library.is_default_library:
        return is_admin(user)
    return can_edit_library(user, data_source.library)


@predicate
def can_edit_document(user, document):
    return can_edit_library(user, document.data_source.library)


@predicate
def can_delete_document(user, document):
    return can_edit_library(user, document.data_source.library)


@predicate
def can_manage_library_users(user, library):
    if library.is_personal_library:
        return False
    if library.is_public:
        if is_admin(user):
            return True
        return can_manage_public_libraries(user) and is_library_admin(user, library)
    return is_library_admin(user, library)


@predicate
def can_download_document(user, document):
    return can_view_library(user, document.data_source.library)


add_perm("librarian.manage_public_libraries", can_manage_public_libraries)
add_perm("librarian.change_publicity", can_change_publicity)
add_perm("librarian.view_library", can_view_library)
add_perm("librarian.edit_library", can_edit_library)
add_perm("librarian.delete_library", can_delete_library)
add_perm("librarian.edit_data_source", can_edit_data_source)
add_perm("librarian.delete_data_source", can_delete_data_source)
add_perm("librarian.edit_document", can_edit_document)
add_perm("librarian.delete_document", can_delete_document)
add_perm("librarian.manage_library_users", can_manage_library_users)
add_perm("librarian.download_document", can_download_document)
