"""Tests for SMTP connection lifecycle management.

Tests cover NOOP health checks, stale connection detection,
automatic reconnection, and retry logic.
"""

from unittest.mock import AsyncMock

import pytest
from aiosmtplib import SMTPException
from mailcore import EmailAddress, SMTPError

from mailcore_aiosmtplib import AIOSMTPAdapter


@pytest.fixture
def adapter():
    """Create AIOSMTPAdapter instance for testing."""
    return AIOSMTPAdapter(
        host="smtp.example.com",
        port=587,
        username="test@example.com",
        password="test-password",  # pragma: allowlist secret
        use_tls=False,
        timeout=10,
    )


@pytest.mark.asyncio
async def test_ensure_connected_calls_noop_on_existing_connection(adapter):
    """_ensure_connected() checks health with NOOP on existing connection."""
    # Setup: Simulate already connected
    adapter._connected = True
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    # Execute
    await adapter._ensure_connected()

    # Verify: NOOP called, no reconnect
    adapter._smtp.noop.assert_called_once()
    adapter._smtp.connect.assert_not_called()
    adapter._smtp.login.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_connected_reconnects_on_noop_failure(adapter):
    """_ensure_connected() reconnects if NOOP fails (stale connection)."""
    # Setup: Simulate connected but NOOP fails
    adapter._connected = True
    adapter._smtp.noop = AsyncMock(side_effect=SMTPException("Connection lost"))
    adapter._smtp.quit = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    # Execute
    await adapter._ensure_connected()

    # Verify: Reconnected
    adapter._smtp.noop.assert_called_once()
    adapter._smtp.connect.assert_called_once()
    adapter._smtp.login.assert_called_once()
    assert adapter._connected is True


@pytest.mark.asyncio
async def test_ensure_connected_creates_new_connection_when_not_connected(adapter):
    """_ensure_connected() creates connection when not connected."""
    # Setup: Not connected
    adapter._connected = False
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    # Execute
    await adapter._ensure_connected()

    # Verify: Connected, no NOOP (nothing to check)
    adapter._smtp.noop.assert_not_called()
    adapter._smtp.connect.assert_called_once()
    adapter._smtp.login.assert_called_once()
    assert adapter._connected is True


@pytest.mark.asyncio
async def test_ensure_connected_handles_quit_failure_gracefully(adapter):
    """_ensure_connected() handles QUIT errors when cleaning up stale connection."""
    # Setup: NOOP fails, QUIT also fails (connection already dead)
    adapter._connected = True
    adapter._smtp.noop = AsyncMock(side_effect=SMTPException("Connection lost"))
    adapter._smtp.quit = AsyncMock(side_effect=Exception("Already closed"))
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    # Execute: Should not raise despite QUIT failure
    await adapter._ensure_connected()

    # Verify: Reconnected despite QUIT error
    assert adapter._connected is True
    adapter._smtp.connect.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_retries_on_timeout_error(adapter):
    """send_message() auto-reconnects and retries on timeout error."""
    # Setup
    adapter._connected = True

    # First send attempt fails with timeout
    send_error = SMTPException("451 4.4.2 Timeout - closing connection")
    adapter._smtp.send_message = AsyncMock(
        side_effect=[
            send_error,  # First attempt: timeout
            (
                {},
                "250 OK",
            ),  # Second attempt: success
        ]
    )
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.quit = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    # Execute
    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]
    result = await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test", body_text="Hello")

    # Verify: Reconnected and retried
    assert adapter._smtp.send_message.call_count == 2
    adapter._smtp.quit.assert_called_once()  # Cleanup after timeout
    adapter._smtp.connect.assert_called_once()  # Reconnect
    assert result.message_id  # Successful send


@pytest.mark.asyncio
async def test_send_message_retries_on_451_error(adapter):
    """send_message() retries on 451 temporary failure."""
    adapter._connected = True

    send_error = SMTPException("451 Temporary failure, please try again")
    adapter._smtp.send_message = AsyncMock(side_effect=[send_error, ({}, "250 OK")])
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.quit = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]
    result = await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test", body_text="Hello")

    assert adapter._smtp.send_message.call_count == 2
    assert result.message_id


@pytest.mark.asyncio
async def test_send_message_retries_on_421_error(adapter):
    """send_message() retries on 421 service closing."""
    adapter._connected = True

    send_error = SMTPException("421 Service closing transmission channel")
    adapter._smtp.send_message = AsyncMock(side_effect=[send_error, ({}, "250 OK")])
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.quit = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]
    result = await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test", body_text="Hello")

    assert adapter._smtp.send_message.call_count == 2
    assert result.message_id


@pytest.mark.asyncio
async def test_send_message_does_not_retry_on_permanent_error(adapter):
    """send_message() does NOT retry on non-timeout errors (e.g., 550 rejected)."""
    adapter._connected = True

    # Permanent error (should not retry)
    send_error = SMTPException("550 Mailbox not found")
    adapter._smtp.send_message = AsyncMock(side_effect=send_error)
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.quit = AsyncMock()
    adapter._smtp.connect = AsyncMock()

    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]

    with pytest.raises(SMTPError, match="Failed to send email"):
        await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test", body_text="Hello")

    # Verify: Only tried once (no retry)
    assert adapter._smtp.send_message.call_count == 1
    adapter._smtp.quit.assert_not_called()  # No reconnection attempted
    adapter._smtp.connect.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_retry_fails_raises_error(adapter):
    """send_message() raises error if retry also fails."""
    adapter._connected = True

    # Both attempts fail with timeout
    send_error = SMTPException("451 Timeout")
    adapter._smtp.send_message = AsyncMock(side_effect=[send_error, send_error])
    adapter._smtp.noop = AsyncMock()
    adapter._smtp.quit = AsyncMock()
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]

    with pytest.raises(SMTPError, match="Failed to send email"):
        await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test", body_text="Hello")

    # Verify: Attempted twice (initial + retry)
    assert adapter._smtp.send_message.call_count == 2
    adapter._smtp.connect.assert_called_once()  # Reconnected once


@pytest.mark.asyncio
async def test_multiple_sends_reuse_healthy_connection(adapter):
    """Multiple sends reuse same connection if healthy."""
    adapter._connected = True
    adapter._smtp.send_message = AsyncMock(return_value=({}, "250 OK"))
    adapter._smtp.noop = AsyncMock()  # Health check passes
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]

    # Send 3 messages
    await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test1", body_text="Hello1")
    await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test2", body_text="Hello2")
    await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test3", body_text="Hello3")

    # Verify: No reconnects (connection was healthy), NOOPs for health checks
    adapter._smtp.connect.assert_not_called()  # No reconnects (already connected)
    assert adapter._smtp.noop.call_count == 3  # Health check before each send
    assert adapter._smtp.send_message.call_count == 3


@pytest.mark.asyncio
async def test_send_message_ensures_connection_before_send(adapter):
    """send_message() calls _ensure_connected() before sending."""
    adapter._connected = False
    adapter._smtp.send_message = AsyncMock(return_value=({}, "250 OK"))
    adapter._smtp.connect = AsyncMock()
    adapter._smtp.login = AsyncMock()

    from_addr = EmailAddress("sender@example.com")
    to_addrs = [EmailAddress("recipient@example.com")]

    result = await adapter.send_message(from_=from_addr, to=to_addrs, subject="Test", body_text="Hello")

    # Verify: Connected before send
    adapter._smtp.connect.assert_called_once()
    adapter._smtp.login.assert_called_once()
    assert result.message_id
