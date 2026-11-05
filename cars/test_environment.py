from pathlib import Path
from importlib import metadata
from tempfile import TemporaryDirectory
from unittest import TestCase

from validate_environment import configuration_errors, dependency_errors, python_error


class EnvironmentValidationTests(TestCase):
    def valid_environment(self):
        return {'DJANGO_SECRET_KEY': 'a' * 96, 'POSTGRES_PASSWORD': 'b' * 48}

    def test_supported_python_versions(self):
        self.assertIsNone(python_error((3, 10)))
        self.assertIsNone(python_error((3, 14)))
        self.assertIsNotNone(python_error((3, 9)))
        self.assertIsNotNone(python_error((3, 15)))

    def test_defaults_work_with_exported_credentials(self):
        self.assertEqual(configuration_errors(self.valid_environment()), [])

    def test_missing_and_placeholder_secrets_are_rejected(self):
        self.assertTrue(configuration_errors({}))
        environment = self.valid_environment()
        environment['DJANGO_SECRET_KEY'] = 'replace-with-a-long-random-secret'
        self.assertTrue(configuration_errors(environment))
        environment = self.valid_environment()
        environment['POSTGRES_PASSWORD'] = 'replace-with-a-random-database-password'
        self.assertTrue(configuration_errors(environment))

    def test_invalid_ports_and_debug_values_are_rejected(self):
        for values in [{'POSTGRES_PORT': 'abc'}, {'POSTGRES_PORT': '0'},
                       {'POSTGRES_PORT': '65536'}, {'DJANGO_DEBUG': 'yes'}]:
            with self.subTest(values=values):
                self.assertTrue(configuration_errors(self.valid_environment() | values))

    def test_empty_hosts_and_database_fields_are_rejected(self):
        for field in ['DJANGO_ALLOWED_HOSTS', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_HOST']:
            with self.subTest(field=field):
                self.assertTrue(configuration_errors(self.valid_environment() | {field: ' '}))

    def test_diagnostics_do_not_expose_credentials(self):
        environment = self.valid_environment() | {'POSTGRES_PORT': 'invalid'}
        output = str(configuration_errors(environment))
        self.assertNotIn(environment['POSTGRES_PASSWORD'], output)
        self.assertNotIn(environment['DJANGO_SECRET_KEY'], output)

    def test_dependency_version_mismatch_is_reported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements.txt'
            path.write_text('Django==5.2.19\n')
            self.assertEqual(dependency_errors(path, lambda name: '5.2.19'), [])
            self.assertTrue(dependency_errors(path, lambda name: '5.1.0'))

    def test_missing_dependency_is_reported(self):
        def missing(name):
            raise metadata.PackageNotFoundError(name)

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements.txt'
            path.write_text('Django==5.2.19\n')
            self.assertIn('Django is missing', dependency_errors(path, missing)[0])
