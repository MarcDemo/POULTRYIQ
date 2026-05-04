from django.urls import path
from . import views

app_name = 'alerts'

urlpatterns = [
    # Main inbox
    path('inbox/', views.alerts_inbox, name='inbox'),
    
    # Alert detail and actions
    path('alert/<int:alert_id>/', views.alert_detail, name='detail'),
    path('alert/<int:alert_id>/read/', views.mark_as_read, name='mark_as_read'),
    path('alert/<int:alert_id>/acknowledge/', views.mark_as_acknowledged, name='mark_as_acknowledged'),
    path('alert/<int:alert_id>/resolve/', views.mark_as_resolved, name='mark_as_resolved'),
    
    # Sending messages
    path('send/', views.send_message, name='send_message'),
    path('send/bulk/', views.send_bulk_message, name='send_bulk_message'),
    
    # AJAX endpoints
    path('api/unread-count/', views.get_unread_count, name='api_unread_count'),
    path('api/recent/', views.get_recent_alerts, name='api_recent'),
]
