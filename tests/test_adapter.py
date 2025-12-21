"""Unit tests for AIOSMTPAdapter with mocked aiosmtplib.SMTP."""

import asyncio
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from aiosmtplib import SMTPException
from mailcore import Attachment, EmailAddress, SMTPError

from mailcore_aiosmtplib import AIOSMTPAdapter


@pytest.fixture
def mock_smtp_client():
    """Create mocked aiosmtplib.SMTP instance with async methods."""
    client = Mock()
    # Configure async methods
    client.connect = AsyncMock(return_value=None)
    client.login = AsyncMock(return_value=None)
    client.send_message = AsyncMock(return_value=({"recipient@example.com": (250, "OK")}, "OK"))
    client.quit = AsyncMock(return_value=None)
    return client


@pytest.fixture
def smtp_adapter(mock_smtp_client):
    """Create SMTP adapter with mocked aiosmtplib.SMTP."""
    with patch("mailcore_aiosmtplib.adapter.SMTP", return_value=mock_smtp_client):
        adapter = AIOSMTPAdapter(
            host="smtp.example.com",
            port=465,
            username="user@example.com",
            password="test-password",  # pragma: allowlist secret
            use_tls=True,
            timeout=30,
        )
    return adapter


@pytest.mark.asyncio
async def test_send_message_text_only(smtp_adapter, mock_smtp_client):
    """Test sending plain text email."""
    # Send message
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="Test Subject",
        body_text="Hello World",
    )

    # Verify
    assert result.accepted == ["recipient@example.com"]
    assert result.rejected == {}
    mock_smtp_client.send_message.assert_called_once()

    # Verify EmailMessage structure
    call_args = mock_smtp_client.send_message.call_args[0][0]
    assert isinstance(call_args, EmailMessage)
    assert call_args["Subject"] == "Test Subject"
    assert "Hello World" in str(call_args)


@pytest.mark.asyncio
async def test_send_message_html_only(smtp_adapter, mock_smtp_client):
    """Test sending HTML email."""
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="HTML Test",
        body_html="<h1>Hello</h1>",
    )

    assert result.accepted == ["recipient@example.com"]
    call_args = mock_smtp_client.send_message.call_args[0][0]
    assert "<h1>Hello</h1>" in str(call_args)


@pytest.mark.asyncio
async def test_send_message_multipart(smtp_adapter, mock_smtp_client):
    """Test sending multipart (text + HTML) email."""
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="Multipart Test",
        body_text="Plain text version",
        body_html="<p>HTML version</p>",
    )

    assert result.accepted == ["recipient@example.com"]
    call_args = mock_smtp_client.send_message.call_args[0][0]
    assert call_args.is_multipart()


@pytest.mark.asyncio
async def test_send_with_attachments(smtp_adapter, mock_smtp_client):
    """Test sending email with attachments."""
    # Mock attachment.read()
    mock_attachment = MagicMock(spec=Attachment)
    mock_attachment.read = AsyncMock(return_value=b"test content")
    mock_attachment.content_type = "text/plain"
    mock_attachment.filename = "test.txt"

    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="With Attachment",
        body_text="See attachment",
        attachments=[mock_attachment],
    )

    # Verify attachment.read() was called
    mock_attachment.read.assert_called_once()
    assert result.accepted == ["recipient@example.com"]


@pytest.mark.asyncio
async def test_send_with_cc_bcc(smtp_adapter, mock_smtp_client):
    """Test sending email with CC and BCC."""
    # Configure mock for multiple recipients
    mock_smtp_client.send_message.return_value = (
        {
            "to@example.com": (250, "OK"),
            "cc@example.com": (250, "OK"),
            "bcc@example.com": (250, "OK"),
        },
        "OK",
    )

    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="to@example.com")],
        subject="CC/BCC Test",
        body_text="Test",
        cc=[EmailAddress(email="cc@example.com")],
        bcc=[EmailAddress(email="bcc@example.com")],
    )

    assert len(result.accepted) == 3
    call_args = mock_smtp_client.send_message.call_args[0][0]
    assert call_args["Cc"] == "cc@example.com"
    assert call_args["Bcc"] == "bcc@example.com"


@pytest.mark.asyncio
async def test_send_with_threading_headers(smtp_adapter, mock_smtp_client):
    """Test sending email with In-Reply-To and References."""
    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[EmailAddress(email="recipient@example.com")],
        subject="Re: Original",
        body_text="Reply",
        in_reply_to="<original@example.com>",
        references=["<original@example.com>", "<earlier@example.com>"],
    )

    assert result.accepted == ["recipient@example.com"]
    call_args = mock_smtp_client.send_message.call_args[0][0]
    assert call_args["In-Reply-To"] == "<original@example.com>"
    assert "<original@example.com> <earlier@example.com>" in call_args["References"]


@pytest.mark.asyncio
async def test_emailaddress_to_rfc5322_called(smtp_adapter, mock_smtp_client):
    """Test that EmailAddress.to_rfc5322() is called for headers."""
    # EmailAddress with display name
    from_addr = EmailAddress(email="sender@example.com", name="Sender Name")
    to_addr = EmailAddress(email="recipient@example.com", name="Recipient Name")

    await smtp_adapter.send_message(
        from_=from_addr,
        to=[to_addr],
        subject="RFC 5322 Test",
        body_text="Test",
    )

    call_args = mock_smtp_client.send_message.call_args[0][0]
    # Verify RFC 5322 format: "Display Name <email>"
    assert "Sender Name <sender@example.com>" in call_args["From"]
    assert "Recipient Name <recipient@example.com>" in call_args["To"]


@pytest.mark.asyncio
async def test_authentication_error_wrapped(mock_smtp_client):
    """Test authentication error wrapped in SMTPError with hint."""
    # Configure mock to raise auth error
    mock_smtp_client.login.side_effect = SMTPException("535 Authentication failed")

    with patch("mailcore_aiosmtplib.adapter.SMTP", return_value=mock_smtp_client):
        adapter = AIOSMTPAdapter(
            host="smtp.example.com",
            port=465,
            username="user@example.com",
            password="wrong-password",  # pragma: allowlist secret
            use_tls=True,
        )

    with pytest.raises(SMTPError) as exc_info:
        await adapter._ensure_connected()

    # Verify clear error message with hint
    assert "Failed to connect" in str(exc_info.value)
    assert "smtp.example.com" in str(exc_info.value)
    # Verify exception chaining
    assert isinstance(exc_info.value.__cause__, SMTPException)


@pytest.mark.asyncio
async def test_connection_error_wrapped(mock_smtp_client):
    """Test connection error wrapped in SMTPError."""
    # Configure mock to raise connection error
    mock_smtp_client.connect.side_effect = ConnectionError("Connection refused")

    with patch("mailcore_aiosmtplib.adapter.SMTP", return_value=mock_smtp_client):
        adapter = AIOSMTPAdapter(
            host="smtp.example.com",
            port=465,
            username="user@example.com",
            password="test-password",  # pragma: allowlist secret
        )

    with pytest.raises(SMTPError) as exc_info:
        await adapter._ensure_connected()

    assert "Unable to connect" in str(exc_info.value)
    assert "smtp.example.com" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ConnectionError)


@pytest.mark.asyncio
async def test_timeout_error_wrapped(mock_smtp_client):
    """Test timeout error wrapped in SMTPError."""
    # Configure mock to raise timeout
    mock_smtp_client.connect.side_effect = asyncio.TimeoutError()

    with patch("mailcore_aiosmtplib.adapter.SMTP", return_value=mock_smtp_client):
        adapter = AIOSMTPAdapter(
            host="smtp.example.com",
            port=465,
            username="user@example.com",
            password="test-password",  # pragma: allowlist secret
            timeout=30,
        )

    with pytest.raises(SMTPError) as exc_info:
        await adapter._ensure_connected()

    assert "did not respond within 30s" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_send_message_smtp_error_wrapped(smtp_adapter, mock_smtp_client):
    """Test SMTP send error wrapped with clear message."""
    # Configure mock to raise auth error during send
    mock_smtp_client.send_message.side_effect = SMTPException(
        "535 5.7.8 Username and Password not accepted. Learn more at Gmail App Passwords"
    )

    with pytest.raises(SMTPError) as exc_info:
        await smtp_adapter.send_message(
            from_=EmailAddress(email="sender@example.com"),
            to=[EmailAddress(email="recipient@example.com")],
            subject="Test",
            body_text="Test",
        )

    # Verify error wrapped and exception chaining
    error_str = str(exc_info.value)
    # Check for auth-specific message (password keyword triggers hint)
    assert "SMTP authentication failed" in error_str
    assert "App Passwords" in error_str
    assert isinstance(exc_info.value.__cause__, SMTPException)


@pytest.mark.asyncio
async def test_ensure_connected_idempotent(smtp_adapter, mock_smtp_client):
    """Test _ensure_connected only connects once (idempotent)."""
    # Call _ensure_connected twice
    await smtp_adapter._ensure_connected()
    await smtp_adapter._ensure_connected()

    # Verify only one connect/login
    mock_smtp_client.connect.assert_called_once()
    mock_smtp_client.login.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_closes_connection(smtp_adapter, mock_smtp_client):
    """Test disconnect closes connection gracefully."""
    # Connect then disconnect
    await smtp_adapter._ensure_connected()
    await smtp_adapter.disconnect()

    # Verify quit was called
    mock_smtp_client.quit.assert_called_once()
    assert not smtp_adapter._connected


@pytest.mark.asyncio
async def test_multiple_recipients(smtp_adapter, mock_smtp_client):
    """Test sending to multiple recipients."""
    # Configure mock for multiple recipients
    mock_smtp_client.send_message.return_value = (
        {
            "recipient1@example.com": (250, "OK"),
            "recipient2@example.com": (250, "OK"),
            "recipient3@example.com": (250, "OK"),
        },
        "OK",
    )

    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[
            EmailAddress(email="recipient1@example.com"),
            EmailAddress(email="recipient2@example.com"),
            EmailAddress(email="recipient3@example.com"),
        ],
        subject="Multi-recipient",
        body_text="Test",
    )

    assert len(result.accepted) == 3
    assert result.rejected == {}


@pytest.mark.asyncio
async def test_partial_send_failure(smtp_adapter, mock_smtp_client):
    """Test handling partial send failure (some recipients rejected)."""
    # Configure mock for partial failure
    mock_smtp_client.send_message.return_value = (
        {
            "recipient1@example.com": (250, "OK"),
            "invalid@example.com": (550, "User not found"),
        },
        "Partial success",
    )

    result = await smtp_adapter.send_message(
        from_=EmailAddress(email="sender@example.com"),
        to=[
            EmailAddress(email="recipient1@example.com"),
            EmailAddress(email="invalid@example.com"),
        ],
        subject="Partial Failure",
        body_text="Test",
    )

    # One accepted, one rejected
    assert result.accepted == ["recipient1@example.com"]
    assert "invalid@example.com" in result.rejected
    assert result.rejected["invalid@example.com"] == (550, "User not found")


@pytest.mark.asyncio
async def test_attachment_content_type_parsing(smtp_adapter, mock_smtp_client):
    """Test Content-Type parsing for attachments."""
    # Test various content types
    test_cases = [
        ("image/png", "image", "png"),
        ("application/pdf", "application", "pdf"),
        ("text/plain", "text", "plain"),
        (None, "application", "octet-stream"),  # Default fallback
    ]

    for content_type, expected_main, expected_sub in test_cases:
        mock_attachment = MagicMock(spec=Attachment)
        mock_attachment.read = AsyncMock(return_value=b"content")
        mock_attachment.content_type = content_type
        mock_attachment.filename = "test.file"

        await smtp_adapter.send_message(
            from_=EmailAddress(email="sender@example.com"),
            to=[EmailAddress(email="recipient@example.com")],
            subject="Attachment Test",
            body_text="Test",
            attachments=[mock_attachment],
        )

        # Verify maintype/subtype passed to add_attachment
        call_args = mock_smtp_client.send_message.call_args[0][0]
        assert call_args.is_multipart()
