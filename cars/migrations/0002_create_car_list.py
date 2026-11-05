from django.db import migrations


def create_car_list(apps, schema_editor):
    CarList = apps.get_model('cars', 'CarList')
    CarList.objects.using(schema_editor.connection.alias).get_or_create(id=1)


def remove_car_list(apps, schema_editor):
    CarList = apps.get_model('cars', 'CarList')
    CarList.objects.using(schema_editor.connection.alias).filter(id=1).delete()


class Migration(migrations.Migration):
    dependencies = [('cars', '0001_initial')]

    operations = [migrations.RunPython(create_car_list, remove_car_list)]
