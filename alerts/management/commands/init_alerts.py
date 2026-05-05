from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from accounts.models import User, Role
from alerts.models import AlertType, Alert, AlertTemplate, AlertSchedule
from poultry.models import PoultryHouse


class Command(BaseCommand):
    help = 'Initialize alert types, templates, and demo alerts'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Initializing alerts system...'))
        
        # Create alert types
        alert_types = [
            {'code': AlertType.AlertTypeCode.SYSTEM, 'name': 'System Alert'},
            {'code': AlertType.AlertTypeCode.DIRECT, 'name': 'Direct Message'},
            {'code': AlertType.AlertTypeCode.MAINTENANCE, 'name': 'Maintenance Task'},
            {'code': AlertType.AlertTypeCode.HEALTH, 'name': 'Health Alert'},
            {'code': AlertType.AlertTypeCode.COMPLIANCE, 'name': 'Compliance Alert'},
        ]
        
        for alert_type_data in alert_types:
            alert_type, created = AlertType.objects.get_or_create(
                code=alert_type_data['code'],
                defaults={'name': alert_type_data['name']}
            )
            if created:
                self.stdout.write(f'✓ Created alert type: {alert_type.name}')
            else:
                self.stdout.write(f'  Alert type already exists: {alert_type.name}')
        
        # Create alert templates
        templates = [
            {
                'code': 'BEDDING_CHANGE',
                'name': 'Bedding Change Due',
                'alert_type': 'MAINTENANCE',
                'title_template': 'Bedding change required - {house}',
                'message_template': 'The bedding in {house} needs to be changed. Please schedule this task as soon as possible. Refresh the bedding to maintain bird health and reduce disease risk.',
                'priority': 'HIGH',
                'frequency_days': 14,
            },
            {
                'code': 'WATER_SYSTEM_CHECK',
                'name': 'Water System Check',
                'alert_type': 'MAINTENANCE',
                'title_template': 'Weekly water system inspection - {house}',
                'message_template': 'Check water system in {house}:\n• Verify water quality and clarity\n• Check for leaks in lines\n• Ensure all nipples are working\n• Sanitize water tanks if needed',
                'priority': 'MEDIUM',
                'frequency_days': 7,
            },
            {
                'code': 'FEED_CHECK',
                'name': 'Feed Stock Check',
                'alert_type': 'SYSTEM',
                'title_template': 'Check feed inventory - {house}',
                'message_template': 'Verify feed stock levels in {house}. Order new feed if stock is running low. Current stock level should be at least 3 days of feed.',
                'priority': 'MEDIUM',
                'frequency_days': 3,
            },
            {
                'code': 'EQUIPMENT_INSPECT',
                'name': 'Equipment Inspection',
                'alert_type': 'MAINTENANCE',
                'title_template': 'Equipment inspection due - {house}',
                'message_template': 'Monthly equipment inspection for {house}:\n• Check feeders for damage\n• Inspect drinkers for functionality\n• Verify ventilation system operation\n• Check for any rust or corrosion',
                'priority': 'MEDIUM',
                'frequency_days': 30,
            },
            {
                'code': 'HEALTH_CHECK',
                'name': 'Health Status Check',
                'alert_type': 'HEALTH',
                'title_template': 'Health check reminder - {house}',
                'message_template': 'Perform health check in {house}:\n• Observe bird behavior and movement\n• Check for signs of illness or injury\n• Monitor for respiratory issues\n• Report any abnormalities',
                'priority': 'HIGH',
                'frequency_days': 1,
            },
        ]
        
        for template_data in templates:
            alert_type = AlertType.objects.get(code=template_data.pop('alert_type'))
            template_data['alert_type'] = alert_type
            
            template, created = AlertTemplate.objects.get_or_create(
                code=template_data['code'],
                defaults=template_data
            )
            if created:
                self.stdout.write(f'✓ Created template: {template.name}')
            else:
                self.stdout.write(f'  Template already exists: {template.name}')
        
        # Create demo alerts if workers exist
        workers = User.objects.filter(role__code='WORKER', is_active=True)[:3]
        
        if workers:
            self.stdout.write(self.style.SUCCESS('\nCreating demo alerts...'))
            
            # Get alert types
            system_alert_type = AlertType.objects.get(code='SYSTEM')
            direct_alert_type = AlertType.objects.get(code='DIRECT')
            maint_alert_type = AlertType.objects.get(code='MAINTENANCE')
            health_alert_type = AlertType.objects.get(code='HEALTH')
            
            demo_alerts = []
            
            # System alert to first worker
            if workers:
                alert = Alert.objects.create(
                    alert_type=system_alert_type,
                    title='System: Welcome to Alerts',
                    message='Welcome to the new alerts and messaging system! This is where you will receive important messages from your supervisor and system notifications about maintenance tasks.',
                    receiver=workers[0],
                    priority='MEDIUM',
                    status='UNREAD',
                )
                demo_alerts.append(alert)
                self.stdout.write(f'✓ Created system alert for {workers[0].display_name}')
            
            # Maintenance alert
            if len(workers) > 1:
                house = PoultryHouse.objects.filter(is_active=True).first()
                alert = Alert.objects.create(
                    alert_type=maint_alert_type,
                    title='Bedding change required - House A',
                    message='The bedding in House A needs to be changed today. This is important for maintaining bird health and reducing disease risk. Please complete this task before end of shift.',
                    receiver=workers[1],
                    priority='HIGH',
                    status='UNREAD',
                    due_date=timezone.now() + timedelta(hours=4),
                    related_house=house,
                )
                demo_alerts.append(alert)
                self.stdout.write(f'✓ Created maintenance alert for {workers[1].display_name}')
            
            # Health alert
            if len(workers) > 2:
                alert = Alert.objects.create(
                    alert_type=health_alert_type,
                    title='Perform health check',
                    message='Morning health check for all birds:\n• Observe general movement and behavior\n• Check for any signs of illness\n• Count any mortalities overnight\n• Report findings to supervisor',
                    receiver=workers[2],
                    priority='HIGH',
                    status='UNREAD',
                    due_date=timezone.now() + timedelta(hours=1),
                )
                demo_alerts.append(alert)
                self.stdout.write(f'✓ Created health alert for {workers[2].display_name}')
            
            # Supervisor alert to worker
            supervisor = User.objects.filter(role__code='SUPERVISOR', is_active=True).first()
            if supervisor and workers:
                alert = Alert.objects.create(
                    alert_type=direct_alert_type,
                    title='Direct Message: Task assignment',
                    message='Please ensure water system in House B is cleaned and sanitized today. Check all nipples are functioning correctly. Let me know when completed.',
                    receiver=workers[0],
                    sender=supervisor,
                    priority='HIGH',
                    status='UNREAD',
                    due_date=timezone.now() + timedelta(hours=6),
                )
                demo_alerts.append(alert)
                self.stdout.write(f'✓ Created direct message from supervisor to {workers[0].display_name}')
        
        self.stdout.write(self.style.SUCCESS('\n✅ Alerts system initialized successfully!'))
        self.stdout.write('\nNext steps:')
        self.stdout.write('1. Log in as a worker to see demo alerts in /alerts/inbox/')
        self.stdout.write('2. Log in as supervisor to send messages to workers')
        self.stdout.write('3. Use Django admin to configure alert templates and schedules')
