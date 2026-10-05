"""Тесты `scripts/deploy.py`.

Скрипт нельзя выполнить в тестах — за ним боевой сервер с живыми учениками.
Поэтому проверяются чистые функции, которые решают, что уедет на прод и куда:
они и есть носители гарантий, заявленных в CLAUDE.md.
"""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_deploy():
    spec = importlib.util.spec_from_file_location("deploy_script", ROOT / "scripts" / "deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy()


# ── Целевой сервер ────────────────────────────────────────────────────────────

def test_unknown_host_is_refused(monkeypatch):
    """`.env.deploy` по умолчанию указывает на чужой боевой проект.

    Ошибка здесь бьёт не по своему проду, а по соседнему, поэтому отказ должен
    случиться до подключения — до того, как хоть один байт уедет по SFTP.
    """
    monkeypatch.delenv("PORTFOLIO_ALLOW_ANY_HOST", raising=False)
    with pytest.raises(SystemExit) as exc:
        deploy.assert_known_host("89.23.96.254")
    assert "89.23.96.254" in str(exc.value)


def test_known_host_passes(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_ALLOW_ANY_HOST", raising=False)
    deploy.assert_known_host("139.100.237.57")


def test_host_check_can_be_bypassed_explicitly(monkeypatch):
    """Обход остаётся, но только явным флагом — новый сервер не должен блокировать работу."""
    monkeypatch.setenv("PORTFOLIO_ALLOW_ANY_HOST", "1")
    deploy.assert_known_host("203.0.113.10")


# ── Что можно заливать поштучно ───────────────────────────────────────────────

@pytest.mark.parametrize("relative", [".env", ".env.prod", ".env.deploy"])
def test_env_files_are_refused_in_per_file_mode(relative):
    """Локальный `.env` — dev-конфигурация с другой БД и другим Redis.

    Однажды она уже заливалась на сервер при каждом деплое; заливка окружения
    должна идти только через --sync-env, с бэкапом и разницей по именам ключей.
    """
    with pytest.raises(SystemExit) as exc:
        deploy.assert_uploadable([deploy.LOCAL_DIR / relative])
    assert "--sync-env" in str(exc.value)


@pytest.mark.parametrize("relative", ["tests/conftest.py", "prototypes/video-player/index.html"])
def test_skipped_paths_are_refused_in_per_file_mode(relative):
    with pytest.raises(SystemExit) as exc:
        deploy.assert_uploadable([deploy.LOCAL_DIR / relative])
    assert "SKIP" in str(exc.value)


def test_regular_app_file_is_allowed():
    deploy.assert_uploadable([deploy.LOCAL_DIR / "app" / "api" / "video.py"])


# ── Множество полного деплоя ──────────────────────────────────────────────────

def test_upload_set_never_contains_environment_files():
    """Полный деплой берёт файлы из индекса git, а не обходом дерева.

    Обход увозил на сервер всё игнорируемое, включая `.env.prod` с боевыми
    паролями, и `--status` этого не показывал: он сверяет ровно `git ls-files`.
    Образец окружения — часть поставки и уезжать должен.
    """
    names = [Path(p).name for p in deploy.deployable_files()]
    env_files = sorted(n for n in names if n.startswith(".env"))
    assert env_files == [".env.example"]


def test_upload_set_excludes_tests_and_prototypes():
    files = deploy.deployable_files()
    assert not [p for p in files if p.startswith("tests/")]
    assert not [p for p in files if p.startswith("prototypes/")]
    assert "app/api/video.py" in files


# ── Разбор окружения ──────────────────────────────────────────────────────────

def test_fingerprint_reads_export_quotes_and_multiline():
    """На разборе стоит отказ от потери ключей — ложная картина ломает защиту.

    Многострочные значения в `.env` реальны: приватные ключи и сертификаты.
    Наивный разбор считал их продолжение отдельными переменными.
    """
    text = (
        "# комментарий\n"
        "export PLAIN=1\n"
        'QUOTED="два слова"\n'
        "SINGLE='значение'\n"
        'KEY="-----BEGIN-----\nстрока\n-----END-----"\n'
        "TRAILING=последняя\n"
    )
    assert sorted(deploy.env_fingerprint(text)) == [
        "KEY", "PLAIN", "QUOTED", "SINGLE", "TRAILING",
    ]


def test_fingerprint_hides_values():
    """В консоль уходит разница по именам, значения не должны быть восстановимы."""
    fingerprint = deploy.env_fingerprint("SECRET=пароль\n")
    assert "пароль" not in json.dumps(fingerprint, ensure_ascii=False)
    assert len(fingerprint["SECRET"]) == 8


def test_fingerprint_distinguishes_changed_value():
    assert deploy.env_fingerprint("A=1\n")["A"] != deploy.env_fingerprint("A=2\n")["A"]


# ── Маркер версии ─────────────────────────────────────────────────────────────

class _FakeSftpFile:
    def __init__(self, sink):
        self.sink = sink

    def write(self, text):
        self.sink.append(text)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSftp:
    def __init__(self):
        self.written = []

    def open(self, path, mode):
        return _FakeSftpFile(self.written)

    def listdir(self, path):
        return []

    def payload(self):
        return json.loads("".join(self.written))


def _state():
    return {
        "available": True,
        "commit": "b" * 40,
        "short": "bbbbbbb",
        "branch": "main",
        "modified": [],
        "untracked": [],
    }


def test_per_file_marker_does_not_claim_head_is_on_server():
    """Приехал один файл — сервер остался на прошлом полном деплое.

    Пока маркер писал сюда HEAD, `--status` уверенно печатал «коммиты сходятся»
    для сервера, на котором этого коммита нет.
    """
    sftp = _FakeSftp()
    previous = {"commit": "a" * 40, "mode": "full"}

    deploy.write_version_marker(
        sftp,
        _state(),
        target_files=[deploy.LOCAL_DIR / "app" / "api" / "video.py"],
        dirty_ok=False,
        previous=previous,
    )

    payload = sftp.payload()
    assert payload["commit"] == "a" * 40
    assert payload["patched_from_commit"] == "b" * 40
    assert payload["mode"] == "files"
    assert payload["files"] == ["app/api/video.py"]
    assert payload["patches"][-1]["files"] == ["app/api/video.py"]


def test_full_deploy_marker_records_head():
    sftp = _FakeSftp()

    deploy.write_version_marker(
        sftp, _state(), target_files=[], dirty_ok=False, previous=None
    )

    payload = sftp.payload()
    assert payload["commit"] == "b" * 40
    assert payload["mode"] == "full"
    assert "patched_from_commit" not in payload


def test_patch_history_accumulates_across_per_file_deploys():
    """Иначе после второй заплатки нельзя восстановить, что откуда приехало."""
    sftp = _FakeSftp()
    previous = {
        "commit": "a" * 40,
        "patches": [{"commit": "c" * 40, "deployed_at": "2026-08-01T00:00:00Z", "files": ["old.py"]}],
    }

    deploy.write_version_marker(
        sftp,
        _state(),
        target_files=[deploy.LOCAL_DIR / "app" / "api" / "video.py"],
        dirty_ok=False,
        previous=previous,
    )

    patches = sftp.payload()["patches"]
    assert len(patches) == 2
    assert patches[0]["files"] == ["old.py"]
    assert patches[1]["files"] == ["app/api/video.py"]


def test_untracked_file_is_refused_in_per_file_mode(tmp_path, monkeypatch):
    """Игнорируемый git файл не проверить ни гейтом чистоты, ни `--status`.

    `git status --porcelain` его не печатает, поэтому поштучный режим принимал
    больше, чем проверки вообще способны увидеть, а маркер помечал такой деплой
    чистым.
    """
    monkeypatch.setattr(deploy, "is_tracked_by_git", lambda rel: False)
    with pytest.raises(SystemExit) as exc:
        deploy.assert_uploadable([deploy.LOCAL_DIR / "app" / "api" / "video.py"])
    assert "не отслеживается git" in str(exc.value)


def test_renamed_file_is_seen_by_the_clean_gate(monkeypatch):
    """Переименование печатается как `R  old -> new`.

    Без разбора в список попадала вся строка, она не совпадала ни с одним
    реальным путём, и незакоммиченный переименованный файл проходил поштучный
    гейт как чистый.
    """
    def fake_git(*args):
        if args[:1] == ("rev-parse",) and args[-1] == "HEAD":
            return "a" * 40
        if args[:1] == ("status",):
            return "R  alembic/versions/old_name.py -> alembic/versions/new_name.py"
        return "main"

    monkeypatch.setattr(deploy, "git", fake_git)
    state = deploy.git_state()

    assert "alembic/versions/new_name.py" in state["modified"]
    assert "alembic/versions/old_name.py" in state["modified"]
    with pytest.raises(SystemExit):
        deploy.assert_clean_enough(
            state,
            [deploy.LOCAL_DIR / "alembic" / "versions" / "new_name.py"],
            allow_dirty=False,
        )


class _FakeChannel:
    def __init__(self, status):
        self._status = status

    def recv_exit_status(self):
        return self._status


class _FakeStdout:
    def __init__(self, text, status=0):
        self._text = text
        self.channel = _FakeChannel(status)

    def read(self):
        return self._text.encode("utf-8")


class _FakeClient:
    """Отдаёт заранее заданную последовательность ответов на exec_command."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def exec_command(self, command, timeout=None):
        self.calls += 1
        text, status = self.responses.pop(0) if self.responses else ("", 1)
        return None, _FakeStdout(text, status), _FakeStdout("")


def test_deploy_waits_for_healthy_container(monkeypatch):
    """`up -d` значит «контейнер запущен», а не «приложение работает».

    Миграции идут в CMD уже после ответа сборки, поэтому здоровье проверяется
    отдельно — иначе упавший `alembic upgrade head` оставил бы маркер с новым
    коммитом при контейнере в рестарт-петле.
    """
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    client = _FakeClient([("restarting starting", 0), ("running healthy", 0)])

    assert deploy.wait_until_healthy(client, attempts=5, delay_seconds=0) is True
    assert client.calls == 2


def test_deploy_reports_failure_when_container_exits(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    client = _FakeClient([("exited unhealthy", 0)])

    assert deploy.wait_until_healthy(client, attempts=5, delay_seconds=0) is False


def test_deploy_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    client = _FakeClient([("running starting", 0)] * 3)

    assert deploy.wait_until_healthy(client, attempts=3, delay_seconds=0) is False


def test_fingerprint_survives_trailing_comment_after_quoted_value():
    """`KEY="v"  # коммент` не должен съедать следующие переменные.

    Разбор искал закрывающую кавычку только в конце строки, уходил в режим
    многострочного значения и терял всё до следующей строки с кавычкой. Потерянные
    ключи попадали в «исчезнет», а на этом списке стоит отказ от потери ключей —
    человек увидел бы десяток чужих пропаж и мог снять защиту флагом.
    """
    text = (
        'DATABASE_URL="postgresql://user:pass@db/app"  # основная база\n'
        "SESSION_SECRET=secret\n"
        "REDIS_PASSWORD=redis\n"
    )
    assert sorted(deploy.env_fingerprint(text)) == [
        "DATABASE_URL", "REDIS_PASSWORD", "SESSION_SECRET",
    ]


def test_fingerprint_still_reads_multiline_values():
    """Хвостовые комментарии не должны сломать разбор приватных ключей."""
    text = 'KEY="-----BEGIN-----\nтело\n-----END-----"\nNEXT=1\n'
    assert sorted(deploy.env_fingerprint(text)) == ["KEY", "NEXT"]


# ── Дамп базы перед деплоем ───────────────────────────────────────────────────


def test_predeploy_dump_command_fails_loudly_on_broken_dump():
    """Код-ревью 28.09.2026, P2 № 13: миграцию применяет старт контейнера, а
    свежей копии перед ней не было. Команда дампа обязана падать, если упал
    `pg_dump` (pipefail, иначе gzip «успешно» упакует пустоту), если архив
    битый или подозрительно мал."""
    command = deploy.predeploy_dump_command("abcdef1234567", "20260929T120000Z")

    assert command.startswith("bash -c ")
    assert "set -euo pipefail" in command
    assert "exec -T db pg_dump -U portfolio -d portfolio --clean --if-exists" in command
    assert "gzip -t" in command
    script = deploy.shlex.split(command)[2]
    assert """trap 'rm -f "$f"' ERR""" in script
    # После проверки размера ловушка снята: сбой ротации не удаляет годный дамп.
    assert script.index("trap - ERR") > script.index(f"-lt {deploy.MIN_DUMP_BYTES}")
    assert script.index("trap - ERR") < script.index("xargs -r rm -f")
    assert f"-lt {deploy.MIN_DUMP_BYTES}" in command
    assert "pre-deploy-20260929T120000Z-abcdef12.sql.gz" in command
    # Своя ротация: ночная (`portfolio-*`, 14 дней) эти файлы не видит.
    assert f"tail -n +{deploy.PREDEPLOY_DUMPS_KEEP + 1}" in command


def test_predeploy_dump_returns_path_on_success():
    client = _FakeClient([("/var/backups/portfolio/pre-deploy-x.sql.gz 1400000", 0)])

    assert deploy.dump_database_before_deploy(client, "abc") == (
        "/var/backups/portfolio/pre-deploy-x.sql.gz"
    )


@pytest.mark.parametrize("response", [("", 1), ("дамп подозрительно мал: 20 байт", 1), ("", 0)])
def test_failed_predeploy_dump_stops_deploy(response):
    client = _FakeClient([response])

    with pytest.raises(SystemExit) as exc:
        deploy.dump_database_before_deploy(client, "abc")
    assert "ОТМЕНА" in str(exc.value)


# --- Блокировка выкатки (05.10.2026) -------------------------------------
# Две выкатки разом дважды клали прод (03.10 и 05.10.2026): параллельные
# `docker compose up -d --build` сталкивались на пересоздании `app`.


def test_lock_is_taken_before_dump_and_released_after(monkeypatch):
    """Порядок в `main`: замок до дампа, снятие — в finally до закрытия связи."""
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    main_src = source[source.index("def main():"):]
    assert main_src.index("acquire_deploy_lock(client") < main_src.index("dump_database_before_deploy(")
    finally_src = main_src[main_src.index("    finally:"):]
    assert finally_src.index("release_deploy_lock(") < finally_src.index("client.close()")
    # Внутри выкатки связь не закрывается: иначе упавшая сборка оставила бы
    # замок висеть до признания брошенным.
    body = main_src[main_src.index("acquire_deploy_lock(client"):main_src.index("    finally:")]
    assert "client.close()" not in body


def test_lock_acquired_at_once(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _: pytest.fail("ждать не нужно"))
    client = _FakeClient([("ACQUIRED", 0)])

    deploy.acquire_deploy_lock(client, "me", wait_seconds=60, poll_seconds=0)
    assert client.calls == 1


def test_lock_waits_for_neighbour_then_takes_it(monkeypatch, capsys):
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    client = _FakeClient([
        ("BUSY 90\nother-host pid=1 commit=abc", 0),
        ("BUSY 100\nother-host pid=1 commit=abc", 0),
        ("ACQUIRED", 0),
    ])

    deploy.acquire_deploy_lock(client, "me", wait_seconds=600, poll_seconds=0)
    assert client.calls == 3
    out = capsys.readouterr().out
    assert out.count("Идёт другая выкатка") == 1
    assert "other-host pid=1 commit=abc" in out


def test_lock_gives_up_without_touching_prod(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    client = _FakeClient([("BUSY 300\nother-host pid=1", 0)])

    with pytest.raises(SystemExit) as exc:
        deploy.acquire_deploy_lock(client, "me", wait_seconds=0, poll_seconds=0)
    message = str(exc.value)
    assert "ОТМЕНА" in message and "ничего не менялось" in message
    assert "other-host pid=1" in message
    assert deploy.DEPLOY_LOCK_DIR in message


@pytest.mark.parametrize("reply", [("", 1), ("garbage", 0)])
def test_lock_unknown_reply_stops_deploy(monkeypatch, reply):
    client = _FakeClient([reply])

    with pytest.raises(SystemExit):
        deploy.acquire_deploy_lock(client, "me", wait_seconds=60, poll_seconds=0)


def test_release_survives_connection_failure(capsys):
    class _Broken:
        def exec_command(self, command, timeout=None):
            raise OSError("связь оборвалась")

    deploy.release_deploy_lock(_Broken(), "me")
    assert "замок выкатки не снят" in capsys.readouterr().out


def _bash():
    import shutil

    path = shutil.which("bash")
    if path is None or "system32" in path.lower():  # WSL-заглушка Windows — не тот bash
        pytest.skip("нужен bash")
    return path


def _run(command):
    import subprocess

    argv = deploy.shlex.split(command)
    assert argv[:2] == ["bash", "-c"]
    out = subprocess.run([_bash(), "-c", argv[2]], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()


def test_lock_shell_commands_really_exclude_each_other(tmp_path, monkeypatch):
    """Настоящие shell-команды замка на временном каталоге: второй не входит,
    чужой не снимает, свой снимает, брошенный забирается."""
    monkeypatch.setattr(deploy, "DEPLOY_LOCK_DIR", (tmp_path / "lock").as_posix())

    assert _run(deploy.deploy_lock_acquire_command("first host pid=1"))[0] == "ACQUIRED"
    busy = _run(deploy.deploy_lock_acquire_command("second"))
    assert busy[0].startswith("BUSY ") and busy[1] == "first host pid=1"

    assert _run(deploy.deploy_lock_release_command("second")) == ["NOT_OWNER"]
    assert (tmp_path / "lock").exists()

    monkeypatch.setattr(deploy, "DEPLOY_LOCK_STALE_SECONDS", 0)
    stale = _run(deploy.deploy_lock_acquire_command("second"))
    assert stale[0].startswith("STALE ") and stale[1] == "first host pid=1"

    assert _run(deploy.deploy_lock_release_command("first host pid=1")) == ["NOT_OWNER"]
    assert _run(deploy.deploy_lock_release_command("second")) == ["RELEASED"]
    assert not (tmp_path / "lock").exists()
