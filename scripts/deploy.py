"""Deploy portfolio-saas to remote server via SFTP.

Usage:
  python scripts/deploy.py                          # full deploy (все файлы)
  python scripts/deploy.py app/templates/foo.html  # только указанные файлы
  python scripts/deploy.py app/templates/a.html app/api/b.py  # несколько файлов
  python scripts/deploy.py --sync-env               # + залить окружение из .env.prod
  python scripts/deploy.py --sync-env --allow-remove-env-keys  # разрешить удаление ключей
  python scripts/deploy.py --status                 # что за версия на сервере, есть ли расхождения
  python scripts/deploy.py --allow-dirty            # деплой незакоммиченного (пометит версию грязной)

**Прод не должен расходиться с коммитом.** Скрипт отказывается деплоить
незакоммиченное: полный деплой требует чистого дерева, поштучный — чтобы были
закоммичены заливаемые файлы. Обход — `--allow-dirty`, тогда деплой помечается
грязным в маркере. После успешной сборки на сервер пишется `.deployed-version`
с коммитом, веткой, временем и режимом; `--status` сравнивает его с локальным
HEAD и дополнительно сверяет sha256 всех отслеживаемых файлов.

**Полный деплой заливает файлы из индекса git**, за вычетом `SKIP`, — то же
множество, которое потом сверяет `--status`. Раньше он обходил файловую систему
и увозил на сервер всё игнорируемое: `.env.prod` с боевыми паролями,
`.env.deploy` (а тот по умолчанию целится в чужой прод), кэши и выгрузки. Ни
гейт чистоты, ни `--status` этого не показывали — оба смотрят только в git.
Файлы, оставшиеся на сервере вне индекса, деплой не удаляет, но называет вслух.

**Целевой хост сверяется с `ALLOWED_HOSTS`** до подключения. Разовый обход —
`PORTFOLIO_ALLOW_ANY_HOST=1`.

⚠️ **`.env` сервера по умолчанию НЕ трогается.** До 2026-08-05 скрипт заливал туда
локальный `.env` при каждом запуске, включая поштучный режим. Локальный `.env` —
это dev-конфигурация: на 05.08 из 33 переменных 32 расходились с боевыми, в том
числе `DATABASE_URL`, `POSTGRES_PASSWORD`, `SESSION_SECRET`, `REDIS_PASSWORD` и
ключи S3/VK. Любой деплой положил бы прод и разлогинил всех учеников.

Прод-окружение живёт в отдельном файле `.env.prod` (в `.gitignore` правилом
`.env.*`, значения вносит владелец руками) и заливается только явным флагом
`--sync-env`. На сервер сам файл не уезжает — только его содержимое в `.env`. Перед заливкой
скрипт делает бэкап на сервере, показывает разницу по именам переменных — значения
не печатаются никогда — и отказывается работать, если ключ пропадает.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    import paramiko
except ImportError as exc:
    raise SystemExit("Paramiko is required. Install it with: pip install paramiko") from exc

LOCAL_DIR = Path(__file__).resolve().parent.parent
REMOTE_DIR = os.getenv("PORTFOLIO_REMOTE_DIR", "/home/portfolio-saas")
COMPOSE_FILE = os.getenv("PORTFOLIO_COMPOSE_FILE", "docker-compose.prod-ru.yml")

# Из индекса git на сервер не уезжают тесты и служебные каталоги. Множество
# используется и поштучным режимом: что исключено из полного деплоя, то нельзя
# протащить и поимённо.
SKIP = {
    ".git", "__pycache__", ".env", "tests", "venv", ".venv", "node_modules",
    ".artifacts", "prototypes",
}

# Куда этому проекту вообще позволено выкатываться. Список нужен из-за конкретной
# ловушки: `.env.deploy` по умолчанию указывает на 89.23.96.254 — прежний хост
# Apparchi, где сейчас живёт чужой боевой проект. Команда деплоя из документации
# подхватывает этот файл как есть, и ошибка стоит чужого прода, а не своего.
# Расширять список осознанно; разовый обход — PORTFOLIO_ALLOW_ANY_HOST=1.
ALLOWED_HOSTS = {
    "139.100.237.57",  # боевой Apparchi
}


def assert_known_host(host: str) -> None:
    if os.getenv("PORTFOLIO_ALLOW_ANY_HOST") == "1":
        print(f"  ВНИМАНИЕ: проверка хоста отключена вручную, цель — {host}")
        return
    if host not in ALLOWED_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_HOSTS))
        raise SystemExit(
            f"\nОТМЕНА: {host} не в списке серверов этого проекта ({allowed}).\n"
            "Проверьте PORTFOLIO_SSH_HOST — .env.deploy по умолчанию указывает на\n"
            "чужой боевой сервер. Если хост верный, добавьте его в ALLOWED_HOSTS\n"
            "или разово запустите с PORTFOLIO_ALLOW_ANY_HOST=1."
        )


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
    """Прод-окружение для заливки. По умолчанию `.env.prod`, а НЕ `.env`.

    `.env` — локальная dev-конфигурация с другой БД, другим Redis и dev-приложением
    VK. Заливать её на сервер нельзя (см. модуль-докстринг).
    """
    env_path = Path(os.getenv("PORTFOLIO_APP_ENV_FILE", LOCAL_DIR / ".env.prod"))
    if not env_path.exists():
        raise RuntimeError(
            f"Prod env file not found: {env_path}.\n"
            "Создайте portfolio-saas/.env.prod — это источник правды для окружения\n"
            "сервера. Взять текущее боевое состояние можно так:\n"
            "  ssh apparchi-prod 'cat /home/portfolio-saas/.env' > portfolio-saas/.env.prod\n"
            "Либо укажите другой файл через PORTFOLIO_APP_ENV_FILE."
        )
    assert_not_tracked_by_git(env_path)
    return env_path.read_text(encoding="utf-8")


def is_tracked_by_git(relative: Path) -> bool:
    """Лежит ли файл в индексе git. При недоступном git — считаем, что да."""
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative.as_posix()],
            cwd=LOCAL_DIR,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # git недоступен — блокировать деплой не наше дело
    return completed.returncode == 0


def assert_not_tracked_by_git(path: Path) -> None:
    """Отказ, если файл с боевыми секретами отслеживается git.

    Защита стоит в коде, а не только в `.gitignore`: правило легко потерять при
    мерже или на свежем клоне, а цена — боевые пароли в истории репозитория.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=LOCAL_DIR,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return  # git недоступен — не наше дело блокировать деплой
    if completed.returncode == 0:
        raise SystemExit(
            f"\nОТМЕНА: {path.name} отслеживается git — боевые секреты попадут в историю.\n"
            f"Уберите файл из индекса и добавьте в .gitignore:\n"
            f"  git rm --cached {path.name}\n"
            f"  echo '{path.name}' >> .gitignore"
        )


def env_fingerprint(text: str) -> dict[str, str]:
    """{имя переменной: короткий хеш значения}. Значения не возвращаются наружу.

    Разбор терпим к тому, что реально встречается в `.env`: префикс `export`,
    значения в кавычках и многострочные значения вроде приватных ключей. Наивный
    разбор «строка = одна переменная» ломался на них дважды: продолжение
    многострочного значения принималось за новую переменную, а `export KEY=`
    давал ключ `export KEY`. Оба случая дают ложную картину в diff по именам, а
    на ней стоит отказ от потери ключей — то есть защита срабатывала бы невпопад.
    """
    result: dict[str, str] = {}
    pending_key: str | None = None
    pending_value: list[str] = []
    quote: str | None = None

    def _store(key: str, value: str) -> None:
        result[key] = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]

    for line in text.splitlines():
        if pending_key is not None:
            # Внутри многострочного значения: копим, пока не встретим кавычку.
            if quote and line.rstrip().endswith(quote):
                pending_value.append(line.rstrip()[:-1])
                _store(pending_key, "\n".join(pending_value))
                pending_key, pending_value, quote = None, [], None
            else:
                pending_value.append(line)
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key or not (key[0].isalpha() or key[0] == "_"):
            continue
        value = value.strip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            body = value[1:]
            # Закрывающую кавычку ищем внутри строки, а не только в её конце:
            # `KEY="value"  # комментарий` иначе принимался за начало
            # многострочного значения и съедал все переменные до следующей
            # строки с кавычкой. Они попадали в «исчезнет», и человек видел
            # десяток пропадающих ключей, которых не трогал, — а на этом списке
            # стоит отказ от потери ключей.
            closing = body.find(quote)
            if closing != -1:
                _store(key, body[:closing])
                quote = None
            else:
                pending_key, pending_value = key, [body]
            continue
        _store(key, value)

    if pending_key is not None:
        # Незакрытая кавычка: значение всё равно принадлежит этому ключу.
        _store(pending_key, "\n".join(pending_value))
    return result


def _write_secret_file(sftp, remote_path: str, text: str) -> None:
    """Записать файл с секретами так, чтобы он ни секунды не лежал открытым.

    Прямая запись с последующим chmod оставляла окно: между созданием и сменой
    прав полный слепок боевого окружения доступен на чтение всем. Пишем во
    временный файл, закрываем права и только потом ставим на место.

    Подмена идёт через `posix_rename` — расширение OpenSSH, которое
    перезаписывает цель одним шагом. Обычный SFTP-rename на существующий путь
    падает, и обход через `remove` + `rename` оставлял бы окно, где `.env` на
    сервере нет вовсе: обрыв связи ровно там — и следующий `docker compose up`
    поднимется без единой переменной окружения. Fallback оставлен для серверов
    без расширения, но он честно назван менее безопасным.
    """
    tmp_path = f"{remote_path}.tmp-{os.getpid()}"
    with sftp.open(tmp_path, "w") as f:
        f.write(text)
    sftp.chmod(tmp_path, 0o600)
    posix_rename = getattr(sftp, "posix_rename", None)
    if posix_rename is not None:
        try:
            posix_rename(tmp_path, remote_path)
            return
        except IOError:
            print("    (сервер не поддерживает posix-rename, подменяем в два шага)")
    try:
        sftp.remove(remote_path)
    except FileNotFoundError:
        pass
    sftp.rename(tmp_path, remote_path)


def sync_app_env(sftp, *, allow_remove: bool) -> None:
    """Залить прод-окружение с бэкапом и защитой от потери ключей.

    Печатает только имена переменных — значения не попадают ни в консоль, ни в логи.
    """
    local_text = read_app_env()
    local_fp = env_fingerprint(local_text)
    if not local_fp:
        raise RuntimeError("Prod env file has no variables — заливка отменена.")

    remote_path = f"{REMOTE_DIR}/.env"
    try:
        with sftp.open(remote_path, "r") as f:
            remote_text = f.read().decode("utf-8")
    except FileNotFoundError:
        remote_text = ""
    remote_fp = env_fingerprint(remote_text)

    removed = sorted(set(remote_fp) - set(local_fp))
    added = sorted(set(local_fp) - set(remote_fp))
    changed = sorted(k for k in set(local_fp) & set(remote_fp) if local_fp[k] != remote_fp[k])

    print("\n  Синхронизация окружения сервера:")
    print(f"    добавится:  {', '.join(added) or '—'}")
    print(f"    изменится:  {', '.join(changed) or '—'}")
    print(f"    исчезнет:   {', '.join(removed) or '—'}")

    if removed and not allow_remove:
        raise SystemExit(
            "\nОТМЕНА: на сервере есть переменные, которых нет в прод-файле — "
            "заливка стёрла бы их.\nДобавьте их в прод-файл или запустите с "
            "--allow-remove-env-keys, если удаление осознанное."
        )

    if remote_text:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = f"{REMOTE_DIR}/.env.bak-{stamp}"
        _write_secret_file(sftp, backup_path, remote_text)
        print(f"    бэкап:      {backup_path}")
        # Каждый бэкап — полный слепок боевых секретов. Ротацию не делаем сами:
        # удалять файлы на проде вслепую опаснее, чем сказать о них вслух.
        try:
            backups = [n for n in sftp.listdir(REMOTE_DIR) if n.startswith(".env.bak-")]
        except OSError:
            backups = []
        if len(backups) > 5:
            print(f"    ВНИМАНИЕ:   на сервере {len(backups)} копий окружения "
                  f"с боевыми секретами — старые стоит удалить руками")

    _write_secret_file(sftp, remote_path, local_text)
    print("    окружение залито")


VERSION_FILE = ".deployed-version"


def git(*args: str) -> str | None:
    """Вывод git-команды или None, если git недоступен либо команда упала."""
    try:
        done = subprocess.run(
            # -c core.quotepath=false: иначе git экранирует не-ASCII пути в \NNN.
            # encoding задаём явно — консоль Windows (cp1251) на UTF-8 именах падает.
            ["git", "-c", "core.quotepath=false", *args],
            cwd=LOCAL_DIR,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    if done.returncode != 0 or done.stdout is None:
        return None
    return done.stdout.strip()


def git_state() -> dict:
    """Текущее состояние репозитория: коммит, ветка, незакоммиченное."""
    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {"available": False}
    porcelain = git("status", "--porcelain") or ""
    modified: list[str] = []
    untracked: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip().strip('"')
        # Переименование печатается как `R  old -> new`. Без разбора в список
        # попадала вся строка, она не совпадала ни с одним реальным путём, и
        # поштучный гейт считал незакоммиченный переименованный файл чистым.
        if "R" in status and " -> " in path:
            source, _, target = path.partition(" -> ")
            modified.append(source.strip().strip('"'))
            modified.append(target.strip().strip('"'))
            continue
        (untracked if status == "??" else modified).append(path)
    return {
        "available": True,
        "commit": commit,
        "short": commit[:7],
        "branch": git("rev-parse", "--abbrev-ref", "HEAD") or "?",
        "modified": sorted(modified),
        "untracked": sorted(untracked),
    }


def assert_clean_enough(state: dict, target_files: list[Path], *, allow_dirty: bool) -> None:
    """Не дать проду разойтись с коммитом.

    Полный деплой требует чистого дерева целиком. Обратите внимание на асимметрию:
    изменения отслеживаемых файлов уедут на сервер, а новые (untracked) — нет,
    потому что множество берётся из индекса git. Поэтому грязное дерево опаснее,
    чем кажется: под `--allow-dirty` уже изменённый код уедет, а новый модуль или
    шаблон, на который он ссылается, останется локально, и приложение упадёт на
    импорте при следующем рестарте.

    Поштучный режим смотрит только на свои файлы — остальной WIP на сервер не
    поедет и разойтись не может.
    """
    if not state.get("available"):
        print("  git недоступен — проверка версии пропущена")
        return

    if target_files:
        rel = {p.relative_to(LOCAL_DIR).as_posix() for p in target_files}
        dirty = sorted(rel & (set(state["modified"]) | set(state["untracked"])))
        problem = "заливаемые файлы не закоммичены"
    else:
        dirty = sorted(state["modified"] + state["untracked"])
        problem = (
            "дерево грязное: изменения отслеживаемых файлов уедут, "
            "а новые файлы — нет (заливка идёт по индексу git)"
        )

    if not dirty:
        return

    print(f"\n  ВНИМАНИЕ: {problem}:")
    for path in dirty[:20]:
        print(f"      {path}")
    if len(dirty) > 20:
        print(f"      … и ещё {len(dirty) - 20}")

    if not allow_dirty:
        raise SystemExit(
            "\nОТМЕНА: прод разошёлся бы с коммитом.\n"
            "Закоммитьте изменения или запустите с --allow-dirty, если это осознанно "
            "(маркер версии на сервере тогда пометит деплой как грязный)."
        )
    print("  --allow-dirty: продолжаем, деплой будет помечен как грязный")


def write_version_marker(
    sftp, state: dict, *, target_files: list[Path], dirty_ok: bool, previous: dict | None
) -> None:
    """Записать на сервер, какой версией кода он сейчас живёт.

    Поштучный деплой не переводит сервер на локальный HEAD: приехал один файл, а
    остальное дерево осталось от прошлой полной заливки. Поэтому `commit`
    продолжает указывать на последний полный деплой, а коммит-источник заплатки
    пишется отдельным полем. Иначе `--status` уверенно печатал бы «коммиты
    сходятся» для сервера, где этого коммита нет.
    """
    if not state.get("available"):
        # Git недоступен, но файлы уже уехали. Прежний маркер оставлять нельзя:
        # он продолжал бы называть старый коммит выкаченным, хотя дерево сервера
        # уже другое. Пишем честное «неизвестно».
        payload = {
            "commit": None,
            "branch": "?",
            "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": "files" if target_files else "full",
            "files": sorted(p.relative_to(LOCAL_DIR).as_posix() for p in target_files),
            "dirty": True,
            "dirty_files": [],
            "note": "git был недоступен при деплое — версия не установлена",
        }
        with sftp.open(f"{REMOTE_DIR}/{VERSION_FILE}", "w") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\n  Версия на сервере: неизвестна (git был недоступен)")
        return
    rel = sorted(p.relative_to(LOCAL_DIR).as_posix() for p in target_files)
    dirty = sorted(set(state["modified"]) | set(state["untracked"]))
    if rel:
        dirty = [p for p in dirty if p in set(rel)]
    payload = {
        "commit": state["commit"],
        "branch": state["branch"],
        "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "files" if rel else "full",
        "files": rel,
        "dirty": bool(dirty) and dirty_ok,
        "dirty_files": dirty,
    }
    if rel:
        # Базовым остаётся коммит прошлой полной заливки, если он известен.
        base = (previous or {}).get("commit")
        payload["commit"] = base or None
        payload["patched_from_commit"] = state["commit"]
        history = list((previous or {}).get("patches", []))
        history.append({
            "commit": state["commit"],
            "deployed_at": payload["deployed_at"],
            "files": rel,
        })
        # Историю заплаток храним, иначе после второй поштучной заливки нельзя
        # восстановить, какой файл приехал из какого коммита.
        payload["patches"] = history[-20:]
    with sftp.open(f"{REMOTE_DIR}/{VERSION_FILE}", "w") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2))
    mark = " (грязный)" if payload["dirty"] else ""
    if rel:
        base_short = (payload["commit"] or "неизвестен")[:7]
        print(f"\n  Сервер: база {base_short}, заплатка {state['short']} / {state['branch']}{mark}")
    else:
        print(f"\n  Версия на сервере: {state['short']} / {state['branch']}{mark}")


def read_version_marker(sftp) -> dict | None:
    try:
        with sftp.open(f"{REMOTE_DIR}/{VERSION_FILE}", "r") as f:
            return json.loads(f.read().decode("utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def deployable_files() -> list[str]:
    """Отслеживаемые git файлы, которые реально уезжают на сервер."""
    listing = git("ls-files") or ""
    result = []
    for path in listing.splitlines():
        parts = Path(path).parts
        if any(part in SKIP for part in parts):
            continue
        if (LOCAL_DIR / path).is_file():
            result.append(path)
    return result


def wait_until_healthy(client, *, attempts: int = 40, delay_seconds: int = 5) -> bool:
    """Дождаться, пока контейнер приложения станет healthy.

    `docker compose up -d` отвечает «контейнеры запущены», а не «приложение
    работает»: в образе `CMD` сначала гонит `alembic upgrade head` и только потом
    поднимает uvicorn. То есть миграции идут уже после того, как сборка вернула
    ноль. Упавшая миграция роняет контейнер в рестарт-петлю, а деплой без этой
    проверки печатал «Done!», записывал маркер с новым коммитом, и `--status`
    после этого уверенно показывал коммит выкаченным.

    Опираемся на HEALTHCHECK из образа: он уже описан и бьёт в `/health`.
    """
    print("\nЖдём, пока приложение станет healthy...")
    probe = (
        f"cd {REMOTE_DIR} && cid=$(docker compose -f {COMPOSE_FILE} ps -q app) && "
        '[ -n "$cid" ] && docker inspect -f '
        "'{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}nohealth{{end}}' "
        '"$cid"'
    )
    for attempt in range(1, attempts + 1):
        _, stdout, _ = client.exec_command(probe, timeout=30)
        line = stdout.read().decode("utf-8", errors="replace").strip()
        if stdout.channel.recv_exit_status() != 0 or not line:
            time.sleep(delay_seconds)
            continue
        status, _, health = line.partition(" ")
        if health == "healthy":
            print(f"  приложение отвечает (попытка {attempt})")
            return True
        if health == "nohealth" and status == "running":
            # У образа нет HEALTHCHECK — большего отсюда не проверить.
            print("  у контейнера нет HEALTHCHECK, ограничиваемся статусом running")
            return True
        if status in {"exited", "dead"}:
            print(f"  контейнер {status} — приложение не поднялось")
            return False
        time.sleep(delay_seconds)
    print(f"  не дождались за {attempts * delay_seconds} секунд")
    return False


def warn_about_stale_remote_files(client, tracked: list[str]) -> None:
    """Предупредить о файлах, которые остались на сервере лишними.

    Деплой ничего не удаляет, поэтому файл, убранный из git, продолжает жить на
    проде и импортироваться. Удалять его отсюда нельзя — на той стороне боевой
    сервер, а сравнение не знает про файлы, созданные приложением в рантайме.
    Поэтому только называем расхождение вслух, решение остаётся за человеком.
    """
    listing = (
        f"cd {REMOTE_DIR} && find . -type f "
        r"-not -path './.git/*' -not -path './__pycache__/*' "
        r"-not -name '.env' -not -name '.env.bak-*' -not -name '" + VERSION_FILE + "' "
        r"-printf '%P\n' 2>/dev/null"
    )
    _, stdout, _ = client.exec_command(listing, timeout=60)
    payload = stdout.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    remote = {line.strip() for line in payload.splitlines() if line.strip()}
    # `find` возвращает ненулевой код на любом нечитаемом каталоге в обходе.
    # Молчать в этом случае нельзя: человек получил бы подтверждение, что лишних
    # файлов нет, ровно там, где проверка ничего не смогла узнать.
    if status != 0 and not remote:
        print("\n  ВНИМАНИЕ: не удалось перечислить файлы на сервере "
              f"(find вернул {status}) — проверка остатков не выполнена.")
        return
    if status != 0:
        print(f"\n  ВНИМАНИЕ: find вернул {status}, список ниже может быть неполным.")
    if not remote:
        return
    stale = sorted(remote - set(tracked))
    if not stale:
        return
    # Окружения показываем первыми: именно они опаснее всего, а по алфавиту
    # точечные каталоги вытеснили бы `.env.prod` за границу вывода.
    stale.sort(key=lambda p: (0 if PurePosixPath(p).name.startswith(".env") else 1, p))
    print(f"\n  ВНИМАНИЕ: на сервере {len(stale)} файл(ов) вне индекса git.")
    print("  Деплой их не удаляет — проверьте и уберите руками, если это остатки:")
    for path in stale[:20]:
        print(f"      {path}")
    if len(stale) > 20:
        print(f"      … и ещё {len(stale) - 20}")


def show_status(client, sftp) -> None:
    """Сравнить прод с локальным репозиторием: версия и фактические файлы."""
    state = git_state()
    marker = read_version_marker(sftp)

    print("\n=== Версия ===")
    if state.get("available"):
        print(f"  локально:  {state['short']} / {state['branch']}")
        if state["modified"] or state["untracked"]:
            print(f"             незакоммичено файлов: "
                  f"{len(state['modified']) + len(state['untracked'])}")
    else:
        print("  локально:  git недоступен")

    if marker is None:
        print("  на сервере: маркера нет — деплой был до появления версионирования")
    else:
        mark = " (грязный)" if marker.get("dirty") else ""
        base = marker.get("commit") or "?"
        print(f"  на сервере: {base[:7]} / "
              f"{marker.get('branch','?')}{mark}, {marker.get('deployed_at','?')}")
        if marker.get("mode") == "files":
            patched = marker.get("patched_from_commit") or "?"
            print(f"              поверх базы заплатки, последняя из {patched[:7]}, "
                  f"файлов: {len(marker.get('files', []))}")
            print(f"              всего заплаток в истории: {len(marker.get('patches', []))}")
        if state.get("available") and marker.get("commit") != state.get("commit"):
            print("  ВНИМАНИЕ: коммиты расходятся")

    print("\n=== Файлы (sha256 рабочего дерева против сервера) ===")
    files = deployable_files()
    if not files:
        print("  список пуст — git недоступен")
        return

    local_hashes = {}
    for path in files:
        digest = hashlib.sha256((LOCAL_DIR / path).read_bytes()).hexdigest()
        local_hashes[path] = digest

    quoted = " ".join(f"'{p}'" for p in files)
    _, stdout, _ = client.exec_command(
        f"cd {REMOTE_DIR} && sha256sum {quoted} 2>/dev/null", timeout=120
    )
    remote_hashes = {}
    for line in stdout.read().decode("utf-8", errors="replace").splitlines():
        digest, _, path = line.partition("  ")
        if path:
            remote_hashes[path.strip()] = digest.strip()

    differ = [p for p in files if remote_hashes.get(p) != local_hashes[p]]
    missing = [p for p in files if p not in remote_hashes]
    print(f"  проверено: {len(files)}, совпало: {len(files) - len(differ)}")
    if differ:
        print("  расходятся:")
        for path in differ[:25]:
            tail = " (нет на сервере)" if path in missing else ""
            print(f"      {path}{tail}")
        if len(differ) > 25:
            print(f"      … и ещё {len(differ) - 25}")
    else:
        print("  расхождений нет")


def assert_uploadable(local_paths: list[Path]) -> None:
    """Не пустить в поштучный режим то, что исключено из полного деплоя.

    Гейт чистоты дерева сравнивает пути с `git status --porcelain`, а `.env` и
    прочее игнорируемое туда не попадает вовсе. Без этой проверки `deploy.py .env`
    молча кладёт локальную dev-конфигурацию поверх боевой — ровно та авария, от
    которой уходили, когда перестали заливать окружение автоматически. Заливка
    окружения — только через `--sync-env`, с бэкапом и разницей по именам ключей.
    """
    for path in local_paths:
        rel = path.relative_to(LOCAL_DIR)
        # Окружение проверяем первым: `.env` попадает и под SKIP, но человеку
        # нужен не факт исключения, а рабочий способ залить окружение.
        # Образец окружения секретов не содержит и является частью поставки —
        # проверку на «файл окружения» он проходит, остальные ниже обязан пройти.
        if (rel.name == ".env" or rel.name.startswith(".env.")) and rel.name != ".env.example":
            raise SystemExit(
                f"\nОТМЕНА: {rel.as_posix()} — файл окружения.\n"
                "Окружение сервера заливается только флагом --sync-env из .env.prod:\n"
                "  python scripts/deploy.py --sync-env"
            )
        if any(part in SKIP for part in rel.parts):
            raise SystemExit(
                f"\nОТМЕНА: {rel.as_posix()} исключён из деплоя (SKIP).\n"
                "Полный деплой такие файлы не заливает, поштучный тоже не должен."
            )
        # Файл обязан быть в индексе git. Игнорируемый не попадает в
        # `git status --porcelain`, поэтому гейт чистоты пропускал бы его молча,
        # маркер помечал деплой чистым, а `--status` никогда бы о нём не узнал:
        # поштучный режим принимал больше, чем проверка вообще способна увидеть.
        if not is_tracked_by_git(rel):
            raise SystemExit(
                f"\nОТМЕНА: {rel.as_posix()} не отслеживается git.\n"
                "Такой файл не проверить ни гейтом чистоты, ни --status, а прод\n"
                "обязан совпадать с коммитом. Закоммитьте файл или уберите из деплоя."
            )


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
    args = sys.argv[1:]
    known = {"--sync-env", "--allow-remove-env-keys", "--allow-dirty", "--status"}
    sync_env = "--sync-env" in args
    allow_remove = "--allow-remove-env-keys" in args
    allow_dirty = "--allow-dirty" in args
    status_only = "--status" in args
    unknown = [a for a in args if a.startswith("-") and a not in known]
    if unknown:
        raise SystemExit(f"Unknown option(s): {' '.join(unknown)}")

    # Позиционные аргументы = пути файлов относительно LOCAL_DIR
    file_args = [a for a in args if not a.startswith("-")]
    target_files: list[Path] = []
    for arg in file_args:
        p = (LOCAL_DIR / arg).resolve()
        if not p.exists():
            raise SystemExit(f"File not found: {p}")
        if not p.is_file():
            raise SystemExit(f"Not a file: {p}")
        target_files.append(p)
    assert_uploadable(target_files)

    # Прод-окружение валидируем до подключения: падать на нём после заливки
    # половины файлов — худший из возможных моментов.
    if sync_env and not status_only:
        read_app_env()

    state = git_state()
    if not status_only:
        assert_clean_enough(state, target_files, allow_dirty=allow_dirty)

    host = require_env("PORTFOLIO_SSH_HOST")
    assert_known_host(host)
    print(f"Connecting to {host}...")
    client = connect_client()

    sftp = client.open_sftp()

    if status_only:
        show_status(client, sftp)
        sftp.close()
        client.close()
        return

    # Create remote dir
    try:
        sftp.stat(REMOTE_DIR)
    except FileNotFoundError:
        sftp.mkdir(REMOTE_DIR)

    # Читаем маркер до заливки: поштучный режим опирается на коммит прошлого
    # полного деплоя, а после записи нового маркера прежний уже не восстановить.
    previous_marker = read_version_marker(sftp)

    if target_files:
        print(f"Uploading {len(target_files)} file(s) -> {REMOTE_DIR}")
        upload_files(sftp, target_files)
    else:
        # Источник множества — индекс git, а не обход файловой системы. Обход
        # увозил на сервер всё игнорируемое: `.env.prod` с боевыми секретами,
        # `.env.deploy` (а он по умолчанию целится в чужой прод), `.codegraph/`,
        # `.pytest_cache/`, `visual_smoke.db`, выгрузки `reports/`. Причём
        # `--status` их не показывал — он сверяет ровно `git ls-files`, поэтому
        # проверка молчала именно про те файлы, которых на сервере быть не должно.
        # Теперь заливается и сверяется одно и то же множество.
        tracked = deployable_files()
        if not tracked:
            raise SystemExit(
                "\nОТМЕНА: список файлов пуст — git недоступен или каталог не репозиторий.\n"
                "Полный деплой берёт файлы из индекса git, вслепую дерево не заливается."
            )
        print(f"Uploading {len(tracked)} tracked file(s) -> {REMOTE_DIR}")
        upload_files(sftp, [LOCAL_DIR / path for path in tracked])
        warn_about_stale_remote_files(client, tracked)

    if sync_env:
        sync_app_env(sftp, allow_remove=allow_remove)
    else:
        print("\n  Окружение сервера не трогаем (нужен флаг --sync-env).")

    print("\nDone! Files uploaded.")

    # Build and start (пересобирает только app, db и redis не трогает)
    print(f"\nBuilding and starting containers (compose: {COMPOSE_FILE})...")
    stdin, stdout, stderr = client.exec_command(
        f"cd {REMOTE_DIR} && docker compose -f {COMPOSE_FILE} up -d --build 2>&1",
        timeout=600,
    )
    output = stdout.read().decode("utf-8", errors="replace")
    errors = stderr.read().decode("utf-8", errors="replace")
    build_status = stdout.channel.recv_exit_status()
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

    # Маркер пишется только после успешной сборки и читает её код возврата.
    # Иначе упавший build оставлял бы на сервере запись о новом коммите при
    # работающем старом образе, а `--status` подтверждал бы, что всё выкачено.
    if build_status != 0:
        sftp.close()
        client.close()
        raise SystemExit(
            f"\nОТМЕНА: сборка на сервере вернула код {build_status}.\n"
            "Маркер версии не записан, но прод УЖЕ ЗАТРОНУТ: каталог app/ примонтирован\n"
            "в контейнер, поэтому залитые шаблоны отдаются ученикам сразу, а Python-код\n"
            "останется старым до ближайшего рестарта — после которого поедет новый код\n"
            "без пересобранного образа.\n"
            "Разберите вывод выше и либо доведите деплой до конца, либо выкатите\n"
            "предыдущий коммит целиком."
        )

    if not wait_until_healthy(client):
        sftp.close()
        client.close()
        raise SystemExit(
            "\nОТМЕНА: контейнер не вышел в состояние healthy.\n"
            "Маркер версии не записан, поэтому --status не покажет этот коммит\n"
            "выкаченным. Но прод УЖЕ ЗАТРОНУТ: файлы залиты, а каталог app/\n"
            "примонтирован в контейнер — шаблоны отдаются новые. Чаще всего сюда\n"
            "приводит упавшая миграция: alembic upgrade head идёт в CMD, то есть\n"
            "уже после успешной сборки.\n"
            f"Логи: docker compose -f {COMPOSE_FILE} logs --tail=100 app"
        )

    write_version_marker(
        sftp,
        state,
        target_files=target_files,
        dirty_ok=allow_dirty,
        previous=previous_marker,
    )
    sftp.close()

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
