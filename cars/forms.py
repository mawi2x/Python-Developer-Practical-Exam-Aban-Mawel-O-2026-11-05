from django import forms

from .models import Car


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css_class = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            if self.is_bound and name in self.errors:
                css_class += ' is-invalid'
            field.widget.attrs['class'] = css_class


class CarForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Car
        fields = ['name', 'color']


class ColorFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(
        required=False, max_length=200, label='Search cars',
        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Search name or ID'}),
    )
    color = forms.ChoiceField(
        choices=[('', 'All colors')] + Car.Color.choices,
        required=False,
        label='Color',
    )


class MoveCarForm(BootstrapFormMixin, forms.Form):
    placement = forms.ChoiceField(
        choices=[('before', 'Before a car'), ('beginning', 'Beginning'), ('end', 'End')],
        initial='before',
    )
    target_id = forms.IntegerField(
        min_value=1, max_value=2**63 - 1, required=False,
        label='Destination car ID',
        help_text='Required only when moving before another car.',
    )

    def clean(self):
        cleaned = super().clean()
        placement = cleaned.get('placement')
        target_id = cleaned.get('target_id')
        if placement == 'before' and target_id is None and 'target_id' not in self.errors:
            self.add_error('target_id', 'Enter the destination car ID.')
        elif placement in ('beginning', 'end') and target_id is not None:
            self.add_error('target_id', 'Leave the destination empty for beginning or end moves.')
        return cleaned
