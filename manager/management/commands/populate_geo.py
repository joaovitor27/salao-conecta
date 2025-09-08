import requests
from django.core.management.base import BaseCommand
from manager.models import Country, State, City
from django.db import IntegrityError, transaction


class Command(BaseCommand):
    help = 'Populates Country, State, and City tables for Brazil using the IBGE API with bulk operations.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Starting bulk population of geographical data...'))

        # 1. Populating Country (Brazil)
        try:
            brazil_country, created = Country.objects.get_or_create(
                name="Brasil",
                code="BRA"
            )
            if created:
                self.stdout.write(self.style.SUCCESS('Successfully added Country: Brasil'))
            else:
                self.stdout.write(self.style.WARNING('Country: Brasil already exists. Skipping.'))
        except IntegrityError:
            self.stdout.write(self.style.ERROR('Error creating Country: Brasil'))
            return

        # 2. Populating States (from IBGE API)
        states_url = 'https://servicodados.ibge.gov.br/api/v1/localidades/estados?orderBy=nome'
        try:
            response = requests.get(states_url)
            response.raise_for_status()
            states_data = response.json()
        except requests.RequestException as e:
            self.stdout.write(self.style.ERROR(f'Error fetching states from IBGE API: {e}'))
            return

        state_objects = []
        for state_data in states_data:
            state_objects.append(
                State(
                    name=state_data['nome'],
                    abbreviation=state_data['sigla'],
                    region=state_data['regiao']['nome'],
                    country=brazil_country
                )
            )

        with transaction.atomic():
            created_states = State.objects.bulk_create(state_objects, ignore_conflicts=True)

        self.stdout.write(self.style.SUCCESS(f'Successfully added {len(created_states)} new states.'))

        # 3. Populating Cities (from IBGE API)
        self.stdout.write(self.style.NOTICE('Populating cities... This may take a few minutes.'))

        all_cities_to_create = []
        for state_obj in State.objects.all():
            cities_url = f'https://servicodados.ibge.gov.br/api/v1/localidades/estados/{state_obj.abbreviation}/municipios'
            try:
                response = requests.get(cities_url)
                response.raise_for_status()
                cities_data = response.json()
            except requests.RequestException as e:
                self.stdout.write(self.style.ERROR(f'Error fetching cities for {state_obj.name}: {e}'))
                continue

            for city_data in cities_data:
                all_cities_to_create.append(
                    City(
                        name=city_data['nome'],
                        state=state_obj
                    )
                )

        self.stdout.write(self.style.NOTICE(f'Found a total of {len(all_cities_to_create)} cities to populate.'))

        with transaction.atomic():
            created_cities = City.objects.bulk_create(all_cities_to_create, ignore_conflicts=True)

        self.stdout.write(self.style.SUCCESS(f'Successfully added {len(created_cities)} new cities.'))
        self.stdout.write(self.style.SUCCESS('Geographical data population finished successfully!'))