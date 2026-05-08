from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from health.models import TreatmentPlanItem
from health.services import get_treatment_alert_receiver, create_treatment_alert


class Command(BaseCommand):
    help = 'Create alerts for treatment plan items that become due within the next 2 hours'

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + timedelta(hours=2)

        due_items = TreatmentPlanItem.objects.filter(
            is_given=False,
            alert__isnull=True,
            scheduled_for__gte=now,
            scheduled_for__lte=window_end,
        ).select_related('sickness_report__reported_by')

        created = 0
        for item in due_items:
            receiver = get_treatment_alert_receiver(item.sickness_report, item.sickness_report.reported_by)
            alert_obj = create_treatment_alert(item, receiver)
            if alert_obj:
                item.alert = alert_obj
                item.save(update_fields=['alert'])
                created += 1

        self.stdout.write(self.style.SUCCESS(f'Created {created} treatment alert(s)'))
