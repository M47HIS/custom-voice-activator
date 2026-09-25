# Security Policy

## Supported versions

Security fixes are applied to the latest revision on `main` until the project
publishes versioned releases.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository.
If private reporting is unavailable, contact the maintainer privately through
their GitHub profile. Do not include credentials, auth tokens, private audio, or
transcripts in a public issue.

Include the affected component, reproduction steps, impact, and any suggested
mitigation. You should receive an acknowledgement within seven days.

## Security expectations

- The optional backend must remain bound to `127.0.0.1`.
- Runtime auth tokens must never be committed or printed to logs.
- Backend tokens must be provisioned locally; unauthenticated network bootstrap
  is not supported.
- Transcript, configuration, upload, and WebSocket endpoints require a bearer
  token plus loopback Host/Origin validation. The optional backend keeps a
  short transcript history in memory for authenticated local clients.
- Backend action configuration is clipboard-only and must not reach command,
  AppleScript, or arbitrary HTTP execution.
- Audio recordings and transcripts should be treated as sensitive user data.
- Voice control uses macOS Accessibility and optional Screen Recording access;
  grant these permissions only on a Mac you trust. Its spoken confirmation
  heuristic is a safeguard, not a transaction authorization boundary.
- Custom transcription commands are user-controlled trust boundaries and
  should be reviewed before use.
