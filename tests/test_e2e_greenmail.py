"""E2E tests for AIOSMTPAdapter with Greenmail SMTP server.

These tests require Greenmail running:
    docker run -p 3025:3025 -p 8080:8080 greenmail/standalone

Uses Greenmail API (port 8080) for:
- Verification: GET /api/user/{email}/messages/{folder}
- Cleanup: POST /api/service/reset
- Status: GET /api/service/readiness
"""

import asyncio

import pytest
from mailcore import Attachment, EmailAddress

from mailcore_aiosmtplib import AIOSMTPAdapter

# Check if httpx and Greenmail are available
try:
    import httpx

    response = httpx.get("http://localhost:8080/api/service/readiness", timeout=1.0)
    GREENMAIL_AVAILABLE = response.status_code == 200
except ImportError:
    GREENMAIL_AVAILABLE = False
    httpx = None  # type: ignore
except Exception:  # Any connection error
    GREENMAIL_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not GREENMAIL_AVAILABLE,
    reason="Greenmail server not running. Start with: docker run -p 3025:3025 -p 8080:8080 greenmail/standalone",
)


@pytest.fixture
async def smtp_adapter():
    """Create SMTP adapter connected to Greenmail."""
    adapter = AIOSMTPAdapter(
        host="localhost",
        port=3025,
        username="test@example.com",  # Greenmail accepts any credentials
        password="test",  # pragma: allowlist secret
        use_tls=False,  # Greenmail test server doesn't require TLS
        timeout=10,
    )
    yield adapter
    await adapter.disconnect()


@pytest.fixture
async def clear_greenmail():
    """Clear Greenmail state before/after each test using API."""
    async with httpx.AsyncClient() as client:
        # Reset Greenmail (purges all mail and resets to initial state)
        await client.post("http://localhost:8080/api/service/reset")
        yield
        # Reset after test
        await client.post("http://localhost:8080/api/service/reset")


@pytest.mark.asyncio
async def test_send_plain_text_email(smtp_adapter, clear_greenmail):
    """Send plain text email and verify via Greenmail API."""
    # Send email
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com", name="Sender Name"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="E2E Plain Text Test",
        body_text="This is a plain text email from E2E test.",
    )

    # Verify send result
    assert result.accepted == ["recipient@example.com"]
    assert result.rejected == {}

    # Wait for email to be delivered
    await asyncio.sleep(0.5)

    # Verify via Greenmail API (Greenmail auto-creates users)
    async with httpx.AsyncClient() as client:
        # Get messages for recipient
        response = await client.get("http://localhost:8080/api/user/recipient@example.com/messages/INBOX")
        assert response.status_code == 200
        messages = response.json()

        assert len(messages) >= 1, "Expected at least one message in INBOX"

        # Check message details
        msg = messages[0]
        assert msg["subject"] == "E2E Plain Text Test"
        # Verify raw MIME contains expected content
        assert "This is a plain text email from E2E test" in msg["mimeMessage"]


@pytest.mark.asyncio
async def test_send_html_email(smtp_adapter, clear_greenmail):
    """Send HTML email and verify structure."""
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="E2E HTML Test",
        body_html="<h1>HTML Email</h1><p>This is HTML content.</p>",
    )

    assert result.accepted == ["recipient@example.com"]

    # Wait for delivery
    await asyncio.sleep(0.5)

    # Verify via Greenmail API
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:8080/api/user/recipient@example.com/messages/INBOX")
        assert response.status_code == 200
        messages = response.json()

        assert len(messages) >= 1
        msg = messages[0]
        assert msg["subject"] == "E2E HTML Test"
        assert "<h1>HTML Email</h1>" in msg["mimeMessage"]


@pytest.mark.asyncio
async def test_send_with_attachment_data_uri(smtp_adapter, clear_greenmail):
    """Send with data: URI attachment and verify attachment present."""
    # Create attachment from bytes
    test_content = b"Test attachment content"
    attachment = Attachment.from_bytes(test_content, filename="test.txt", content_type="text/plain")

    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="E2E Attachment Test",
        body_text="Email with attachment",
        attachments=[attachment],
    )

    assert result.accepted == ["recipient@example.com"]

    # Wait for delivery
    await asyncio.sleep(0.5)

    # Verify via Greenmail API
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:8080/api/user/recipient@example.com/messages/INBOX")
        assert response.status_code == 200
        messages = response.json()

        assert len(messages) >= 1
        msg = messages[0]
        assert msg["subject"] == "E2E Attachment Test"
        # Multipart indicates attachment present
        assert "Content-Type: multipart" in msg["mimeMessage"]
        assert "test.txt" in msg["mimeMessage"]


@pytest.mark.asyncio
async def test_send_multiple_recipients(smtp_adapter, clear_greenmail):
    """Send to multiple recipients (To, CC, BCC) and verify all received."""
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="to@example.com")],
        cc=[EmailAddress(email="cc@example.com")],
        bcc=[EmailAddress(email="bcc@example.com")],
        subject="E2E Multiple Recipients",
        body_text="Test message",
    )

    # All recipients should be accepted
    assert len(result.accepted) == 3
    assert "to@example.com" in result.accepted
    assert "cc@example.com" in result.accepted
    assert "bcc@example.com" in result.accepted

    # Wait for delivery
    await asyncio.sleep(0.5)

    # Verify each recipient received the email via Greenmail API
    async with httpx.AsyncClient() as client:
        for recipient in ["to@example.com", "cc@example.com", "bcc@example.com"]:
            response = await client.get(f"http://localhost:8080/api/user/{recipient}/messages/INBOX")
            assert response.status_code == 200
            messages = response.json()
            assert len(messages) >= 1, f"Expected message for {recipient}"
            assert messages[0]["subject"] == "E2E Multiple Recipients"


@pytest.mark.asyncio
async def test_greenmail_api_reset():
    """Verify Greenmail API reset works for cleanup."""
    async with httpx.AsyncClient() as client:
        # Test readiness endpoint
        response = await client.get("http://localhost:8080/api/service/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Service running"

        # Test reset endpoint (purges all mail)
        response = await client.post("http://localhost:8080/api/service/reset")
        assert response.status_code == 200
        data = response.json()
        assert "reset" in data["message"].lower()
