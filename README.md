# Cars

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

The commands in this section use Bash/Zsh. For Windows, use the PowerShell
section below.

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

## Windows (PowerShell)

Install Python 3.14 and Docker Desktop, start Docker Desktop using Linux
containers, and open PowerShell in the project directory. These commands use the
virtual environment's Python directly, so activating `Activate.ps1` is optional.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(48))"
```

Edit `.env` and replace both secret/password placeholders with separate generated
values. Do not copy over an existing configured `.env`. Load it into the current
PowerShell process with the following block; repeat it in each new terminal:

```powershell
Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq $last) -and ($first -eq '"' -or $first -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}
```

This reads literal `KEY=value` assignments, ignoring comments/blank lines and
removing matching surrounding quotes. It does not evaluate shell expressions or
expand variables. The generated hexadecimal secrets work with both shell formats.

```powershell
docker compose up -d --wait db
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check --database default
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. Stop the server with Ctrl+C. To seed an empty list
and run checks/tests in PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py seed_cars --count 100000
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe manage.py check --database default
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

Skip seeding if you restored the supplied database export. The test runner creates
and removes its own database; its PostgreSQL role needs database-creation rights.

## Sample data

With migrations applied, the environment loaded, and an empty car list:

```sh
python manage.py seed_cars --count 100000
```

The default random seed is 42 and the default insertion batch size is 1000.
Names are `Car 000001`, `Car 000002`, and so on; colors are generated from the
accepted choices and sort keys are spaced by 1000. To choose another reproducible
dataset, use `--seed 17`. `--batch-size` changes insertion batch size without
changing the generated dataset.

Seeding uses one transaction and the same list lock as moves, appends, deletes,
and rebalancing. A failed batch rolls back all inserted cars. Rerunning against
any nonempty list returns an error and preserves every existing car. There is no
implicit reset or deletion option; use a separate empty database to regenerate
data without affecting a collection. Database-generated IDs are not part of the
reproducible name/color/order dataset.

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

Run the single read-only environment validator after loading `.env`, starting
PostgreSQL, and applying migrations:

```sh
python validate_environment.py
```

PowerShell equivalent:

```powershell
.\.venv\Scripts\python.exe validate_environment.py
```

It checks the supported Python version, exact dependency pins and compatibility,
exported environment settings, Django startup, PostgreSQL connectivity/version,
system checks, applied migrations, model/migration agreement, the shared lock row,
and static assets. Output uses `[PASS]`, `[FAIL]`, `[WARN]`, and `[SKIP]` labels.
It exits with code 0 when required checks pass and code 1 when they fail. Missing
test-database creation permission is reported as a warning because the application
can run without it. Credentials are not printed.

The script reads exported variables, just like Django; it does not automatically
load `.env`, install packages, start Docker, create databases, apply migrations,
or change car data. A failed check tells you what to fix. It checks environment
readiness and does not replace the application test suite below.

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
- `validate_environment.py`: cross-platform, read-only environment checks.

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

The list accepts `q` (search), `color`, and `page` query parameters, for example
`/?color=blue&page=2`. Each page contains at most 50 cars, in global sort-key order.
An unsupported color returns HTTP 400 with a filter error. Invalid or out-of-range
page numbers return HTTP 404. Empty lists use page 1. Numbered pagination uses
database counts and offsets, so deep-page cost can grow with the dataset.

Search matches car names by case-insensitive substring. Numeric searches (with
an optional `#` prefix) match an exact car ID. Search combines with color filtering
and is preserved during pagination and after changes. Name substring searches may
scan the table; they do not use a dedicated text-search index.

Create/edit forms accept only `name` and `color`; supplied IDs and sort keys cannot
change those fields. The move endpoint takes its source ID from the URL and accepts
`placement` (`before`, `beginning`, `end`) and `target_id` in the POST body. A target
ID is required for `before` and must be empty for beginning/end. It may refer to a
car on another page or hidden by the current filter. Missing sources return 404;
missing destinations and other invalid form inputs show errors without applying
the requested change.

All mutations require POST with a valid CSRF token. GET requests only display pages
or confirmation forms. Successful changes return to page 1 of the list, preserving
valid `color` and `q` query parameters. Form links and actions carry that state;
return URLs are constructed internally and arbitrary `next` URLs are ignored.

## Interface assets

The interface uses Django templates, locally bundled Bootstrap 5.3.8 CSS, and
custom CSS in `cars/static/cars/app.css`. The desktop interface uses a compact
table with horizontal scrolling on narrow screens. Color names accompany swatches; forms include linked validation errors,
and keyboard users have a skip link and visible focus indicators.

`cars/static/cars/app.js` opens Edit and Move forms in native modal dialogs,
shows the destination input for before moves, and focuses validation summaries.
Color filter pills and search work with standard links and GET requests. All operations also work without JavaScript; when
moving to the beginning or end without JavaScript, leave the destination empty.
No external fonts, CDN requests, or frontend build step are required. Bootstrap's
MIT license is included in `cars/static/cars/vendor/bootstrap/LICENSE`.

## Benchmark command

To measure performance using a separate database on the local Compose service,
load `.env` into the shell first, then run:

```sh
docker compose exec db createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" ordered_cars_benchmark
POSTGRES_DB=ordered_cars_benchmark python manage.py migrate
POSTGRES_DB=ordered_cars_benchmark DJANGO_DEBUG=false python manage.py benchmark_cars \
  --count 100000 --seed 42 --runs 5 --rebalance-runs 3
```

PowerShell equivalent, after loading `.env` as shown above:

```powershell
docker compose exec db createdb -U $env:POSTGRES_USER -O $env:POSTGRES_USER ordered_cars_benchmark
$previousDatabase = $env:POSTGRES_DB
$previousDebug = $env:DJANGO_DEBUG
try {
    $env:POSTGRES_DB = 'ordered_cars_benchmark'
    $env:DJANGO_DEBUG = 'false'
    .\.venv\Scripts\python.exe manage.py migrate
    if ($LASTEXITCODE -ne 0) { throw 'Benchmark database migration failed.' }
    .\.venv\Scripts\python.exe manage.py benchmark_cars --count 100000 --seed 42 --runs 5 --rebalance-runs 3
} finally {
    $env:POSTGRES_DB = $previousDatabase
    $env:DJANGO_DEBUG = $previousDebug
}
```

The command requires an empty car list in a database whose name contains
`benchmark`. It seeds that database, measures paging, filtering, ordinary moves,
and rebalancing, and prints timings and `EXPLAIN (ANALYZE, BUFFERS)` plans to the
terminal. It also checks CRUD endpoints with CSRF enforcement. It does not create
report files.

The benchmark dataset remains in that dedicated database. For another run, create
a new empty benchmark database. Existing cars are never silently erased.

## Decisions and limitations

| Choice | Benefit | Tradeoff |
| --- | --- | --- |
| Spaced decimal keys | Ordinary moves update one car | Repeated moves can exhaust a gap |
| Shared list lock | Consistent moves, insertion, deletion, and seeding | Ordering and membership writes are serialized |
| Full-list rebalance | Preserves order with a simple transactional algorithm | Updates every car and holds the list lock until commit |
| Offset pagination | Numbered pages with 50 rows per page | Counts and deep offsets scan more data |
| Name substring search | Simple partial-name matching | No dedicated text-search index |
| Django templates and minimal JavaScript | Forms work without a frontend build step | Full list refresh after successful changes |

There is one shared collection. The application has no authentication and is
intended for local assessment use. Desktop layout is the primary interface;
narrow screens can scroll the table horizontally. Moves always use global order,
regardless of the current search or color filter.

## Database export and restore

The submission includes `cars-source.zip` and a PostgreSQL custom-format export,
`cars-database.dump`. Extract the source ZIP, enter `cars-project`, and follow
Local setup. The database export already contains the schema, migrations, and
car data, so do not seed a restored database.

To create an export of the local Compose database:

```sh
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > cars-database.dump
```

To restore into a **new empty database**, with `.env` exported in the shell:

```sh
docker compose exec db createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" cars_restored
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d cars_restored \
  --no-owner --no-privileges --exit-on-error < cars-database.dump
POSTGRES_DB=cars_restored python manage.py migrate --check
POSTGRES_DB=cars_restored python manage.py check --database default
POSTGRES_DB=cars_restored python manage.py runserver 127.0.0.1:8000
```

Place the dump in the working directory or adjust the input path. Use the bundled
PostgreSQL 17 container tools to export and restore. Owner and grant restoration
is disabled so the reviewer can use their own local role. Use a different new
database name if `cars_restored` already exists; these commands do not erase an
existing database. Keep exports outside version control.

PowerShell export equivalent, after loading `.env`:

```powershell
docker compose exec -T db pg_dump -U $env:POSTGRES_USER -d $env:POSTGRES_DB --format=custom --no-owner --no-acl --file=/tmp/cars-export.dump
if ($LASTEXITCODE -ne 0) { throw 'Database export failed.' }
docker compose cp db:/tmp/cars-export.dump .\cars-database.dump
```

PowerShell restore equivalent, targeting a **new empty database**:

```powershell
docker compose exec db createdb -U $env:POSTGRES_USER -O $env:POSTGRES_USER cars_restored
if ($LASTEXITCODE -ne 0) { throw 'Use a new database name; creation failed.' }
docker compose cp .\cars-database.dump db:/tmp/cars-import.dump
if ($LASTEXITCODE -ne 0) { throw 'Copying the database export failed.' }
docker compose exec -T db pg_restore -U $env:POSTGRES_USER -d cars_restored --no-owner --no-privileges --exit-on-error /tmp/cars-import.dump
if ($LASTEXITCODE -ne 0) { throw 'Database restore failed.' }
$env:POSTGRES_DB = 'cars_restored'
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py check --database default
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Reload `.env` to switch back to the original database. These export/restore
commands use `docker compose cp` to transfer the binary dump rather than shell
redirection, so they also work with Windows PowerShell 5.1. Native binary output
redirection changed in PowerShell 7.4; see [Microsoft's redirection documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_redirection).
