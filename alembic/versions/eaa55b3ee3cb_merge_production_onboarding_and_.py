"""merge production onboarding and reviewed controls

Revision ID: eaa55b3ee3cb
Revises: 9d8fe4b2c1a7, c43fd8a9e210
Create Date: 2026-11-27 16:08:13.474444

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eaa55b3ee3cb'
down_revision: Union[str, None] = ('9d8fe4b2c1a7', 'c43fd8a9e210')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
