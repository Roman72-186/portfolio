"""Проверки покупки, мутации в изолированной копии и воспроизведение baseline.

python scripts/check_public_purchase.py
python scripts/check_public_purchase.py --mutations
python scripts/check_public_purchase.py --baseline
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    'test_public_purchase.py', 'test_payments.py', 'test_routes_telegram_login.py',
    'test_routes_telegram_auth.py', 'test_student_behavior.py', 'test_annual_transfer.py',
    'test_holiday_track_payment.py', 'test_reuse_ratchet.py',
    'test_routes_auth.py', 'test_routes_cabinet_personal.py', 'test_access_until.py',
]


def run(args, cwd=ROOT, *, capture=False):
    env = {**os.environ, 'SESSION_SECRET': 'isolated-public-purchase-check', 'PYTHONIOENCODING': 'utf-8'}
    return subprocess.run([sys.executable, '-m', 'pytest', *args, '-q', '-p', 'no:cacheprovider'],
                          cwd=cwd, env=env, capture_output=capture, text=True, encoding='utf-8')


def isolated_copy(destination):
    shutil.copytree(ROOT / 'app', destination / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    (destination / 'tests').mkdir()
    shutil.copy(ROOT / 'tests/conftest.py', destination / 'tests/conftest.py')


def mutations():
    cases = [
        ('Обход допуска', 'app/services/public_purchase.py',
         'def allows_path(db, enrollment, path: str) -> bool:',
         'def allows_path(db, enrollment, path: str) -> bool:\n    return True',
         'unpaid_cannot_open_other_routes'),
        ('Возврат считается оплатой', 'app/services/public_purchase.py',
         'and payment.status == STATUS_PAID', 'and True', 'refund_before_admission'),
        ('Подмена тарифа анкетой', 'app/api/cabinet_student.py',
         'tariff = user["purchase_paid_tariff"]', 'tariff = tariff', 'paid_buyer_can_only'),
        ('Членство не проверяется', 'app/api/public_purchase.py',
         'if member is not True:', 'if False:', 'channel_admission_checks'),
        ('Продажи включены без флага', 'app/services/public_purchase.py',
         'settings.public_purchase_enabled and payments.payments_configured()',
         'payments.payments_configured()', 'incomplete_settings_disable_orders'),
    ]
    killed = 0
    with tempfile.TemporaryDirectory(prefix='public-purchase-mutations-') as directory:
        target = Path(directory)
        isolated_copy(target)
        shutil.copy(ROOT / 'tests/test_public_purchase.py', target / 'tests/test_public_purchase.py')
        for label, relative, old, new, selection in cases:
            path = target / relative
            original = path.read_text(encoding='utf-8')
            if original.count(old) != 1:
                raise RuntimeError(f'Неоднозначная мутация: {label}')
            try:
                path.write_text(original.replace(old, new, 1), encoding='utf-8')
                result = run(['tests/test_public_purchase.py', '-k', selection], target, capture=True)
                # Ошибка сбора/окружения не считается убитым мутантом.
                dead = result.returncode == 1 and 'AssertionError' in result.stdout
                killed += dead
                print(f'{label}: {"KILLED" if dead else "SURVIVED"}', flush=True)
                if not dead:
                    print(result.stdout + result.stderr)
            finally:
                path.write_text(original, encoding='utf-8')
    print(f'Mutations: {killed}/{len(cases)} killed')
    return int(killed != len(cases))


def baseline():
    with tempfile.TemporaryDirectory(prefix='public-purchase-baseline-') as directory:
        target = Path(directory)
        isolated_copy(target)
        # Только исходники HEAD активного приложения; .env не копируется.
        tracked = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'HEAD', 'app'],
                                          cwd=ROOT, text=True).splitlines()
        for relative in tracked:
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output(['git', 'show', f'HEAD:{relative}'], cwd=ROOT))
        shutil.copy(ROOT / 'tests/test_student_behavior.py', target / 'tests/test_student_behavior.py')
        result = run(['tests/test_student_behavior.py::test_portfolio_gate_blocks_direct_url_to_locked_pages'],
                     target)
        return result.returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mutations', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--visual', action='store_true')
    args = parser.parse_args()
    if args.mutations:
        sys.exit(mutations())
    if args.baseline:
        sys.exit(baseline())
    if args.visual:
        sys.exit(run(['tests/test_public_purchase_visual.py']).returncode)
    sys.exit(run([f'tests/{name}' for name in TESTS]).returncode)
