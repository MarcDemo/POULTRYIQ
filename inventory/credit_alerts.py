from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.utils import timezone

from alerts.models import Alert, AlertType


def _get_or_create_system_alert_type():
    alert_type, _ = AlertType.objects.get_or_create(
        code=AlertType.AlertTypeCode.SYSTEM,
        defaults={"name": "System Alert", "is_active": True},
    )
    if not alert_type.is_active:
        alert_type.is_active = True
        alert_type.save(update_fields=["is_active"])
    return alert_type


def _credit_alert_receivers(created_by):
    User = get_user_model()
    receivers = list(
        User.objects.filter(
            is_active=True,
            role__code__in=["MANAGER", "OWNER"],
        )
    )
    if created_by and created_by.is_active and all(user.pk != created_by.pk for user in receivers):
        receivers.append(created_by)
    return receivers


def create_credit_payment_alerts(*, supplier_name, item_name, amount_due, due_date, paid_upfront, created_by):
    if not due_date:
        return []

    due_at = timezone.make_aware(datetime.combine(due_date, time(hour=9)))
    alert_type = _get_or_create_system_alert_type()
    paid_upfront_text = f"{paid_upfront}%" if paid_upfront is not None else "the agreed upfront amount"
    amount_text = f"{amount_due:,.2f}" if amount_due is not None else "the credit balance"

    alerts = []
    for receiver in _credit_alert_receivers(created_by):
        alerts.append(
            Alert.objects.create(
                alert_type=alert_type,
                title=f"Credit payment due: {supplier_name}",
                message=(
                    f"{paid_upfront_text} was marked as paid upfront for {item_name} supplied by {supplier_name}. "
                    f"Remaining credit balance due: UGX {amount_text}. Due date: {due_date:%d %b %Y}."
                ),
                receiver=receiver,
                priority=Alert.Priority.HIGH,
                due_date=due_at,
                persist_until_resolved=True,
            )
        )
    return alerts
