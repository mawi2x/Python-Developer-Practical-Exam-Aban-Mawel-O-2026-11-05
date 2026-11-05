"""Read-only preflight checks. Run with the project's virtual-environment Python."""

import os
from importlib import metadata
from io import StringIO
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def python_error(version):
    if not (3, 10) <= tuple(version[:2]) <= (3, 14):
        return 'Use Python 3.10-3.14; Python 3.14 is the documented development version.'
    return None


def dependency_errors(requirements, version_lookup=None):
    version_lookup = version_lookup or metadata.version
    errors = []
    for line in requirements.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name, expected = line.strip().split('==', 1)
        try:
            actual = version_lookup(name)
        except metadata.PackageNotFoundError:
            errors.append(f'{name} is missing. Install requirements.txt with this Python interpreter.')
        else:
            if actual != expected:
                errors.append(f'{name} must match the pinned version {expected}; reinstall requirements.txt.')
    return errors


def configuration_errors(environment):
    errors = []
    for name, placeholder in [
        ('DJANGO_SECRET_KEY', 'replace-with-a-long-random-secret'),
        ('POSTGRES_PASSWORD', 'replace-with-a-random-database-password'),
    ]:
        value = environment.get(name, '').strip()
        if not value or value == placeholder:
            errors.append(f'{name} is missing or still an example placeholder. Configure and export .env.')
    for name, default in [
        ('POSTGRES_DB', 'ordered_cars'), ('POSTGRES_USER', 'ordered_cars'),
        ('POSTGRES_HOST', '127.0.0.1'),
    ]:
        if not environment.get(name, default).strip():
            errors.append(f'{name} must not be empty.')
    try:
        port = int(environment.get('POSTGRES_PORT', '5432'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        errors.append('POSTGRES_PORT must be an integer between 1 and 65535.')
    if environment.get('DJANGO_DEBUG', 'false').lower() not in ('true', 'false'):
        errors.append('DJANGO_DEBUG must be true or false.')
    hosts = environment.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]')
    if not any(host.strip() for host in hosts.split(',')):
        errors.append('DJANGO_ALLOWED_HOSTS must contain at least one host.')
    return errors


def main():
    failures = 0

    def report(label, errors):
        nonlocal failures
        if errors:
            failures += 1
            print(f'[FAIL] {label}')
            for error in errors:
                print(f'       {error}')
        else:
            print(f'[PASS] {label}')

    print('Cars environment validation (read-only)')
    print('Uses exported environment variables; .env is not loaded automatically.\n')
    error = python_error(sys.version_info)
    report('Python version', [error] if error else [])
    try:
        report('Pinned dependencies', dependency_errors(ROOT / 'requirements.txt'))
    except (OSError, ValueError):
        report('Pinned dependencies', ['requirements.txt is missing or invalid. Use the complete project source.'])
    report('Environment settings', configuration_errors(os.environ))
    if failures:
        print('\nFix the failed checks above and run this command again.')
        return 1

    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'check'], capture_output=True, text=True,
            timeout=30, check=False,
        )
        report('Dependency compatibility', [] if result.returncode == 0 else [
            'Run python -m pip check for details, then reinstall requirements.txt if needed.',
        ])
    except (OSError, subprocess.TimeoutExpired):
        report('Dependency compatibility', ['Could not run pip check using this interpreter.'])

    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()
        from django.core import checks
        from django.db import connection
    except Exception:
        report('Django startup', ['Could not initialize Django. Check settings and run python manage.py check.'])
        return 1
    report('Django startup', [])

    try:
        if connection.vendor != 'postgresql':
            raise ValueError('Wrong database backend')
        connection.settings_dict.setdefault('OPTIONS', {}).setdefault('connect_timeout', 5)
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            assert cursor.fetchone() == (1,)
        report('PostgreSQL connection and supported server version', [])
    except Exception:
        report('PostgreSQL connection', [
            'Connection failed or server version is unsupported. Start PostgreSQL and check POSTGRES_* settings.',
            'For Docker, run docker compose up -d --wait db. Django 5.2 requires PostgreSQL 14 or higher.',
        ])
        connection.close()
        return 1

    try:
        issues = checks.run_checks(databases=['default'])
        fatal = [issue for issue in issues if issue.level >= checks.ERROR]
        report('Django system checks', [
            'Run python manage.py check --database default to inspect the reported configuration errors.',
        ] if fatal else [])
        warnings = [issue for issue in issues if checks.WARNING <= issue.level < checks.ERROR]
        if warnings:
            print('[WARN] Django reported warnings; inspect them with python manage.py check --database default.')
    except Exception:
        report('Django system checks', ['Could not complete checks. Run python manage.py check --database default.'])

    try:
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        report('Applied migrations', [
            f'{len(pending)} migration(s) are pending. Run python manage.py migrate.',
        ] if pending else [])
    except Exception:
        report('Applied migrations', ['Could not inspect migration history. Run python manage.py showmigrations.'])
        pending = True

    try:
        from django.core.management import call_command
        call_command('makemigrations', '--check', '--dry-run', '--noinput',
                     stdout=StringIO(), stderr=StringIO())
        report('Models match migration files', [])
    except (Exception, SystemExit):
        report('Models match migration files', [
            'Run python manage.py makemigrations --check --dry-run and resolve missing migrations.',
        ])

    if not pending:
        try:
            from cars.models import CarList
            exists = CarList.objects.filter(pk=1).exists()
            report('Shared list lock row', [] if exists else [
                'CarList row 1 is missing. Restore it from a valid database export or data migration.',
            ])
        except Exception:
            report('Shared list lock row', ['Could not read CarList. Check the schema and migration history.'])
    else:
        print('[SKIP] Shared list lock row (apply migrations first).')

    try:
        from django.contrib.staticfiles import finders
        required = ['cars/app.css', 'cars/app.js', 'cars/vendor/bootstrap/bootstrap.min.css']
        missing = [name for name in required if not finders.find(name)]
        report('Static assets', ['Restore missing project static files.'] if missing else [])
    except Exception:
        report('Static assets', ['Could not locate static files. Check the Django staticfiles configuration.'])

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user')
            can_create_test_database = cursor.fetchone()[0]
        if can_create_test_database:
            report('Permission to create test databases', [])
        else:
            print('[WARN] This role cannot create test databases. App checks may pass, but the default test runner needs CREATEDB permission.')
    except Exception:
        print('[WARN] Could not inspect test-database creation permission.')
    finally:
        connection.close()

    if failures:
        print(f'\nEnvironment validation failed: {failures} check(s) need attention.')
        return 1
    print('\nEnvironment validation passed. This does not replace python manage.py test.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
