from contextlib import closing
from decimal import Decimal, localcontext

from django.core.exceptions import ValidationError
from django.db import connection, transaction

from .models import Car, CarList


class CarOrderingService:
    """Serialize membership and order mutations through the shared list row.

    All code that inserts/deletes cars or changes sort keys must use this lock
    protocol. Name/color edits must save only those fields to avoid stale keys.
    """

    SPACING = Decimal('1000')
    QUANTUM = Decimal('1e-18')
    KEY_LIMIT = Decimal('1e20')  # DecimalField(38, 18) has 20 integer digits.
    DECIMAL_PRECISION = 76
    BATCH_SIZE = 500

    @staticmethod
    def _validate_id(value, field):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationError({field: 'Enter a positive integer car ID.'})

    @staticmethod
    def _lock_list():
        # Do not recreate a missing row here: concurrent creation would not
        # provide the common lock that every mutation must acquire first.
        CarList.objects.select_for_update().get(pk=1)

    @classmethod
    def _candidate(cls, lower, upper):
        """Return a representable key in this gap, or None if it is exhausted."""
        with localcontext() as ctx:
            ctx.prec = cls.DECIMAL_PRECISION
            if lower is None and upper is None:
                value = cls.SPACING
            elif lower is None:
                value = upper - cls.SPACING
            elif upper is None:
                value = lower + cls.SPACING
            else:
                value = (lower + upper) / 2
            value = value.quantize(cls.QUANTUM)
            if not -cls.KEY_LIMIT < value < cls.KEY_LIMIT:
                return None
            if lower is not None and value <= lower:
                return None
            if upper is not None and value >= upper:
                return None
            return value

    @staticmethod
    def _neighbors(car_id, placement, target):
        others = Car.objects.exclude(pk=car_id)
        if placement == 'beginning':
            return None, others.values_list('sort_key', flat=True).first()
        if placement == 'end':
            return others.values_list('sort_key', flat=True).last(), None
        lower = (
            others.filter(sort_key__lt=target.sort_key)
            .order_by('-sort_key').values_list('sort_key', flat=True).first()
        )
        return lower, target.sort_key

    @classmethod
    @transaction.atomic
    def move(cls, car_id, *, placement, target_id=None):
        """Move globally; return True if changed, False for an existing position.

        Placement is 'before' (requires target_id), 'beginning', or 'end'.
        Missing cars raise Car.DoesNotExist; invalid instructions raise
        ValidationError. Filtered views must pass IDs, never filtered querysets.
        """
        cls._validate_id(car_id, 'car_id')
        if placement not in ('before', 'beginning', 'end'):
            raise ValidationError({'placement': 'Choose before, beginning, or end.'})
        if placement == 'before':
            cls._validate_id(target_id, 'target_id')
        elif target_id is not None:
            raise ValidationError({'target_id': 'A target is only used for before moves.'})

        cls._lock_list()
        car = Car.objects.get(pk=car_id)
        target = Car.objects.get(pk=target_id) if placement == 'before' else None
        if target is not None and target.pk == car.pk:
            return False
        lower, upper = cls._neighbors(car.pk, placement, target)
        if ((lower is None or lower < car.sort_key)
                and (upper is None or car.sort_key < upper)):
            return False

        key = cls._candidate(lower, upper)
        if key is None:
            cls._rebalance_locked()
            if target is not None:
                target.refresh_from_db(fields=['sort_key'])
            lower, upper = cls._neighbors(car.pk, placement, target)
            key = cls._candidate(lower, upper)
        if key is None:
            raise ValidationError('The list has no representable space for this move.')
        Car.objects.filter(pk=car.pk).update(sort_key=key)
        return True

    @classmethod
    @transaction.atomic
    def append(cls, *, name, color):
        """Validate and append a car, returning the persisted model instance."""
        car = Car(name=name, color=color, sort_key=cls.SPACING)
        # Validate user fields before any possible rebalance; PostgreSQL enforces
        # constraints when saving the generated, lock-protected key.
        car.full_clean(validate_unique=False, validate_constraints=False)
        cls._lock_list()
        lower = Car.objects.values_list('sort_key', flat=True).last()
        key = cls._candidate(lower, None)
        if key is None:
            cls._rebalance_locked()
            lower = Car.objects.values_list('sort_key', flat=True).last()
            key = cls._candidate(lower, None)
        if key is None:
            raise ValidationError('The list has no representable space for another car.')
        car.sort_key = key
        car.save(force_insert=True)
        return car

    @classmethod
    @transaction.atomic
    def delete(cls, car_id):
        """Delete one car without changing surviving keys."""
        cls._validate_id(car_id, 'car_id')
        cls._lock_list()
        Car.objects.get(pk=car_id).delete()

    @classmethod
    @transaction.atomic
    def rebalance(cls):
        """Redistribute spaced keys while preserving global order."""
        cls._lock_list()
        cls._rebalance_locked()

    @classmethod
    def _rebalance_locked(cls):
        """Caller must hold the list lock inside an atomic transaction."""
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS cars_sort_key_unique DEFERRED')
        # A PostgreSQL server-side cursor keeps the original ordered snapshot as
        # batches are updated. Each bulk_update receives only a bounded batch.
        ids = Car.objects.values_list('pk', flat=True).iterator(chunk_size=cls.BATCH_SIZE)
        batch = []
        # Close the iterator before an exception rolls back the transaction and
        # invalidates its PostgreSQL cursor.
        with closing(ids), localcontext() as ctx:
            ctx.prec = cls.DECIMAL_PRECISION
            for position, car_id in enumerate(ids, start=1):
                key = (cls.SPACING * position).quantize(cls.QUANTUM)
                if key >= cls.KEY_LIMIT:
                    raise ValidationError('The list is too large for spaced sort keys.')
                batch.append(Car(pk=car_id, sort_key=key))
                if len(batch) == cls.BATCH_SIZE:
                    Car.objects.bulk_update(batch, ['sort_key'], batch_size=cls.BATCH_SIZE)
                    batch = []
            if batch:
                Car.objects.bulk_update(batch, ['sort_key'], batch_size=cls.BATCH_SIZE)
        # Check uniqueness now and restore the ordinary immediate behavior even
        # when the public method runs inside a caller's larger transaction.
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS cars_sort_key_unique IMMEDIATE')
