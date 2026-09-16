"""Persistence for users and refresh tokens."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.models import OidcIdentityRecord, RefreshTokenRecord, UserRecord
from app.generation.models import ConversationMessageRecord, ConversationRecord
from app.ingestion.models import ChunkRecord, DocumentRecord


def get_user_by_email(session: Session, email: str) -> UserRecord | None:
    """Return the user with `email`, or `None` if none exists."""
    return session.scalars(select(UserRecord).where(UserRecord.email == email)).first()


def get_user_by_id(session: Session, user_id: uuid.UUID) -> UserRecord | None:
    """Return the user with `user_id`, or `None` if none exists."""
    return session.get(UserRecord, user_id)


def list_users(session: Session) -> list[UserRecord]:
    """Return all users, ordered by creation time."""
    return list(session.scalars(select(UserRecord).order_by(UserRecord.created_at)))


def set_user_active(session: Session, user: UserRecord, is_active: bool) -> None:
    """Set `user.is_active`. Does not commit."""
    user.is_active = is_active
    session.flush()


def revoke_all_refresh_tokens_for_user(session: Session, user_id: uuid.UUID) -> None:
    """Mark every non-revoked refresh token belonging to `user_id` as revoked. Does not commit."""
    now = datetime.now(timezone.utc)
    session.execute(
        update(RefreshTokenRecord)
        .where(RefreshTokenRecord.user_id == user_id, RefreshTokenRecord.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def create_user(
    session: Session, email: str, hashed_password: str | None, role: str = "user"
) -> UserRecord:
    """Create and flush a new user row. Does not commit — the caller controls the transaction."""
    user = UserRecord(email=email, hashed_password=hashed_password, role=role)
    session.add(user)
    session.flush()
    return user


def create_oidc_user(session: Session, email: str, role: str = "user") -> UserRecord:
    """Create and flush a new OIDC-only user row (no local password). Does not commit."""
    return create_user(session, email, hashed_password=None, role=role)


def get_oidc_identity(session: Session, provider: str, external_id: str) -> OidcIdentityRecord | None:
    """Return the linked identity for `(provider, external_id)`, or `None` if none exists."""
    return session.scalars(
        select(OidcIdentityRecord).where(
            OidcIdentityRecord.provider == provider, OidcIdentityRecord.external_id == external_id
        )
    ).first()


def create_oidc_identity(
    session: Session, user_id: uuid.UUID, provider: str, external_id: str, email: str
) -> OidcIdentityRecord:
    """Create and flush a new OIDC identity link. Does not commit."""
    identity = OidcIdentityRecord(user_id=user_id, provider=provider, external_id=external_id, email=email)
    session.add(identity)
    session.flush()
    return identity


def create_refresh_token(
    session: Session, user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> RefreshTokenRecord:
    """Create and flush a new refresh-token row. Does not commit."""
    record = RefreshTokenRecord(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    session.add(record)
    session.flush()
    return record


def get_refresh_token_by_hash(session: Session, token_hash: str) -> RefreshTokenRecord | None:
    """Return the refresh-token row for `token_hash`, or `None` if none exists."""
    return session.scalars(
        select(RefreshTokenRecord).where(RefreshTokenRecord.token_hash == token_hash)
    ).first()


def revoke_refresh_token(session: Session, record: RefreshTokenRecord) -> None:
    """Mark `record` revoked (idempotent). Does not commit."""
    record.revoked_at = datetime.now(timezone.utc)
    session.flush()


def delete_user_and_owned_data(session: Session, user_id: uuid.UUID) -> None:
    """Delete every row `user_id` owns, then the user row itself. Does not commit.

    Order matters: all of these are plain (non-cascading) foreign keys, so children must be
    deleted before their parents -- mirrors `app.evaluation.repository.cleanup_eval_data`, which
    does the same thing for eval-run data.
    """
    conversation_ids = list(
        session.scalars(select(ConversationRecord.id).where(ConversationRecord.owner_id == user_id))
    )
    if conversation_ids:
        session.execute(
            delete(ConversationMessageRecord).where(
                ConversationMessageRecord.conversation_id.in_(conversation_ids)
            )
        )
    session.execute(delete(ConversationRecord).where(ConversationRecord.owner_id == user_id))

    document_ids = list(
        session.scalars(select(DocumentRecord.document_id).where(DocumentRecord.owner_id == user_id))
    )
    if document_ids:
        session.execute(delete(ChunkRecord).where(ChunkRecord.document_id.in_(document_ids)))
    session.execute(delete(DocumentRecord).where(DocumentRecord.owner_id == user_id))

    session.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user_id))
    session.execute(delete(OidcIdentityRecord).where(OidcIdentityRecord.user_id == user_id))
    session.execute(delete(UserRecord).where(UserRecord.id == user_id))
