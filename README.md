# Ordered Cars

A Django and PostgreSQL assessment project for an ordered list of 100,000 cars.

## Prerequisites

- Python 3.14 (used locally); Django 5.2 also supports Python 3.10–3.13.
- Docker Engine and Docker Compose, or a PostgreSQL 17 server you manage.
- Git.

Exact Python dependency versions are recorded in `requirements.txt`.

## Local setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`, replacing the secret and password placeholders with separate random
values. Generate a value with `python -c "import secrets; print(secrets.token_hex(48))"`.
The file uses shell-compatible assignments; quote values containing shell special
characters. Keep it private (`chmod 600 .env`). Never commit it.

```sh
docker compose up -d --wait db
set -a
source .env
set +a
python manage.py migrate
python manage.py check --database default
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. Reload the environment in every new shell.
Django reads environment variables directly; it does not automatically parse `.env`.

The database is isolated in the `ordered-cars` Compose project with its own volume
and is published only on localhost port 5432.
Change `POSTGRES_PORT` if needed. Compose provisions the configured database and
role on the first start. This role is the container's bootstrap superuser, intended
only for local development and test database creation. A deployment needs separate
administration and restricted application roles.

Use `docker compose stop` to stop the database while retaining data. Changing
credentials in `.env` does not change an already initialized PostgreSQL volume.
Do not remove that volume unless you intend to erase its contents.

For an existing PostgreSQL server, create a dedicated login and database owned by
that login, set the `POSTGRES_*` variables accordingly, and omit the Compose step.
The test runner needs permission to create its test database (or a precreated test
database with suitable permissions).

## Settings

| Variable | Purpose / default |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required; no committed fallback |
| `DJANGO_DEBUG` | `false`; `.env.example` enables it for local development |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated; localhost, 127.0.0.1, [::1] |
| `POSTGRES_DB` | `ordered_cars` |
| `POSTGRES_USER` | `ordered_cars` |
| `POSTGRES_PASSWORD` | Local database password |
| `POSTGRES_HOST` | `127.0.0.1` |
| `POSTGRES_PORT` | `5432` |

## Checks and tests

With the environment loaded and PostgreSQL running:

```sh
python -m pip check
python manage.py check --database default
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
python manage.py test
```

## Structure

- `config/`: settings, root URLs, ASGI and WSGI entry points.
- `cars/`: Django application for cars.
- `compose.yaml`: dedicated local PostgreSQL service and persistent volume.
