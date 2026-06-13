"""One-off: check if a tg_username exists in prod (tg_username encrypted -> filter in Python)."""
import base64
import os
import sys

import paramiko

TARGET = sys.argv[1] if len(sys.argv) > 1 else "bebebe5208"
REMOTE_DIR = os.getenv("PORTFOLIO_REMOTE_DIR", "/home/portfolio-saas")
COMPOSE_FILE = os.getenv("PORTFOLIO_COMPOSE_FILE", "docker-compose.prod-ru.yml")

INNER = f'''
from app.db.database import SessionLocal
from app.models.user import User
from app.models.role import Role

target = {TARGET!r}.strip().lstrip("@").lower()
db = SessionLocal()

def norm(s):
    return (s or "").strip().lstrip("@").lower()

exact, partial = [], []
total = 0
with_tg = 0
for u in db.query(User).all():
    total += 1
    t = norm(u.tg_username)
    if not t:
        continue
    with_tg += 1
    if t == target:
        exact.append(u)
    elif target in t or t in target:
        partial.append(u)

print(f"TOTAL users={{total}} with_tg={{with_tg}}")

def show(u):
    role = db.query(Role).filter(Role.id == u.role_id).first()
    print(f"  id={{u.id}} vk_id={{u.vk_id}} name={{u.name!r}} tg={{u.tg_username!r}} "
          f"role={{role.name if role else None}} active={{u.is_active}} "
          f"deleted={{u.deleted_at}} periods={{u.course_periods!r}} lessons={{u.lessons_count!r}}")

print(f"TARGET={{target!r}}")
print(f"EXACT matches: {{len(exact)}}")
for u in exact:
    show(u)
print(f"PARTIAL matches: {{len(partial)}}")
for u in partial:
    show(u)
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
