# Ordered Cars

A Django and PostgreSQL assessment project for an ordered list of 100,000 cars.

## Development environment

| Tool | Version / configuration |
| --- | --- |
| Operating system | Ubuntu 26.04.1 LTS, x86_64 |
| Editor | Visual Studio Code 1.138.0 |
| Python | 3.14.4, with a project-local virtual environment |
| Django | 5.2.19 |
| PostgreSQL | 17.6, running in Docker |
| PostgreSQL driver | Psycopg 3.3.8 |
| Docker Engine | 29.8.1 |
| Docker Compose | 5.5.1 |
| Git | 2.53.0 |

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

## Database schema

`Car` stores a stable `BigAutoField` ID, a required name of at most 200 characters,
a color, and an independent `DecimalField(38, 18)` sort key. Names may repeat,
but must contain a non-whitespace character. Accepted colors are red, blue, green,
black, white, silver, and yellow. Queries use ascending sort-key order by default.

The primary key indexes IDs. The named `cars_sort_key_unique` constraint enforces
unique sort keys and provides an index for global ordering. It is deferrable but
initially immediate: a transaction must explicitly defer it when temporarily
overlapping keys are necessary. PostgreSQL checks deferred uniqueness at commit.
The `cars_color_sort_idx` B-tree index on `(color, sort_key)` supports ordered
color filtering. Database checks also enforce accepted colors and nonblank names.

`CarList` contains the fixed row with ID 1, created by a data migration. A check
constraint prevents other IDs. It provides a shared row for locking list mutations;
application code must preserve this row.

Django model `save()` does not call `full_clean()`. Callers must use model/form
validation for useful input errors; database constraints protect stored data.

## Ordering service

Use `CarOrderingService` in `cars/services.py` for membership and ordering changes:

```python
from cars.services import CarOrderingService

car = CarOrderingService.append(name='Roadster', color='blue')
CarOrderingService.move(car.id, placement='beginning')
CarOrderingService.move(car.id, placement='end')
# Move before an existing car, using its ID:
CarOrderingService.move(car.id, placement='before', target_id=destination_id)
CarOrderingService.delete(car.id)
```

Each operation opens an atomic transaction and locks `CarList` row 1 before
reading order state. This serializes ordering and membership writes. Direct ORM
inserts, deletes, and sort-key updates bypass the protocol and must not be used
by application callers. Name/color edits must save only those fields so a stale
model instance cannot overwrite a concurrent move.

New cars use spacing of 1000. An interior move uses an exact decimal midpoint;
beginning and end moves subtract or add 1000 to the remaining list's boundary.
For example, moving C before B in D(1000), B(2000), E(3000), C(4000), A(5000)
assigns C the key 1500, producing D, C, B, E, A. Neighbor lookup always uses the
global list, including cars hidden by a color filter.

An ordinary move updates one car row and reads bounded results from indexed
queries. Self moves and already satisfied positions return `False` without any
car updates; successful moves return `True`. Append returns the saved car.
Invalid instructions or append fields raise Django `ValidationError`; missing
source or target IDs raise `Car.DoesNotExist`. IDs must be positive integers.

Finite decimal gaps and field bounds can require rebalancing. The service
preserves global order, redistributes spaced keys in batches of 500, then retries
the requested operation in the same transaction. It temporarily defers the named
sort-key uniqueness constraint and restores immediate checking after rebalancing.
The explicit `CarOrderingService.rebalance()` method uses the same lock and
transaction rules. A failed operation rolls back its rebalance as well.

Full-list rebalancing is exceptional and can update every car while holding the
list lock. Ordinary one-row moves still incur index maintenance and can wait for
other writers; one updated row does not imply constant total database work.

## Pages and endpoints

| URL | GET | POST |
| --- | --- | --- |
| `/` | Ordered car list | Not allowed |
| `/cars/add/` | Add form | Append a car |
| `/cars/<id>/` | Car details | Not allowed |
| `/cars/<id>/edit/` | Edit form | Update name and color |
| `/cars/<id>/delete/` | Delete confirmation | Delete through the ordering service |
| `/cars/<id>/move/` | Move form | Move through the ordering service |

The list accepts `color` and `page` query parameters, for example
`/?color=blue&page=2`. Each page contains at most 50 cars, in global sort-key order.
An unsupported color returns HTTP 400 with a filter error. Invalid or out-of-range
page numbers return HTTP 404. Empty lists use page 1. Numbered pagination uses
database counts and offsets, so deep-page cost can grow with the dataset.

Create/edit forms accept only `name` and `color`; supplied IDs and sort keys cannot
change those fields. The move endpoint takes its source ID from the URL and accepts
`placement` (`before`, `beginning`, `end`) and `target_id` in the POST body. A target
ID is required for `before` and must be empty for beginning/end. It may refer to a
car on another page or hidden by the current filter. Missing sources return 404;
missing destinations and other invalid form inputs show errors without applying
the requested change.

All mutations require POST with a valid CSRF token. GET requests only display pages
or confirmation forms. Successful changes return to page 1 of the list, preserving
a valid `color` query parameter. Form links and actions carry that parameter;
return URLs are constructed internally and arbitrary `next` URLs are ignored.

## Interface assets

The interface uses Django templates, locally bundled Bootstrap 5.3.8 CSS, and
custom CSS in `cars/static/cars/app.css`. The desktop interface uses a compact table with horizontal scrolling on narrow
screens. Color names accompany swatches; forms include linked validation errors,
and keyboard users have a skip link and visible focus indicators.

`cars/static/cars/app.js` shows the destination input for before moves and focuses
server validation summaries. All operations also work without JavaScript; when
moving to the beginning or end without JavaScript, leave the destination empty.
No external fonts, CDN requests, or frontend build step are required. Bootstrap's
MIT license is included in `cars/static/cars/vendor/bootstrap/LICENSE`.
