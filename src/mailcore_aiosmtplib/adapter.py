"""aiosmtplib adapter for mailcore SMTPConnection protocol."""

import asyncio
from email.message import EmailMessage
from email.utils import make_msgid

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

    Connection Lifecycle:
        The adapter automatically manages SMTP connection health:
        - NOOP health check before each send (5s timeout for fast stale detection)
        - Automatic reconnection if health check fails
        - Automatic retry on timeout errors (451, 421, timeout)
        - Graceful cleanup of dead connections

        You don't need to manage connections manually - the adapter handles it transparently.
        Stale connections are detected within 5 seconds instead of waiting for full timeout.

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

        Checks connection health with NOOP command. If connection is stale
        or dead, reconnects automatically.

        This method is idempotent - safe to call multiple times.

        Raises:
            SMTPError: Connection or authentication failure with clear message
        """
        # Check if existing connection is alive
        if self._connected:
            try:
                # Use short timeout (5s) for health check - fail fast on stale connections
                # Full timeout (self._timeout) is for actual SMTP operations
                await asyncio.wait_for(self._smtp.noop(), timeout=5)
                return  # Connection is healthy
            except Exception:
                # Connection dead - will reconnect below
                self._connected = False
                try:
                    await self._smtp.quit()
                except Exception:
                    pass  # Already dead, ignore cleanup errors

        # Create new connection
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
        reply_to: list[EmailAddress] | None = None,
        sender: EmailAddress | None = None,
        priority: str | None = None,
        disposition_notification_to: str | None = None,
        notify: str | None = None,
        dsn_return: str | None = None,
        dsn_envelope_id: str | None = None,
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
            reply_to: Reply-To addresses (optional, RFC 5322)
            sender: Sender header (optional, RFC 5322 Section 3.6.2)
            priority: Priority level string (optional, 'highest'/'high'/'normal'/'low'/'lowest')
            disposition_notification_to: Read receipt email (optional, RFC 3798 MDN)
            notify: Delivery receipt notification types (optional, RFC 3461 DSN)
            dsn_return: DSN return content (optional, "full" or "headers" - what to include in bounces)
            dsn_envelope_id: DSN envelope ID (optional, tracking identifier for bounces)

        Returns:
            SendResult with message_id and recipient status

        Raises:
            SMTPError: Send failure with clear message and exception chaining

        Note:
            DSN notify parameter requires server support (RFC 3461). Not all SMTP
            servers support DSN. If server doesn't support, parameter is silently ignored.
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

        # New headers (Story 3.34)
        if reply_to:
            msg["Reply-To"] = ", ".join(addr.to_rfc5322() for addr in reply_to)

        if sender:
            msg["Sender"] = sender.to_rfc5322()

        if disposition_notification_to:
            msg["Disposition-Notification-To"] = disposition_notification_to

        if priority:
            # Map priority to three header formats for maximum client compatibility
            priority_map = {
                "highest": {"x_priority": "1", "importance": "high", "priority": "urgent"},
                "high": {"x_priority": "2", "importance": "high", "priority": "urgent"},
                "normal": {"x_priority": "3", "importance": "normal", "priority": "normal"},
                "low": {"x_priority": "4", "importance": "low", "priority": "non-urgent"},
                "lowest": {"x_priority": "5", "importance": "low", "priority": "non-urgent"},
            }
            if priority in priority_map:
                p = priority_map[priority]
                msg["X-Priority"] = p["x_priority"]
                msg["Importance"] = p["importance"]
                msg["Priority"] = p["priority"]

        # Generate Message-ID if not present (RFC 5322 requirement)
        if "Message-ID" not in msg:
            # Extract domain from sender email for Message-ID
            domain = from_.email.split("@")[1] if "@" in from_.email else "localhost"
            msg["Message-ID"] = make_msgid(domain=domain)

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
            # Check DSN support if delivery receipt requested (RFC 3461)
            if notify or dsn_return or dsn_envelope_id:
                if not self._smtp.supports_extension("DSN"):
                    raise SMTPError(
                        "Delivery receipt requested but SMTP server does not support DSN (RFC 3461). "
                        "The server must advertise DSN extension in EHLO response. "
                        "Either the server doesn't support DSN or it's disabled. "
                        "Remove request_delivery_receipt() call or use a server with DSN support."
                    )

            # Build DSN options (RFC 3461)
            # MAIL FROM options: RET, ENVID
            mail_options = []
            if dsn_return:
                ret_value = "FULL" if dsn_return == "full" else "HDRS"
                mail_options.append(f"RET={ret_value}")
            if dsn_envelope_id:
                mail_options.append(f"ENVID={dsn_envelope_id}")

            # RCPT TO options: NOTIFY
            rcpt_options = []
            if notify:
                rcpt_options.append(f"NOTIFY={notify}")

            # Send message with automatic retry on stale connection
            try:
                response = await self._smtp.send_message(
                    msg,
                    mail_options=mail_options if mail_options else None,
                    rcpt_options=rcpt_options if rcpt_options else None,
                )
            except SMTPException as send_error:
                # Check if it's a timeout/stale connection error
                error_str = str(send_error).lower()
                error_code = str(send_error)
                is_timeout = (
                    "timeout" in error_str
                    or "451" in error_code  # Temporary failure
                    or "421" in error_code  # Service closing transmission
                )

                if is_timeout:
                    # Connection became stale - reconnect and retry once
                    self._connected = False
                    try:
                        await self._smtp.quit()
                    except Exception:
                        pass  # Already dead

                    await self._ensure_connected()

                    # Retry send with fresh connection
                    response = await self._smtp.send_message(
                        msg,
                        mail_options=mail_options if mail_options else None,
                        rcpt_options=rcpt_options if rcpt_options else None,
                    )
                else:
                    # Not a timeout error - propagate immediately
                    raise

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
                message_id=str(msg["Message-ID"]),  # Convert header object to string
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
