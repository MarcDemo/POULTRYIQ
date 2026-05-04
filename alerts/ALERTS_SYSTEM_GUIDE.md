# Alerts & Messaging System - Setup & Usage Guide

## Overview

The Alerts & Messaging system for Poultry IQ enables:
- **System-generated alerts** - Automated reminders for maintenance, health checks, feed checks
- **Direct messaging** - Supervisors/managers send task assignments and messages to workers
- **Alert tracking** - Track alert status (unread, read, acknowledged, resolved)
- **Priority management** - Mark alerts as Low, Medium, High, or Urgent
- **Context tracking** - Link alerts to specific poultry houses or batches

## Installation & Setup

### 1. Run Migrations

First, create and apply migrations for the alerts app:

```bash
python manage.py makemigrations alerts
python manage.py migrate alerts
```

### 2. Initialize Alert System (Optional)

Initialize alert types, templates, and demo alerts:

```bash
python manage.py init_alerts
```

This command creates:
- Alert types (System, Direct, Maintenance, Health, Compliance)
- Reusable alert templates (Bedding Change, Water System Check, Feed Check, etc.)
- Demo alerts for testing (if workers exist in the database)

## Features & Usage

### User Roles & Permissions

| Feature | Worker | Supervisor | Manager | Owner |
|---------|--------|------------|---------|-------|
| View own alerts | ✅ | ✅ | ✅ | ✅ |
| Mark alerts as read/resolved | ✅ | ✅ | ✅ | ✅ |
| Send direct messages to workers | ❌ | ✅ | ✅ | ✅ |
| Send bulk messages | ❌ | ✅ | ✅ | ✅ |
| View all alerts (admin) | ❌ | ❌ | ❌ | ✅ |
| Configure alert templates | ❌ | ❌ | ❌ | ✅ |

### For Workers/Supervisors

#### Viewing Alerts
- Go to `/alerts/inbox/` to see all your alerts and messages
- Filter by status (Unread, Read, Acknowledged, Resolved)
- Filter by priority (Low, Medium, High, Urgent)
- Filter by type (System, Direct Message, Maintenance, Health)

#### Alert Details
- Click on any alert to see full details
- View sender, date, priority, and context (house/batch if applicable)
- Mark as read, acknowledged, or resolved
- View due dates and check if overdue

### For Supervisors/Managers

#### Sending Direct Messages
1. Go to `/alerts/send/` 
2. Select a specific worker
3. Enter subject and message
4. Set priority and optional due date
5. Send

#### Sending Bulk Messages
1. Go to `/alerts/send/bulk/`
2. Select multiple poultry houses
3. Message will be sent to all workers assigned to those houses
4. Perfect for farm-wide announcements or emergency alerts

### Supervisor Dashboard Integration
Add this snippet to your supervisor dashboard to show unread alerts:

```html
<!-- Unread Alerts Widget -->
<div class="alert alert-info">
    <a href="{% url 'alerts:inbox' %}">
        <i class="fas fa-bell"></i> You have X unread alerts
    </a>
</div>
```

### AJAX Endpoint for Real-time Updates
Get unread alert count without page reload:

```javascript
fetch('/alerts/api/unread-count/')
    .then(response => response.json())
    .then(data => {
        document.getElementById('alert-count').textContent = data.unread_count;
    });
```

## System Alert Configuration

### Alert Types

- **SYSTEM**: Automated alerts from the system
- **DIRECT**: Direct message from supervisor/manager to worker
- **MAINTENANCE**: Maintenance task reminders
- **HEALTH**: Health and vaccination-related alerts
- **COMPLIANCE**: Compliance and regulatory alerts

### Pre-configured Templates

After running `init_alerts`, these templates are available:

#### 1. Bedding Change Due
- Frequency: Every 14 days
- Priority: HIGH
- Message: Reminds to change bedding in specified house

#### 2. Water System Check
- Frequency: Every 7 days
- Priority: MEDIUM
- Message: Weekly water system inspection checklist

#### 3. Feed Stock Check
- Frequency: Every 3 days
- Priority: MEDIUM
- Message: Verify feed inventory levels

#### 4. Equipment Inspection
- Frequency: Every 30 days
- Priority: MEDIUM
- Message: Monthly equipment check

#### 5. Health Status Check
- Frequency: Every 1 day
- Priority: HIGH
- Message: Daily health observation checklist

## API/Programmatic Usage

### Creating Alerts Programmatically

#### System Alert

```python
from alerts.views import create_system_alert
from accounts.models import User
from poultry.models import PoultryHouse

worker = User.objects.get(username='worker1')
house = PoultryHouse.objects.first()

create_system_alert(
    title='Bedding change required',
    message='Change bedding in House A today',
    receiver=worker,
    alert_type_code='MAINTENANCE',
    priority='HIGH',
    related_house=house,
    due_date=timezone.now() + timedelta(hours=4)
)
```

#### Direct Message

```python
from alerts.models import Alert, AlertType

supervisor = User.objects.get(username='supervisor1')
worker = User.objects.get(username='worker1')

alert = Alert.objects.create(
    alert_type=AlertType.objects.get(code='DIRECT'),
    title='Water system inspection needed',
    message='Please check water system in House B. Report back when done.',
    sender=supervisor,
    receiver=worker,
    priority='HIGH',
    due_date=timezone.now() + timedelta(hours=6)
)
```

### Using Alert Templates

```python
from alerts.models import AlertTemplate
from accounts.models import User
from poultry.models import PoultryHouse

template = AlertTemplate.objects.get(code='BEDDING_CHANGE')
worker = User.objects.get(username='worker1')
house = PoultryHouse.objects.get(name='House A')

alert = template.create_alert(
    receiver=worker,
    house=house.name
)
```

### Triggering Scheduled Alerts

For production, set up a cron job or celery task to run:

```python
from alerts.views import trigger_scheduled_alerts

# This runs every hour
alerts_created = trigger_scheduled_alerts()
print(f'Created {len(alerts_created)} scheduled alerts')
```

## Django Admin Interface

Access `/admin/alerts/` to:

- **Alert Types**: Create new alert categories
- **Alerts**: View, filter, and manage all alerts
  - Search by title, message, username
  - Filter by status, priority, type
  - Readonly fields protect creation dates
- **Alert Templates**: Create and manage reusable templates
  - Set frequency for recurring alerts
  - Configure title and message templates
- **Alert Schedules**: Configure recurring alert schedules
  - Assign templates to users
  - Set next alert due date
  - Track last alert sent

## Advanced Integration

### Trigger Alerts from Other Models (Signals)

```python
# In poultry/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from alerts.views import create_system_alert
from .models import MortalityRecord

@receiver(post_save, sender=MortalityRecord)
def alert_on_high_mortality(sender, instance, created, **kwargs):
    """Send alert if mortality is unusually high"""
    if created and instance.count > 10:  # threshold
        create_system_alert(
            title='High mortality alert',
            message=f'Mortality count: {instance.count} - investigate immediately',
            receiver=instance.recorded_by,
            alert_type_code='HEALTH',
            priority='URGENT',
            related_batch=instance.batch
        )
```

### Alert Email Notifications (Optional)

For production, send email notifications:

```python
# In alerts/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from .models import Alert

@receiver(post_save, sender=Alert)
def email_high_priority_alert(sender, instance, created, **kwargs):
    """Email user on high-priority alerts"""
    if created and instance.priority in ['HIGH', 'URGENT']:
        send_mail(
            f'Alert: {instance.title}',
            instance.message,
            'noreply@poultryiq.com',
            [instance.receiver.email],
        )
```

## Customization

### Adding Custom Alert Type

```python
# In alerts/models.py - add to AlertTypeCode choices
CUSTOM = "CUSTOM", "Custom Alert"

# Or create in admin:
AlertType.objects.create(
    code='EQUIPMENT_FAILURE',
    name='Equipment Failure Alert'
)
```

### Extending Alert Model

To add custom fields:

```python
# Create migration
python manage.py makemigrations

# Add field like:
# alert.custom_field = models.CharField(max_length=100)
```

## Troubleshooting

### Alerts not showing up
1. Verify user is the receiver: `Alert.objects.filter(receiver=user).count()`
2. Check permissions: Worker role can view own alerts
3. Ensure app is in INSTALLED_APPS: Check `settings.py`

### Alert status not updating
1. Verify form submission is working
2. Check user permissions - only receivers can update own alerts
3. Review Django error logs

### Migrations not applying
1. Remove old migration files if starting fresh
2. Reset database: `python manage.py flush` (dev only)
3. Re-run migrations

## Performance Considerations

For large systems:
- Add database indexes on `receiver`, `status`, `created_at` (already configured)
- Use `select_related()` in views: `alerts.select_related('receiver', 'sender')`
- Paginate alert lists (currently showing 50, implement pagination for production)
- Archive old alerts periodically

## Next Steps

1. ✅ Alerts system installed and configured
2. 📧 (Optional) Configure email notifications for alerts
3. 🔄 (Optional) Set up cron job for scheduled alerts
4. 📱 (Optional) Add SMS notifications for critical alerts
5. 📊 (Optional) Create analytics dashboard for alert metrics

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/alerts/inbox/` | GET | View all alerts |
| `/alerts/alert/<id>/` | GET | View alert detail |
| `/alerts/alert/<id>/read/` | POST | Mark as read |
| `/alerts/alert/<id>/acknowledge/` | POST | Mark as acknowledged |
| `/alerts/alert/<id>/resolve/` | POST | Mark as resolved |
| `/alerts/send/` | GET, POST | Send direct message |
| `/alerts/send/bulk/` | GET, POST | Send bulk message |
| `/alerts/api/unread-count/` | GET | Get unread count (JSON) |

---

**Version**: 1.0  
**Last Updated**: 2026-05-03  
**Author**: Poultry IQ Development Team
