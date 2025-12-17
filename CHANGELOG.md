# Changelog

All notable changes to mailcore-aiosmtplib will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-12-16

Initial alpha release of mailcore-aiosmtplib adapter.

### Added

- **AIOSMTPAdapter class** - Thin async wrapper around aiosmtplib.SMTP
  - Native async (no ThreadPoolExecutor overhead)
  - Implements `SMTPConnection` protocol from mailcore v1.0.0
  - Connection management: idempotent `_ensure_connected()`, graceful `disconnect()`
  - Domain type translation: `EmailAddress`, `Attachment`, `SendResult`
  - Lazy attachment fetching via `await attachment.read()` during send
  - Error wrapping: All aiosmtplib exceptions wrapped in `SMTPError` with clear messages
  - Exception chaining: Original errors preserved via `raise...from e`
  - Content-Type parsing: Automatic maintype/subtype detection for attachments
  - Full SMTP support: text, HTML, multipart, attachments, CC/BCC, threading headers

- **Error handling patterns**
  - `SMTPError` with actionable hints (e.g., "Gmail requires App Passwords")
  - Timeout errors: Clear message with timeout value
  - Connection errors: Include hostname and port in message
  - Authentication errors: Guidance for app passwords

- **Test coverage**
  - 16 unit tests with mocked aiosmtplib.SMTP (91% coverage)
  - 5 E2E tests with Greenmail SMTP server (skipped if unavailable)
  - Tests verify: text/HTML/multipart sending, attachments, CC/BCC, threading, error handling, connection idempotency

- **Documentation**
  - Complete README with Gmail/Outlook/Yahoo setup examples
  - Docstrings with Args, Returns, Raises, Examples
  - Usage examples: basic send, attachments, multipart, reply

### Dependencies

- mailcore>=1.0.0,<2.0.0 (stable contract)
- aiosmtplib>=3.0.0 (MIT license, native async)

### Notes

**Version 0.1.0 signals initial implementation:** This adapter implements the stable mailcore v1.0.0 SMTPConnection contract, but the internal implementation is alpha quality. The v0.x.x series allows iteration on performance, error handling, and edge cases based on real-world usage before committing to a v1.0.0 adapter API.

**Why not v1.0.0?** While mailcore's ABC contract is stable, adapter internals need battle-testing. Examples:
- Connection pooling strategies (currently single connection)
- Retry logic tuning (currently fail-fast)
- Error message refinement based on user feedback
- Performance optimization based on production metrics

**Migration to v1.0.0:** When adapter behavior is proven in production (Epic 4+), we'll release v1.0.0 with semver guarantees. Until then, v0.x.x allows us to improve the adapter without breaking mailcore's contract.
