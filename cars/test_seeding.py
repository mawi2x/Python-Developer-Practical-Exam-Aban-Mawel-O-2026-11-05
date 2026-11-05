from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Car
from .services import CarOrderingService


class SeedCarsTests(TestCase):
    def rows(self):
        return list(Car.objects.values_list('name', 'color', 'sort_key'))

    def test_seed_creates_requested_count_with_spaced_keys(self):
        self.assertEqual(CarOrderingService.seed(count=7, batch_size=3), 7)
        self.assertEqual(Car.objects.count(), 7)
        self.assertEqual(list(Car.objects.values_list('sort_key', flat=True)),
                         [Decimal(i * 1000) for i in range(1, 8)])
        self.assertEqual(Car.objects.first().name, 'Car 000001')
        for car in Car.objects.all():
            car.full_clean()

    def test_seed_is_deterministic_across_batch_sizes(self):
        CarOrderingService.seed(count=20, seed=42, batch_size=3)
        expected = self.rows()
        Car.objects.all().delete()
        CarOrderingService.seed(count=20, seed=42, batch_size=7)
        self.assertEqual(self.rows(), expected)

    def test_seed_refuses_to_overwrite_existing_data(self):
        CarOrderingService.append(name='Keep me', color='blue')
        before = self.rows()
        with self.assertRaisesMessage(ValidationError, 'empty'):
            CarOrderingService.seed(count=5)
        self.assertEqual(self.rows(), before)

    def test_rerun_is_explicit_and_preserves_first_dataset(self):
        call_command('seed_cars', count=5, stdout=StringIO())
        before = self.rows()
        with self.assertRaises(CommandError):
            call_command('seed_cars', count=5, stdout=StringIO())
        self.assertEqual(self.rows(), before)

    def test_invalid_seed_options_do_not_insert_anything(self):
        for options in [
            {'count': 0}, {'count': -1}, {'count': True}, {'count': '5'},
            {'count': 10**17}, {'count': 5, 'batch_size': 0},
            {'count': 5, 'batch_size': -1}, {'count': 5, 'seed': 'invalid'},
        ]:
            with self.subTest(options=options):
                with self.assertRaises(ValidationError):
                    CarOrderingService.seed(**options)
                self.assertEqual(Car.objects.count(), 0)

    def test_command_reports_created_count(self):
        output = StringIO()
        call_command('seed_cars', count=5, seed=17, batch_size=2, stdout=output)
        self.assertIn('5 cars', output.getvalue())
        self.assertEqual(Car.objects.count(), 5)

    def test_batch_failure_rolls_back_all_inserts(self):
        original = Car.objects.bulk_create
        calls = 0

        def fail_second_batch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Batch failure')
            return original(*args, **kwargs)

        with patch.object(Car.objects, 'bulk_create', side_effect=fail_second_batch):
            with self.assertRaises(RuntimeError):
                CarOrderingService.seed(count=5, batch_size=2)
        self.assertEqual(calls, 2)
        self.assertEqual(Car.objects.count(), 0)


class BenchmarkSafetyTests(TestCase):
    def test_requires_a_dedicated_benchmark_database(self):
        with self.assertRaisesMessage(CommandError, 'benchmark'):
            call_command('benchmark_cars', stdout=StringIO())
        self.assertEqual(Car.objects.count(), 0)

    def test_refuses_existing_cars_even_in_benchmark_database(self):
        from django.db import connection
        CarOrderingService.append(name='Keep me', color='blue')
        with patch.dict(connection.settings_dict, {'NAME': 'ordered_cars_benchmark'}):
            with self.assertRaisesMessage(CommandError, 'empty'):
                call_command('benchmark_cars', stdout=StringIO())
        self.assertEqual(Car.objects.get().name, 'Keep me')

    def test_invalid_benchmark_sizes_are_rejected(self):
        for options in [{'count': 0}, {'count': 99}, {'runs': 0}, {'rebalance_runs': 0}]:
            with self.subTest(options=options):
                with self.assertRaises(CommandError):
                    call_command('benchmark_cars', stdout=StringIO(), **options)
