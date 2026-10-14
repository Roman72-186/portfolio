from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.crypto import EncryptedString
from app.db.database import Base


class VpnKey(Base):
    """Ключ своего VPN ученика (план 2026-10-13-vpn-amnezia).

    Ключ выпускает служба `deploy/vpn_keys/` на сервере Amnezia; `peer_id` —
    публичный ключ пира там, по нему ключ и отзывается. Сам `vpn://` хранится
    зашифрованным: повторный запрос присылает тот же ключ, не заводя новый пир.

    Ключ — на одно устройство (AmneziaWG с одним ключом на двух устройствах
    рвётся у обоих), у ученика до двух: `device` — 1 или 2 (владелец
    14.10.2026). Живой ключ — `revoked_at IS NULL`, уникален по паре
    (ученик, устройство) — частичный уникальный индекс. Отзыв ставит
    `revoked_at` только после того, как служба удалила пир; сорвавшийся отзыв добивает сверка (`vpn_keys.reconcile`).
    `ondelete=CASCADE`: при полном удалении ученика строка уходит вместе с ним,
    а осиротевший пир на сервере снимает та же сверка.
    """

    __tablename__ = "vpn_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    peer_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    key: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_vpn_keys_live_user_device", "user_id", "device", unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )
