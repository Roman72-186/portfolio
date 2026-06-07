"""Deploy portfolio-saas to remote server via SFTP.

Usage:
  python scripts/deploy.py                          # full deploy (все файлы)
  python scripts/deploy.py app/templates/foo.html  # только указанные файлы
  python scripts/deploy.py app/templates/a.html app/api/b.py  # несколько файлов
"""
import os
import sys
from pathlib import Path

try:
    import paramiko
except ImportError as exc:
    raise SystemExit("Paramiko is required. Install it with: pip install paramiko") from exc

LOCAL_DIR = Path(__file__).resolve().parent.parent
REMOTE_DIR = os.getenv("PORTFOLIO_REMOTE_DIR", "/home/portfolio-saas")
COMPOSE_FILE = os.getenv("PORTFOLIO_COMPOSE_FILE", "docker-compose.prod-ru.yml")

SKIP = {".git", "__pycache__", ".env", "tests", "venv", ".venv", "node_modules"}


def require_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def connect_client() -> "paramiko.SSHClient":
    host = require_env("PORTFOLIO_SSH_HOST")
    user = require_env("PORTFOLIO_SSH_USER", "root")
    port = int(os.getenv("PORTFOLIO_SSH_PORT", "22"))
    password = os.getenv("PORTFOLIO_SSH_PASSWORD")
    key_path = os.getenv("PORTFOLIO_SSH_KEY_PATH")

    if not password and not key_path:
        raise RuntimeError(
            "Provide PORTFOLIO_SSH_PASSWORD or PORTFOLIO_SSH_KEY_PATH for deployment."
        )

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = os.getenv("PORTFOLIO_SSH_KNOWN_HOSTS")
    if known_hosts:
        client.load_host_keys(os.path.expanduser(known_hosts))
    if os.getenv("PORTFOLIO_SSH_ALLOW_UNKNOWN_HOST") == "1":
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    connect_kwargs = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": 15,
    }
    if key_path:
        connect_kwargs["key_filename"] = os.path.expanduser(key_path)
        connect_kwargs["look_for_keys"] = False
    if password:
        connect_kwargs["password"] = password
        connect_kwargs["look_for_keys"] = False
        connect_kwargs["allow_agent"] = False

    client.connect(**connect_kwargs)
    return client


def read_app_env() -> str:
    env_path = Path(os.getenv("PORTFOLIO_APP_ENV_FILE", LOCAL_DIR / ".env"))
    if not env_path.exists():
        raise RuntimeError(
            f"App env file not found: {env_path}. "
            "Create portfolio-saas/.env or set PORTFOLIO_APP_ENV_FILE."
        )
    return env_path.read_text(encoding="utf-8")


def upload_dir(sftp, local_path, remote_path):
    """Recursively upload directory."""
    for item in os.listdir(local_path):
        if item in SKIP:
            continue
        local_item = os.path.join(local_path, item)
        remote_item = f"{remote_path}/{item}"

        if os.path.isdir(local_item):
            try:
                sftp.stat(remote_item)
            except FileNotFoundError:
                sftp.mkdir(remote_item)
                print(f"  mkdir {remote_item}")
            upload_dir(sftp, local_item, remote_item)
        else:
            print(f"  upload {remote_item}")
            sftp.put(local_item, remote_item)


def upload_files(sftp, local_paths: list[Path]):
    """Upload specific files, creating parent dirs as needed."""
    for local_path in local_paths:
        rel = local_path.relative_to(LOCAL_DIR)
        remote_path = f"{REMOTE_DIR}/{rel.as_posix()}"
        # Ensure parent dir exists
        parent = remote_path.rsplit("/", 1)[0]
        parts = parent.replace(REMOTE_DIR, "").strip("/").split("/")
        cur = REMOTE_DIR
        for part in parts:
            if not part:
                continue
            cur = f"{cur}/{part}"
            try:
                sftp.stat(cur)
            except FileNotFoundError:
                sftp.mkdir(cur)
        print(f"  upload {remote_path}")
        sftp.put(str(local_path), remote_path)


def main():
    # Позиционные аргументы = пути файлов относительно LOCAL_DIR
    file_args = sys.argv[1:]
    target_files: list[Path] = []
    for arg in file_args:
        p = (LOCAL_DIR / arg).resolve()
        if not p.exists():
            raise SystemExit(f"File not found: {p}")
        if not p.is_file():
            raise SystemExit(f"Not a file: {p}")
        target_files.append(p)

    host = require_env("PORTFOLIO_SSH_HOST")
    print(f"Connecting to {host}...")
    client = connect_client()

    sftp = client.open_sftp()

    # Create remote dir
    try:
        sftp.stat(REMOTE_DIR)
    except FileNotFoundError:
        sftp.mkdir(REMOTE_DIR)

    if target_files:
        print(f"Uploading {len(target_files)} file(s) -> {REMOTE_DIR}")
        upload_files(sftp, target_files)
    else:
        print(f"Uploading {LOCAL_DIR} -> {REMOTE_DIR}")
        upload_dir(sftp, str(LOCAL_DIR), REMOTE_DIR)

    print("\n  Uploading app .env to server...")
    env_content = read_app_env()
    with sftp.open(f"{REMOTE_DIR}/.env", "w") as f:
        f.write(env_content)

    sftp.close()
    print("\nDone! Files uploaded.")

    # Build and start (пересобирает только app, db и redis не трогает)
    print(f"\nBuilding and starting containers (compose: {COMPOSE_FILE})...")
    stdin, stdout, stderr = client.exec_command(
        f"cd {REMOTE_DIR} && docker compose -f {COMPOSE_FILE} up -d --build 2>&1",
        timeout=300,
    )
    output = stdout.read().decode("utf-8", errors="replace")
    errors = stderr.read().decode("utf-8", errors="replace")
    # Windows console cp1251 не печатает часть UTF-символов — заменяем их на ?
    def _safe_print(text):
        try:
            print(text)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "cp1251"
            print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
    _safe_print(output)
    if errors:
        _safe_print("STDERR: " + errors)

    # Сброс Redis-кэша после деплоя (сессии не трогаем — только app-кэш)
    print("\nFlushing Redis cache...")
    stdin, stdout, stderr = client.exec_command(
        f"cd {REMOTE_DIR} && docker compose -f {COMPOSE_FILE} exec -T redis sh -c 'redis-cli -a \"$REDIS_PASSWORD\" FLUSHDB' 2>&1",
        timeout=30,
    )
    redis_out = stdout.read().decode().strip()
    redis_err = stderr.read().decode().strip()
    print(f"  Redis FLUSHDB: {redis_out or redis_err or '(no output)'}")

    client.close()


if __name__ == "__main__":
    main()
