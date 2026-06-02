# Contributing to Otto

This file summarizes the PR and commit guidance from `README.md` in a format GitHub and tooling can find quickly.

## Branches

- Do not push directly to `main`.
- Branch from `main` for each change, for example `git checkout -b chatbot-error-messages`.
- Before opening a PR, make sure your branch is up to date with `main` and resolve any conflicts.

## Issues and pull requests

- Link every PR to an issue.
- If no issue exists yet, create one first so the change can be discussed.
- Use a **Draft PR** when the work is not ready for review.
- Get at least one reviewer before merging.

## Conventional commits

Use conventional commit style for both **commit messages** and **PR titles**.

Examples:

- `fix: chatbot not displaying errors`
- `feat: upload document preview`
- `chore: upgrade llama-index version`
- `refactor(librarian): extract document sync logic into utils`
- `fix(chat_next): preserve tool-call streaming state`

Preferred format:

- `type: short summary`
- `type(scope): short summary`

Common types include `fix`, `feat`, `refactor`, `chore`, `docs`, and `test`.

## Before opening a PR

- Run the relevant tests locally.
- Add or update tests for new functionality and bug fixes.
- If you changed user-facing strings, run `python manage.py load_app_localization` before opening the PR.

For full setup, testing, translation, and environment details, see `README.md`.