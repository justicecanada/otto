from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from modeltranslation.admin import TranslationAdmin

from otto.models import App

User = get_user_model()


class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("upn",)


class CustomUserAdmin(BaseUserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm

    list_display = ("email", "first_name", "last_name", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("last_name", "first_name")
    fieldsets = (
        (None, {"fields": ("upn", "password")}),
        (
            "Personal info",
            {"fields": ("first_name", "last_name", "accepted_terms_date")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("upn", "password1", "password2"),
            },
        ),
    )


class AppAdmin(TranslationAdmin):
    pass


# AC-16 & AC-16(2): Allows authorized administrators to modify various security-related attributes of user accounts
admin.site.register(User, CustomUserAdmin)
admin.site.register(App, AppAdmin)
