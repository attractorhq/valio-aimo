from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """SQLite drops timezone info; explicitly restore UTC on every persisted timestamp."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Persisted timestamps must be timezone-aware.")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OfferRow(Base):
    """v0.1 persisted offer snapshot (P1.2, P1.5)."""

    __tablename__ = "v01_offers"
    offer_id: Mapped[str] = mapped_column(String, primary_key=True)
    offer_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    offer_hash: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    request_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ProposalRow(Base):
    """v0.1 persisted proposal snapshot (P1.2, P1.4)."""

    __tablename__ = "v01_proposals"
    proposal_id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    offer_id: Mapped[str] = mapped_column(String, index=True)
    offer_version: Mapped[int] = mapped_column(Integer)
    offer_hash: Mapped[str] = mapped_column(String)
    mandate_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String, index=True)
    decision: Mapped[str] = mapped_column(String)
    superseded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ApprovalTokenRow(Base):
    """v0.1 single-use approval token bound to proposalId + offerVersion + offerHash (P1.3, P3)."""

    __tablename__ = "v01_approval_tokens"
    token: Mapped[str] = mapped_column(String, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    offer_id: Mapped[str] = mapped_column(String)
    offer_version: Mapped[int] = mapped_column(Integer)
    offer_hash: Mapped[str] = mapped_column(String)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    approver_name: Mapped[str] = mapped_column(String)
    approver_role: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    used_action: Mapped[str | None] = mapped_column(String, nullable=True)
    voided: Mapped[int] = mapped_column(Integer, default=0)


class DecisionLogRow(Base):
    """v0.1 immutable decision-log entry (P1.4, P6 P0-5)."""

    __tablename__ = "v01_decision_log"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)
    proposal_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    offer_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    offer_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime())
