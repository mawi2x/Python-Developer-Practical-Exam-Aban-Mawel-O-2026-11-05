from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from .forms import CarForm, ColorFilterForm, MoveCarForm
from .models import Car
from .services import CarOrderingService


class ListReturnMixin:
    """Construct list navigation internally using valid filter state."""

    @property
    def selected_color(self):
        color = self.request.GET.get('color', '')
        return color if color in Car.Color.values else ''

    @property
    def search_query(self):
        query = self.request.GET.get('q', '').strip()
        return query if len(query) <= 200 else ''

    @property
    def filter_query(self):
        params = {}
        if self.selected_color:
            params['color'] = self.selected_color
        if self.search_query:
            params['q'] = self.search_query
        return '?' + urlencode(params) if params else ''

    def get_success_url(self):
        return reverse('cars:list') + self.filter_query

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            selected_color=self.selected_color,
            search_query=self.search_query,
            filter_query=self.filter_query,
            return_url=self.get_success_url(),
        )
        return context

    @staticmethod
    def add_validation_errors(form, error):
        if hasattr(error, 'error_dict'):
            for field, errors in error.error_dict.items():
                form.add_error(field if field in form.fields else None, errors)
        else:
            form.add_error(None, error)


class CarListView(ListReturnMixin, ListView):
    model = Car
    context_object_name = 'cars'
    paginate_by = 50
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        self.filter_form = ColorFilterForm(self.request.GET)
        if not self.filter_form.is_valid():
            return Car.objects.none()
        queryset = Car.objects.all()
        color = self.filter_form.cleaned_data['color']
        if color:
            queryset = queryset.filter(color=color)
        query = self.filter_form.cleaned_data['q']
        if query:
            numeric_id = query.removeprefix('#')
            if numeric_id.isdecimal():
                if len(numeric_id) > 19 or not 0 < int(numeric_id) <= 2**63 - 1:
                    return queryset.none()
                queryset = queryset.filter(pk=int(numeric_id))
            else:
                queryset = queryset.filter(name__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = self.filter_form
        context['total_count'] = (
            Car.objects.count() if self.selected_color or self.search_query or self.filter_form.errors
            else context['paginator'].count
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.filter_form.errors:
            response_kwargs['status'] = 400
        return super().render_to_response(context, **response_kwargs)


class CarDetailView(ListReturnMixin, DetailView):
    model = Car
    http_method_names = ['get', 'head', 'options']


class CarCreateView(ListReturnMixin, CreateView):
    model = Car
    form_class = CarForm
    http_method_names = ['get', 'post', 'head', 'options']

    def form_valid(self, form):
        try:
            self.object = CarOrderingService.append(**form.cleaned_data)
        except ValidationError as error:
            self.add_validation_errors(form, error)
            return self.form_invalid(form)
        messages.success(self.request, 'Car added at the end of the list.')
        return HttpResponseRedirect(self.get_success_url())


class ModalFormMixin:
    @property
    def modal_request(self):
        return self.request.headers.get('X-Modal-Request') == 'true'

    def get_template_names(self):
        if self.modal_request:
            return [self.modal_template_name]
        return super().get_template_names()

    def form_invalid(self, form):
        if self.modal_request:
            return self.render_to_response(self.get_context_data(form=form), status=422)
        return super().form_invalid(form)


class CarUpdateView(ModalFormMixin, ListReturnMixin, UpdateView):
    modal_template_name = 'cars/_edit_modal_form.html'
    model = Car
    form_class = CarForm
    http_method_names = ['get', 'post', 'head', 'options']

    def form_valid(self, form):
        # The form instance can contain a stale key after a concurrent move.
        # A targeted update also avoids recreating a concurrently deleted car.
        updated = Car.objects.filter(pk=self.object.pk).update(
            name=form.cleaned_data['name'], color=form.cleaned_data['color'],
        )
        if not updated:
            raise Http404('This car no longer exists.')
        messages.success(self.request, 'Car updated.')
        if self.modal_request:
            return JsonResponse({'redirect_url': self.get_success_url()})
        return HttpResponseRedirect(self.get_success_url())


class CarDeleteView(ListReturnMixin, DeleteView):
    model = Car
    http_method_names = ['get', 'post', 'head', 'options']

    def form_valid(self, form):
        try:
            CarOrderingService.delete(self.object.pk)
        except Car.DoesNotExist as error:
            raise Http404('This car no longer exists.') from error
        messages.success(self.request, 'Car deleted.')
        return HttpResponseRedirect(self.get_success_url())


class CarMoveView(ModalFormMixin, ListReturnMixin, SingleObjectMixin, FormView):
    modal_template_name = 'cars/_move_modal_form.html'
    model = Car
    form_class = MoveCarForm
    template_name = 'cars/car_move_form.html'
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            changed = CarOrderingService.move(self.object.pk, **form.cleaned_data)
        except Car.DoesNotExist as error:
            if not Car.objects.filter(pk=self.object.pk).exists():
                raise Http404('This car no longer exists.') from error
            form.add_error('target_id', 'The destination car does not exist.')
            return self.form_invalid(form)
        except ValidationError as error:
            self.add_validation_errors(form, error)
            return self.form_invalid(form)
        messages.success(self.request, 'Car moved.' if changed else 'Car is already in that position.')
        if self.modal_request:
            return JsonResponse({'redirect_url': self.get_success_url()})
        return HttpResponseRedirect(self.get_success_url())
