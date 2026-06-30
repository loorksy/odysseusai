"""EmailComposer — Template-based transactional email builder (C81).

Builds and dispatches transactional emails via pluggable transports.
Ships with stdlib SMTP and a dry-run (log-only) fallback.

Features:
  - Named template registry with {{var}} substitution (no external deps)
  - Plain-text and HTML body support
  - File and bytes attachments
  - CC / BCC / Reply-To headers
  - Dry-run mode: log instead of send
  - Pluggable transport: SMTP (stdlib), custom callable, or dry-run
  - Delivery receipt with Message-ID

Public API:
  ec = EmailComposer(dry_run=False, from_addr="noreply@shadowrealm.local")
  ec.set_smtp(host, port, user="", password="", use_tls=True)
  ec.register_template(name, subject, body, html_body=None)
  receipt = ec.send(to, template_name, context,
                    from_addr=None, cc=None, bcc=None, reply_to=None)
  receipt = ec.send_raw(to, subject, body,
                        html_body=None, from_addr=None, attachments=None)
"""
from __future__ import annotations
import logging, re, smtplib, time, uuid
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email                import encoders
from pathlib              import Path
from typing               import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass
class EmailReceipt:
    receipt_id: str
    to:         List[str]
    subject:    str
    status:     str         # sent | dry_run | failed
    sent_at:    float = field(default_factory=time.time)
    message_id: str = ""
    error:      str = ""


@dataclass
class _Template:
    name:      str
    subject:   str
    body:      str
    html_body: Optional[str] = None


@dataclass
class _SMTPConfig:
    host:     str
    port:     int   = 587
    user:     str   = ""
    password: str   = ""
    use_tls:  bool  = True


class EmailComposer:
    """Template-based transactional email with pluggable SMTP or custom transport."""

    def __init__(
        self,
        transport:  Optional[Callable] = None,
        dry_run:    bool = False,
        from_addr:  str  = "noreply@shadowrealm.local",
    ):
        self._transport  = transport
        self._dry_run    = dry_run
        self._from       = from_addr
        self._templates: Dict[str, _Template] = {}
        self._smtp:      Optional[_SMTPConfig] = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_smtp(
        self, host: str, port: int = 587,
        *, user: str = "", password: str = "", use_tls: bool = True,
    ) -> None:
        self._smtp = _SMTPConfig(host=host, port=port,
                                  user=user, password=password, use_tls=use_tls)

    def register_template(
        self,
        name:      str,
        subject:   str,
        body:      str,
        *,
        html_body: Optional[str] = None,
    ) -> None:
        self._templates[name] = _Template(
            name=name, subject=subject, body=body, html_body=html_body
        )

    # ------------------------------------------------------------------
    # Send API
    # ------------------------------------------------------------------

    def send(
        self,
        to:            Union[str, List[str]],
        template_name: str,
        context:       Optional[Dict[str, Any]] = None,
        *,
        from_addr:  Optional[str]       = None,
        cc:         Optional[List[str]]  = None,
        bcc:        Optional[List[str]]  = None,
        reply_to:   Optional[str]        = None,
    ) -> EmailReceipt:
        tpl = self._templates.get(template_name)
        if not tpl:
            raise KeyError(f"EmailComposer: template '{template_name}' not registered")
        ctx       = context or {}
        subject   = self._render(tpl.subject, ctx)
        body      = self._render(tpl.body, ctx)
        html_body = self._render(tpl.html_body, ctx) if tpl.html_body else None
        return self.send_raw(
            to, subject, body,
            html_body=html_body, from_addr=from_addr,
            cc=cc, bcc=bcc, reply_to=reply_to,
        )

    def send_raw(
        self,
        to:          Union[str, List[str]],
        subject:     str,
        body:        str,
        *,
        html_body:   Optional[str]                                    = None,
        from_addr:   Optional[str]                                    = None,
        cc:          Optional[List[str]]                              = None,
        bcc:         Optional[List[str]]                              = None,
        reply_to:    Optional[str]                                    = None,
        attachments: Optional[List[Union[str, Tuple[str, bytes]]]]    = None,
    ) -> EmailReceipt:
        to_list = [to] if isinstance(to, str) else to
        sender  = from_addr or self._from
        receipt = EmailReceipt(
            receipt_id=uuid.uuid4().hex,
            to=to_list, subject=subject, status="pending",
        )
        if self._dry_run:
            logger.info(
                f"[EmailComposer dry-run] to={to_list} subject='{subject}' "
                f"body_len={len(body)}"
            )
            receipt.status = "dry_run"
            return receipt
        try:
            msg = self._build_mime(
                to_list, subject, body, html_body,
                sender, cc, bcc, reply_to, attachments or [],
            )
            receipt.message_id = msg["Message-ID"]
            if self._transport:
                self._transport(msg, to_list)
            elif self._smtp:
                self._send_smtp(msg, to_list)
            else:
                logger.warning(
                    "EmailComposer: no transport configured — falling back to dry-run"
                )
                receipt.status = "dry_run"
                return receipt
            receipt.status = "sent"
            logger.info(
                f"EmailComposer: sent to={to_list} subject='{subject}' "
                f"msg_id={receipt.message_id}"
            )
        except Exception as exc:
            receipt.status = "failed"
            receipt.error  = str(exc)
            logger.error(f"EmailComposer: send failed: {exc}")
        return receipt

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send_smtp(self, msg: MIMEMultipart, to_list: List[str]) -> None:
        cfg = self._smtp
        if cfg.use_tls:
            server = smtplib.SMTP(cfg.host, cfg.port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(cfg.host, cfg.port)
        try:
            if cfg.user:
                server.login(cfg.user, cfg.password)
            server.sendmail(msg["From"], to_list, msg.as_string())
        finally:
            server.quit()

    @staticmethod
    def _build_mime(
        to_list:     List[str],
        subject:     str,
        body:        str,
        html_body:   Optional[str],
        sender:      str,
        cc:          Optional[List[str]],
        bcc:         Optional[List[str]],
        reply_to:    Optional[str],
        attachments: list,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative" if (html_body and not attachments) else "mixed")
        msg["From"]       = sender
        msg["To"]         = ", ".join(to_list)
        msg["Subject"]    = subject
        msg["Message-ID"] = f"<{uuid.uuid4().hex}@shadowrealm>"
        if cc:       msg["Cc"]       = ", ".join(cc)
        if bcc:      msg["Bcc"]      = ", ".join(bcc)
        if reply_to: msg["Reply-To"] = reply_to
        msg.attach(MIMEText(body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))
        for att in attachments:
            if isinstance(att, str):
                p    = Path(att)
                data = p.read_bytes()
                name = p.name
            else:
                name, data = att
            part = MIMEBase("application", "octet-stream")
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition", f'attachment; filename="{name}"'
            )
            msg.attach(part)
        return msg

    @staticmethod
    def _render(template: str, ctx: Dict[str, Any]) -> str:
        """Minimal {{var}} substitution — zero external dependencies."""
        return re.sub(
            r'\{\{\s*([\w.]+)\s*\}\}',
            lambda m: str(ctx.get(m.group(1).strip(), m.group(0))),
            template,
        )
