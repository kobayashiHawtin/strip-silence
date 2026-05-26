# Security Policy

## Supported Versions

This project is pre-1.0 and currently supports the latest `main` branch.

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead, report privately via one of the following:

- GitHub Security Advisories (preferred)
- Direct contact with maintainers (if configured in repository settings)

When reporting, include:

- Affected version / commit hash
- Reproduction steps
- Impact assessment
- Suggested mitigation (if available)

We will acknowledge receipt as quickly as possible and coordinate a responsible disclosure timeline.

## Scope Notes

- Treat all file-path handling and XML parsing changes as security-sensitive.
- Never commit personal project files, local absolute paths, tokens, API keys, or credentials.
- CI security checks are enabled to reduce accidental leaks and known dependency vulnerabilities.
