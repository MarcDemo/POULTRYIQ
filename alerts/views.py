from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from datetime import timedelta

from .models import Alert, AlertType, AlertTemplate, AlertSchedule
from .forms import SendMessageForm, RespondToAlertForm


def _get_or_create_alert_type(alert_type_code: str) -> AlertType:
    """Return an alert type, creating it when seed data is missing."""
    normalized_code = (alert_type_code or "").upper()
    default_name = dict(AlertType.AlertTypeCode.choices).get(
        normalized_code,
        normalized_code.replace("_", " ").title() or "Alert",
    )

    alert_type, _ = AlertType.objects.get_or_create(
        code=normalized_code,
        defaults={
            "name": default_name,
            "is_active": True,
        },
    )

    if not alert_type.is_active:
        alert_type.is_active = True
        alert_type.save(update_fields=["is_active"])

    return alert_type


@login_required
def alerts_inbox(request):
    """Display user's alerts/messages inbox"""
    user = request.user
    
    # Get filter parameters
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    type_filter = request.GET.get('type', '')
    
    # Get all alerts for this user
    alerts = Alert.objects.filter(receiver=user)
    
    # Apply filters
    if status_filter:
        alerts = alerts.filter(status=status_filter)
    if priority_filter:
        alerts = alerts.filter(priority=priority_filter)
    if type_filter:
        alerts = alerts.filter(alert_type__code=type_filter)
    
    # Get counts
    unread_count = Alert.objects.filter(receiver=user, status=Alert.Status.UNREAD).count()
    urgent_count = alerts.filter(priority=Alert.Priority.URGENT).count()
    overdue_count = alerts.filter(
        due_date__lt=timezone.now(),
        status__in=[Alert.Status.UNREAD, Alert.Status.READ, Alert.Status.ACKNOWLEDGED]
    ).count()
    
    context = {
        'alerts': alerts[:50],  # Paginate in production
        'unread_count': unread_count,
        'urgent_count': urgent_count,
        'overdue_count': overdue_count,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'type_filter': type_filter,
        'alert_statuses': Alert.Status.choices,
        'alert_priorities': Alert.Priority.choices,
        'alert_types': AlertType.objects.filter(is_active=True),
    }
    
    return render(request, 'alerts/inbox.html', context)


@login_required
def alert_detail(request, alert_id):
    """View detailed alert/message and respond"""
    alert = get_object_or_404(Alert, alert_id=alert_id, receiver=request.user)
    
    # Mark as read if not already
    if alert.status == Alert.Status.UNREAD:
        alert.mark_as_read()
    
    form = RespondToAlertForm(instance=alert)
    
    if request.method == 'POST':
        form = RespondToAlertForm(request.POST, instance=alert)
        if form.is_valid():
            form.save()
            messages.success(request, f'Alert marked as {alert.get_status_display()}')
            return redirect('alerts:inbox')
    
    context = {
        'alert': alert,
        'form': form,
        'is_sender': alert.sender == request.user,
    }
    
    return render(request, 'alerts/alert_detail.html', context)


@login_required
def send_message(request):
    """Send direct message to worker(s)"""
    # Only supervisors, managers, and owners can send messages
    if request.user.role.code not in ['SUPERVISOR', 'MANAGER', 'OWNER']:
        messages.error(request, 'You do not have permission to send messages.')
        return redirect('alerts:inbox')
    
    form = SendMessageForm(sender=request.user)
    
    if request.method == 'POST':
        form = SendMessageForm(request.POST, sender=request.user)
        if form.is_valid():
            alert = form.save(commit=False)

            # receiver is a custom form field and must be assigned explicitly.
            alert.receiver = form.cleaned_data['receiver']

            # Set alert type to DIRECT
            alert.alert_type = _get_or_create_alert_type(AlertType.AlertTypeCode.DIRECT)
            alert.sender = request.user

            alert.save()

            messages.success(
                request,
                f'Message sent to {alert.receiver.display_name}'
            )
            return redirect('alerts:inbox')
    
    context = {
        'form': form,
        'page_title': 'Send Message to Worker'
    }
    
    return render(request, 'alerts/send_message.html', context)


@login_required
def send_bulk_message(request):
    """Send message to multiple workers (supervisor/manager feature)"""
    if request.user.role.code not in ['SUPERVISOR', 'MANAGER', 'OWNER']:
        messages.error(request, 'You do not have permission to send messages.')
        return redirect('alerts:inbox')
    
    if request.method == 'POST':
        title = request.POST.get('title')
        message_text = request.POST.get('message')
        priority = request.POST.get('priority', 'MEDIUM')
        house_ids = request.POST.getlist('houses')
        
        if not title or not message_text:
            messages.error(request, 'Title and message are required.')
            return redirect('alerts:send_bulk_message')
        
        # Get users assigned to selected houses
        from accounts.models import User
        from poultry.models import PoultryHouse
        
        houses = PoultryHouse.objects.filter(id__in=house_ids)
        recipients = User.objects.filter(
            houses__in=houses,
            is_active=True,
            role__code__in=['WORKER', 'SUPERVISOR']
        ).distinct()
        
        # Create alert for each recipient
        alert_type = _get_or_create_alert_type(AlertType.AlertTypeCode.DIRECT)
        created_count = 0
        
        for recipient in recipients:
            Alert.objects.create(
                alert_type=alert_type,
                title=title,
                message=message_text,
                sender=request.user,
                receiver=recipient,
                priority=priority,
            )
            created_count += 1
        
        messages.success(request, f'Message sent to {created_count} worker(s)')
        return redirect('alerts:inbox')
    
    # GET: show form
    from poultry.models import PoultryHouse
    houses = PoultryHouse.objects.filter(is_active=True)
    
    context = {
        'houses': houses,
        'priorities': Alert.Priority.choices,
        'page_title': 'Send Bulk Message'
    }
    
    return render(request, 'alerts/send_bulk_message.html', context)


@login_required
@require_http_methods(["POST"])
def mark_as_read(request, alert_id):
    """Quick action to mark alert as read"""
    alert = get_object_or_404(Alert, alert_id=alert_id, receiver=request.user)
    alert.mark_as_read()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'status': alert.status})
    
    return redirect('alerts:inbox')


@login_required
@require_http_methods(["POST"])
def mark_as_acknowledged(request, alert_id):
    """Quick action to mark alert as acknowledged"""
    alert = get_object_or_404(Alert, alert_id=alert_id, receiver=request.user)
    alert.mark_as_acknowledged()
    messages.success(request, 'Alert acknowledged')
    
    return redirect('alerts:alert_detail', alert_id=alert_id)


@login_required
@require_http_methods(["POST"])
def mark_as_resolved(request, alert_id):
    """Quick action to mark alert as resolved"""
    alert = get_object_or_404(Alert, alert_id=alert_id, receiver=request.user)
    alert.mark_as_resolved()
    messages.success(request, 'Alert marked as resolved')
    
    return redirect('alerts:alert_detail', alert_id=alert_id)


def create_system_alert(title, message, receiver, alert_type_code='SYSTEM', priority='MEDIUM', 
                       related_house=None, related_batch=None, due_date=None):
    """
    Utility function to create system alerts programmatically.
    Call this from signal handlers, cron jobs, or business logic.
    
    Example:
        create_system_alert(
            title='Time to change bedding',
            message='House A bedding needs to be changed today',
            receiver=worker_user,
            related_house=house_a,
            priority='HIGH'
        )
    """
    alert_type = _get_or_create_alert_type(alert_type_code)
    
    return Alert.objects.create(
        alert_type=alert_type,
        title=title,
        message=message,
        receiver=receiver,
        sender=None,  # System alert
        priority=priority,
        related_house=related_house,
        related_batch=related_batch,
        due_date=due_date,
    )


def trigger_scheduled_alerts():
    """
    Cron job / scheduled task to send due alerts.
    Run this periodically (e.g., every hour) to check and send alerts.
    """
    now = timezone.now()
    schedules = AlertSchedule.objects.filter(
        is_active=True,
        next_alert_due__lte=now
    )
    
    created_alerts = []
    
    for schedule in schedules:
        # Create alert from template
        alert = schedule.template.create_alert(
            receiver=schedule.receiver,
            house=schedule.related_house.name if schedule.related_house else 'All Houses'
        )
        created_alerts.append(alert)
        
        # Update schedule
        if schedule.template.frequency_days:
            schedule.last_alert_sent = now
            schedule.next_alert_due = now + timedelta(days=schedule.template.frequency_days)
            schedule.save()
        else:
            # One-time alert, disable schedule
            schedule.is_active = False
            schedule.save()
    
    return created_alerts


# Optional: JSON responses for AJAX
from django.http import JsonResponse

@login_required
def get_unread_count(request):
    """AJAX endpoint to get unread alert count"""
    count = Alert.objects.filter(receiver=request.user, status=Alert.Status.UNREAD).count()
    return JsonResponse({'unread_count': count})


@login_required
def get_recent_alerts(request):
    """AJAX endpoint — returns the 10 most recent alerts for the popup panel"""
    qs = (
        Alert.objects
        .filter(receiver=request.user)
        .select_related('sender', 'alert_type')
        .order_by('-created_at')[:10]
    )

    items = []
    for alert in qs:
        items.append({
            'alert_id': alert.alert_id,
            'title': alert.title,
            'message': alert.message[:120] + ('…' if len(alert.message) > 120 else ''),
            'priority': alert.priority,
            'status': alert.status,
            'is_unread': alert.status == Alert.Status.UNREAD,
            'type_name': alert.alert_type.name,
            'sender_name': alert.sender.display_name if alert.sender else 'System',
            'created_at': alert.created_at.strftime('%b %d, %H:%M'),
            'detail_url': f'/alerts/alert/{alert.alert_id}/',
        })

    return JsonResponse({'alerts': items})
