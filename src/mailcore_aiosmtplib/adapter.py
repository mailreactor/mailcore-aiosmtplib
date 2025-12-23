"""aiosmtplib adapter for mailcore SMTPConnection protocol."""

import asyncio
from email.message import EmailMessage

from aiosmtplib import SMTP, SMTPException
from mailcore import (
    Attachment,
    EmailAddress,
    SendResult,
    SMTPConnection,
    SMTPError,
)


class AIOSMTPAdapter(SMTPConnection):
    """Thin async wrapper around aiosmtplib.SMTP for mailcore.

    This adapter translates mailcore domain types (EmailAddress, Attachment, SendResult)
    to SMTP protocol via stdlib EmailMessage. No ThreadPoolExecutor needed - aiosmtplib
    is natively async.

    Args:
        host: SMTP server hostname
        port: SMTP server port (465 for TLS, 587 for STARTTLS)
        username: SMTP username (usually email address)
        password: SMTP password (app password recommended)
        use_tls: Use TLS connection (True for port 465, False for port 587 + STARTTLS)
        timeout: Operation timeout in seconds

    Example:
        >>> smtp = AIOSMTPAdapter(
        ...     host='smtp.gmail.com',
        ...     port=465,
        ...     username='user@gmail.com',
        ...     password='app-password',  # pragma: allowlist secret
        ...     use_tls=True
        ... )
        >>> result = await smtp.send_message(
        ...     from_=EmailAddress(email='user@gmail.com'),
        ...     to=[EmailAddress(email='recipient@example.com')],
        ...     subject='Hello',
        ...     body_text='Hello World'
        ... )
        >>> print(result.message_id)
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool = True,
        timeout: int = 30,
    ) -> None:
        """Initialize SMTP adapter with connection parameters."""
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout
        self._smtp = SMTP(hostname=host, port=port, use_tls=use_tls, timeout=timeout)
        self._connected = False

    @property
    def username(self) -> str:
        """Get SMTP authentication username.

        Returns:
            Username used for SMTP authentication.
        """
        return self._username

    async def _ensure_connected(self) -> None:
        """Connect and authenticate if not already connected.

        This method is idempotent - safe to call multiple times. Only
        connects once, subsequent calls are no-op.

        Raises:
            SMTPError: Connection or authentication failure with clear message
        """
        if not self._connected:
            try:
                await self._smtp.connect()
                await self._smtp.login(self._username, self._password)
                self._connected = True
            except SMTPException as e:
                raise SMTPError(f"Failed to connect to SMTP server {self._host}:{self._port}") from e
            except asyncio.TimeoutError as e:
                raise SMTPError(f"SMTP server {self._host} did not respond within {self._timeout}s") from e
            except ConnectionError as e:
                raise SMTPError(f"Unable to connect to SMTP server {self._host}:{self._port}") from e

    async def send_message(
        self,
        from_: EmailAddress,
        to: list[EmailAddress],
        subject: str,
        body_text: str | None = None,
        body_html: str | None = None,
        cc: list[EmailAddress] | None = None,
        bcc: list[EmailAddress] | None = None,
        attachments: list[Attachment] | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> SendResult:
        """Send email message via SMTP.

        Translates mailcore domain types to SMTP protocol. Fetches attachment
        content lazily during send via await attachment.read().

        Args:
            from_: Sender email address
            to: List of recipient email addresses
            subject: Email subject
            body_text: Plain text body (optional)
            body_html: HTML body (optional)
            cc: CC recipients (optional)
            bcc: BCC recipients (optional)
            attachments: List of attachments (optional, content fetched during send)
            in_reply_to: Message-ID of email being replied to (optional)
            references: List of Message-IDs for threading (optional)

        Returns:
            SendResult with message_id and recipient status

        Raises:
            SMTPError: Send failure with clear message and exception chaining
        """
        await self._ensure_connected()

        # Build EmailMessage
        msg = EmailMessage()
        msg["From"] = from_.to_rfc5322()
        msg["To"] = ", ".join(addr.to_rfc5322() for addr in to)
        msg["Subject"] = subject

        if cc:
            msg["Cc"] = ", ".join(addr.to_rfc5322() for addr in cc)
        if bcc:
            msg["Bcc"] = ", ".join(addr.to_rfc5322() for addr in bcc)

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = " ".join(references)

        # Set body (text-only, HTML-only, or multipart)
        if body_text and body_html:
            msg.set_content(body_text)
            msg.add_alternative(body_html, subtype="html")
        elif body_html:
            msg.set_content(body_html, subtype="html")
        elif body_text:
            msg.set_content(body_text)

        # Fetch and add attachments (lazy fetch via resolvers)
        if attachments:
            for att in attachments:
                content = await att.read()  # Triggers resolver I/O
                # Parse Content-Type for maintype/subtype
                content_type = att.content_type or "application/octet-stream"
                maintype, _, subtype = content_type.partition("/")
                msg.add_attachment(
                    content,
                    maintype=maintype or "application",
                    subtype=subtype or "octet-stream",
                    filename=att.filename,
                )

        # Send via SMTP
        try:
            response = await self._smtp.send_message(msg)
            # aiosmtplib returns tuple: (response_dict, response_str)
            # response_dict: {recipient: SMTPResponse(code, message)} or {} if all succeeded
            result_dict = response[0] if isinstance(response, tuple) else response

            if result_dict:
                # Per-recipient status available (some failures)
                accepted = [addr for addr, resp in result_dict.items() if resp[0] == 250]
                rejected = {addr: (resp[0], resp[1]) for addr, resp in result_dict.items() if resp[0] != 250}
            else:
                # Empty dict means all recipients accepted (success case)
                all_recipients = [addr.email for addr in to]
                if cc:
                    all_recipients.extend([addr.email for addr in cc])
                if bcc:
                    all_recipients.extend([addr.email for addr in bcc])
                accepted = all_recipients
                rejected = {}

            return SendResult(
                message_id=msg["Message-ID"] or "",
                accepted=accepted,
                rejected=rejected,
            )
        except SMTPException as e:
            # Wrap SMTP protocol errors in domain exception
            error_msg = str(e).lower()
            if "authentication" in error_msg or "password" in error_msg:
                raise SMTPError(
                    f"SMTP authentication failed for {self._username}. "
                    "Check credentials. Gmail/Outlook require App Passwords."
                ) from e
            raise SMTPError(f"Failed to send email: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise SMTPError(f"SMTP server did not respond within {self._timeout}s") from e
        except ConnectionError as e:
            raise SMTPError(f"Connection lost to SMTP server {self._host}:{self._port}") from e

    async def disconnect(self) -> None:
        """Disconnect from SMTP server gracefully.

        Safe to call multiple times (idempotent). Errors during disconnect
        are silently ignored.

        Example:
            >>> smtp = AIOSMTPAdapter(...)
            >>> await smtp._ensure_connected()
            >>> await smtp.disconnect()
            >>> await smtp.disconnect()  # Safe, no-op
        """
        if self._connected:
            try:
                await self._smtp.quit()
            except Exception:
                pass  # Ignore errors during disconnect
            finally:
                self._connected = False
