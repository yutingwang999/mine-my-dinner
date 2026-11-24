# Project Rules

## Git

The user performs all Git operations that change the repository or a remote, including commits, pushes, branch changes, and staging. When one is needed, give the user the exact commands to run and stop until they confirm it is done.

Read-only Git commands, such as `git status`, `git diff`, and `git log`, may be run directly.

These rules override any skill or workflow step that tells Claude to run Git commands that change the repository or a remote.

## Python

Use the Python interpreter set by `python.defaultInterpreterPath` in `.vscode/settings.json` for all Python work. Read the path from that file each time instead of copying it elsewhere.

Do not install Python packages. If a package is needed, stop and tell the user which package is needed and why.

## Secrets

Never read, print, create, edit, or delete `.env` files. When a value in `.env` is needed, tell the user what to set. Never print or log the values of secret environment variables.

Application code gets its configuration from environment variables, so running the app, scripts, and database queries is allowed.

`.env.example` is not covered by this rule and must never hold real secrets.

## Language

Write every file in this repository in English, including code, comments, documentation, plans, and configuration.

Chinese is allowed only where the product's business scenarios require it:

- Chinese UI translations
- Chinese names and aliases in ingredient and cookware dictionaries
- Chinese recipe text, and the expected results for that text, in tests and prompt examples
- The Chinese copy of the README (`README.zh-CN.md`), which must match `README.md`

Conversations with the user are not covered by this rule.
