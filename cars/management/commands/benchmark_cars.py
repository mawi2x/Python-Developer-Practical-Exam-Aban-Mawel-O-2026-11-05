import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter

import django
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count
from django.test import Client

from cars.models import Car
from cars.services import CarOrderingService


class Command(BaseCommand):
    help = 'Measure a fresh dataset in an empty database whose name contains benchmark.'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=100000)
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--runs', type=int, default=5)
        parser.add_argument('--rebalance-runs', type=int, default=3)
        parser.add_argument('--output-dir', default='docs/benchmark')

    def handle(self, *args, **options):
        if options['count'] < 100 or options['runs'] < 1 or options['rebalance_runs'] < 1:
            raise CommandError('Use count >= 100 and positive run counts.')
        if 'benchmark' not in connection.settings_dict['NAME']:
            raise CommandError('Use a dedicated database whose name contains benchmark.')
        if Car.objects.exists():
            raise CommandError('The benchmark requires an empty car list; no existing cars were changed.')
        output = Path(options['output_dir'])
        if output.exists() and any(output.iterdir()):
            raise CommandError('Output directory must be empty; previous reports will not be overwritten.')
        output.mkdir(parents=True, exist_ok=True)
        report = {
            'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
            'environment': self.environment(),
            'dataset': {'count': options['count'], 'seed': options['seed'], 'seed_batch_size': 1000},
            'measurements': {},
        }
        started = perf_counter()
        CarOrderingService.seed(count=options['count'], seed=options['seed'])
        report['seed_ms'] = (perf_counter() - started) * 1000
        self.stdout.write(f'Seeded {options["count"]} cars in {report["seed_ms"]:.2f} ms.')
        with connection.cursor() as cursor:
            cursor.execute('ANALYZE cars_car')
        report['dataset']['colors'] = dict(
            Car.objects.order_by().values('color').annotate(count=Count('pk')).values_list('color', 'count')
        )
        blue_count = report['dataset']['colors'].get('blue', 0)
        if not blue_count:
            raise CommandError('This seed generated no blue cars; use another seed or a larger count.')
        last_page = (options['count'] + 49) // 50
        blue_last_page = (blue_count + 49) // 50
        pages = [
            ('all_first_page', None, 1), ('all_deep_page', None, last_page),
            ('blue_first_page', 'blue', 1), ('blue_deep_page', 'blue', blue_last_page),
        ]
        report['dataset'].update(last_page=last_page, blue_last_page=blue_last_page)
        client = Client(HTTP_HOST='localhost')
        measurements = report['measurements']
        for label, color, number in pages:
            def fetch_page(color=color, number=number):
                queryset = Car.objects.filter(color=color) if color else Car.objects.all()
                page = Paginator(queryset, 50).page(number)
                rows = list(page.object_list)
                if not 1 <= len(rows) <= 50:
                    raise CommandError('Invalid page size during benchmark.')

            def render_page(color=color, number=number):
                params = {'page': number}
                if color:
                    params['color'] = color
                response = client.get('/', params)
                if response.status_code != 200:
                    raise CommandError(f'List endpoint returned {response.status_code}.')

            fetch_page()  # One untimed warm-up, with fresh querysets for every run.
            measurements[label + '_db'] = self.measure(fetch_page, options['runs'])
            render_page()
            measurements[label + '_django'] = self.measure(render_page, options['runs'])
            self.stdout.write(f'{label}: DB median {measurements[label + "_db"]["median_ms"]:.2f} ms.')

        first = Car.objects.first()
        target = Car.objects.all()[1]
        source = Car.objects.last()
        self.write_plans(output / 'query_plans.txt', first, target, source, blue_count, options['count'])
        report['row_update_evidence'] = self.audit_move(source.pk, target.pk)
        turn = 0

        def ordinary_move():
            nonlocal turn
            if turn % 2:
                changed = CarOrderingService.move(source.pk, placement='end')
            else:
                changed = CarOrderingService.move(source.pk, placement='before', target_id=target.pk)
            turn += 1
            if not changed:
                raise CommandError('An ordinary benchmark move unexpectedly became a no-op.')

        measurements['ordinary_move'] = self.measure(ordinary_move, options['runs'])
        CarOrderingService.move(source.pk, placement='end')
        original_order = self.order_fingerprint()
        measurements['full_rebalance'] = self.measure(CarOrderingService.rebalance, options['rebalance_runs'])
        if self.order_fingerprint() != original_order:
            raise CommandError('Rebalancing changed order or membership.')
        self.stdout.write(f'Full rebalance median: {measurements["full_rebalance"]["median_ms"]:.2f} ms.')

        def prepare_gap():
            CarOrderingService.move(source.pk, placement='end')
            with transaction.atomic():
                CarOrderingService._lock_list()
                first.refresh_from_db(fields=['sort_key'])
                # Deliberately exhaust a gap in this isolated benchmark fixture.
                from decimal import localcontext
                with localcontext() as ctx:
                    ctx.prec = CarOrderingService.DECIMAL_PRECISION
                    key = first.sort_key + CarOrderingService.QUANTUM
                Car.objects.filter(pk=target.pk).update(sort_key=key)

        measurements['move_with_fallback_rebalance'] = self.measure(
            lambda: CarOrderingService.move(source.pk, placement='before', target_id=target.pk),
            options['rebalance_runs'], prepare=prepare_gap,
        )
        if list(Car.objects.values_list('pk', flat=True)[:3]) != [first.pk, source.pk, target.pk]:
            raise CommandError('Fallback move produced an unexpected order.')
        CarOrderingService.move(source.pk, placement='end')
        if self.order_fingerprint() != original_order:
            raise CommandError('Fallback movement lost or reordered other cars.')
        self.stdout.write('Fallback measurements complete. Checking CRUD at this dataset size.')
        self.check_crud(client, options['count'])
        report['verification'] = {
            'final_count': Car.objects.count(), 'unique_keys': self.order_fingerprint()[2],
            'rebalance_preserves_order': True, 'fallback_move_correct': True,
            'crud_endpoints': True,
        }
        (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        self.write_summary(output / 'README.md', report)
        self.stdout.write(self.style.SUCCESS(f'Wrote timings, query plans, and report to {output}.'))

    @staticmethod
    def measure(operation, runs, prepare=None):
        samples = []
        for _ in range(runs):
            if prepare:
                prepare()
            started = perf_counter()
            operation()
            samples.append((perf_counter() - started) * 1000)
        return {'runs': runs, 'min_ms': min(samples), 'median_ms': median(samples),
                'max_ms': max(samples), 'samples_ms': samples}

    @staticmethod
    def environment():
        cpu = 'unknown'
        cpuinfo = Path('/proc/cpuinfo')
        if cpuinfo.exists():
            cpu = next((line.split(':', 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                        if line.startswith('model name')), cpu)
        memory = 'unknown'
        meminfo = Path('/proc/meminfo')
        if meminfo.exists():
            memory = next((line.split(':', 1)[1].strip() for line in meminfo.read_text().splitlines()
                           if line.startswith('MemTotal:')), memory)
        with connection.cursor() as cursor:
            cursor.execute('SELECT version()')
            postgres = cursor.fetchone()[0]
            config = {}
            for name in ['shared_buffers', 'effective_cache_size', 'work_mem']:
                cursor.execute(f'SHOW {name}')
                config[name] = cursor.fetchone()[0]
        return {'platform': platform.platform(), 'cpu': cpu, 'logical_cpus': os.cpu_count(),
                'host_memory': memory, 'python': platform.python_version(), 'django': django.get_version(),
                'psycopg': psycopg.__version__, 'postgres': postgres, 'postgres_settings': config,
                'django_debug': settings.DEBUG, 'database': connection.settings_dict['NAME'],
                'transport': 'PostgreSQL over configured local TCP connection',
                'rebalance_batch_size': CarOrderingService.BATCH_SIZE}

    @staticmethod
    def order_fingerprint():
        with connection.cursor() as cursor:
            cursor.execute("SELECT md5(string_agg(id::text, ',' ORDER BY sort_key)), count(*), "
                           'count(DISTINCT sort_key) FROM cars_car')
            return cursor.fetchone()

    @staticmethod
    def write_plans(path, first, target, source, blue_count, count):
        blue_offset = ((blue_count - 1) // 50) * 50
        all_offset = ((count - 1) // 50) * 50
        queries = {
            'Unfiltered first page': Car.objects.all()[:50],
            'Unfiltered deepest page': Car.objects.all()[all_offset:all_offset + 50],
            'Blue first page': Car.objects.filter(color='blue')[:50],
            'Blue deepest page': Car.objects.filter(color='blue')[blue_offset:blue_offset + 50],
            'Source by primary key': Car.objects.filter(pk=source.pk)[:1],
            'Destination predecessor excluding source': Car.objects.exclude(pk=source.pk).filter(
                sort_key__lt=target.sort_key).order_by('-sort_key')[:1],
        }
        sections = ['EXPLAIN (ANALYZE, BUFFERS), after seeding and ANALYZE; before write benchmarks.']
        for label, queryset in queries.items():
            sql, params = queryset.query.sql_with_params()
            sections.append(f'\n{label}\nSQL: {sql}\nParameters: {params!r}\n'
                            + queryset.explain(analyze=True, buffers=True))
        for label, sql, params in [
            ('Unfiltered count', 'SELECT COUNT(*) FROM cars_car', []),
            ('Blue count', 'SELECT COUNT(*) FROM cars_car WHERE color = %s', ['blue']),
        ]:
            with connection.cursor() as cursor:
                cursor.execute('EXPLAIN (ANALYZE, BUFFERS) ' + sql, params)
                plan = '\n'.join(row[0] for row in cursor.fetchall())
            sections.append(f'\n{label}\nSQL: {sql}\nParameters: {params!r}\n{plan}')
        path.write_text('\n'.join(sections) + '\n')

    @staticmethod
    def audit_move(source_id, target_id):
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMP TABLE benchmark_updates (car_id bigint)')
            cursor.execute('''CREATE FUNCTION pg_temp.benchmark_track_update() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    INSERT INTO pg_temp.benchmark_updates VALUES (NEW.id); RETURN NEW;
                END $$''')
            cursor.execute('''CREATE TRIGGER benchmark_track_update AFTER UPDATE ON cars_car
                FOR EACH ROW EXECUTE FUNCTION pg_temp.benchmark_track_update()''')
        try:
            CarOrderingService.move(source_id, placement='before', target_id=target_id)
            with connection.cursor() as cursor:
                cursor.execute('SELECT car_id FROM pg_temp.benchmark_updates')
                updated = [row[0] for row in cursor.fetchall()]
            if updated != [source_id]:
                raise CommandError('Ordinary move updated more than its source row.')
            if CarOrderingService.move(source_id, placement='before', target_id=target_id):
                raise CommandError('Expected a no-op move.')
            with connection.cursor() as cursor:
                cursor.execute('SELECT count(*) FROM pg_temp.benchmark_updates')
                if cursor.fetchone()[0] != 1:
                    raise CommandError('No-op move updated rows.')
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TRIGGER benchmark_track_update ON cars_car')
                cursor.execute('DROP FUNCTION pg_temp.benchmark_track_update()')
                cursor.execute('DROP TABLE pg_temp.benchmark_updates')
        CarOrderingService.move(source_id, placement='end')
        return {'ordinary_move_updated_rows': 1, 'noop_updated_rows': 0,
                'method': 'PostgreSQL AFTER UPDATE row trigger, removed before timings'}

    @staticmethod
    def check_crud(client, count):
        client = Client(enforce_csrf_checks=True, HTTP_HOST='localhost')
        client.get('/cars/add/')
        token = client.cookies['csrftoken'].value

        def post(path, data):
            response = client.post(path, data, HTTP_X_CSRFTOKEN=token)
            if response.status_code != 302:
                raise CommandError(f'CRUD verification failed at {path}: {response.status_code}.')

        post('/cars/add/', {'name': 'Benchmark CRUD verification', 'color': 'blue'})
        car = Car.objects.get(name='Benchmark CRUD verification')
        original_key = car.sort_key
        post(f'/cars/{car.pk}/edit/', {'name': 'Benchmark edited car', 'color': 'red'})
        car.refresh_from_db()
        if car.sort_key != original_key or car.color != 'red':
            raise CommandError('Editing changed the car position or failed to save its color.')
        if client.get(f'/cars/{car.pk}/').status_code != 200:
            raise CommandError('Car detail verification failed.')
        post(f'/cars/{car.pk}/move/', {'placement': 'beginning'})
        if Car.objects.first().pk != car.pk:
            raise CommandError('Move endpoint failed.')
        post(f'/cars/{car.pk}/delete/', {})
        if Car.objects.count() != count:
            raise CommandError('CRUD verification did not restore the dataset count.')

    @staticmethod
    def write_summary(path, report):
        env = report['environment']
        data = report['dataset']
        lines = [
            '# Performance measurements', '',
            f'Recorded at {report["recorded_at_utc"]}.', '',
            f'Dataset: {data["count"]:,} cars, seed {data["seed"]}, seed batches of 1000.',
            f'Color distribution: `{json.dumps(data["colors"], sort_keys=True)}`.', '',
            f'Environment: {env["platform"]}; {env["cpu"]}; {env["logical_cpus"]} logical CPUs; '
            f'host memory {env["host_memory"]}.',
            f'Python {env["python"]}, Django {env["django"]}, Psycopg {env["psycopg"]}.',
            f'PostgreSQL: {env["postgres"]}.',
            f'PostgreSQL settings: `{json.dumps(env["postgres_settings"], sort_keys=True)}`.',
            f'Django DEBUG: {env["django_debug"]}. Database uses local Docker PostgreSQL and TCP.', '',
            f'Single seed run: {report["seed_ms"]:.2f} ms.', '',
            '| Operation | Runs | Min (ms) | Median (ms) | Max (ms) |',
            '| --- | ---: | ---: | ---: | ---: |',
        ]
        for label, values in report['measurements'].items():
            lines.append(f'| {label} | {values["runs"]} | {values["min_ms"]:.2f} | '
                         f'{values["median_ms"]:.2f} | {values["max_ms"]:.2f} |')
        lines += [
            '', '## Method and interpretation', '',
            '- `_db` includes a fresh paginator count plus fetching at most 50 model instances.',
            '- `_django` uses the in-process Django test client, including view and template rendering. '
            'It excludes browser, network, static assets, and application-server overhead.',
            f'- Deep pages are page {data["last_page"]} unfiltered and page {data["blue_last_page"]} for blue.',
            '- Read operations have one untimed warm-up each. Connections and caches are warm; '
            'these are local sequential measurements, not a cold-cache or production load test.',
            '- Ordinary moves alternate between before a target and the end. The row audit independently '
            'verified one updated car for an ordinary move and zero for an already satisfied move. '
            'The audit trigger was removed before timing.',
            '- Full rebalancing uses batches of 500 and updates the whole list. Fallback move timings '
            'include an actual rebalance after deliberately exhausting a decimal gap. Gap preparation '
            'and restoration are excluded from those timings.',
            '- Timings include transaction commits. No competing writers were active; lock contention '
            'is not measured here and can add waiting time to any mutation.',
            '- Order fingerprints, row counts, unique keys, and CSRF-protected CRUD endpoints were checked '
            'at this dataset size. One updated row still incurs index maintenance.',
            '- Offset pagination scans skipped rows; counts and deep offsets are not constant-time. '
            'Full rebalancing holds the list lock and delays other ordering/membership writers.',
            '- See [raw samples](results.json) and [actual query plans](query_plans.txt). '
            'Plans show what PostgreSQL chose for this dataset; index use is not assumed.',
            '',
        ]
        path.write_text('\n'.join(lines))
