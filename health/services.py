from datetime import datetime, timedelta

from django.utils import timezone
from django.contrib.auth import get_user_model

from alerts.models import Alert, AlertType

User = get_user_model()


def get_health_alert_type():
    alert_type, _ = AlertType.objects.get_or_create(
        code=AlertType.AlertTypeCode.HEALTH,
        defaults={
            "name": "Health Alert",
            "description": "Vet diagnosis and treatment reminders.",
        },
    )
    return alert_type


def get_treatment_alert_receiver(report, fallback_user=None):
    reporter_role = (getattr(report.reported_by.role, "code", "") or "").upper()
    if report.reported_by.is_active and reporter_role in {"SUPERVISOR", "MANAGER", "OWNER"}:
        return report.reported_by

    supervisors = User.objects.filter(
        is_active=True,
        role__code__in=["SUPERVISOR", "MANAGER", "OWNER"],
    )
    if getattr(report, "house_ref_id", None):
        supervisors = supervisors.filter(houses=report.house_ref)

    return supervisors.distinct().first() or fallback_user


def create_treatment_alert(plan_item, receiver):
    """Create an Alert for a TreatmentPlanItem only if it's due within 2 hours.

    Returns the created Alert instance or None.
    """
    due_dt = plan_item.scheduled_for
    if not due_dt:
        return None

    now = timezone.now()
    # ensure timezone-aware
    if timezone.is_naive(due_dt):
        # treat naive as UTC-aware at midnight if needed
        due_dt = timezone.make_aware(datetime.combine(due_dt.date(), due_dt.time()))

    if due_dt - now <= timedelta(hours=2):
        return Alert.objects.create(
            alert_type=get_health_alert_type(),
            title=f"Treatment due for {plan_item.sickness_report.case_label}",
            message=(
                f"Diagnosis: {plan_item.sickness_report.diagnosis_label}\n"
                f"Case size: {plan_item.sickness_report.affected} bird(s)\n"
                f"Medicine: {plan_item.medicine_name}\n"
                f"Dosage: {plan_item.dosage}\n"
                f"Instructions: {plan_item.instructions or 'Follow the vet guidance recorded in the treatment plan.'}"
            ),
            receiver=receiver,
            sender=None,
            priority=Alert.Priority.HIGH,
            related_house=plan_item.sickness_report.house_ref,
            related_batch=plan_item.sickness_report.batch,
            due_date=due_dt,
            persist_until_resolved=True,
        )
    return None


def get_vaccination_alert_receiver(schedule, fallback_user=None):
    reporter = fallback_user
    # prefer supervisors assigned to the house
    supervisors = User.objects.filter(
        is_active=True,
        role__code__in=["SUPERVISOR", "MANAGER", "OWNER"],
    )
    if getattr(schedule, "house_ref_id", None):
        supervisors = supervisors.filter(houses=schedule.house_ref)

    return supervisors.distinct().first() or reporter


def create_vaccination_alert(schedule, receiver):
    """Create an Alert for a VaccinationSchedule only if it's due within 2 days.

    Returns the created Alert instance or None.
    """
    due_dt = schedule.scheduled_for
    if not due_dt:
        return None

    now = timezone.now()
    if timezone.is_naive(due_dt):
        try:
            due_dt = timezone.make_aware(datetime.combine(due_dt.date(), due_dt.time()))
        except Exception:
            pass

    if due_dt - now <= timedelta(days=2):
        return Alert.objects.create(
            alert_type=get_health_alert_type(),
            title=f"Vaccination due: {schedule.vaccine_name}",
            message=(
                f"Vaccine: {schedule.vaccine_name}\n"
                f"Brand: {schedule.brand or 'N/A'}\n"
                f"Dosage: {schedule.dosage or 'N/A'}\n"
                f"Administration: {schedule.administration_mode or 'N/A'}\n"
                f"Scheduled for: {due_dt.strftime('%Y-%m-%d %H:%M')}"
            ),
            receiver=receiver,
            sender=None,
            priority=Alert.Priority.MEDIUM,
            related_house=schedule.house_ref,
            related_batch=schedule.batch,
            due_date=due_dt,
            persist_until_resolved=True,
        )
    return None
