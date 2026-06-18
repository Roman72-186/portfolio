"""One-off: check why a student only sees one mock-exam subject (exam_subjects/tags)."""
import base64
import os
import sys

import paramiko

TARGET = sys.argv[1] if len(sys.argv) > 1 else "Махметов"
REMOTE_DIR = os.getenv("PORTFOLIO_REMOTE_DIR", "/home/portfolio-saas")
COMPOSE_FILE = os.getenv("PORTFOLIO_COMPOSE_FILE", "docker-compose.prod-ru.yml")

INNER = f'''
from app.db.database import SessionLocal
from app.models.user import User
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.services.mock_exam_access import get_allowed_mock_subjects

target = {TARGET!r}.strip().lower()
db = SessionLocal()

matches = [u for u in db.query(User).all() if target in (u.name or "").lower()
           or target in (u.first_name or "").lower() or target in (u.last_name or "").lower()]

print(f"TARGET={{target!r}} matches={{len(matches)}}")
for u in matches:
    role = db.query(Role).filter(Role.id == u.role_id).first()
    tags = (
        db.query(Tag.id, Tag.name)
        .join(UserTag, UserTag.tag_id == Tag.id)
        .filter(UserTag.user_id == u.id)
        .all()
    )
    allowed = get_allowed_mock_subjects(db, u.id)
    print(f"  id={{u.id}} name={{u.name!r}} first={{u.first_name!r}} last={{u.last_name!r}} "
          f"role={{role.name if role else None}} active={{u.is_active}} "
          f"exam_subjects={{u.exam_subjects!r}} tags={{tags!r}} allowed_subjects={{allowed!r}}")
db.close()
'''

b64 = base64.b64encode(INNER.encode("utf-8")).decode("ascii")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
connect_kwargs = {
    "hostname": os.environ["PORTFOLIO_SSH_HOST"],
    "port": int(os.getenv("PORTFOLIO_SSH_PORT", "22")),
    "username": os.getenv("PORTFOLIO_SSH_USER", "root"),
    "look_for_keys": False,
    "allow_agent": False,
    "timeout": 20,
}
key_path = os.getenv("PORTFOLIO_SSH_KEY_PATH")
password = os.getenv("PORTFOLIO_SSH_PASSWORD")
if key_path:
    connect_kwargs["key_filename"] = os.path.expanduser(key_path)
if password:
    connect_kwargs["password"] = password
client.connect(**connect_kwargs)
cmd = (
    f"cd {REMOTE_DIR} && echo {b64} | base64 -d | "
    f"docker compose -f {COMPOSE_FILE} exec -T app python - 2>&1"
)
stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
print(stdout.read().decode("utf-8", errors="replace"))
err = stderr.read().decode("utf-8", errors="replace")
if err.strip():
    print("STDERR:", err)
client.close()
