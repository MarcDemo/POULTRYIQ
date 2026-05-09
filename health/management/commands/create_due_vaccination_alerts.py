from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from health.models import VaccinationSchedule
from health.services import get_vaccination_alert_receiver, create_vaccination_alert


class Command(BaseCommand):
    help = 'Create alerts for vaccination schedules that become due within the next 2 days'

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + timedelta(days=2)

        due_items = VaccinationSchedule.objects.filter(
            status=VaccinationSchedule.Status.SCHEDULED,
            alert__isnull=True,
            scheduled_for__gte=now,
            scheduled_for__lte=window_end,
        ).select_related('house_ref')

        created = 0
        for item in due_items:
            receiver = get_vaccination_alert_receiver(item, None)
            alert_obj = create_vaccination_alert(item, receiver)
            if alert_obj:
                item.alert = alert_obj
                item.save(update_fields=['alert'])
                created += 1

        self.stdout.write(self.style.SUCCESS(f'Created {created} vaccination alert(s)'))
