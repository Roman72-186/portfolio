"""One-time activation of all currently visible 3Dlab cards for every student.

Refuses to replace existing school rules. Run inside the production app container.
"""
from __future__ import annotations

import argparse

from app.db.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.lab_access import LabCardAccess
from app.models.role import Role
from app.models.user import User
from app.services.lab_access import visible_cards


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit the access rules")
    args = parser.parse_args()
    card_ids = sorted(visible_cards())
    with SessionLocal() as db:
        existing = db.query(LabCardAccess).count()
        if existing:
            raise SystemExit(f"Refusing to overwrite {existing} configured card rules")
        if not args.apply:
            print(f"Would open {len(card_ids)} visible cards; configured rules: {existing}")
            return
        actor = db.query(User.id).join(Role, Role.id == User.role_id).filter(
            Role.rank == 5, User.is_active.is_(True)
        ).order_by(User.id).first()
        if actor is None:
            raise SystemExit("No active superadmin found for audit attribution")
        for card_id in card_ids:
            db.add(LabCardAccess(card_id=card_id))
            db.add(AuditLog(
                action="lab_card_access",
                performed_by_id=actor.id,
                details=f"card:{card_id};everyone:1;owner-instruction:2026-10-31;via:script",
            ))
        db.commit()
        print(f"Opened {len(card_ids)} visible cards for every student")


if __name__ == "__main__":
    main()
