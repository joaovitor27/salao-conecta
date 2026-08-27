import logging
import re
from datetime import timedelta


from django.utils import timezone
from psycopg2.extras import DateTimeTZRange
from rest_framework import serializers

from auth_users.models import User
from business.models import Appointment, ServiceSalon, Employee, Customer, Salon, AppointmentItem, EmployeeService

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  Serializers de leitura (aninhados, somente GET)
# ──────────────────────────────────────────────────────────────

class EmployeeServiceSummarySerializer(serializers.ModelSerializer):
    """Resumo de um serviço vinculado ao profissional."""
    service_id = serializers.IntegerField(source='service.id', read_only=True)
    service_name = serializers.CharField(source='service.service.name', read_only=True)

    class Meta:
        model = EmployeeService
        fields = ('service_id', 'service_name', 'commission_rate')


class EmployeeSummarySerializer(serializers.ModelSerializer):
    """Resumo do profissional (para exibição em listas)."""
    services = EmployeeServiceSummarySerializer(source='employee_services', many=True, read_only=True)

    class Meta:
        model = Employee
        fields = ('id', 'full_name', 'role', 'services')


class ServiceSalonSummarySerializer(serializers.ModelSerializer):
    """Resumo do serviço do salão com o nome global do serviço."""
    service_name = serializers.CharField(source='service.name', read_only=True)

    class Meta:
        model = ServiceSalon
        fields = ('id', 'service_name', 'price', 'duration_minutes')


class CustomerSummarySerializer(serializers.ModelSerializer):
    """Resumo do cliente para exibição no agendamento."""

    class Meta:
        model = Customer
        fields = ('id', 'name', 'phone', 'cpf', 'email')


# ──────────────────────────────────────────────────────────────
#  Serializer de LEITURA do Appointment
# ──────────────────────────────────────────────────────────────

class AppointmentItemSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service.service.name', read_only=True)

    class Meta:
        model = AppointmentItem
        fields = ('id', 'service', 'service_name', 'price', 'duration_minutes')


class AppointmentReadSerializer(serializers.ModelSerializer):
    """Serializer completo de leitura para Agendamentos."""
    professional = EmployeeSummarySerializer(read_only=True)
    client = CustomerSummarySerializer(read_only=True)
    items = AppointmentItemSerializer(many=True, read_only=True)
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Appointment
        fields = (
            'id',
            'client',
            'professional',
            'items',
            'start_time',
            'end_time',
            'status',
            'status_display',
            'total_price',
            'discount',
            'notes',
            'created_at',
            'updated_at',
        )

    @staticmethod
    def get_start_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.lower:
            return obj.time_range.lower.isoformat()
        return None

    @staticmethod
    def get_end_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.upper:
            return obj.time_range.upper.isoformat()
        return None


# ──────────────────────────────────────────────────────────────
#  Serializer de CRIAÇÃO do Appointment
# ──────────────────────────────────────────────────────────────

class AppointmentItemCreateSerializer(serializers.Serializer):
    service_id = serializers.IntegerField(help_text="ID do ServiceSalon")
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    duration_minutes = serializers.IntegerField(
        required=False, min_value=1,
        help_text="Duração customizada em minutos. Se omitido, usa o padrão do serviço."
    )


class AppointmentCreateSerializer(serializers.Serializer):
    """
    Serializer de escrita: recebe itens (serviços) e monta o DateTimeRangeField
    automaticamente somando as durações.
    Preço e duração podem ser customizados por item.
    """
    client_id = serializers.UUIDField()
    professional_id = serializers.UUIDField(required=False, allow_null=True)
    services = AppointmentItemCreateSerializer(many=True, help_text="Lista de serviços")
    start_time = serializers.DateTimeField(
        help_text="Data e hora de início do agendamento (ISO 8601)"
    )
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0.00, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_start_time(self, value):
        now = timezone.now()
        if value < now - timedelta(minutes=5):
            raise serializers.ValidationError("Não é possível agendar no passado.")
        return value

    def validate(self, attrs):
        salon: Salon = self.context['request'].salon

        try:
            client = Customer.objects.get(
                id=attrs['client_id'], salon=salon, is_active=True
            )
        except Customer.DoesNotExist:
            raise serializers.ValidationError({"client_id": "Cliente não encontrado neste salão."})

        services_data = attrs.get('services', [])
        if not services_data:
            raise serializers.ValidationError({"services": "Pelo menos um serviço é obrigatório."})

        # Buscar profissional (se informado)
        professional = None
        if attrs.get('professional_id'):
            try:
                professional = Employee.objects.get(
                    id=attrs['professional_id'],
                    salon=salon,
                    is_active=True,
                    is_schedulable=True,
                )
            except Employee.DoesNotExist:
                raise serializers.ValidationError(
                    {"professional_id": "Profissional não encontrado, inativo ou não-agendável."}
                )

        # Coletar IDs dos serviços vinculados ao profissional (se houver)
        professional_service_ids = set()
        if professional:
            professional_service_ids = set(
                EmployeeService.objects.filter(
                    employee=professional
                ).values_list('service_id', flat=True)
            )

        validated_items = []
        total_duration = 0
        from decimal import Decimal
        total_price = Decimal('0.00')

        for item_data in services_data:
            try:
                service_salon = ServiceSalon.objects.select_related('service').get(
                    id=item_data['service_id'], salon=salon, is_active=True
                )
            except ServiceSalon.DoesNotExist:
                raise serializers.ValidationError(
                    {"services": f"Serviço ID {item_data['service_id']} não encontrado ou inativo."}
                )

            # Validar que o profissional realiza este serviço
            if professional and professional_service_ids and service_salon.id not in professional_service_ids:
                raise serializers.ValidationError(
                    {"services": f"O profissional '{professional.full_name}' não realiza o serviço "
                                 f"'{service_salon.service.name}'."}
                )
            
            price = item_data.get('price')
            if price is None:
                price = service_salon.price

            # Duração customizada ou padrão do serviço
            duration = item_data.get('duration_minutes')
            if duration is None:
                duration = service_salon.duration_minutes

            total_duration += duration
            total_price += price

            validated_items.append({
                'service_salon': service_salon,
                'price': price,
                'duration_minutes': duration
            })

        start = attrs['start_time']
        end = start + timedelta(minutes=total_duration)
        time_range = DateTimeTZRange(lower=start, upper=end)

        if professional:
            conflict = Appointment.objects.filter(
                professional=professional,
                time_range__overlap=time_range,
            ).exclude(
                status=Appointment.Status.CANCELLED,
            )
            if self.instance:
                conflict = conflict.exclude(pk=self.instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"start_time": "O profissional já possui um atendimento nesse horário."}
                )

        attrs['_client'] = client
        attrs['_validated_items'] = validated_items
        attrs['_total_price'] = total_price
        attrs['_professional'] = professional
        attrs['_time_range'] = time_range
        return attrs

    def create(self, validated_data):
        salon = self.context['request'].salon
        appointment = Appointment.objects.create(
            salon=salon,
            client=validated_data['_client'],
            professional=validated_data['_professional'],
            time_range=validated_data['_time_range'],
            total_price=validated_data['_total_price'],
            discount=validated_data.get('discount', 0.00),
            notes=validated_data.get('notes', ''),
            status=Appointment.Status.PENDING,
        )
        
        items_to_create = [
            AppointmentItem(
                appointment=appointment,
                service=item['service_salon'],
                price=item['price'],
                duration_minutes=item['duration_minutes']
            )
            for item in validated_data['_validated_items']
        ]
        AppointmentItem.objects.bulk_create(items_to_create)

        logger.info(
            "Agendamento %s criado | salão=%s profissional=%s",
            appointment.pk, salon.slug,
            validated_data['_professional'].full_name if validated_data['_professional'] else 'N/A',
        )
        return appointment

    def update(self, instance, validated_data):
        instance.client = validated_data['_client']
        instance.professional = validated_data['_professional']
        instance.time_range = validated_data['_time_range']
        instance.total_price = validated_data['_total_price']
        instance.discount = validated_data.get('discount', instance.discount)
        instance.notes = validated_data.get('notes', instance.notes)
        instance.save()
        
        # Recriar itens
        instance.items.all().delete()
        items_to_create = [
            AppointmentItem(
                appointment=instance,
                service=item['service_salon'],
                price=item['price'],
                duration_minutes=item['duration_minutes']
            )
            for item in validated_data['_validated_items']
        ]
        AppointmentItem.objects.bulk_create(items_to_create)

        logger.info("Agendamento %s atualizado | salão=%s", instance.pk, instance.salon.slug)
        return instance


# ──────────────────────────────────────────────────────────────
#  Serializer para ATUALIZAÇÃO de STATUS
# ──────────────────────────────────────────────────────────────

class AppointmentStatusSerializer(serializers.Serializer):
    """Permite apenas a transição de status do agendamento."""

    ALLOWED_TRANSITIONS = {
        Appointment.Status.PENDING: [
            Appointment.Status.CONFIRMED,
            Appointment.Status.CANCELLED,
        ],
        Appointment.Status.CONFIRMED: [
            Appointment.Status.PENDING,
            Appointment.Status.COMPLETED,
            Appointment.Status.CANCELLED,
        ],
        Appointment.Status.COMPLETED: [],
        Appointment.Status.CANCELLED: [],
    }

    status = serializers.ChoiceField(choices=Appointment.Status.choices)

    def validate_status(self, value):
        current = self.instance.status
        allowed = self.ALLOWED_TRANSITIONS.get(current, [])
        if value not in allowed:
            raise serializers.ValidationError(
                f"Transição de '{current}' para '{value}' não é permitida. "
                f"Transições válidas: {[s for s in allowed]}"
            )
        return value

    def update(self, instance, validated_data):
        instance.status = validated_data['status']
        instance.save(update_fields=['status', 'updated_at'])
        logger.info(
            "Status do agendamento %s atualizado para '%s'",
            instance.pk, instance.status,
        )
        return instance


# ──────────────────────────────────────────────────────────────
#  Serializer do DASHBOARD
# ──────────────────────────────────────────────────────────────

class DashboardAppointmentSerializer(serializers.ModelSerializer):
    """Serializer enxuto para os itens da lista do dashboard."""
    professional_name = serializers.CharField(
        source='professional.full_name', default='Sem profissional', read_only=True
    )
    client_name = serializers.CharField(source='client.name', read_only=True)
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    items = AppointmentItemSerializer(many=True, read_only=True)

    class Meta:
        model = Appointment
        fields = (
            'id',
            'client_name',
            'professional_name',
            'items',
            'total_price',
            'discount',
            'start_time',
            'end_time',
            'status',
            'status_display',
        )

    @staticmethod
    def get_start_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.lower:
            return obj.time_range.lower.isoformat()
        return None

    @staticmethod
    def get_end_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.upper:
            return obj.time_range.upper.isoformat()
        return None


class DashboardSummarySerializer(serializers.Serializer):
    """Serializer para o resumo do dashboard."""
    total_appointments = serializers.IntegerField()
    completed_appointments = serializers.IntegerField()
    pending_appointments = serializers.IntegerField()
    confirmed_appointments = serializers.IntegerField()
    cancelled_appointments = serializers.IntegerField()
    estimated_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    completed_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    appointments = DashboardAppointmentSerializer(many=True)


# ──────────────────────────────────────────────────────────────
#  Serializers de IDENTIDADE VISUAL / PERFIL DO SALÃO
# ──────────────────────────────────────────────────────────────

class HexColorField(serializers.CharField):
    """Campo de cor hexadecimal normalizado para maiúsculas (#RRGGBB)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('max_length', 7)
        kwargs.setdefault('min_length', 4)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data).strip()
        if not re.fullmatch(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})', value):
            raise serializers.ValidationError("Informe uma cor em hexadecimal. Ex: #233B5C.")
        if len(value) == 4:
            value = '#' + ''.join(ch * 2 for ch in value[1:])
        return value.upper()


class SalonBrandingSerializer(serializers.ModelSerializer):
    """Identidade visual do salão consumida pelo front para montar o tema."""
    brand_name = serializers.CharField(read_only=True)
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Salon
        fields = (
            'slug',
            'name',
            'brand_name',
            'display_name',
            'tagline',
            'logo_url',
            'primary_color',
            'secondary_color',
            'accent_color',
            'updated_at',
        )
        read_only_fields = fields

    def get_logo_url(self, obj: Salon) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        url = obj.logo.url
        return request.build_absolute_uri(url) if request else url


class SalonProfileSerializer(serializers.ModelSerializer):
    """Perfil editável do salão (dados cadastrais + identidade visual)."""
    brand_name = serializers.CharField(read_only=True)
    logo_url = serializers.SerializerMethodField()
    primary_color = HexColorField(required=False)
    secondary_color = HexColorField(required=False, allow_null=True, allow_blank=True)
    accent_color = HexColorField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = Salon
        fields = (
            'id',
            'slug',
            'name',
            'brand_name',
            'display_name',
            'tagline',
            'description',
            'email',
            'phone_number',
            'website',
            'logo',
            'logo_url',
            'primary_color',
            'secondary_color',
            'accent_color',
            'operating_hours',
            'updated_at',
        )
        read_only_fields = ('id', 'slug', 'brand_name', 'logo_url', 'updated_at')
        extra_kwargs = {
            'logo': {'write_only': True, 'required': False, 'allow_null': True},
        }

    def get_logo_url(self, obj: Salon) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        url = obj.logo.url
        return request.build_absolute_uri(url) if request else url

    @staticmethod
    def validate_name(value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("O nome do salão deve ter ao menos 2 caracteres.")
        return value

    def validate(self, attrs):
        """Cores opcionais vazias são persistidas como NULL (derivadas no front)."""
        for field in ('secondary_color', 'accent_color'):
            if field in attrs and not attrs[field]:
                attrs[field] = None
        return attrs


# ──────────────────────────────────────────────────────────────
#  Serializers de FUNCIONÁRIOS (CRUD)
# ──────────────────────────────────────────────────────────────

class EmployeeServiceWriteSerializer(serializers.Serializer):
    """Vínculo profissional × serviço enviado no cadastro."""
    service_id = serializers.IntegerField(help_text="ID do ServiceSalon")
    commission_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True,
        help_text="Comissão específica deste serviço (%). Se vazio, usa a comissão padrão."
    )


class EmployeeReadSerializer(serializers.ModelSerializer):
    """Funcionário completo para a listagem e o detalhe."""
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    contract_type_display = serializers.CharField(source='get_contract_type_display', read_only=True)
    services = EmployeeServiceSummarySerializer(source='employee_services', many=True, read_only=True)
    email = serializers.SerializerMethodField()
    has_login = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = (
            'id',
            'full_name',
            'cpf_cnpj',
            'role',
            'role_display',
            'contract_type',
            'contract_type_display',
            'is_schedulable',
            'is_active',
            'fixed_salary',
            'default_commission_rate',
            'email',
            'has_login',
            'services',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    @staticmethod
    def get_email(obj: Employee) -> str | None:
        return obj.user.email if obj.user_id else None

    @staticmethod
    def get_has_login(obj: Employee) -> bool:
        return obj.user_id is not None


class EmployeeWriteSerializer(serializers.ModelSerializer):
    """
    Cadastro e edição de funcionários.

    - `services`: lista de vínculos (`service_id` + `commission_rate` opcional).
      Sempre sincroniza (o que não vier na lista é desvinculado).
    - `email`/`password`: opcionais e apenas para papéis com acesso ao sistema
      (Gerente, Financeiro e Recepcionista). Profissional e Apoio não fazem login.
    """
    services = EmployeeServiceWriteSerializer(many=True, required=False)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True, write_only=True)
    password = serializers.CharField(
        required=False, allow_blank=True, write_only=True, min_length=6, trim_whitespace=False
    )

    class Meta:
        model = Employee
        fields = (
            'id',
            'full_name',
            'cpf_cnpj',
            'role',
            'contract_type',
            'is_schedulable',
            'is_active',
            'fixed_salary',
            'default_commission_rate',
            'services',
            'email',
            'password',
        )
        read_only_fields = ('id',)

    @staticmethod
    def validate_full_name(value: str) -> str:
        value = ' '.join(value.split())
        if len(value) < 3:
            raise serializers.ValidationError("Informe o nome completo do funcionário.")
        return value

    @staticmethod
    def validate_cpf_cnpj(value: str) -> str:
        digits = re.sub(r'\D', '', value or '')
        if len(digits) not in (11, 14):
            raise serializers.ValidationError("Informe um CPF (11 dígitos) ou CNPJ (14 dígitos).")
        return digits

    def _resolved(self, attrs, field, default=None):
        """Valor final do campo considerando o estado atual em edições parciais."""
        if field in attrs:
            return attrs[field]
        if self.instance is not None:
            return getattr(self.instance, field)
        return default

    def validate(self, attrs):
        salon: Salon = self.context['request'].salon

        role = self._resolved(attrs, 'role', Employee.Role.PROFESSIONAL)
        email = (attrs.get('email') or '').strip().lower()
        password = attrs.get('password') or ''

        # ── CPF/CNPJ único dentro do salão ───────────────────
        cpf_cnpj = attrs.get('cpf_cnpj')
        if cpf_cnpj:
            duplicated = Employee.objects.filter(salon=salon, cpf_cnpj=cpf_cnpj)
            if self.instance is not None:
                duplicated = duplicated.exclude(pk=self.instance.pk)
            if duplicated.exists():
                raise serializers.ValidationError(
                    {"cpf_cnpj": "Já existe um funcionário com este CPF/CNPJ neste salão."}
                )

        # ── Acesso ao sistema ────────────────────────────────
        if role in Employee.ROLES_WITHOUT_LOGIN:
            if email or password:
                raise serializers.ValidationError(
                    {"email": "Profissional da Beleza e Apoio não possuem acesso ao sistema."}
                )
        else:
            has_user = self.instance is not None and self.instance.user_id is not None
            if password and not (email or has_user):
                raise serializers.ValidationError({"email": "Informe o e-mail de acesso."})
            if email and not has_user and not password:
                raise serializers.ValidationError(
                    {"password": "Informe uma senha de acesso com no mínimo 6 caracteres."}
                )
            if email:
                conflict = User.objects.filter(email__iexact=email)
                if has_user:
                    conflict = conflict.exclude(pk=self.instance.user_id)
                if conflict.exists():
                    raise serializers.ValidationError(
                        {"email": "Já existe um usuário cadastrado com este e-mail."}
                    )

        attrs['email'] = email
        attrs['password'] = password

        # ── Serviços realizados ──────────────────────────────
        services = attrs.get('services')
        if services is not None:
            service_ids = [item['service_id'] for item in services]
            if len(service_ids) != len(set(service_ids)):
                raise serializers.ValidationError({"services": "Há serviços repetidos na lista."})

            valid_ids = set(
                ServiceSalon.objects.filter(
                    id__in=service_ids, salon=salon, is_active=True
                ).values_list('id', flat=True)
            )
            invalid = [str(sid) for sid in service_ids if sid not in valid_ids]
            if invalid:
                raise serializers.ValidationError(
                    {"services": f"Serviços não encontrados ou inativos: {', '.join(invalid)}."}
                )

        is_schedulable = self._resolved(attrs, 'is_schedulable', True)
        if is_schedulable:
            total = len(services) if services is not None else (
                self.instance.employee_services.count() if self.instance is not None else 0
            )
            if total == 0:
                raise serializers.ValidationError(
                    {"services": "Selecione ao menos um serviço para quem atende na agenda."}
                )

        return attrs

    @staticmethod
    def _sync_services(employee: Employee, services: list | None) -> None:
        if services is None:
            return

        wanted = {item['service_id']: item.get('commission_rate') for item in services}
        EmployeeService.objects.filter(employee=employee).exclude(
            service_id__in=wanted.keys()
        ).delete()

        existing = {
            link.service_id: link
            for link in EmployeeService.objects.filter(employee=employee)
        }
        for service_id, rate in wanted.items():
            link = existing.get(service_id)
            if link is None:
                EmployeeService.objects.create(
                    employee=employee, service_id=service_id, commission_rate=rate
                )
            elif link.commission_rate != rate:
                link.commission_rate = rate
                link.save(update_fields=['commission_rate', 'updated_at'])

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str]:
        parts = full_name.split()
        return (parts[0] if parts else '', ' '.join(parts[1:]))

    def create(self, validated_data):
        salon: Salon = self.context['request'].salon
        services = validated_data.pop('services', None)
        email = validated_data.pop('email', '')
        password = validated_data.pop('password', '')

        user = None
        if email:
            first_name, last_name = self._split_name(validated_data['full_name'])
            user = User.objects.create_user(
                email=email, password=password, first_name=first_name, last_name=last_name
            )

        employee = Employee.objects.create(salon=salon, user=user, **validated_data)
        self._sync_services(employee, services)

        logger.info("Funcionário %s criado | salão=%s", employee.pk, salon.slug)
        return employee

    def update(self, instance: Employee, validated_data):
        services = validated_data.pop('services', None)
        email = validated_data.pop('email', '')
        password = validated_data.pop('password', '')

        for field, value in validated_data.items():
            setattr(instance, field, value)

        # Papéis sem login não podem manter usuário vinculado
        if instance.role in Employee.ROLES_WITHOUT_LOGIN:
            instance.user = None
        elif email or password:
            user = instance.user
            if user is None:
                first_name, last_name = self._split_name(instance.full_name)
                user = User.objects.create_user(
                    email=email, password=password, first_name=first_name, last_name=last_name
                )
                instance.user = user
            else:
                if email and user.email.lower() != email:
                    user.email = email
                    user.save(update_fields=['email', 'updated_at'])
                if password:
                    user.set_password(password)
                    user.save(update_fields=['password', 'updated_at'])

        instance.save()
        self._sync_services(instance, services)

        logger.info("Funcionário %s atualizado | salão=%s", instance.pk, instance.salon.slug)
        return instance


# ──────────────────────────────────────────────────────────────
#  Serializer de LISTAGEM de CLIENTES
# ──────────────────────────────────────────────────────────────

class CustomerListSerializer(serializers.ModelSerializer):
    """Cliente com métricas de atendimento (usado na tela de clientes)."""
    appointments_count = serializers.IntegerField(read_only=True)
    last_visit = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Customer
        fields = (
            'id',
            'name',
            'phone',
            'cpf',
            'email',
            'birth_date',
            'is_active',
            'appointments_count',
            'last_visit',
            'created_at',
        )
        read_only_fields = fields


# ──────────────────────────────────────────────────────────────
#  Serializers de HORÁRIOS DISPONÍVEIS
# ──────────────────────────────────────────────────────────────

class AvailabilitySlotSerializer(serializers.Serializer):
    """Um horário livre para agendamento."""
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    label = serializers.CharField(help_text="Horário formatado (HH:MM)")
    end_label = serializers.CharField(help_text="Horário de término formatado (HH:MM)")
    period = serializers.CharField(help_text="morning | afternoon | evening")


class AvailabilityResponseSerializer(serializers.Serializer):
    """Resposta da consulta de horários disponíveis."""
    date = serializers.DateField()
    professional_id = serializers.UUIDField(allow_null=True)
    duration_minutes = serializers.IntegerField()
    opens_at = serializers.CharField(allow_null=True)
    closes_at = serializers.CharField(allow_null=True)
    is_closed = serializers.BooleanField()
    slots = AvailabilitySlotSerializer(many=True)
