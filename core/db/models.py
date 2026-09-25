"""ORM models for the uploads database (column mapping matches the live schema)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.db.timefmt import _utc_now


class Base(DeclarativeBase):
    """Modern SQLAlchemy Declarative Base."""

class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_name: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    itype: Mapped[Optional[str]] = mapped_column(String)
    filesize: Mapped[Optional[int]] = mapped_column(Integer)
    # Parsed metadata - populated automatically on insert, indexed for grouped queries
    parsed_title: Mapped[Optional[str]] = mapped_column(String, index=True)
    media_type: Mapped[Optional[str]] = mapped_column(String, index=True)  # tv, movie, other
    season_number: Mapped[Optional[int]] = mapped_column(Integer)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer)
    episode_end_number: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )
    results: Mapped[List["UploadResult"]] = relationship(
        "UploadResult", back_populates="upload", cascade="all, delete-orphan"
    )

class UploadResult(Base):
    __tablename__ = "upload_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id"), nullable=False, index=True)
    indexer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    duration: Mapped[Optional[float]] = mapped_column(Float)
    speed_bps: Mapped[Optional[float]] = mapped_column(Float)
    server_name: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="success", index=True)  # success, failed
    error: Mapped[Optional[str]] = mapped_column(String)
    upload: Mapped["Upload"] = relationship("Upload", back_populates="results")

class JobHistory(Base):
    __tablename__ = "job_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    items_total: Mapped[int] = mapped_column(Integer, default=0)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_message: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

class SystemStat(Base):
    """Historical system metrics for the dashboard."""

    __tablename__ = "system_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float)
    memory_percent: Mapped[Optional[float]] = mapped_column(Float)
    upload_mbps: Mapped[Optional[float]] = mapped_column(Float)
    download_mbps: Mapped[Optional[float]] = mapped_column(Float)
    total_sent_mb: Mapped[float] = mapped_column(Float, default=0.0)
    total_recv_mb: Mapped[float] = mapped_column(Float, default=0.0)
    connections: Mapped[int] = mapped_column(Integer, default=0)
    disk_percent: Mapped[float] = mapped_column(Float, default=0.0)
    disk_free_gb: Mapped[float] = mapped_column(Float, default=0.0)
    disk_read_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    disk_write_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    errors_in: Mapped[int] = mapped_column(Integer, default=0)
    errors_out: Mapped[int] = mapped_column(Integer, default=0)
    drops_in: Mapped[int] = mapped_column(Integer, default=0)
    drops_out: Mapped[int] = mapped_column(Integer, default=0)
    swap_percent: Mapped[float] = mapped_column(Float, default=0.0)
    load_avg: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)

class InterfaceStat(Base):
    """Per-interface network statistics."""

    __tablename__ = "interface_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    interface_name: Mapped[str] = mapped_column(String, nullable=False)
    upload_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    download_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)

class QueueItem(Base):
    """Item queued for upload, persisted across restarts."""

    __tablename__ = "queue_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, default="misc")
    itype: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

class MutedIssue(Base):
    """A known-issue group the operator has silenced from the default view.

    WHY: the known-issues grouping (get_grouped_upload_errors) has no primary key
    of its own - a group is identified only by (indexer_id, signature), the same
    pair it is grouped by - so muting is keyed on that pair rather than any single
    UploadResult row. A signature stays muted across every future occurrence of
    the same recurring failure until explicitly unmuted.
    """

    __tablename__ = "muted_issues"
    __table_args__ = (UniqueConstraint("indexer_id", "signature", name="uq_muted_issue_indexer_signature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    indexer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    signature: Mapped[str] = mapped_column(String, nullable=False)
    muted_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
