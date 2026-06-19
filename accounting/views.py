from datetime import datetime
from decimal import Decimal

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.utils import timezone

from .services import get_pl_data
from accounts.models import Role


@login_required
def chart_of_accounts(request):
    """
    Display Chart of Accounts (P&L Report) with accounting codes.
    Accessible to managers only.
    """
    # Check if user is a manager
    if not hasattr(request.user, 'role') or (request.user.role and request.user.role.code != 'MANAGER'):
        if not request.user.is_superuser:
            from django.contrib import messages
            messages.error(request, "Access denied: Managers only.")
            return render(request, 'access_denied.html', status=403)
    
    # Get date range from request
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')
    
    # Parse dates
    today = timezone.now().date()
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = None
    else:
        start_date = None
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = None
    else:
        end_date = None
    
    # Get P&L data
    pl_data = get_pl_data(start_date, end_date)
    
    context = {
        'pl_data': pl_data,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'start_date_obj': start_date,
        'end_date_obj': end_date,
        'today': today,
    }
    
    return render(request, 'accounting/chart_of_accounts.html', context)
