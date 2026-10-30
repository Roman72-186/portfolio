from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class LabCardAccess(Base):
    __tablename__ = "lab_card_access"

    card_id: Mapped[str] = mapped_column(String(150), primary_key=True)


class LabCardTariff(Base):
    __tablename__ = "lab_card_tariffs"

    card_id: Mapped[str] = mapped_column(ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True)
    tariff: Mapped[str] = mapped_column(String(50), primary_key=True)


class LabCardLevel(Base):
    __tablename__ = "lab_card_levels"

    card_id: Mapped[str] = mapped_column(ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, primary_key=True)
    __table_args__ = (CheckConstraint("level IN (1, 2)", name="ck_lab_card_levels_level"),)


class LabCardTag(Base):
    __tablename__ = "lab_card_tags"

    card_id: Mapped[str] = mapped_column(ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class LabCardStudent(Base):
    __tablename__ = "lab_card_students"

    card_id: Mapped[str] = mapped_column(ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
