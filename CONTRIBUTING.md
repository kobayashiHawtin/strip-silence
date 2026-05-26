# Contributing

Thanks for contributing to `ableton-strip-silence`.

## Security-first rules

- Do **not** commit secrets (tokens, API keys, private keys, credentials).
- Do **not** commit personal local paths (for example, `C:\\Users\\...`).
- Do **not** commit personal audio projects, rendered media, or generated outputs.
- Use relative paths in docs and scripts whenever possible.

## Setup

1. Create and activate a virtual environment.
2. Install the project in editable mode.
3. Install pre-commit and set up hooks.

## Before opening a PR

Run these checks locally:

- Unit tests
- Static security scan
- Dependency vulnerability scan
- Secret scan (pre-commit hooks)

The GitHub Actions workflow will run the same checks on PRs.

## Pull Request checklist

- [ ] No secrets or private credentials are present.
- [ ] No absolute personal paths are present.
- [ ] Tests pass locally.
- [ ] Behavior changes are documented in `README.md`.
- [ ] Security-sensitive changes are explained in the PR description.
