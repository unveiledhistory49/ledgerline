# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | Yes |

## Reporting a vulnerability

Open a **private security advisory** on GitHub (Security tab → Advisories) or
contact the maintainers directly. Please include:

- What you did, step by step, to trigger the issue
- What you expected vs. what happened
- The commit hash you tested against

We aim to acknowledge reports within 3 business days. Do not open a public
issue for a suspected vulnerability until a fix is released.

## What is already documented

- Threat model and trust boundaries: `docs/THREAT_MODEL.md`
- Key rotation procedure (manual, no endpoint): `docs/RUNBOOK.md`
- Known residual risks (single-node SQLite default, shared-pepper key hashing,
  no key rotation endpoint, permissive overdrafts) are listed in
  `docs/THREAT_MODEL.md` and `README.md` under Limitations.

## Dependency audits

`pip-audit` runs in CI on every push and pull request
(see `.github/workflows/ci.yml`). Dependabot is configured for pip and
GitHub Actions updates (see `.github/dependabot.yml`).
