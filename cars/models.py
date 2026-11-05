from django.core.validators import RegexValidator
from django.db import models


class CarList(models.Model):
    """The shared list's lock row, provisioned by a data migration."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name='cars_single_list'),
        ]


class Car(models.Model):
    class Color(models.TextChoices):
        RED = 'red', 'Red'
        BLUE = 'blue', 'Blue'
        GREEN = 'green', 'Green'
        BLACK = 'black', 'Black'
        WHITE = 'white', 'White'
        SILVER = 'silver', 'Silver'
        YELLOW = 'yellow', 'Yellow'

    name = models.CharField(
        max_length=200,
        validators=[RegexValidator(r'\S', 'Enter a name containing a non-whitespace character.')],
    )
    color = models.CharField(max_length=10, choices=Color.choices)
    sort_key = models.DecimalField(max_digits=38, decimal_places=18)

    class Meta:
        ordering = ['sort_key']
        indexes = [models.Index(fields=['color', 'sort_key'], name='cars_color_sort_idx')]
        constraints = [
            models.UniqueConstraint(
                fields=['sort_key'],
                name='cars_sort_key_unique',
                deferrable=models.Deferrable.IMMEDIATE,
            ),
            models.CheckConstraint(
                condition=models.Q(color__in=['red', 'blue', 'green', 'black', 'white', 'silver', 'yellow']),
                name='cars_color_valid',
            ),
            models.CheckConstraint(condition=models.Q(name__regex=r'\S'), name='cars_name_nonblank'),
        ]

    def __str__(self):
        return self.name
