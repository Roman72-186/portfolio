from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)

    @property
    def effective_rank(self) -> int:
        """Уровень прав роли. Для сравнений «кто кем может управлять и кого
        назначать» — только он, не `rank`: у модератора в БД 3, а права ГП (4)."""
        from app.services.rbac import effective_role_rank  # rbac импортирует Role

        return effective_role_rank(self.name, self.rank)
