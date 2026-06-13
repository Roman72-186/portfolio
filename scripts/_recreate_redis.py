"""One-off: upload .env (with REDIS_PASSWORD/REDIS_URL) and recreate redis+app
without touching db. Run with the same env vars as deploy.py."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import connect_client, REMOTE_DIR, COMPOSE_FILE, read_app_env  # noqa: E402


def run(client, cmd, timeout=120):
    print(f"$ {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out:
        print(out)
    if err:
        print("STDERR:", err)


def main():
    client = connect_client()

    sftp = client.open_sftp()
    print("Uploading .env ...")
    with sftp.open(f"{REMOTE_DIR}/.env", "w") as f:
        f.write(read_app_env())
    sftp.close()

    base = f"cd {REMOTE_DIR} && docker compose -f {COMPOSE_FILE}"
    run(client, f"{base} up -d --force-recreate redis")
    time.sleep(3)
    run(client, f"{base} up -d --force-recreate app")

    client.close()


if __name__ == "__main__":
    main()
