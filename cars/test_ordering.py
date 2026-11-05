from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from .models import Car, CarList
from .services import CarOrderingService


def create_fixture():
    return {
        name: Car.objects.create(name=name, color=color, sort_key=Decimal(key))
        for name, color, key in [
            ('D', 'blue', '1000'), ('B', 'red', '2000'), ('E', 'blue', '3000'),
            ('C', 'red', '4000'), ('A', 'blue', '5000'),
        ]
    }


class OrderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cars = create_fixture()

    def names(self):
        return list(Car.objects.values_list('name', flat=True))

    def move(self, name, placement='before', target=None):
        return CarOrderingService.move(
            self.cars[name].pk, placement=placement,
            target_id=self.cars[target].pk if target else None,
        )

    def snapshot(self):
        return list(Car.objects.values_list('pk', 'name', 'color', 'sort_key'))

    def install_update_audit(self):
        # Count actual updated rows, including updates that write the same values.
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMP TABLE car_update_audit (car_id bigint) ON COMMIT DROP')
            cursor.execute('''
                CREATE FUNCTION pg_temp.audit_car_update() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO pg_temp.car_update_audit VALUES (NEW.id);
                    RETURN NEW;
                END $$
            ''')
            cursor.execute('''
                CREATE TRIGGER audit_car_update AFTER UPDATE ON cars_car
                FOR EACH ROW EXECUTE FUNCTION pg_temp.audit_car_update()
            ''')

    def updated_ids(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT car_id FROM pg_temp.car_update_audit')
            return [row[0] for row in cursor.fetchall()]

    def test_c_before_b_uses_midpoint_and_updates_one_row(self):
        self.install_update_audit()
        self.assertTrue(self.move('C', target='B'))
        self.assertEqual(self.names(), ['D', 'C', 'B', 'E', 'A'])
        self.cars['C'].refresh_from_db()
        self.assertEqual(self.cars['C'].sort_key, Decimal('1500'))
        self.assertEqual(self.updated_ids(), [self.cars['C'].pk])

    def test_d_before_a(self):
        self.move('D', target='A')
        self.assertEqual(self.names(), ['B', 'E', 'C', 'D', 'A'])

    def test_ordinary_move_reads_only_bounded_car_results(self):
        with CaptureQueriesContext(connection) as queries:
            self.move('C', target='B')
        reads = [q['sql'] for q in queries if q['sql'].startswith('SELECT')
                 and '"cars_car"' in q['sql']]
        self.assertTrue(reads)
        for sql in reads:
            self.assertIn('LIMIT', sql)

    def test_beginning_uses_minimum_minus_spacing(self):
        self.move('C', placement='beginning')
        self.assertEqual(self.names(), ['C', 'D', 'B', 'E', 'A'])
        self.cars['C'].refresh_from_db()
        self.assertEqual(self.cars['C'].sort_key, Decimal('0'))

    def test_end_uses_maximum_plus_spacing(self):
        self.move('B', placement='end')
        self.assertEqual(self.names(), ['D', 'E', 'C', 'A', 'B'])
        self.cars['B'].refresh_from_db()
        self.assertEqual(self.cars['B'].sort_key, Decimal('6000'))

    def test_self_and_already_satisfied_moves_update_no_rows(self):
        self.install_update_audit()
        before = self.snapshot()
        for name, placement, target in [
            ('C', 'before', 'C'), ('D', 'before', 'B'),
            ('D', 'beginning', None), ('A', 'end', None),
        ]:
            with self.subTest(name=name, placement=placement):
                self.assertFalse(self.move(name, placement, target))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.updated_ids(), [])

    def test_singleton_moves_are_noops(self):
        Car.objects.exclude(pk=self.cars['A'].pk).delete()
        self.install_update_audit()
        for placement, target in [('beginning', None), ('end', None), ('before', 'A')]:
            self.assertFalse(self.move('A', placement, target))
        self.assertEqual(self.updated_ids(), [])

    def test_neighbors_include_other_colors(self):
        self.move('A', target='E')
        self.cars['A'].refresh_from_db()
        self.assertEqual(self.cars['A'].sort_key, Decimal('2500'))
        self.assertEqual(list(Car.objects.filter(color='blue').values_list('name', flat=True)),
                         ['D', 'A', 'E'])

    def test_missing_source_or_target_does_not_change_data(self):
        before = self.snapshot()
        for source, target in [(999999, self.cars['B'].pk), (self.cars['C'].pk, 999999)]:
            with self.subTest(source=source, target=target):
                with self.assertRaises(Car.DoesNotExist):
                    CarOrderingService.move(source, placement='before', target_id=target)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_instructions_do_not_change_data(self):
        before = self.snapshot()
        for kwargs in [
            {'placement': 'after', 'target_id': self.cars['B'].pk},
            {'placement': 'before'},
            {'placement': 'end', 'target_id': self.cars['B'].pk},
            {'placement': 'before', 'target_id': 'invalid'},
        ]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    CarOrderingService.move(self.cars['C'].pk, **kwargs)
        with self.assertRaises(ValidationError):
            CarOrderingService.move('invalid', placement='beginning')
        self.assertEqual(self.snapshot(), before)

    def test_append_and_delete_preserve_existing_keys(self):
        before = self.snapshot()
        car = CarOrderingService.append(name='New car', color='silver')
        self.assertEqual(car.sort_key, Decimal('6000'))
        self.assertEqual(self.names(), ['D', 'B', 'E', 'C', 'A', 'New car'])
        CarOrderingService.delete(car.pk)
        self.assertEqual(self.snapshot(), before)

    def test_append_to_empty_list(self):
        Car.objects.all().delete()
        car = CarOrderingService.append(name='Only car', color='green')
        self.assertEqual(car.sort_key, Decimal('1000'))
        self.assertEqual(self.names(), ['Only car'])

    def test_invalid_append_and_missing_delete_leave_list_unchanged(self):
        before = self.snapshot()
        for kwargs in [{'name': ' ', 'color': 'blue'}, {'name': 'Car', 'color': 'purple'}]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    CarOrderingService.append(**kwargs)
        with self.assertRaises(Car.DoesNotExist):
            CarOrderingService.delete(999999)
        self.assertEqual(self.snapshot(), before)

    def test_delete_only_removes_requested_car(self):
        keys = dict(Car.objects.exclude(pk=self.cars['E'].pk).values_list('pk', 'sort_key'))
        CarOrderingService.delete(self.cars['E'].pk)
        self.assertEqual(self.names(), ['D', 'B', 'C', 'A'])
        self.assertEqual(dict(Car.objects.values_list('pk', 'sort_key')), keys)

    def test_exhausted_gap_rebalances_and_retries_move(self):
        Car.objects.filter(pk=self.cars['B'].pk).update(sort_key=Decimal('1000.000000000000000001'))
        self.move('C', target='B')
        self.assertEqual(self.names(), ['D', 'C', 'B', 'E', 'A'])
        self.cars['C'].refresh_from_db()
        self.assertEqual(self.cars['C'].sort_key, Decimal('1500'))

    def test_repeated_insertion_into_same_gap(self):
        # >70 halvings exhaust an 18-place gap and require rebalancing.
        for index in range(80):
            car = CarOrderingService.append(name=f'New {index}', color='black')
            CarOrderingService.move(car.pk, placement='before', target_id=self.cars['B'].pk)
        self.assertEqual(self.names(), ['D'] + [f'New {i}' for i in range(80)] + ['B', 'E', 'C', 'A'])
        keys = list(Car.objects.values_list('sort_key', flat=True))
        self.assertEqual(len(keys), len(set(keys)))

    def test_beginning_overflow_rebalances(self):
        Car.objects.filter(pk=self.cars['D'].pk).update(
            sort_key=Decimal('-99999999999999999999.999999999999999999'))
        self.move('C', placement='beginning')
        self.assertEqual(self.names(), ['C', 'D', 'B', 'E', 'A'])
        self.cars['C'].refresh_from_db()
        self.assertEqual(self.cars['C'].sort_key, Decimal('0'))

    def test_end_overflow_rebalances(self):
        Car.objects.filter(pk=self.cars['A'].pk).update(
            sort_key=Decimal('99999999999999999999.999999999999999999'))
        self.move('B', placement='end')
        self.assertEqual(self.names(), ['D', 'E', 'C', 'A', 'B'])

    def test_append_overflow_rebalances(self):
        Car.objects.filter(pk=self.cars['A'].pk).update(
            sort_key=Decimal('99999999999999999999.999999999999999999'))
        car = CarOrderingService.append(name='New', color='white')
        self.assertEqual(car.sort_key, Decimal('6000'))
        self.assertEqual(self.names(), ['D', 'B', 'E', 'C', 'A', 'New'])

    def test_decimal_arithmetic_is_independent_of_ambient_precision(self):
        Car.objects.all().delete()
        Car.objects.create(name='Lower', color='blue',
                           sort_key=Decimal('99999999999999990000.000000000000000000'))
        upper = Car.objects.create(name='Upper', color='red',
                                   sort_key=Decimal('99999999999999990000.000000000000000004'))
        source = Car.objects.create(name='Source', color='blue', sort_key=Decimal('0'))
        with localcontext() as ctx:
            ctx.prec = 10
            CarOrderingService.move(source.pk, placement='before', target_id=upper.pk)
        source.refresh_from_db()
        self.assertEqual(source.sort_key, Decimal('99999999999999990000.000000000000000002'))

    def test_batched_rebalance_preserves_order_despite_temporary_collisions(self):
        Car.objects.all().delete()
        for index in range(7):
            Car.objects.create(name=str(index), color='blue', sort_key=Decimal(index * 1000))
        before = self.names()
        with patch.object(CarOrderingService, 'BATCH_SIZE', 2):
            CarOrderingService.rebalance()
        self.assertEqual(self.names(), before)
        self.assertEqual(list(Car.objects.values_list('sort_key', flat=True)),
                         [Decimal(index * 1000) for index in range(1, 8)])
        # Rebalancing must restore immediate uniqueness checks.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Car.objects.create(name='Duplicate', color='red', sort_key=Decimal('1000'))

    def test_empty_rebalance(self):
        Car.objects.all().delete()
        CarOrderingService.rebalance()
        self.assertEqual(self.names(), [])

    def test_failed_rebalance_rolls_back_all_batches(self):
        before = self.snapshot()
        original = Car.objects.bulk_update
        calls = 0

        def fail_second_batch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Simulated batch failure')
            return original(*args, **kwargs)

        with patch.object(CarOrderingService, 'BATCH_SIZE', 2):
            with patch.object(Car.objects, 'bulk_update', side_effect=fail_second_batch):
                with self.assertRaises(RuntimeError):
                    CarOrderingService.rebalance()
        self.assertEqual(calls, 2)
        self.assertEqual(self.snapshot(), before)

    def test_failure_after_rebalance_rolls_back_the_requested_move(self):
        Car.objects.filter(pk=self.cars['B'].pk).update(
            sort_key=Decimal('1000.000000000000000001'))
        before = self.snapshot()
        with patch.object(CarOrderingService, '_candidate', side_effect=[None, None]):
            with self.assertRaises(ValidationError):
                self.move('C', target='B')
        self.assertEqual(self.snapshot(), before)

    def test_database_error_during_rebalance_rolls_back_all_batches(self):
        before = self.snapshot()
        original = Car.objects.bulk_update
        calls = 0

        def fail_second_batch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1 / 0')
            return original(*args, **kwargs)

        with patch.object(CarOrderingService, 'BATCH_SIZE', 2):
            with patch.object(Car.objects, 'bulk_update', side_effect=fail_second_batch):
                with self.assertRaises(DataError):
                    CarOrderingService.rebalance()
        self.assertEqual(calls, 2)
        self.assertEqual(self.snapshot(), before)

    def test_outer_transaction_rollback_restores_membership_and_order(self):
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.move('C', target='B')
                CarOrderingService.append(name='New', color='white')
                CarOrderingService.delete(self.cars['E'].pk)
                raise RuntimeError('Abort')
        self.assertEqual(self.snapshot(), before)


class OrderingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        # TransactionTestCase flushes data migrations between tests.
        CarList.objects.get_or_create(pk=1)
        self.cars = create_fixture()

    def worker(self, operation, barrier=None, pid_queue=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            if pid_queue is not None:
                pid_queue.put(pid)
            if barrier is not None:
                barrier.wait(timeout=5)
            return operation()
        finally:
            connections['default'].close()

    def assert_waits_for_list_lock(self, operation):
        pids = Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                CarList.objects.select_for_update().get(pk=1)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    holder_pid = cursor.fetchone()[0]
                future = executor.submit(self.worker, operation, None, pids)
                worker_pid = pids.get(timeout=5)
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [worker_pid])
                        blocked = holder_pid in cursor.fetchone()[0]
                    if blocked or future.done():
                        break
                    sleep(0.01)
                self.assertTrue(blocked, 'Operation did not wait for the singleton row lock')
            future.result(timeout=10)

    def test_move_waits_for_list_lock(self):
        self.assert_waits_for_list_lock(lambda: CarOrderingService.move(
            self.cars['C'].pk, placement='before', target_id=self.cars['B'].pk))

    def test_append_waits_for_list_lock(self):
        self.assert_waits_for_list_lock(lambda: CarOrderingService.append(name='New', color='red'))

    def test_delete_waits_for_list_lock(self):
        self.assert_waits_for_list_lock(lambda: CarOrderingService.delete(self.cars['E'].pk))

    def test_rebalance_waits_for_list_lock(self):
        self.assert_waits_for_list_lock(CarOrderingService.rebalance)

    def test_seed_waits_for_list_lock(self):
        Car.objects.all().delete()
        self.assert_waits_for_list_lock(lambda: CarOrderingService.seed(count=5))
        self.assertEqual(Car.objects.count(), 5)

    def run_concurrently(self, *operations):
        barrier = Barrier(len(operations))
        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            futures = [executor.submit(self.worker, operation, barrier) for operation in operations]
            return [future.result(timeout=15) for future in futures]

    def test_two_moves_to_same_gap_preserve_all_cars(self):
        self.run_concurrently(
            lambda: CarOrderingService.move(self.cars['C'].pk, placement='before', target_id=self.cars['B'].pk),
            lambda: CarOrderingService.move(self.cars['A'].pk, placement='before', target_id=self.cars['B'].pk),
        )
        names = list(Car.objects.values_list('name', flat=True))
        self.assertIn(names, [['D', 'C', 'A', 'B', 'E'], ['D', 'A', 'C', 'B', 'E']])
        self.assertEqual(Car.objects.count(), 5)

    def test_move_and_append_preserve_membership(self):
        self.run_concurrently(
            lambda: CarOrderingService.move(self.cars['C'].pk, placement='before', target_id=self.cars['B'].pk),
            lambda: CarOrderingService.append(name='New', color='red'),
        )
        self.assertEqual(list(Car.objects.values_list('name', flat=True)), ['D', 'C', 'B', 'E', 'A', 'New'])

    def test_concurrent_appends_receive_distinct_end_keys(self):
        self.run_concurrently(
            lambda: CarOrderingService.append(name='One', color='red'),
            lambda: CarOrderingService.append(name='Two', color='blue'),
        )
        self.assertEqual(Car.objects.count(), 7)
        self.assertEqual(set(Car.objects.filter(name__in=['One', 'Two']).values_list(
            'sort_key', flat=True)), {Decimal('6000'), Decimal('7000')})

    def test_move_and_delete_of_destination_have_serial_outcome(self):
        def move():
            try:
                CarOrderingService.move(self.cars['C'].pk, placement='before', target_id=self.cars['B'].pk)
                return 'moved'
            except Car.DoesNotExist:
                return 'target-deleted'

        moved, _ = self.run_concurrently(move, lambda: CarOrderingService.delete(self.cars['B'].pk))
        expected = ['D', 'C', 'E', 'A'] if moved == 'moved' else ['D', 'E', 'C', 'A']
        self.assertEqual(list(Car.objects.values_list('name', flat=True)), expected)
        self.assertEqual(Car.objects.count(), 4)
