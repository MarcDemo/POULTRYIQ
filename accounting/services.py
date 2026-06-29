"""
Accounting utilities and services for expense allocation and financial calculations.
This module provides shared services used across expenses, payroll, and other financial modules.
"""

from decimal import Decimal
from datetime import timedelta
from django.db.models import Sum
from expenses.models import ExpenseAllocation, ExpenseTransaction


OTHER_EXPENSE_CATEGORY_CODES = {"OTHER", "LOSS_ON_DISPOSAL", "DONATIONS"}


def _as_decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _clamp(value, lower, upper):
    return max(lower, min(value, upper))


def _asset_accumulated_depreciation(asset, as_of_date):
    if not asset.is_depreciable or not asset.in_service_date or not asset.useful_life_years:
        return Decimal('0.00')
    if as_of_date < asset.in_service_date:
        return Decimal('0.00')

    base = _as_decimal(asset.amount) - _as_decimal(asset.residual_value)
    if base <= Decimal('0.00'):
        return Decimal('0.00')

    try:
        life_end = asset.in_service_date.replace(year=asset.in_service_date.year + asset.useful_life_years)
    except ValueError:
        life_end = asset.in_service_date.replace(month=2, day=28, year=asset.in_service_date.year + asset.useful_life_years)

    total_days = (life_end - asset.in_service_date).days
    if total_days <= 0:
        return Decimal('0.00')

    cutoff = min(as_of_date, life_end)
    elapsed_days = (cutoff - asset.in_service_date).days
    elapsed_days = _clamp(elapsed_days, 0, total_days)
    if elapsed_days <= 0:
        return Decimal('0.00')

    depreciation = (base * Decimal(elapsed_days) / Decimal(total_days)).quantize(Decimal('0.01'))
    return min(depreciation, base)


def _asset_period_depreciation(asset, start_date, end_date):
    if end_date < start_date:
        return Decimal('0.00')
    prev_day = start_date - timedelta(days=1)
    return (_asset_accumulated_depreciation(asset, end_date) - _asset_accumulated_depreciation(asset, prev_day)).quantize(Decimal('0.01'))


def _pl_class_for_account_type(account_type):
    if account_type in {"REVENUE", "OTHER_INCOME"}:
        return "INCOME"
    return "EXPENSE"


def _is_other_expense_account_name(account_name):
    normalized = (account_name or "").strip().lower()
    return any(
        token in normalized
        for token in ("loss_on_disposal", "donation", "other_expense", "expense_of_other")
    )


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
            'expenses': Decimal,
            'other_expenses': Decimal,
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
        'expenses': expenses.filter(
            category__expense_type='MONTHLY_EXPENSES'
        ).exclude(
            category__code__in=OTHER_EXPENSE_CATEGORY_CODES
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00'),
        'other_expenses': expenses.filter(
            category__expense_type='MONTHLY_EXPENSES',
            category__code__in=OTHER_EXPENSE_CATEGORY_CODES,
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00'),
    }

    summary['total'] = (
        summary['cost_of_revenue']
        + summary['depreciation']
        + summary['expenses']
        + summary['other_expenses']
    )
    # Backward-compatible key used by older callers.
    summary['monthly_expenses'] = summary['expenses']
    
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
            'income': [
                {'code': 'SE0034', 'account_name': 'Eggs', 'type': 'REVENUE', 'amount': Decimal, 'date': date},
                ...
            ],
            'other_income': [...],
            'cost_of_revenue': [...],
            'depreciation': [...],
            'expenses': [...],
            'other_expenses': [...],
            'totals': {
                'income': Decimal,
                'other_income': Decimal,
                'cost_of_revenue': Decimal,
                'depreciation': Decimal,
                'expenses': Decimal,
                'other_expenses': Decimal,
                'total_income': Decimal,
                'total_expenses': Decimal,
                'gross_profit': Decimal,
                'profit_loss': Decimal,
            }
        }
    """
    from .models import AccountingCode, FixedAssetAcquisition
    
    # Get all accounting codes within the date range
    codes_query = AccountingCode.objects.all()
    if start_date:
        codes_query = codes_query.filter(created_at__date__gte=start_date)
    if end_date:
        codes_query = codes_query.filter(created_at__date__lte=end_date)
    
    # Aggregate data by account type
    pl_data = {
        'income': [],
        'other_income': [],
        'cost_of_revenue': [],
        'depreciation': [],
        'expenses': [],
        'other_expenses': [],
        'totals': {
            'income': Decimal('0.00'),
            'other_income': Decimal('0.00'),
            'cost_of_revenue': Decimal('0.00'),
            'depreciation': Decimal('0.00'),
            'expenses': Decimal('0.00'),
            'other_expenses': Decimal('0.00'),
            'total_income': Decimal('0.00'),
            'total_expenses': Decimal('0.00'),
            'gross_profit': Decimal('0.00'),
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
            'class': _pl_class_for_account_type(code.account_type),
            'amount': amount,
            'date': date_obj,
        }
        
        # Add to appropriate category
        if code.account_type == 'REVENUE':
            pl_data['income'].append(entry)
            pl_data['totals']['income'] += amount
        elif code.account_type == 'OTHER_INCOME':
            pl_data['other_income'].append(entry)
            pl_data['totals']['other_income'] += amount
        elif code.account_type == 'COST_OF_REVENUE':
            pl_data['cost_of_revenue'].append(entry)
            pl_data['totals']['cost_of_revenue'] += amount
        elif code.account_type == 'DEPRECIATION':
            pl_data['depreciation'].append(entry)
            pl_data['totals']['depreciation'] += amount
        elif code.account_type == 'EXPENSES':
            pl_data['expenses'].append(entry)
            pl_data['totals']['expenses'] += amount
        elif code.account_type == 'OTHER_EXPENSES':
            pl_data['other_expenses'].append(entry)
            pl_data['totals']['other_expenses'] += amount
        elif code.account_type == 'MONTHLY_EXPENSES':
            # Legacy account type: split into Expenses vs Other Expenses by account name.
            if _is_other_expense_account_name(code.account_name):
                pl_data['other_expenses'].append(entry)
                pl_data['totals']['other_expenses'] += amount
            else:
                pl_data['expenses'].append(entry)
                pl_data['totals']['expenses'] += amount

    # Straight-line depreciation from fixed assets.
    dep_start = start_date
    dep_end = end_date
    if dep_start is None and dep_end is None:
        dep_end = None
    elif dep_start is None:
        dep_start = dep_end
    elif dep_end is None:
        dep_end = dep_start

    if dep_start and dep_end:
        assets = FixedAssetAcquisition.objects.filter(is_active=True, is_depreciable=True)
        for asset in assets:
            period_dep = _asset_period_depreciation(asset, dep_start, dep_end)
            if period_dep > 0:
                pl_data['depreciation'].append(
                    {
                        'code': f'DEP-FA-{asset.pk}',
                        'account_name': f'depreciation_of_{asset.asset_name.lower().replace(" ", "_")}',
                        'type': 'Depreciation',
                        'class': 'EXPENSE',
                        'amount': period_dep,
                        'date': dep_end,
                    }
                )
                pl_data['totals']['depreciation'] += period_dep

    total_income = pl_data['totals']['income'] + pl_data['totals']['other_income']
    total_expenses = (
        pl_data['totals']['cost_of_revenue'] +
        pl_data['totals']['depreciation'] +
        pl_data['totals']['expenses'] +
        pl_data['totals']['other_expenses']
    )

    pl_data['totals']['total_income'] = total_income
    pl_data['totals']['total_expenses'] = total_expenses
    pl_data['totals']['gross_profit'] = total_income - pl_data['totals']['cost_of_revenue']
    pl_data['totals']['profit_loss'] = total_income - total_expenses

    # Backward-compatible keys used by existing callers/templates.
    pl_data['revenue'] = pl_data['income']
    pl_data['monthly_expenses'] = pl_data['expenses']
    pl_data['totals']['revenue'] = pl_data['totals']['income']
    pl_data['totals']['monthly_expenses'] = pl_data['totals']['expenses']
    
    return pl_data


def get_bs_data(start_date=None, end_date=None):
    """Retrieve Balance Sheet data from transaction-backed sources."""
    from .models import (
        BalanceSheetAccount,
        FixedAssetAcquisition,
        AssetConstructionProject,
    )
    from inventory.models import InventoryTransaction
    from hr.models import WelfareRequest
    from payroll.models import SalaryPayment
    from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice

    accounts_query = BalanceSheetAccount.objects.filter(is_active=True).order_by('group', 'account_type', 'code')

    account_amounts = {}

    inv_in = InventoryTransaction.objects.filter(tx_type='IN')
    inv_out = InventoryTransaction.objects.filter(tx_type='OUT')
    if start_date:
        inv_in = inv_in.filter(tx_date__gte=start_date)
        inv_out = inv_out.filter(tx_date__gte=start_date)
    if end_date:
        inv_in = inv_in.filter(tx_date__lte=end_date)
        inv_out = inv_out.filter(tx_date__lte=end_date)
    purchase_refs = ExpenseTransaction.objects.exclude(item_name="").values_list("expense_id", flat=True)
    purchase_refs = [f"EXP-{expense_id}" for expense_id in purchase_refs]
    purchase_inventory = inv_in.filter(reference__in=purchase_refs).select_related("item")

    current_asset_rows = []
    for tx in purchase_inventory.order_by("tx_date", "tx_id"):
        amount = tx.quantity * tx.unit_price if tx.unit_price else Decimal("0.00")
        supplier_label = f" - {tx.supplier_name}" if tx.supplier_name else ""
        current_asset_rows.append(
            {
                "code": f"CA-INV-{tx.tx_id}",
                "account_name": f"Inventory Purchase / {tx.item.name} ({tx.quantity} {tx.item.unit}){supplier_label}",
                "type": "Current Asset",
                "amount": amount,
                "is_active": True,
            }
        )

    in_amount = Decimal('0.00')
    out_amount = Decimal('0.00')
    for tx in inv_in:
        if tx.unit_price:
            in_amount += tx.quantity * tx.unit_price
    for tx in inv_out:
        if tx.unit_price:
            out_amount += tx.quantity * tx.unit_price
    account_amounts['321001'] = in_amount - out_amount

    credit_expenses = ExpenseTransaction.objects.filter(
        payment_method=ExpenseTransaction.PAYMENT_CREDIT,
        status=ExpenseTransaction.Status.APPROVED,
    )
    if start_date:
        credit_expenses = credit_expenses.filter(expense_date__gte=start_date)
    if end_date:
        credit_expenses = credit_expenses.filter(expense_date__lte=end_date)
    account_amounts['431001'] = credit_expenses.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    open_receivables = ReceivableLedger.objects.filter(
        balance__gt=0,
    ).exclude(
        invoice__status=SaleInvoice.Status.CANCELLED,
    ).select_related("invoice", "invoice__customer")
    if start_date:
        open_receivables = open_receivables.filter(invoice__invoice_date__gte=start_date)
    if end_date:
        open_receivables = open_receivables.filter(invoice__invoice_date__lte=end_date)
    trade_debtor_rows = [
        {
            "code": f"REC-{ledger.invoice.invoice_no}",
            "account_name": f"Trade Debtor / {ledger.invoice.invoice_no} - {ledger.invoice.customer.name}",
            "type": "Receivable",
            "amount": ledger.balance,
            "is_active": True,
        }
        for ledger in open_receivables.order_by("invoice__invoice_date", "invoice__invoice_id")
    ]

    customer_payments = CustomerPayment.objects.exclude(
        invoice__status=SaleInvoice.Status.CANCELLED,
    )
    if start_date:
        customer_payments = customer_payments.filter(payment_date__gte=start_date)
    if end_date:
        customer_payments = customer_payments.filter(payment_date__lte=end_date)

    payment_account_codes = {
        CustomerPayment.Method.CASH: "351008",
        CustomerPayment.Method.MOMO: "351005",
        CustomerPayment.Method.BANK: "351002",
        CustomerPayment.Method.OTHER: "351007",
        CustomerPayment.Method.CREDIT: "331001",
    }
    for method, code in payment_account_codes.items():
        if code == "331001":
            continue
        account_amounts[code] = customer_payments.filter(method=method).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    staff_advance_qs = WelfareRequest.objects.filter(
        request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
        status=WelfareRequest.Status.MANAGER_APPROVED,
        salary_payment__isnull=True,
        advance_amount__gt=0,
    ).select_related("worker")
    if start_date:
        staff_advance_qs = staff_advance_qs.filter(manager_reviewed_at__date__gte=start_date)
    if end_date:
        staff_advance_qs = staff_advance_qs.filter(manager_reviewed_at__date__lte=end_date)
    staff_advance_rows = [
        {
            "code": f"ADV-{advance.request_id}",
            "account_name": f"Staff Advance / {advance.worker.display_name}",
            "type": "Receivable",
            "amount": advance.advance_amount or Decimal("0.00"),
            "is_active": True,
        }
        for advance in staff_advance_qs.order_by("manager_reviewed_at", "request_id")
    ]

    salary_payable_qs = SalaryPayment.objects.filter(status=SalaryPayment.Status.PENDING).select_related("employee")
    if start_date:
        salary_payable_qs = salary_payable_qs.filter(payment_date__gte=start_date)
    if end_date:
        salary_payable_qs = salary_payable_qs.filter(payment_date__lte=end_date)
    salary_payable_rows = [
        {
            "code": f"SAL-PAY-{salary.salary_id}",
            "account_name": f"Salaries Payable / {salary.period_month} - {salary.employee.display_name}",
            "type": "Current Liability",
            "amount": salary.amount,
            "is_active": True,
        }
        for salary in salary_payable_qs.order_by("payment_date", "salary_id")
    ]

    wht_invoices = SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED).filter(
        wht_amount__gt=0,
    ).select_related("customer")
    if start_date:
        wht_invoices = wht_invoices.filter(invoice_date__gte=start_date)
    if end_date:
        wht_invoices = wht_invoices.filter(invoice_date__lte=end_date)
    wht_receivable_rows = [
        {
            "code": f"WHT-{invoice.invoice_no}",
            "account_name": f"WHT Receivable / {invoice.invoice_no} - {invoice.customer.name}",
            "type": "Receivable",
            "amount": invoice.wht_amount,
            "is_active": True,
        }
        for invoice in wht_invoices.order_by("invoice_date", "invoice_id")
    ]

    fixed_asset_rows = []
    accumulated_depreciation_rows = []

    acquisitions = FixedAssetAcquisition.objects.filter(is_active=True)
    if start_date:
        acquisitions = acquisitions.filter(acquisition_date__gte=start_date)
    if end_date:
        acquisitions = acquisitions.filter(acquisition_date__lte=end_date)
    for asset in acquisitions.order_by('acquisition_date', 'id'):
        fixed_asset_rows.append(
            {
                'code': f'FA-AQ-{asset.pk}',
                'account_name': f'{asset.get_asset_category_display()} / {asset.asset_name}',
                'type': 'Fixed Asset',
                'amount': asset.amount,
                'is_active': True,
            }
        )

    dep_assets = FixedAssetAcquisition.objects.filter(is_active=True, is_depreciable=True)
    as_of_date = end_date
    if as_of_date is None:
        from django.utils import timezone
        as_of_date = timezone.now().date()
    for asset in dep_assets:
        acc_dep = _asset_accumulated_depreciation(asset, as_of_date)
        if acc_dep > 0:
            accumulated_depreciation_rows.append(
                {
                    'code': f'AD-FA-{asset.pk}',
                    'account_name': f'Accumulated depreciation-{asset.asset_name}',
                    'type': 'Accumulated Depreciation',
                    'amount': acc_dep,
                    'is_active': True,
                }
            )

    in_progress_projects = AssetConstructionProject.objects.filter(status=AssetConstructionProject.Status.IN_PROGRESS)
    for project in in_progress_projects:
        cost_lines = project.cost_lines.all()
        if start_date:
            cost_lines = cost_lines.filter(cost_date__gte=start_date)
        if end_date:
            cost_lines = cost_lines.filter(cost_date__lte=end_date)
        total = cost_lines.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        if total > 0:
            fixed_asset_rows.append(
                {
                    'code': f'FA-AUC-{project.pk}',
                    'account_name': f'Asset Construction / {project.project_name}',
                    'type': 'Fixed Asset',
                    'amount': total,
                    'is_active': True,
                }
            )

    grouped_accounts = []
    for group_value, group_label in BalanceSheetAccount.Group.choices:
        type_groups = []
        group_total = Decimal('0.00')
        group_accounts = accounts_query.filter(group=group_value)

        for type_value, type_label in BalanceSheetAccount.AccountType.choices:
            if group_value == BalanceSheetAccount.Group.ASSETS and type_value == BalanceSheetAccount.AccountType.FIXED_ASSET:
                accounts_list = list(fixed_asset_rows)
            elif group_value == BalanceSheetAccount.Group.ASSETS and type_value == BalanceSheetAccount.AccountType.ACCUMULATED_DEPRECIATION:
                accounts_list = list(accumulated_depreciation_rows)
            elif group_value == BalanceSheetAccount.Group.ASSETS and type_value == BalanceSheetAccount.AccountType.CURRENT_ASSET:
                accounts_list = list(current_asset_rows)
                type_accounts = group_accounts.filter(account_type=type_value).exclude(code="321001")
                for account in type_accounts:
                    accounts_list.append(
                        {
                            'code': account.code,
                            'account_name': account.account_name,
                            'type': account.get_account_type_display(),
                            'amount': account_amounts.get(account.code, Decimal('0.00')),
                            'is_active': account.is_active,
                        }
                    )
            elif group_value == BalanceSheetAccount.Group.ASSETS and type_value == BalanceSheetAccount.AccountType.RECEIVABLE:
                accounts_list = list(trade_debtor_rows) + list(staff_advance_rows) + list(wht_receivable_rows)
                type_accounts = group_accounts.filter(account_type=type_value).exclude(code__in=["331001", "331003", "331004"])
                for account in type_accounts:
                    accounts_list.append(
                        {
                            'code': account.code,
                            'account_name': account.account_name,
                            'type': account.get_account_type_display(),
                            'amount': account_amounts.get(account.code, Decimal('0.00')),
                            'is_active': account.is_active,
                        }
                    )
            elif group_value == BalanceSheetAccount.Group.LIABILITIES and type_value == BalanceSheetAccount.AccountType.CURRENT_LIABILITY:
                accounts_list = list(salary_payable_rows)
                type_accounts = group_accounts.filter(account_type=type_value).exclude(code="421010")
                for account in type_accounts:
                    accounts_list.append(
                        {
                            'code': account.code,
                            'account_name': account.account_name,
                            'type': account.get_account_type_display(),
                            'amount': account_amounts.get(account.code, Decimal('0.00')),
                            'is_active': account.is_active,
                        }
                    )
            else:
                accounts_list = []
                type_accounts = group_accounts.filter(account_type=type_value)
                for account in type_accounts:
                    accounts_list.append(
                        {
                            'code': account.code,
                            'account_name': account.account_name,
                            'type': account.get_account_type_display(),
                            'amount': account_amounts.get(account.code, Decimal('0.00')),
                            'is_active': account.is_active,
                        }
                    )

            always_show = (
                group_value == BalanceSheetAccount.Group.ASSETS
                and type_value in {
                    BalanceSheetAccount.AccountType.FIXED_ASSET,
                    BalanceSheetAccount.AccountType.ACCUMULATED_DEPRECIATION,
                }
            )
            if accounts_list or always_show:
                type_total = sum((row['amount'] for row in accounts_list), Decimal('0.00'))
                group_total += type_total
                type_groups.append(
                    {
                        'type_label': type_label,
                        'type_value': type_value,
                        'accounts': accounts_list,
                        'type_total': type_total,
                    }
                )

        if type_groups:
            grouped_accounts.append(
                {
                    'group_label': group_label,
                    'group_value': group_value,
                    'type_groups': type_groups,
                    'group_total': group_total,
                }
            )

    return {'grouped_accounts': grouped_accounts}
