"""Admission exception for established students with an assigned level."""
from sqlalchemy.orm import Session

from app.models.portfolio_onboarding import PortfolioOnboarding
from app.models.user import User


def has_legacy_portfolio_admission(db: Session, user: User) -> bool:
    # A calculated homework score is not an assigned admission level.
    # Preserve new enrollment requirements even if someone sets a level early.
    return (
        user.point_a_assigned_level in (1, 2)
        and not user.point_a_before_only
        and db.get(PortfolioOnboarding, user.id) is None
    )
