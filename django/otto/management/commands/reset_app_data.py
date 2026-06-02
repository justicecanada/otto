import os
import subprocess

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

import yaml
from chat_next.models import Skill as SkillNext
from chat_next.models import SkillTag, sync_default_enabled_skills
from django_extensions.management.utils import signalcommand

from otto.models import SecurityLabel
from otto.rules import GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN

from chat.models import Preset
from librarian.models import DataSource, Document, Library, LibraryUserRole

SKILLS_LIBRARY_NAME_EN = GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN
LEGACY_SKILLS_LIBRARY_NAMES_EN = ["Skills Files"]


class Command(BaseCommand):
    help = "Reset otto and librarian model instances based on YAML configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "objects",
            nargs="*",
            type=str,
            help="Specify objects to reset (groups, libraries, library_mini, security_labels, cost_types, cost_groups, presets, skills, skill_tags)",
        )
        parser.add_argument("--all", action="store_true", help="Reset all objects")
        parser.add_argument(
            "--soft",
            action="store_true",
            help=(
                "(Deprecated) Soft mode for groups. This is now the default"
                " behavior; use --hard for destructive group reset."
            ),
        )
        parser.add_argument(
            "--hard",
            action="store_true",
            help=(
                "Hard mode for groups: delete and recreate groups and memberships"
                " from groups.yaml."
            ),
        )

    @signalcommand
    def handle(self, *args, **options):
        reset_all = options.get("all", False)
        objects_to_reset = options.get("objects", [])
        soft_flag = options.get("soft", False)
        hard_mode = options.get("hard", False)

        if soft_flag and hard_mode:
            raise CommandError("Use either --soft or --hard, not both.")

        # Groups now default to soft sync unless --hard is specified.
        soft_mode = not hard_mode

        # Create the database DATABASES["vector_db"] if it doesn't exist
        vector_db_name = settings.DATABASES["vector_db"]["NAME"]
        vector_db_user = settings.DATABASES["vector_db"]["USER"]
        vector_db_password = settings.DATABASES["vector_db"]["PASSWORD"]
        vector_db_host = settings.DATABASES["vector_db"]["HOST"]

        # Set system-wide environment variable PGPASSWORD to avoid password prompt
        os.environ["PGPASSWORD"] = vector_db_password

        if settings.ENVIRONMENT == "LOCAL":
            try:
                # Create the vector database (local only)
                subprocess.run(
                    [
                        "psql",
                        "-U",
                        vector_db_user,
                        "-h",
                        vector_db_host,
                        "-d",
                        "postgres",
                        "-c",
                        f"CREATE DATABASE {vector_db_name}",
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError:
                self.stdout.write(
                    self.style.WARNING(
                        f"Database {vector_db_name} already exists. Skipping creation."
                    )
                )

        if reset_all:
            self.reset_groups(soft=soft_mode)
            self.reset_security_labels()
            self.reset_libraries("library_mini.yaml")
            self.reset_cost_types()
            self.reset_cost_groups()
            self.reset_presets()
            id_registry = self.reset_skills_library()
            self.reset_skills(id_registry=id_registry)
            self.reset_skill_tags()
            self.embed_skill_tags()
        else:
            if "groups" in objects_to_reset:
                self.reset_groups(soft=soft_mode)

            if "cost_types" in objects_to_reset:
                self.reset_cost_types()

            if "cost_groups" in objects_to_reset:
                self.reset_cost_groups()

            if "security_labels" in objects_to_reset:
                self.reset_security_labels()

            if "libraries" in objects_to_reset:
                self.reset_libraries()

            if "library_mini" in objects_to_reset:
                self.reset_libraries("library_mini.yaml")

            if "presets" in objects_to_reset:
                self.reset_presets()

            if "skills" in objects_to_reset:
                id_registry = self.reset_skills_library()
                self.reset_skills(id_registry=id_registry)

            if "skill_tags" in objects_to_reset:
                self.reset_skill_tags()

            if "embed_tags" in objects_to_reset:
                self.embed_skill_tags()

    def reset_groups(self, soft: bool = True):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "otto", "fixtures", "groups.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            groups_data = yaml.safe_load(yaml_file)

        if not groups_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for groups."
                )
            )
            return

        if soft:
            # Soft mode: ensure every fixture group exists with the correct name.
            # Uses old_names to find groups that were renamed, preserving memberships.
            # Only deletes groups explicitly listed in old_names (superseded groups).
            created_count = 0
            renamed_count = 0
            retired_count = 0
            for group_data in groups_data:
                group_fields = (
                    group_data.get("fields", {}) if isinstance(group_data, dict) else {}
                )
                name = group_fields.get("name")
                if not name:
                    continue
                old_names = (
                    group_data.get("old_names", [])
                    if isinstance(group_data, dict)
                    else []
                )

                target_group = Group.objects.filter(name=name).first()

                if target_group:
                    # Target name already exists. Migrate members from any
                    # old-named groups that still linger in the DB, then delete them.
                    for old_name in old_names:
                        old_group = Group.objects.filter(name=old_name).first()
                        if old_group and old_group.pk != target_group.pk:
                            target_group.user_set.add(*old_group.user_set.all())
                            old_group.delete()
                            retired_count += 1
                    continue

                # Target name doesn't exist yet. Try to find a group to rename.
                # Use old_names to find the group (works regardless of PK).
                source_group = None
                for old_name in old_names:
                    source_group = Group.objects.filter(name=old_name).first()
                    if source_group:
                        break

                if source_group:
                    source_group.name = name
                    source_group.save(update_fields=["name"])
                    renamed_count += 1
                    # Merge members from any remaining old-named groups, then delete them.
                    for old_name in old_names:
                        old_group = Group.objects.filter(name=old_name).first()
                        if old_group and old_group.pk != source_group.pk:
                            source_group.user_set.add(*old_group.user_set.all())
                            old_group.delete()
                            retired_count += 1
                else:
                    Group.objects.create(name=name)
                    created_count += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"Groups soft sync: created {created_count}, renamed {renamed_count}, retired {retired_count}. No other deletions."
                )
            )
            return

        # Hard mode: delete and recreate groups and their memberships
        Group.objects.all().delete()

        # Create new groups based on YAML data
        for group_data in groups_data:
            group_fields = group_data.get("fields", {})
            group_instance = Group.objects.create(**group_fields)

            # Add permissions to the group if specified in the YAML file
            permissions = group_data.get("permissions", [])
            for codename in permissions:
                # Retrieve the permission instance
                permission_instance = Permission.objects.get(codename=codename)
                group_instance.permissions.add(permission_instance)

            # Add users to the group if specified in the YAML file
            users = group_data.get("users", [])
            for upn in users:
                user_instance = (
                    get_user_model().objects.find_by_upn(upn, include_inactive=True)
                ) or get_user_model().objects.create_user(upn)
                group_instance.user_set.add(user_instance)

        self.stdout.write(
            self.style.SUCCESS("Groups and user groups reset successfully.")
        )

    def reset_libraries(self, yaml_file_name="library.yaml"):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "librarian", "fixtures", yaml_file_name
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            libraries_data = yaml.safe_load(yaml_file)

        if not libraries_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for libraries."
                )
            )
            return

        # Clear out corporate library
        corporate_library = Library.objects.get_default_library()
        if corporate_library:
            corporate_library.delete()

        for item in libraries_data:
            if item["model"] != "librarian.library":
                continue

            library_fields = item.get("fields", {})
            data_sources = library_fields.pop("data_sources", [])

            library_instance = Library.objects.create(**library_fields)

            for data_source in data_sources:
                data_source_fields = self._resolve_security_label_fields(
                    data_source.get("fields", {}).copy()
                )
                documents = data_source_fields.pop("documents", [])

                data_source_instance = DataSource.objects.create(
                    library=library_instance, **data_source_fields
                )

                for document in documents:
                    document_fields = document.get("fields", {})
                    Document.objects.create(
                        data_source=data_source_instance, **document_fields
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Libraries, DataSources, and Documents reset successfully."
            )
        )

    def reset_skills_library(self):
        """Create or update the default Otto skill files library and return an ID registry.

        Returns a dict mapping stable ``key`` values from the fixture to resolved
        context-hint dicts (with ``type``, ``id``, and ``name``) so that
        ``reset_skills`` can patch ``lookup_key`` references in skills.yaml.
        """
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "librarian", "fixtures", "skills_library.yaml"
        )
        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            libraries_data = yaml.safe_load(yaml_file)

        id_registry = {}

        for item in libraries_data:
            if item["model"] != "librarian.library":
                continue

            library_fields = item.get("fields", {}).copy()
            data_sources = library_fields.pop("data_sources", [])

            # Keep this library admin-only. Skill context_hints still grant
            # per-resource access where needed.
            library_fields["is_public"] = False

            name_en = library_fields.get("name_en")
            if name_en:
                legacy_names = (
                    [name for name in LEGACY_SKILLS_LIBRARY_NAMES_EN if name != name_en]
                    if name_en == SKILLS_LIBRARY_NAME_EN
                    else []
                )
                libraries_qs = Library.objects.filter(
                    name_en__in=[name_en, *legacy_names]
                ).order_by("id")
                library_instance = libraries_qs.first()

                if library_instance:
                    duplicate_ids = list(
                        libraries_qs.exclude(id=library_instance.id).values_list(
                            "id", flat=True
                        )
                    )
                    if duplicate_ids:
                        # Preserve user content by moving folders/docs from
                        # duplicate libraries into the canonical one.
                        DataSource.objects.filter(library_id__in=duplicate_ids).update(
                            library=library_instance
                        )
                        Library.objects.filter(id__in=duplicate_ids).delete()

                    for field, value in library_fields.items():
                        setattr(library_instance, field, value)
                    library_instance.save()
                else:
                    library_instance = Library.objects.create(**library_fields)
            else:
                library_instance = Library.objects.create(**library_fields)

            self._ensure_skills_library_admin_roles(library_instance)

            for data_source in data_sources:
                ds_fields = self._resolve_security_label_fields(
                    data_source.get("fields", {}).copy()
                )
                ds_key = ds_fields.pop("key", None)
                documents = ds_fields.pop("documents", [])

                ds_lookup = {"library": library_instance}
                if ds_fields.get("name_en"):
                    ds_lookup["name_en"] = ds_fields["name_en"]
                elif ds_fields.get("name"):
                    ds_lookup["name"] = ds_fields["name"]

                if len(ds_lookup) > 1:
                    ds_instance, _ = DataSource.objects.update_or_create(
                        **ds_lookup,
                        defaults=ds_fields,
                    )
                else:
                    ds_instance = DataSource.objects.create(
                        library=library_instance,
                        **ds_fields,
                    )

                if ds_key:
                    id_registry[ds_key] = {
                        "type": "folder",
                        "id": ds_instance.id,
                        "name": ds_instance.name,
                    }

                for document in documents:
                    doc_fields = document.get("fields", {}).copy()
                    doc_key = doc_fields.pop("key", None)
                    local_file = doc_fields.pop("local_file", None)

                    if local_file:
                        file_path = os.path.join(settings.BASE_DIR, local_file)
                        with open(file_path, "r", encoding="utf-8") as fp:
                            doc_fields["extracted_text"] = fp.read()
                        doc_fields.setdefault("status", "SUCCESS")

                    filename = doc_fields.get("filename")
                    if filename or doc_key:
                        doc_lookup = {
                            "data_source": ds_instance,
                            "filename": filename or doc_key,
                        }
                        doc_instance, _ = Document.objects.update_or_create(
                            **doc_lookup,
                            defaults=doc_fields,
                        )
                    else:
                        doc_instance = Document.objects.create(
                            data_source=ds_instance,
                            **doc_fields,
                        )
                    if doc_key:
                        id_registry[doc_key] = {
                            "type": "document",
                            "id": doc_instance.id,
                            "name": doc_instance.filename or doc_key,
                        }

        self.stdout.write(
            self.style.SUCCESS(f"{SKILLS_LIBRARY_NAME_EN} library reset successfully.")
        )
        return id_registry

    def _resolve_security_label_fields(self, fields):
        """Resolve fixture-friendly security label fields into model fields."""
        security_label_acronym = fields.pop("security_label_acronym", None)
        if security_label_acronym and "security_label_id" not in fields:
            try:
                fields["security_label_id"] = SecurityLabel.objects.get(
                    acronym_en=security_label_acronym
                ).id
            except SecurityLabel.DoesNotExist as exc:
                raise CommandError(
                    "Security label fixture is missing the label "
                    f"'{security_label_acronym}'. Run reset_app_data security_labels first."
                ) from exc
        return fields

    def _ensure_skills_library_admin_roles(self, library):
        """Ensure current Otto admins have admin role on the default skill files library."""
        admin_users = get_user_model().objects.filter(
            groups__name=settings.OTTO_ADMIN_GROUP
        )
        for user in admin_users.distinct():
            LibraryUserRole.objects.update_or_create(
                library=library,
                user=user,
                defaults={"role": "admin"},
            )

    def reset_presets(self):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "chat", "fixtures", "presets.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            presets_data = yaml.safe_load(yaml_file)

        # Delete existing "default presets" (which have no owner)
        Preset.objects.filter(owner=None).delete()
        Preset.objects.create_from_yaml(presets_data)

        self.stdout.write(self.style.SUCCESS("Presets reset successfully."))

    def reset_skills(self, id_registry=None):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "chat_next", "fixtures", "skills.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            skills_data = yaml.safe_load(yaml_file)

        if id_registry:
            for skill_data in skills_data.values():
                resolved = []
                for hint in skill_data.get("context_hints", []):
                    hint = hint.copy()
                    lookup_key = hint.pop("lookup_key", None)
                    if lookup_key and lookup_key in id_registry:
                        hint.update(id_registry[lookup_key])
                    resolved.append(hint)
                skill_data["context_hints"] = resolved

        SkillNext.objects.create_from_yaml(skills_data)

        updated_settings, added_skill_links = sync_default_enabled_skills(
            add_missing_defaults_to_all=True
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Chat_next skills reset successfully. "
                f"Synced default skills into {updated_settings} chat setting(s) "
                f"and added {added_skill_links} enabled-skill link(s)."
            )
        )

    def reset_skill_tags(self):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "chat_next", "fixtures", "skill_tags.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            tags_data = yaml.safe_load(yaml_file)

        if not tags_data:
            self.stdout.write(
                self.style.WARNING("No data in skill_tags.yaml. Skipping.")
            )
            return

        created_count = 0
        for tag_entry in tags_data:
            name_en = tag_entry.get("name_en", "")
            name_fr = tag_entry.get("name_fr", "")
            if not name_en:
                continue
            _, created = SkillTag.objects.update_or_create(
                name_en=name_en,
                defaults={"name_fr": name_fr},
            )
            if created:
                created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Skill tags reset successfully. {created_count} new tag(s) created."
            )
        )

    def embed_skill_tags(self):
        """Compute and store embeddings for all SkillTags missing them."""
        from chat._llm.core import OttoLLM

        tags = list(SkillTag.objects.filter(embedding__isnull=True))
        if not tags:
            self.stdout.write(self.style.SUCCESS("All tags already have embeddings."))
            return

        llm = OttoLLM(mock_embedding=False)
        texts = [
            f"{tag.name_en or ''} / {tag.name_fr or ''}".strip(" /") for tag in tags
        ]
        embeddings = llm.embed_model.get_text_embedding_batch(texts)
        for tag, emb in zip(tags, embeddings):
            tag.embedding = emb
        SkillTag.objects.bulk_update(tags, ["embedding"])
        self.stdout.write(self.style.SUCCESS(f"Embedded {len(tags)} skill tag(s)."))

    def reset_security_labels(self):
        # Simply call manage.py loaddata security_labels.yaml
        call_command("loaddata", "security_labels.yaml")
        self.stdout.write(self.style.SUCCESS("Security labels reset successfully."))

    def reset_cost_types(self):
        # Simply call manage.py loaddata cost_types.yaml
        call_command("loaddata", "cost_types.yaml")
        self.stdout.write(self.style.SUCCESS("Cost types reset successfully."))

    def reset_cost_groups(self):
        from otto.models import CostGroup

        # Load or update default cost groups from fixture
        # Unlike other reset methods, we don't delete existing cost groups
        # because they may have protected foreign key relationships with Cost objects
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "otto", "fixtures", "cost_groups.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            cost_groups_data = yaml.safe_load(yaml_file)

        if not cost_groups_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for cost groups."
                )
            )
            return

        for item in cost_groups_data:
            if item.get("model") != "otto.CostGroup":
                continue

            fields = item.get("fields", {})
            cost_group_id = fields.get("cost_group_id")

            if not cost_group_id:
                continue

            # Update or create the cost group
            CostGroup.objects.update_or_create(
                cost_group_id=cost_group_id, defaults=fields
            )

        self.stdout.write(self.style.SUCCESS("Cost groups reset successfully."))
