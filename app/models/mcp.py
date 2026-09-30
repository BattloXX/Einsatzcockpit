"""Persistente OAuth-Daten fuer den MCP-Server (Geheimnisse nur gehasht)."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MCPOAuthClient(Base):
    __tablename__ = "mcp_oauth_client"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    client_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class MCPOAuthCode(Base):
    __tablename__ = "mcp_oauth_code"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=True)
    org_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=True
    )
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    state: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MCPOAuthToken(Base):
    __tablename__ = "mcp_oauth_token"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    token_type: Mapped[str] = mapped_column(String(10), nullable=False)  # access | refresh
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class MCPUpload(Base):
    """Kurzlebiger, Bearer-authentifizierter Zwischenspeicher fuer MCP-PDFs."""

    __tablename__ = "mcp_upload"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    objekt_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False)
    dateiname: Mapped[str] = mapped_column(String(255), nullable=False)
    erwartete_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    hochgeladen_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pfad: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    groesse_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    seitenzahl: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uebergeben_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
