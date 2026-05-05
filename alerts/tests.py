from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Alert, AlertType, AlertTemplate, AlertSchedule
from accounts.models import Role

User = get_user_model()


class AlertModelTests(TestCase):
    """Test the Alert model"""
    
    def setUp(self):
        """Set up test data"""
        # Create roles
        self.worker_role = Role.objects.create(
            code='WORKER',
            name='Farm Worker'
        )
        self.supervisor_role = Role.objects.create(
            code='SUPERVISOR',
            name='Farm Supervisor'
        )
        
        # Create users
        self.worker = User.objects.create_user(
            username='worker1',
            email='worker@farm.com',
            password='testpass123',
            role=self.worker_role
        )
        
        self.supervisor = User.objects.create_user(
            username='supervisor1',
            email='supervisor@farm.com',
            password='testpass123',
            role=self.supervisor_role
        )
        
        # Create alert type
        self.alert_type = AlertType.objects.create(
            code='SYSTEM',
            name='System Alert'
        )
    
    def test_create_system_alert(self):
        """Test creating a system alert"""
        alert = Alert.objects.create(
            alert_type=self.alert_type,
            title='Test Alert',
            message='This is a test alert',
            receiver=self.worker,
            priority='MEDIUM',
            status='UNREAD'
        )
        
        self.assertEqual(alert.title, 'Test Alert')
        self.assertEqual(alert.status, 'UNREAD')
        self.assertIsNone(alert.sender)
        self.assertTrue(alert.is_system_alert)
    
    def test_mark_as_read(self):
        """Test marking alert as read"""
        alert = Alert.objects.create(
            alert_type=self.alert_type,
            title='Test Alert',
            message='This is a test alert',
            receiver=self.worker,
            status='UNREAD'
        )
        
        alert.mark_as_read()
        alert.refresh_from_db()
        
        self.assertEqual(alert.status, 'READ')
        self.assertIsNotNone(alert.read_at)
    
    def test_create_direct_message(self):
        """Test creating a direct message from supervisor to worker"""
        alert_type = AlertType.objects.create(
            code='DIRECT',
            name='Direct Message'
        )
        
        alert = Alert.objects.create(
            alert_type=alert_type,
            title='Task for you',
            message='Please change bedding in House A',
            sender=self.supervisor,
            receiver=self.worker,
            priority='HIGH',
            status='UNREAD'
        )
        
        self.assertEqual(alert.sender, self.supervisor)
        self.assertFalse(alert.is_system_alert)


class AlertSendViewTests(TestCase):
    def setUp(self):
        self.worker_role = Role.objects.create(
            code=Role.RoleCode.WORKER,
            name='Farm Worker'
        )
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name='Farm Manager'
        )

        self.worker = User.objects.create_user(
            username='receiver-worker',
            email='receiver@farm.com',
            password='testpass123',
            role=self.worker_role
        )
        self.manager = User.objects.create_user(
            username='sender-manager',
            email='manager@farm.com',
            password='testpass123',
            role=self.manager_role
        )

    def test_send_message_creates_direct_alert_type_if_missing(self):
        self.client.force_login(self.manager)

        self.assertFalse(
            AlertType.objects.filter(code=AlertType.AlertTypeCode.DIRECT).exists()
        )

        response = self.client.post(
            reverse('alerts:send_message'),
            {
                'receiver': self.worker.id,
                'title': 'Task update',
                'message': 'Please check feeders in house A.',
                'priority': Alert.Priority.MEDIUM,
            },
        )

        self.assertRedirects(response, reverse('alerts:inbox'))
        self.assertTrue(
            AlertType.objects.filter(code=AlertType.AlertTypeCode.DIRECT).exists()
        )

        alert = Alert.objects.get(title='Task update')
        self.assertEqual(alert.sender, self.manager)
        self.assertEqual(alert.receiver, self.worker)
        self.assertEqual(alert.alert_type.code, AlertType.AlertTypeCode.DIRECT)
