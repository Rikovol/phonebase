import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ClientRecord(Base):
    __tablename__ = "client_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    full_name_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    passport_series_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    passport_number_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    issued_by_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    issued_date_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    consent_obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_doc_path: Mapped[str | None] = mapped_column(Text)
    doc_file_path: Mapped[str | None] = mapped_column(Text)
    doc_file_hash: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    scheduled_deletion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PDAccessLog(Base):
    __tablename__ = "pd_access_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("client_records.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
