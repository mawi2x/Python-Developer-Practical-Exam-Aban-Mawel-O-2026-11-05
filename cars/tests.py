from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase

from .models import Car, CarList


class CarModelTests(TestCase):
    def make_car(self, **overrides):
        values = {'name': 'Roadster', 'color': 'blue', 'sort_key': Decimal('1000')}
        values.update(overrides)
        return Car(**values)

    def test_valid_car_persists_with_exact_key_and_stable_id(self):
        car = self.make_car(sort_key=Decimal('1000.123456789012345678'))
        car.full_clean()
        car.save()
        original_id = car.pk
        car.refresh_from_db()
        self.assertEqual(car.sort_key, Decimal('1000.123456789012345678'))
        car.name = 'Renamed'
        car.save(update_fields=['name'])
        self.assertEqual(Car.objects.get(pk=original_id).name, 'Renamed')

    def test_required_fields_are_validated(self):
        for field in ('name', 'color', 'sort_key'):
            with self.subTest(field=field):
                car = self.make_car(**{field: None})
                with self.assertRaises(ValidationError) as error:
                    car.full_clean()
                self.assertIn(field, error.exception.message_dict)

    def test_blank_and_whitespace_names_are_invalid(self):
        for name in ('', ' ', '\t\n'):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    self.make_car(name=name).full_clean()

    def test_name_is_bounded(self):
        with self.assertRaises(ValidationError):
            self.make_car(name='x' * 201).full_clean()

    def test_every_color_choice_is_valid(self):
        for color in Car.Color.values:
            with self.subTest(color=color):
                self.make_car(color=color).full_clean()

    def test_unknown_color_is_invalid(self):
        with self.assertRaises(ValidationError) as error:
            self.make_car(color='purple').full_clean()
        self.assertIn('color', error.exception.message_dict)

    def test_sort_key_precision_and_bounds_are_validated(self):
        for key in ('0.0000000000000000001', '100000000000000000000'):
            with self.subTest(key=key):
                with self.assertRaises(ValidationError):
                    self.make_car(sort_key=Decimal(key)).full_clean()

    def test_names_need_not_be_unique(self):
        self.make_car().save()
        self.make_car(sort_key=Decimal('2000')).save()
        self.assertEqual(Car.objects.filter(name='Roadster').count(), 2)

    def test_default_order_is_sort_key_order(self):
        for name, key in [('A', '5000'), ('D', '1000'), ('E', '3000')]:
            self.make_car(name=name, sort_key=Decimal(key)).save()
        self.assertEqual(list(Car.objects.values_list('name', flat=True)), ['D', 'E', 'A'])

    def test_duplicate_sort_key_is_rejected_immediately(self):
        self.make_car().save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_car(name='Another').save()
        self.assertEqual(Car.objects.count(), 1)

    def test_database_rejects_invalid_colors_and_blank_names(self):
        for invalid in ({'color': 'purple'}, {'name': ''}, {'name': ' \t\n'}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self.make_car(**invalid).save()

    def test_database_rejects_null_fields(self):
        for field in ('name', 'color', 'sort_key'):
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self.make_car(**{field: None}).save()

    def test_color_sort_key_index_exists_in_database(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, Car._meta.db_table)
        index = constraints['cars_color_sort_idx']
        self.assertTrue(index['index'])
        self.assertEqual(index['columns'], ['color', 'sort_key'])


class CarListModelTests(TestCase):
    def test_singleton_exists_after_migrations(self):
        self.assertEqual(list(CarList.objects.values_list('pk', flat=True)), [1])

    def test_database_prevents_a_second_list(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CarList.objects.create(id=2)


class SortKeyTransactionTests(TransactionTestCase):
    def setUp(self):
        self.first = Car.objects.create(name='First', color='blue', sort_key=Decimal('1000'))
        self.second = Car.objects.create(name='Second', color='red', sort_key=Decimal('2000'))

    def defer_sort_key_constraint(self):
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS cars_sort_key_unique DEFERRED')

    def test_explicit_deferral_allows_swapping_keys(self):
        with transaction.atomic():
            self.defer_sort_key_constraint()
            Car.objects.filter(pk=self.first.pk).update(sort_key=Decimal('2000'))
            Car.objects.filter(pk=self.second.pk).update(sort_key=Decimal('1000'))
        self.assertEqual(list(Car.objects.values_list('pk', flat=True)), [self.second.pk, self.first.pk])

    def test_duplicate_at_commit_rolls_back_the_transaction(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.defer_sort_key_constraint()
                Car.objects.filter(pk=self.first.pk).update(sort_key=Decimal('2000'))
        self.first.refresh_from_db()
        self.assertEqual(self.first.sort_key, Decimal('1000'))
        self.assertEqual(Car.objects.count(), 2)
