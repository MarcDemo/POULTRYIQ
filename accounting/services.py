"""
Accounting utilities and services for expense allocation and financial calculations.
This module provides shared services used across expenses, payroll, and other financial modules.
"""

from decimal import Decimal
from django.db.models import Sum
from expenses.models import ExpenseAllocation, ExpenseTransaction


def _transaction_amount(source):
    for field_name in ("total_amount", "line_total", "amount", "amount_allocated"):
        if hasattr(source, field_name):
            value = getattr(source, field_name)
            if value is not None:
                return value
    return Decimal('0.00')


def allocate_expense_to_batch(expense, batch, method, allocation_amount, percent=None, rationale=""):
    """
    Allocate an expense transaction to a specific poultry batch.
    
    Args:
        expense: ExpenseTransaction instance
        batch: PoultryBatch instance
        method: Allocation method ('DIRECT', 'PERCENT', 'MANUAL')
        allocation_amount: Amount to allocate (Decimal)
        percent: Percentage (if using PERCENT method)
        rationale: Description of allocation logic
        
    Returns:
        ExpenseAllocation instance
    """
    allocation = ExpenseAllocation.objects.create(
        expense=expense,
        batch=batch,
        method=method,
        amount_allocated=allocation_amount,
        percent=percent,
        rationale=rationale,
    )
    return allocation


def calculate_batch_profitability(batch):
    """
    Calculate profitability metrics for a poultry batch.
    
    Args:
        batch: PoultryBatch instance
        
    Returns:
        dict with profitability data:
        {
            'batch_id': batch.id,
            'batch_name': batch.name,
            'total_cost_of_revenue': Decimal,
            'total_allocated_expenses': Decimal,
            'unallocated_overhead': Decimal,
            'total_cost': Decimal,
            'revenue': Decimal,
            'profit': Decimal,
            'profit_margin_percent': float,
        }
    """
    # Get all expense allocations for this batch
    allocations = ExpenseAllocation.objects.filter(batch=batch).select_related('expense')
    
    total_allocated_expenses = allocations.aggregate(
        total=Sum('amount_allocated')
    )['total'] or Decimal('0.00')
    
    # Cost of revenue expenses for this batch
    cost_of_revenue = allocations.filter(
        expense__category__expense_type='COST_OF_REVENUE'
    ).aggregate(total=Sum('amount_allocated'))['total'] or Decimal('0.00')
    
    # Get revenue from sales (if available)
    from sales.models import SaleInvoice, SaleItem
    revenue = Decimal('0.00')
    # This is a placeholder - adjust based on your actual sales model structure
    try:
        # Example: assume SaleInvoice or SaleItem is linked to batches
        # Adjust the query based on your actual model relationships
        pass
    except:
        pass
    
    profit = revenue - total_allocated_expenses if revenue > Decimal('0.00') else -total_allocated_expenses
    profit_margin = (profit / revenue * 100) if revenue > Decimal('0.00') else Decimal('0.00')
    
    return {
        'batch_id': batch.id,
        'batch_name': str(batch),
        'total_cost_of_revenue': cost_of_revenue,
        'total_allocated_expenses': total_allocated_expenses,
        'revenue': revenue,
        'profit': profit,
        'profit_margin_percent': float(profit_margin),
    }


def get_monthly_expense_summary(year, month):
    """
    Get a summary of monthly expenses categorized by type.
    
    Args:
        year: Year (e.g., 2026)
        month: Month (1-12)
        
    Returns:
        dict with expense summary:
        {
            'cost_of_revenue': Decimal,
            'depreciation': Decimal,
            'monthly_expenses': Decimal,
            'total': Decimal,
        }
    """
    expenses = ExpenseTransaction.objects.filter(
        period_year=year,
        period_month=month,
        status=ExpenseTransaction.Status.APPROVED
    )
    
    summary = {
        'cost_of_revenue': expenses.filter(
            category__expense_type='COST_OF_REVENUE'
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00'),
        'depreciation': expenses.filter(
            category__expense_type='DEPRECIATION'
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00'),
        'monthly_expenses': expenses.filter(
            category__expense_type='MONTHLY_EXPENSES'
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00'),
    }
    
    summary['total'] = summary['cost_of_revenue'] + summary['depreciation'] + summary['monthly_expenses']
    
    return summary


def get_pl_data(start_date=None, end_date=None):
    """
    Retrieve P&L data aggregated by account type with accounting codes.
    
    Args:
        start_date: Optional start date (datetime.date)
        end_date: Optional end date (datetime.date)
        
    Returns:
        dict with structure:
        {
            'revenue': [
                {'code': 'SE0034', 'account_name': 'Eggs', 'type': 'REVENUE', 'amount': Decimal, 'date': date},
                ...
            ],
            'cost_of_revenue': [...],
            'depreciation': [...],
            'monthly_expenses': [...],
            'totals': {
                'revenue': Decimal,
                'cost_of_revenue': Decimal,
                'depreciation': Decimal,
                'monthly_expenses': Decimal,
                'profit_loss': Decimal,
            }
        }
    """
    from .models import AccountingCode
    from sales.models import SaleInvoice, SaleItem
    
    # Get all accounting codes within the date range
    codes_query = AccountingCode.objects.all()
    if start_date:
        codes_query = codes_query.filter(created_at__date__gte=start_date)
    if end_date:
        codes_query = codes_query.filter(created_at__date__lte=end_date)
    
    # Aggregate data by account type
    pl_data = {
        'revenue': [],
        'cost_of_revenue': [],
        'depreciation': [],
        'monthly_expenses': [],
        'totals': {
            'revenue': Decimal('0.00'),
            'cost_of_revenue': Decimal('0.00'),
            'depreciation': Decimal('0.00'),
            'monthly_expenses': Decimal('0.00'),
            'profit_loss': Decimal('0.00'),
        }
    }
    
    # Group by account type and aggregate amounts
    for code in codes_query.order_by('account_type', '-created_at'):
        # Get amount based on content type
        amount = Decimal('0.00')
        date_obj = code.created_at.date() if code.created_at else None
        
        if code.content_object:
            amount = _transaction_amount(code.content_object)
        
        entry = {
            'code': code.code,
            'account_name': code.account_name,
            'type': code.get_account_type_display(),
            'amount': amount,
            'date': date_obj,
        }
        
        # Add to appropriate category
        if code.account_type == 'REVENUE':
            pl_data['revenue'].append(entry)
            pl_data['totals']['revenue'] += amount
        elif code.account_type == 'COST_OF_REVENUE':
            pl_data['cost_of_revenue'].append(entry)
            pl_data['totals']['cost_of_revenue'] += amount
        elif code.account_type == 'DEPRECIATION':
            pl_data['depreciation'].append(entry)
            pl_data['totals']['depreciation'] += amount
        elif code.account_type == 'MONTHLY_EXPENSES':
            pl_data['monthly_expenses'].append(entry)
            pl_data['totals']['monthly_expenses'] += amount
    
    # Calculate profit/loss
    total_expenses = (
        pl_data['totals']['cost_of_revenue'] +
        pl_data['totals']['depreciation'] +
        pl_data['totals']['monthly_expenses']
    )
    pl_data['totals']['profit_loss'] = pl_data['totals']['revenue'] - total_expenses
    
    return pl_data
