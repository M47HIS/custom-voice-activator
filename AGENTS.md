# Voice AI repository guidance

Voice AI is a local macOS app. Read `README.md` and `SECURITY.md` before changing
microphone, Accessibility, Screen Recording, model, or backend behavior.

- Inspect the working tree first and preserve unrelated changes.
- Keep audio transcription and desktop decisions local by default.
- Keep the optional Docker backend authenticated and published on loopback only.
- Never commit credentials, recordings, transcripts, model weights, runtime
  configuration, or logs. Avoid putting transcript text in diagnostics.
- Run the focused Python and Swift checks in `README.md` after code changes.

Maintainer checkouts inside the AI OS workspace also follow that workspace's
`AGENTS.md` and private AI Memory protocol. External clones do not require the
maintainer's private vault.
