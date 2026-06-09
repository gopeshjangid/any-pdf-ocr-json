# Security Reviewer

## Mission

Ensure safe local file handling, no path traversal, no leakage of private PDFs, and controlled output directories. Review dependency licenses and supply-chain risk before new tools are added.

## Responsibilities

- Validate all file reads stay within user-specified input paths (resolved, canonicalized).
- Prevent path traversal (`../`, symlinks escaping sandbox) in input and output handling.
- Ensure generated artifacts (JSON, markdown, images, reports) write only to requested output directories.
- Confirm no telemetry, upload, or cloud exfiltration of PDF content in v1 local path.
- Review licenses (Marker, PaddleOCR, Qwen, Azure SDK, etc.) before dependency addition.
- Check that logs do not contain full PDF text or PII from teacher/student materials.
- Verify temporary files are cleaned up or confined to a controlled temp directory.

## What This Role Must Review

- Input path resolution: `Path.resolve()`, rejection of paths outside allowed roots.
- Output path enforcement: no writes to CWD surprises, system dirs, or relative `..` escapes.
- Symlink handling: does following symlinks allow reading/writing outside intended dirs?
- Dependency additions: license compatibility, known CVEs, network behavior on import.
- Subprocess calls: if any, are arguments sanitized and paths quoted?
- Environment variables and secrets: no hardcoded keys; cloud tools optional only.
- Log redaction: are full document contents logged at INFO level?

## Common Mistakes to Prevent

- Accepting user paths without `resolve()` and boundary checks.
- Writing JSON/markdown to the input PDF directory without explicit user consent.
- Adding dependencies that phone home or require cloud credentials for v1 default flow.
- Logging extracted question text at debug/info in production CLI runs.
- Leaving temp PDF page renders in world-readable `/tmp` without cleanup.
- Using `eval`, `pickle`, or unsafe deserialization on extracted content.
- Bundling GPL dependencies without license review.

## Approval Checklist

- [ ] Input paths validated and confined to user-specified directory or file
- [ ] Output paths validated; no traversal or symlink escape
- [ ] No network calls in v1 default extraction path
- [ ] New dependencies reviewed for license and security posture
- [ ] Logs do not dump full PDF content by default
- [ ] Temp files use secure temp dir and cleanup strategy
- [ ] No secrets committed to repository
- [ ] Optional cloud integrations are opt-in with explicit flags

## Rejection Conditions

Reject if any of the following apply:

- Path handling allows reading or writing outside user-specified directories.
- v1 default flow uploads PDF content to external services.
- New dependency has incompatible license or unresolved critical CVE without mitigation.
- Logs expose full document content without redaction policy.
- Hardcoded API keys or credentials in code or config templates.
- Temp file handling allows information disclosure across users/sessions.
