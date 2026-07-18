"""Self-Service-Backup je Organisation: eigenes Remote-Ziel + Zeitplan.

Anders als das serverweite Backup (app/services/backup_service.py, ein mariadb-dump
aller Orgs) sichert eine Organisation hier NUR ihre eigenen, tenant-gescopten Daten
(app/services/org_export_service.py) an ein SELBST konfiguriertes Ziel.

Muster: OrgSmtpConfig (app/models/org_mail.py) — 1:1-Config je Org, Fernet-verschluesselte
Secrets (app/core/crypto.py), enabled-Toggle, is_fully_configured-Property. Das Remote-Ziel
nutzt dieselben Protokolle wie der zentrale Off-Site-Upload (app/services/remote_backup_service.py).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrgBackupConfig(Base):
    """Backup-Ziel + Zeitplan einer Organisation (1:1 je Org).

    Ohne diese Zeile bzw. ohne enabled=True/is_fully_configured laeuft kein
    automatischer Push; der manuelle Download bleibt davon unabhaengig moeglich.
    """
    __tablename__ = "org_backup_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Remote-Ziel (Protokolle wie remote_backup_service) ───────────────────────
    protocol: Mapped[str] = mapped_column(String(10), nullable=False, default="sftp")
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = Protokoll-Standard
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet (ftp/ftps)
    ssh_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)   # Fernet (privater SSH-Key)
    remote_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ssh_strict: Mapped[str] = mapped_column(String(20), nullable=False, default="accept-new")
    rclone_remote: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Zeitplan (DB = UTC) ──────────────────────────────────────────────────────
    schedule: Mapped[str] = mapped_column(String(10), nullable=False, default="daily")  # daily|weekly
    hour: Mapped[int] = mapped_column(Integer, nullable=False, default=3)      # Stunde (UTC)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)        # 0=Mo .. 6=So (weekly)
    keep_count: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    include_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Letzter Lauf (Statusanzeige/Monitoring) ──────────────────────────────────
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ok|error
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_fully_configured(self) -> bool:
        """Genug Angaben fuer einen Push? (rclone braucht ein Remote, sonst Host.)"""
        if self.protocol == "rclone":
            return bool(self.rclone_remote)
        if not self.host:
            return False
        if self.protocol in ("ftp", "ftps"):
            return bool(self.username)
        return True  # sftp/scp/rsync: Key-Auth via ssh_key_enc oder Agent/known key
