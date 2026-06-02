## Summary

Describe what changed and why.

## Linked issue

Closes #

## Instructions for reviewers

Does the reviewer need to run any management commands, rebuild containers etc. in order to test?

## Checklist

### PR title

- [ ] PR title uses conventional commit format
- [ ] Title follows `type: short summary` or `type(scope): short summary`

Examples:

- `fix: chatbot not displaying errors`
- `feat: upload document preview`
- `refactor(librarian): extract document sync logic into utils`
- `fix(chat_next): preserve tool-call streaming state`

### Tests

- [ ] I ran all tests locally
- [ ] I added or updated tests where needed

### Translations

- [ ] No user-facing strings changed
- [ ] I internationalized all user-facing strings e.g. with `{% trans %}` or `_("text")`, then ran `python manage.py load_app_localization`.

### Readiness

- [ ] I have performed a self-review
- [ ] This PR is ready for a preliminary review
- [ ] This PR is ready to merge after review
