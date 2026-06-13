"""One-off: verify redis requires auth and app/db are healthy after recreate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import connect_client, REMOTE_DIR, COMPOSE_FILE  # noqa: E402


def run(client, cmd, timeout=30):
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
    base = f"cd {REMOTE_DIR} && docker compose -f {COMPOSE_FILE}"

    run(client, f"{base} ps")
    run(client, f"{base} exec -T redis redis-cli ping")  # expect NOAUTH error
    run(client, f"{base} exec -T redis sh -c 'redis-cli -a \"$REDIS_PASSWORD\" ping'")
    run(client, "curl -sS https://apparchi.ru/health")
    run(client, f"{base} logs app --since 2m --no-color")

    client.close()


if __name__ == "__main__":
    main()
