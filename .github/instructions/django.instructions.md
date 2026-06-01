---
applyTo: "django/**"
---

# Django App Development

Use this guidance for work in `django/**` unless a more specific instruction file applies.

## Assistant App Default

- Assume work on the AI assistant should happen in `django/chat_next/**` unless the user explicitly asks to modify legacy `django/chat/**`.
- Do not modify the legacy `chat` app unless the user clearly requests changes there.
- When work is specifically in `django/chat_next/**` or `django/chat/**`, also follow the more specific app instruction file for that directory.

## Commands and Project Structure

- Always run Django management commands as `python /workspace/django/manage.py {command}` to avoid wrong-directory mistakes.
- Run `makemigrations` and `migrate` when model changes require them.
- If `cost_types.yaml` or `presets.yaml` changes, use `manage.py reset_app_data cost_types presets`.

## Localization

- Use Django modeltranslation where configured in `{app}/translation.py`.
- Otherwise use standard gettext patterns such as `_("english string")` and `{% trans "english string" %}`.
- After changing translation strings, run `manage.py load_app_localization`.

## Logging and Background Work

- Use `structlog`, for example `from structlog import get_logger; logger = get_logger(__name__)`.
- Celery workers are typically started from `/workspace/django` with `celery -A otto worker -l INFO --pool=gevent --concurrency=256`.
- Restart Celery workers manually after relevant code changes.

## Templates, Frontend, and UI

- Prefer HTMX in templates when it fits.
- Use vanilla JS only when needed; do not use jQuery.
- Inline scripts are acceptable when an HTMX fragment needs them or when Django template variables must be embedded directly.
- Usually place JavaScript in `{app}/static/{app}/js`.
- Prefer subdirectories for reusable component templates and `{% include %}` when practical.
- Prefer Bootstrap 5 classes, especially grid layout (`row` / `col`) when equally valid.
- Keep CSS overrides in `otto/static/style.css` or app-local static CSS.
- Use SASS/SCSS only when there is a clear need.

## Views, Helpers, and Permissions

- Use Django forms in `{app}/forms.py`, preferably `ModelForm` where appropriate.
- HTMX fragments usually deserve their own views and URL patterns.
- Always update `{app}/urls.py` when adding new views.
- Prefer `{app}/utils.py` or another helper module for logic that does not return HTTP responses.
- If `views.py` gets too large, refactor into `{app}/_views/{component}.py` following existing patterns such as `django/chat/_views/`.
- Use `django-rules` permissions in views and templates.
- We do not use Django admin; create custom admin-style views instead.

## Messages and Streaming

- Use the Django messages framework for success, warning, and error notices.
- Message notifications already integrate with HTMX responses; no full-page reload is needed.
- Many HTMX components rely on polling or SSE (`StreamingHttpResponse`); preserve those patterns when extending existing flows.

## Testing

- Write tests in `django/tests/`.
- Prefer integration tests for views and unit tests for helper functions.
- Most authenticated tests should begin from the existing fixture pattern:

  `@pytest.mark.django_db`

  `def test_something(client, all_apps_user):`

  `    user = all_apps_user()`

  `    client.force_login(user)`

- For SSE views, exhaust the `StreamingHttpResponse` generator in the test before asserting on content.
- Check nearby tests and `conftest.py` before inventing new patterns.

## Working Style

- Prefer the minimal solution that works.
- Look for similar code examples in the repo and follow existing patterns.
- Prefer small, focused file changes when practical; refactor oversized Django modules into smaller helpers or `_views/` modules following existing repo patterns.

## Commit and PR Conventions

- Use conventional commit style for commit messages and pull request titles.
- Prefer `type: short summary` or `type(scope): short summary`, for example `fix: ...`, `feat: ...`, or `refactor(librarian): ...`.
