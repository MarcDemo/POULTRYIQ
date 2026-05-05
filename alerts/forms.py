from django import forms
from .models import Alert, AlertTemplate


class SendMessageForm(forms.ModelForm):
    """Form for sending direct messages"""

    receiver = forms.ModelChoiceField(
        queryset=None,  # Will be set in view
        widget=forms.Select(attrs={'class': 'form-control'}),
        label="Send to"
    )

    class Meta:
        model = Alert
        fields = ['title', 'message', 'priority', 'due_date']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Message title'
            }),
            'message': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 6,
                'placeholder': 'Type your message here...'
            }),
            'priority': forms.Select(attrs={'class': 'form-control'}),
            'due_date': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }, format='%Y-%m-%dT%H:%M'),
        }
        labels = {
            'title': 'Subject',
            'message': 'Message',
            'priority': 'Priority',
            'due_date': 'Due Date (Optional)',
        }

    def __init__(self, *args, sender=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sender = sender
        
        # Only show workers/relevant users (not the sender)
        from accounts.models import User, Role
        workers = User.objects.filter(
            is_active=True,
            role__code__in=['WORKER', 'SUPERVISOR']
        ).exclude(id=sender.id if sender else None)
        self.fields['receiver'].queryset = workers


class RespondToAlertForm(forms.ModelForm):
    """Form for acknowledging/responding to alerts"""

    class Meta:
        model = Alert
        fields = ['status']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-control'})
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only allow certain status transitions
        self.fields['status'].choices = [
            (Alert.Status.READ, 'Mark as Read'),
            (Alert.Status.ACKNOWLEDGED, 'Acknowledge'),
            (Alert.Status.RESOLVED, 'Resolved'),
        ]
