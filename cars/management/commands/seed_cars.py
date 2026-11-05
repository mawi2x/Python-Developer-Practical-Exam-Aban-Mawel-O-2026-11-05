from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from cars.services import CarOrderingService


class Command(BaseCommand):
    help = 'Seed an empty car list with deterministic data. Existing cars are never erased.'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=100000)
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--batch-size', type=int, default=1000)

    def handle(self, *args, **options):
        try:
            count = CarOrderingService.seed(
                count=options['count'], seed=options['seed'], batch_size=options['batch_size'],
            )
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from error
        self.stdout.write(self.style.SUCCESS(f'Created {count} cars (seed={options["seed"]}).'))
