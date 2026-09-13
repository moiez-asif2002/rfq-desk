"""Gmail integration: OAuth (authorization code + PKCE), inbox sync, and reply drafts.

Tokens are stored Fernet-encrypted. Sync is read-only on the mailbox (messages are not marked
read); idempotency comes from the unique Gmail message id on intake.
"""

import base64
import json
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings
from app.extraction import SUPPORTED_ATTACHMENT_TYPES, EmailAttachment
from app.models import GmailAccount, InboundMessage, OAuthState
from app.pipeline import ingest_message
from app.security import TokenCipher

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]
SYNC_QUERY = (
    'in:inbox newer_than:14d subject:(rfq OR quote OR quotation OR "request for quote" OR pricing OR invoice)'
)
STATE_TTL = timedelta(minutes=10)

# Google may return scopes in a different order/superset; don't treat that as an error.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GmailNotConnected(Exception):
    pass


def _flow(settings: Settings, state: str | None = None, code_verifier: str | None = None) -> Flow:
    return Flow.from_client_secrets_file(
        settings.gmail_client_secrets_file,
        scopes=SCOPES,
        state=state,
        redirect_uri=settings.gmail_redirect_uri,
        code_verifier=code_verifier,
        autogenerate_code_verifier=code_verifier is None,
    )


def start_authorization(session: Session, settings: Settings) -> str:
    state = secrets.token_urlsafe(32)
    flow = _flow(settings, state=state)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    session.execute(delete(OAuthState).where(OAuthState.created_at < datetime.now(UTC) - STATE_TTL))
    session.add(OAuthState(state=state, code_verifier=flow.code_verifier))
    session.commit()
    return url


def complete_authorization(session: Session, settings: Settings, state: str, code: str) -> GmailAccount:
    saved = session.get(OAuthState, state)
    if saved is None or saved.created_at < datetime.now(UTC) - STATE_TTL:
        raise PermissionError("OAuth state is invalid or expired")
    session.delete(saved)  # single use

    flow = _flow(settings, state=state, code_verifier=saved.code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    profile = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(
        userId="me").execute()
    email = profile["emailAddress"].lower()

    cipher = TokenCipher(settings.token_encryption_key)
    account = session.scalars(select(GmailAccount).where(GmailAccount.email == email)).first()
    if account is None:
        account = GmailAccount(email=email, encrypted_token=cipher.encrypt(creds.to_json()))
        session.add(account)
    else:
        account.encrypted_token = cipher.encrypt(creds.to_json())
        account.connected_at = datetime.now(UTC)
    audit.record(session, "gmail.connected", actor=f"user:{email}", email=email)
    session.commit()
    return account


def current_account(session: Session) -> GmailAccount | None:
    return session.scalars(select(GmailAccount).order_by(GmailAccount.connected_at.desc())).first()


def _service(session: Session, settings: Settings, account: GmailAccount):
    cipher = TokenCipher(settings.token_encryption_key)
    creds = Credentials.from_authorized_user_info(json.loads(cipher.decrypt(account.encrypted_token)), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise GmailNotConnected("Gmail token expired and no refresh token; reconnect Gmail")
        creds.refresh(Request())
        account.encrypted_token = cipher.encrypt(creds.to_json())
        session.commit()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _walk_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sync_inbox(session: Session, settings: Settings, max_messages: int = 20) -> list[int]:
    """Pull recent candidate RFQ emails. Returns ids of newly created RFQs."""
    account = current_account(session)
    if account is None:
        raise GmailNotConnected("Connect Gmail first")
    gmail = _service(session, settings, account)
    users = gmail.users()

    listed = users.messages().list(userId="me", q=SYNC_QUERY, maxResults=max_messages).execute()
    ids = [m["id"] for m in listed.get("messages", [])]
    known = set(session.scalars(
        select(InboundMessage.external_id).where(InboundMessage.external_id.in_([f"gmail:{i}" for i in ids]))
    ))

    created: list[int] = []
    for gmail_id in ids:
        external_id = f"gmail:{gmail_id}"
        if external_id in known:
            continue
        msg = users.messages().get(userId="me", id=gmail_id, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        from_name, from_email = parseaddr(headers.get("from", ""))

        body_text = ""
        attachments: list[EmailAttachment] = []
        for part in _walk_parts(msg["payload"]):
            mime = part.get("mimeType", "")
            body = part.get("body", {})
            if part.get("filename") and body.get("attachmentId"):
                if mime not in SUPPORTED_ATTACHMENT_TYPES:
                    continue
                att = users.messages().attachments().get(
                    userId="me", messageId=gmail_id, id=body["attachmentId"]).execute()
                attachments.append(EmailAttachment(part["filename"], mime, _b64(att["data"])))
            elif mime == "text/plain" and body.get("data") and not body_text:
                body_text = _b64(body["data"]).decode("utf-8", errors="replace")

        result = ingest_message(
            session,
            source="gmail",
            external_id=external_id,
            thread_id=msg.get("threadId"),
            rfc_message_id=headers.get("message-id"),
            from_email=from_email or "unknown@unknown",
            from_name=from_name or None,
            subject=headers.get("subject", ""),
            body_text=body_text or msg.get("snippet", ""),
            received_at=datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=UTC),
            attachments=attachments,
        )
        if not result.duplicate:
            created.append(result.rfq_id)

    account.last_synced_at = datetime.now(UTC)
    audit.record(session, "gmail.synced", email=account.email, listed=len(ids), created=len(created))
    session.commit()
    return created


def create_reply_draft(session: Session, settings: Settings, message: InboundMessage,
                       to: str, body: str) -> str | None:
    account = current_account(session)
    if account is None:
        return None
    gmail = _service(session, settings, account)

    mime = EmailMessage()
    mime["To"] = to
    mime["From"] = account.email
    subject = message.subject or "Your quote request"
    mime["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if message.rfc_message_id:
        mime["In-Reply-To"] = message.rfc_message_id
        mime["References"] = message.rfc_message_id
    mime.set_content(body)

    draft_body: dict = {"message": {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}}
    if message.source == "gmail" and message.thread_id:
        draft_body["message"]["threadId"] = message.thread_id
    draft = gmail.users().drafts().create(userId="me", body=draft_body).execute()
    return draft["id"]
