from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .forms import CarForm, MoveCarForm
from .models import Car
from .services import CarOrderingService
from .test_ordering import create_fixture


class CarFormTests(TestCase):
    def test_car_form_exposes_only_name_and_color(self):
        form = CarForm(data={'name': ' Roadster ', 'color': 'blue', 'sort_key': '-1000', 'id': '99'})
        self.assertEqual(list(form.fields), ['name', 'color'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data, {'name': 'Roadster', 'color': 'blue'})

    def test_invalid_car_fields(self):
        for data in [
            {'name': ' \t\n', 'color': 'blue'}, {'name': 'x' * 201, 'color': 'blue'},
            {'name': 'Car', 'color': 'purple'}, {'name': 'Car'}, {'color': 'blue'},
        ]:
            with self.subTest(data=data):
                self.assertFalse(CarForm(data=data).is_valid())

    def test_valid_move_instructions(self):
        for data in [
            {'placement': 'before', 'target_id': '12'},
            {'placement': 'beginning'}, {'placement': 'end'},
        ]:
            with self.subTest(data=data):
                form = MoveCarForm(data=data)
                self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_move_instructions(self):
        for data in [
            {}, {'placement': 'after', 'target_id': '1'}, {'placement': 'before'},
            {'placement': 'before', 'target_id': 'abc'},
            {'placement': 'before', 'target_id': '0'},
            {'placement': 'before', 'target_id': '-1'},
            {'placement': 'before', 'target_id': str(2**63)},
            {'placement': 'end', 'target_id': '1'},
        ]:
            with self.subTest(data=data):
                self.assertFalse(MoveCarForm(data=data).is_valid())


class CarViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cars = create_fixture()

    def url(self, action, name=None):
        return reverse(f'cars:{action}', kwargs={'pk': self.cars[name].pk} if name else None)

    def snapshot(self):
        return list(Car.objects.values_list('pk', 'name', 'color', 'sort_key'))

    def test_list_shows_global_order_and_counts(self):
        response = self.client.get(self.url('list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([car.name for car in response.context['cars']], ['D', 'B', 'E', 'C', 'A'])
        self.assertEqual(response.context['total_count'], 5)
        self.assertEqual(response.context['paginator'].count, 5)
        self.assertNotContains(response, 'sort_key')

    def test_blue_filter_preserves_global_order(self):
        response = self.client.get(self.url('list'), {'color': 'blue'})
        self.assertEqual([car.name for car in response.context['cars']], ['D', 'E', 'A'])
        self.assertEqual(response.context['paginator'].count, 3)
        self.assertEqual(response.context['total_count'], 5)

    def test_invalid_color_returns_validation_error(self):
        response = self.client.get(self.url('list'), {'color': 'purple'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('color', response.context['filter_form'].errors)

    def test_detail_page(self):
        response = self.client.get(self.url('detail', 'C'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['car'].pk, self.cars['C'].pk)
        self.assertContains(response, 'Red')
        self.assertNotContains(response, 'sort_key')

    def test_get_forms_and_confirmation_do_not_mutate(self):
        before = self.snapshot()
        for action, name in [('create', None), ('edit', 'C'), ('delete', 'C'), ('move', 'C')]:
            with self.subTest(action=action):
                self.assertEqual(self.client.get(self.url(action, name)).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_create_appends_through_service_and_ignores_supplied_key(self):
        with patch.object(CarOrderingService, 'append', wraps=CarOrderingService.append) as append:
            response = self.client.post(self.url('create') + '?color=blue', {
                'name': 'New', 'color': 'silver', 'sort_key': '-999', 'id': self.cars['C'].pk,
            })
        append.assert_called_once_with(name='New', color='silver')
        self.assertRedirects(response, self.url('list') + '?color=blue')
        self.assertEqual(Car.objects.get(name='New').sort_key, Decimal('6000'))
        self.assertEqual(Car.objects.get(pk=self.cars['C'].pk).name, 'C')

    def test_invalid_create_does_not_change_data(self):
        before = self.snapshot()
        for data in [{'name': ' ', 'color': 'blue'}, {'name': 'New', 'color': 'purple'}]:
            with self.subTest(data=data):
                response = self.client.post(self.url('create'), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.snapshot(), before)

    def test_create_service_validation_is_shown(self):
        before = self.snapshot()
        with patch.object(CarOrderingService, 'append', side_effect=ValidationError('Cannot append.')):
            response = self.client.post(self.url('create'), {'name': 'New', 'color': 'blue'})
        self.assertContains(response, 'Cannot append.')
        self.assertEqual(self.snapshot(), before)

    def test_edit_changes_name_and_color_but_preserves_key(self):
        response = self.client.post(self.url('edit', 'C') + '?color=red', {
            'name': 'Renamed', 'color': 'green', 'sort_key': '0',
        })
        self.assertRedirects(response, self.url('list') + '?color=red')
        car = Car.objects.get(pk=self.cars['C'].pk)
        self.assertEqual((car.name, car.color, car.sort_key), ('Renamed', 'green', Decimal('4000')))

    def test_edit_does_not_overwrite_a_move_after_object_was_loaded(self):
        original_clean = CarForm.clean

        def move_during_validation(form):
            cleaned = original_clean(form)
            CarOrderingService.move(self.cars['C'].pk, placement='beginning')
            return cleaned

        with patch.object(CarForm, 'clean', move_during_validation):
            response = self.client.post(self.url('edit', 'C'), {'name': 'Renamed', 'color': 'blue'})
        self.assertEqual(response.status_code, 302)
        car = Car.objects.get(pk=self.cars['C'].pk)
        self.assertEqual((car.name, car.sort_key), ('Renamed', Decimal('0')))

    def test_invalid_edit_does_not_change_data(self):
        before = self.snapshot()
        response = self.client.post(self.url('edit', 'C'), {'name': ' ', 'color': 'purple'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.snapshot(), before)

    def test_edit_does_not_recreate_a_concurrently_deleted_car(self):
        original_clean = CarForm.clean

        def delete_during_validation(form):
            cleaned = original_clean(form)
            CarOrderingService.delete(self.cars['C'].pk)
            return cleaned

        with patch.object(CarForm, 'clean', delete_during_validation):
            response = self.client.post(self.url('edit', 'C'), {'name': 'Renamed', 'color': 'blue'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Car.objects.filter(pk=self.cars['C'].pk).exists())
        self.assertEqual(Car.objects.count(), 4)

    def test_delete_through_service_preserves_other_keys(self):
        expected = [row for row in self.snapshot() if row[0] != self.cars['C'].pk]
        with patch.object(CarOrderingService, 'delete', wraps=CarOrderingService.delete) as delete:
            response = self.client.post(self.url('delete', 'C') + '?color=blue')
        delete.assert_called_once_with(self.cars['C'].pk)
        self.assertRedirects(response, self.url('list') + '?color=blue')
        self.assertEqual(self.snapshot(), expected)

    def test_missing_car_urls_return_404(self):
        before = self.snapshot()
        for action in ['detail', 'edit', 'delete', 'move']:
            url = reverse(f'cars:{action}', kwargs={'pk': 999999})
            with self.subTest(action=action):
                self.assertEqual(self.client.get(url).status_code, 404)
                if action != 'detail':
                    self.assertEqual(self.client.post(url, {'placement': 'beginning'}).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_and_out_of_range_ids_return_404(self):
        for car_id in ['abc', '-1', '0', str(2**63)]:
            with self.subTest(car_id=car_id):
                self.assertEqual(self.client.get(f'/cars/{car_id}/').status_code, 404)

    def test_move_before_target_preserves_filter_and_global_neighbors(self):
        response = self.client.post(self.url('move', 'A') + '?color=blue', {
            'placement': 'before', 'target_id': self.cars['E'].pk,
        })
        self.assertRedirects(response, self.url('list') + '?color=blue')
        car = Car.objects.get(pk=self.cars['A'].pk)
        self.assertEqual(car.sort_key, Decimal('2500'))
        filtered = self.client.get(self.url('list'), {'color': 'blue'})
        self.assertEqual([car.name for car in filtered.context['cars']], ['D', 'A', 'E'])

    def test_move_to_beginning_and_end(self):
        for placement, expected in [('beginning', '0'), ('end', '6000')]:
            with self.subTest(placement=placement):
                response = self.client.post(self.url('move', 'C'), {'placement': placement})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(Car.objects.get(pk=self.cars['C'].pk).sort_key, Decimal(expected))

    def test_self_move_succeeds_without_changes(self):
        before = self.snapshot()
        response = self.client.post(self.url('move', 'C'), {
            'placement': 'before', 'target_id': self.cars['C'].pk,
        }, follow=True)
        self.assertContains(response, 'already')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_moves_show_errors_without_changes(self):
        before = self.snapshot()
        for data in [
            {'placement': 'before'}, {'placement': 'after', 'target_id': self.cars['D'].pk},
            {'placement': 'before', 'target_id': 999999},
            {'placement': 'before', 'target_id': 'invalid'},
        ]:
            with self.subTest(data=data):
                response = self.client.post(self.url('move', 'C'), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.snapshot(), before)

    def test_move_service_validation_is_shown(self):
        before = self.snapshot()
        with patch.object(CarOrderingService, 'move', side_effect=ValidationError('Cannot move.')):
            response = self.client.post(self.url('move', 'C'), {'placement': 'beginning'})
        self.assertContains(response, 'Cannot move.')
        self.assertEqual(self.snapshot(), before)

    def test_return_urls_are_constructed_internally(self):
        response = self.client.post(self.url('create') + '?color=purple&next=https://example.com', {
            'name': 'New', 'color': 'blue', 'next': '//example.com',
        })
        self.assertRedirects(response, self.url('list'))

    def test_filter_is_preserved_in_links_and_form_actions(self):
        response = self.client.get(self.url('list'), {'color': 'blue'})
        self.assertContains(response, self.url('move', 'D') + '?color=blue')
        for action, name in [('create', None), ('edit', 'C'), ('delete', 'C'), ('move', 'C')]:
            url = self.url(action, name) + '?color=blue'
            with self.subTest(action=action):
                self.assertContains(self.client.get(url), f'action="{url}"')

    def test_non_post_mutation_methods_are_rejected(self):
        before = self.snapshot()
        for action, name in [('create', None), ('edit', 'C'), ('delete', 'C'), ('move', 'C')]:
            for method in ['put', 'patch', 'delete']:
                with self.subTest(action=action, method=method):
                    self.assertEqual(getattr(self.client, method)(self.url(action, name)).status_code, 405)
        self.assertEqual(self.snapshot(), before)

    def test_list_and_detail_are_read_only(self):
        self.assertEqual(self.client.post(self.url('list')).status_code, 405)
        self.assertEqual(self.client.post(self.url('detail', 'C')).status_code, 405)

    def test_csrf_required_and_valid_tokens_work_for_all_mutations(self):
        client = Client(enforce_csrf_checks=True)
        actions = [
            ('create', None, {'name': 'New', 'color': 'blue'}),
            ('edit', 'C', {'name': 'Renamed', 'color': 'red'}),
            ('move', 'C', {'placement': 'beginning'}),
            ('delete', 'C', {}),
        ]
        for action, name, data in actions:
            with self.subTest(action=action):
                url = self.url(action, name)
                before = self.snapshot()
                self.assertEqual(client.post(url, data).status_code, 403)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(client.get(url).status_code, 200)
                token = client.cookies['csrftoken'].value
                self.assertEqual(client.post(url, data, HTTP_X_CSRFTOKEN=token).status_code, 302)


class PaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Car.objects.bulk_create([
            Car(name=f'Car {i}', color='blue' if i % 2 else 'red', sort_key=Decimal(i * 1000))
            for i in range(1, 106)
        ])

    def test_page_boundaries(self):
        for page, first, count in [(1, 'Car 1', 50), (2, 'Car 51', 50), (3, 'Car 101', 5)]:
            with self.subTest(page=page):
                response = self.client.get(reverse('cars:list'), {'page': page})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context['cars']), count)
                self.assertEqual(response.context['cars'][0].name, first)

    def test_filtered_pagination(self):
        response = self.client.get(reverse('cars:list'), {'page': 2, 'color': 'blue'})
        self.assertEqual([car.name for car in response.context['cars']], ['Car 101', 'Car 103', 'Car 105'])
        self.assertEqual(response.context['paginator'].count, 53)
        self.assertEqual(response.context['total_count'], 105)
        self.assertContains(response, '?color=blue&amp;page=1')

    def test_invalid_pages_return_404(self):
        for page in ['abc', '0', '-1', '4', '99999999999999999999999999']:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(reverse('cars:list'), {'page': page}).status_code, 404)

    def test_empty_list_and_no_matches(self):
        response = self.client.get(reverse('cars:list'), {'color': 'green'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['cars']), 0)
        Car.objects.all().delete()
        response = self.client.get(reverse('cars:list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['cars']), 0)

    def test_move_to_target_on_another_page(self):
        source = Car.objects.get(name='Car 1')
        target = Car.objects.get(name='Car 101')
        response = self.client.post(reverse('cars:move', kwargs={'pk': source.pk}), {
            'placement': 'before', 'target_id': target.pk,
        })
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.sort_key, Decimal('100500'))
