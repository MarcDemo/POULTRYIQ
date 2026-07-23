"""
Accounting utilities and services for expense allocation and financial calculations.
This module provides shared services used across expenses, payroll, and other financial modules.
"""

from decimal import Decimal
from calendar import monthrange
from datetime import date, timedelta
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from expenses.models import ExpenseAllocation, ExpenseTransaction


OTHER_EXPENSE_CATEGORY_CODES = {"OTHER", "LOSS_ON_DISPOSAL", "DONATIONS"}


def _as_decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _money(value):
    return _as_decimal(value).quantize(Decimal("0.01"))


def _source_filter(source):
    return {
        "content_type": ContentType.objects.get_for_model(source),
        "object_id": source.pk,
    }


def has_journal_entry(source):
    from .models import JournalEntry

    if not source or not source.pk:
        return False
    return JournalEntry.objects.filter(**_source_filter(source), status=JournalEntry.Status.POSTED).exists()


def has_posted_depreciation(asset):
    from .models import JournalEntry

    if not asset or not asset.pk:
        return False
    return JournalEntry.objects.filter(
        reference__startswith=f"DEP-FA-{asset.pk}-",
        status=JournalEntry.Status.POSTED,
    ).exists()


def _first_account_by_legacy_type(legacy_code):
    from .models import ChartOfAccount

    return (
        ChartOfAccount.objects.select_related("account_type")
        .filter(account_type__legacy_code=legacy_code, is_active=True)
        .order_by("code")
        .first()
    )


def _first_account_by_code_or_name(code=None, name_contains=None, legacy_code=None):
    from .models import ChartOfAccount

    queryset = ChartOfAccount.objects.select_related("account_type").filter(is_active=True)
    if code:
        account = queryset.filter(code=code).first()
        if account:
            return account
    if name_contains:
        account = queryset.filter(account_name__icontains=name_contains).order_by("code").first()
        if account:
            return account
    if legacy_code:
        return queryset.filter(account_type__legacy_code=legacy_code).order_by("code").first()
    return None


def account_by_system_code(system_code):
    from .models import ChartOfAccount

    return (
        ChartOfAccount.objects.select_related("account_type")
        .filter(system_code=system_code, is_active=True)
        .first()
    )


def _payment_account(method):
    from .models import PaymentMethod

    raw = (method or "").strip()
    normalized = raw.upper().replace("_", " ")
    aliases = {
        "CASH": "CASH",
        "MOMO": "MOBILE MONEY",
        "MOBILE MONEY": "MOBILE MONEY",
        "BANK": "BANK",
        "CHECK": "CHECK",
        "CHEQUE": "CHECK",
        "OTHER": "CHECK",
    }
    canonical = aliases.get(normalized, normalized)
    candidates = [raw, normalized, canonical]
    for candidate in candidates:
        payment_method = (
            PaymentMethod.objects.select_related("account", "account__account_type")
            .filter(name__iexact=candidate, is_active=True)
            .first()
        )
        if payment_method:
            return payment_method.account

    system_codes = {
        "CASH": "PAYMENT_CASH",
        "MOBILE MONEY": "PAYMENT_MOBILE_MONEY",
        "BANK": "PAYMENT_BANK",
        "CHECK": "PAYMENT_CHECK",
    }
    return account_by_system_code(system_codes.get(canonical))


def payment_account_for_method(method):
    return _payment_account(method)


def _require_account(account, message):
    if not account:
        raise ValidationError(message)
    return account


def _get_or_create_account_type(*, legacy_code, name, nature):
    from .models import AccountType

    account_type = AccountType.objects.filter(legacy_code=legacy_code).first()
    if account_type:
        changed = False
        if account_type.account_nature != nature:
            account_type.account_nature = nature
            changed = True
        if not account_type.is_active:
            account_type.is_active = True
            changed = True
        if changed:
            account_type.save(update_fields=["account_nature", "is_active", "updated_at"])
        return account_type

    account_type, created = AccountType.objects.get_or_create(
        name=name,
        defaults={
            "account_nature": nature,
            "legacy_code": legacy_code,
            "is_active": True,
        },
    )
    if not created and account_type.legacy_code != legacy_code:
        account_type.legacy_code = legacy_code
        account_type.account_nature = nature
        account_type.is_active = True
        account_type.save(update_fields=["legacy_code", "account_nature", "is_active", "updated_at"])
    return account_type


def _get_or_create_chart_account(*, system_code, account_name, account_type, description=""):
    from .models import ChartOfAccount

    account = ChartOfAccount.objects.filter(system_code=system_code).first()
    attach_system_code = False
    if not account:
        account = ChartOfAccount.objects.filter(
            system_code__isnull=True,
            account_name__iexact=account_name,
            account_type=account_type,
        ).first()
        if account:
            account.system_code = system_code
            attach_system_code = True
    if account:
        changed = attach_system_code
        if account.system_code != system_code:
            account.system_code = system_code
            changed = True
        if account.account_type_id != account_type.pk:
            account.account_type = account_type
            changed = True
        if account.account_name != account_name:
            account.account_name = account_name
            changed = True
        if description and account.description != description:
            account.description = description
            changed = True
        if not account.is_active:
            account.is_active = True
            changed = True
        if changed:
            account.save()
        return account

    return ChartOfAccount.objects.create(
        system_code=system_code,
        account_name=account_name,
        account_type=account_type,
        description=description,
        is_active=True,
    )


def ensure_depreciation_accounts(asset_category):
    from .models import AccountType
    from expenses.models import ExpenseCategory

    if not asset_category or not asset_category.pk:
        raise ValidationError("Asset category is required before depreciation can be recorded.")
    if not asset_category.asset_account_id:
        raise ValidationError(f"{asset_category.name} needs an asset account before depreciation can be recorded.")

    depreciation_type = _get_or_create_account_type(
        legacy_code="DEPRECIATION",
        name="Depreciation",
        nature=AccountType.AccountNature.EXPENSE,
    )
    accumulated_type = _get_or_create_account_type(
        legacy_code="ACCUMULATED_DEPRECIATION",
        name="Accumulated Depreciation",
        nature=AccountType.AccountNature.ASSET,
    )

    affected_account = asset_category.asset_account.account_name
    depreciation_account = _get_or_create_chart_account(
        system_code=f"DEP_EXPENSE_ASSET_CATEGORY_{asset_category.pk}",
        account_name=f"Dep of {affected_account}",
        account_type=depreciation_type,
        description=f"Monthly straight-line depreciation for {affected_account}.",
    )
    accumulated_account = _get_or_create_chart_account(
        system_code=f"ACC_DEP_ASSET_CATEGORY_{asset_category.pk}",
        account_name=f"Accumulated Depreciation - {affected_account}",
        account_type=accumulated_type,
        description=f"Accumulated depreciation contra asset for {affected_account}.",
    )

    changed_fields = []
    if asset_category.depreciation_expense_account_id != depreciation_account.pk:
        asset_category.depreciation_expense_account = depreciation_account
        changed_fields.append("depreciation_expense_account")
    if asset_category.accumulated_depreciation_account_id != accumulated_account.pk:
        asset_category.accumulated_depreciation_account = accumulated_account
        changed_fields.append("accumulated_depreciation_account")
    if changed_fields:
        changed_fields.append("updated_at")
        asset_category.save(update_fields=changed_fields)

    expense_category, created = ExpenseCategory.objects.get_or_create(
        code=f"DEP_FA_{asset_category.pk}",
        defaults={
            "name": depreciation_account.account_name,
            "account": depreciation_account,
            "expense_type": ExpenseCategory.ExpenseType.DEPRECIATION,
            "is_active": True,
        },
    )
    changed = False
    if expense_category.name != depreciation_account.account_name:
        expense_category.name = depreciation_account.account_name
        changed = True
    if expense_category.account_id != depreciation_account.pk:
        expense_category.account = depreciation_account
        changed = True
    if expense_category.expense_type != ExpenseCategory.ExpenseType.DEPRECIATION:
        expense_category.expense_type = ExpenseCategory.ExpenseType.DEPRECIATION
        changed = True
    if not expense_category.is_active:
        expense_category.is_active = True
        changed = True
    if changed and not created:
        expense_category.save(update_fields=["name", "account", "expense_type", "is_active"])

    return depreciation_account, accumulated_account, expense_category


def ensure_payroll_accounts():
    from .models import AccountType, BalanceSheetAccount, ChartOfAccount

    payroll_expense_type = _get_or_create_account_type(
        legacy_code="PAYROLL_EXPENSE",
        name="Payroll Expense",
        nature=AccountType.AccountNature.EXPENSE,
    )
    payroll_prepayment_type = _get_or_create_account_type(
        legacy_code="PAYROLL_PREPAYMENT",
        name="Payroll Prepayments",
        nature=AccountType.AccountNature.ASSET,
    )
    payroll_liability_type = _get_or_create_account_type(
        legacy_code="PAYROLL_PAYABLE",
        name="Payroll Payable",
        nature=AccountType.AccountNature.LIABILITY,
    )
    cash_type = _get_or_create_account_type(
        legacy_code="BANK_AND_CASH",
        name="Bank and Cash",
        nature=AccountType.AccountNature.ASSET,
    )

    for code, account_name, group, account_type, allow_reconciliation in [
        ("341002", "Prepaid Salaries", BalanceSheetAccount.Group.ASSETS, BalanceSheetAccount.AccountType.PREPAYMENT, True),
        ("421010", "Salaries_payable", BalanceSheetAccount.Group.LIABILITIES, BalanceSheetAccount.AccountType.CURRENT_LIABILITY, True),
        ("421012", "PAYE_payable", BalanceSheetAccount.Group.LIABILITIES, BalanceSheetAccount.AccountType.CURRENT_LIABILITY, True),
        ("421016", "NSSF_Payable", BalanceSheetAccount.Group.LIABILITIES, BalanceSheetAccount.AccountType.CURRENT_LIABILITY, True),
    ]:
        BalanceSheetAccount.objects.update_or_create(
            code=code,
            defaults={
                "account_name": account_name,
                "group": group,
                "account_type": account_type,
                "allow_reconciliation": allow_reconciliation,
                "is_active": True,
            },
        )

    def payroll_balance_sheet_chart_account(*, system_code, code, account_name, account_type, description="", allow_reconciliation=True):
        system_account = ChartOfAccount.objects.filter(system_code=system_code).first()
        code_account = ChartOfAccount.objects.filter(code=code).first()
        if system_account and code_account and system_account.pk != code_account.pk:
            system_account.system_code = None
            system_account.save(update_fields=["system_code", "updated_at"])
            account = code_account
        else:
            account = system_account or code_account
        if account:
            changed = False
            for field_name, value in [
                ("system_code", system_code),
                ("code", code),
                ("account_name", account_name),
                ("account_type", account_type),
                ("description", description),
                ("allow_reconciliation", allow_reconciliation),
                ("is_active", True),
            ]:
                current = getattr(account, field_name)
                current_id = getattr(current, "pk", current)
                value_id = getattr(value, "pk", value)
                if current_id != value_id:
                    setattr(account, field_name, value)
                    changed = True
            if changed:
                account.save()
            return account
        return ChartOfAccount.objects.create(
            system_code=system_code,
            code=code,
            account_name=account_name,
            account_type=account_type,
            description=description,
            allow_reconciliation=allow_reconciliation,
            is_active=True,
        )

    prepaid_salaries = payroll_balance_sheet_chart_account(
        system_code="PAYROLL_PREPAID_SALARIES",
        code="341002",
        account_name="Prepaid Salaries",
        account_type=payroll_prepayment_type,
        description="Salary advances paid before the payroll period is earned.",
    )

    accounts = {
        "salary_expense": _get_or_create_chart_account(
            system_code="PAYROLL_SALARY_EXPENSE",
            account_name="Salary Expense",
            account_type=payroll_expense_type,
            description="Gross salaries earned by staff.",
        ),
        "bonus_expense": _get_or_create_chart_account(
            system_code="PAYROLL_BONUS_EXPENSE",
            account_name="Bonus Expense",
            account_type=payroll_expense_type,
            description="Staff bonuses earned.",
        ),
        "employer_nssf_expense": _get_or_create_chart_account(
            system_code="PAYROLL_EMPLOYER_NSSF_EXPENSE",
            account_name="Employer NSSF Expense",
            account_type=payroll_expense_type,
            description="Employer NSSF contribution expense.",
        ),
        "prepaid_salaries": prepaid_salaries,
        "paye_payable": payroll_balance_sheet_chart_account(
            system_code="PAYROLL_PAYE_PAYABLE",
            code="421012",
            account_name="PAYE Payable",
            account_type=payroll_liability_type,
            description="PAYE withheld from salaries and payable to URA.",
        ),
        "nssf_payable": payroll_balance_sheet_chart_account(
            system_code="PAYROLL_NSSF_PAYABLE",
            code="421016",
            account_name="NSSF Payable",
            account_type=payroll_liability_type,
            description="Employee and employer NSSF payable.",
        ),
        "salary_payable": payroll_balance_sheet_chart_account(
            system_code="PAYROLL_SALARY_PAYABLE",
            code="421010",
            account_name="Salary Payable",
            account_type=payroll_liability_type,
            description="Net salaries payable to staff.",
        ),
        "cash": payroll_balance_sheet_chart_account(
            system_code="PAYMENT_CASH",
            code="351008",
            account_name="Cash on Hand",
            account_type=cash_type,
            description="Cash used for staff welfare and payroll payments.",
            allow_reconciliation=False,
        ),
    }
    accounts["staff_advances"] = prepaid_salaries
    return accounts


def post_salary_advance(welfare_request, *, created_by=None):
    from hr.models import WelfareRequest

    if welfare_request.request_type != WelfareRequest.RequestType.SALARY_ADVANCE:
        return None
    amount = _money(welfare_request.advance_amount)
    if amount <= 0:
        return None

    accounts = ensure_payroll_accounts()
    worker_name = getattr(welfare_request.worker, "display_name", str(welfare_request.worker))
    description = f"Salary advance to {worker_name}"
    return post_journal_entry(
        entry_date=(welfare_request.manager_reviewed_at.date() if welfare_request.manager_reviewed_at else date.today()),
        reference=f"ADV-{welfare_request.pk}",
        description=description,
        lines=[
            {"account": accounts["prepaid_salaries"], "debit": amount, "memo": description},
            {"account": accounts["cash"], "credit": amount, "memo": description},
        ],
        source=welfare_request,
        created_by=created_by or welfare_request.manager,
    )


def _salary_breakdown_amounts(salary):
    gross_salary = _money(salary.gross_salary or salary.amount)
    paye_tax = _money(salary.paye_tax)
    nssf_employee = _money(salary.nssf_employee)
    nssf_employer = _money(salary.nssf_employer)
    advances = _money(salary.advances_deducted)
    bonuses = _money(salary.bonus_amount)
    calculated_net_pay = _money(max(gross_salary + bonuses - nssf_employee - paye_tax - advances, Decimal("0.00")))
    net_pay = _money(salary.net_pay or salary.amount or calculated_net_pay)
    if net_pay != calculated_net_pay:
        net_pay = calculated_net_pay
    return {
        "gross_salary": gross_salary,
        "paye_tax": paye_tax,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "advances": advances,
        "bonuses": bonuses,
        "net_pay": net_pay,
    }


def post_salary_accrual(salary, *, created_by=None):
    accounts = ensure_payroll_accounts()
    amounts = _salary_breakdown_amounts(salary)
    gross_salary = amounts["gross_salary"]
    paye_tax = amounts["paye_tax"]
    nssf_employee = amounts["nssf_employee"]
    nssf_employer = amounts["nssf_employer"]
    advances = amounts["advances"]
    bonuses = amounts["bonuses"]
    net_pay = amounts["net_pay"]

    if not any([gross_salary, paye_tax, nssf_employee, nssf_employer, advances, bonuses, net_pay]):
        return None

    employee_name = getattr(salary.employee, "display_name", str(salary.employee))
    description = f"Salary accrued for {employee_name} - {salary.period_month}"
    lines = []
    if gross_salary > 0:
        lines.append({"account": accounts["salary_expense"], "debit": gross_salary, "memo": "Gross salary"})
    if bonuses > 0:
        lines.append({"account": accounts["bonus_expense"], "debit": bonuses, "memo": "Bonus"})
    if nssf_employer > 0:
        lines.append({"account": accounts["employer_nssf_expense"], "debit": nssf_employer, "memo": "Employer NSSF"})
    if advances > 0:
        lines.append({"account": accounts["prepaid_salaries"], "credit": advances, "memo": "Salary advance recovered"})
    if paye_tax > 0:
        lines.append({"account": accounts["paye_payable"], "credit": paye_tax, "memo": "PAYE withheld"})
    total_nssf = nssf_employee + nssf_employer
    if total_nssf > 0:
        lines.append({"account": accounts["nssf_payable"], "credit": total_nssf, "memo": "NSSF payable"})
    if net_pay > 0:
        lines.append({"account": accounts["salary_payable"], "credit": net_pay, "memo": "Net salary payable"})

    entry = post_journal_entry(
        entry_date=salary.payment_date or date.today(),
        reference=f"SAL-ACCRUAL-{salary.pk}",
        description=description,
        lines=lines,
        source=salary,
        created_by=created_by or salary.recorded_by,
        dedupe_source=False,
    )

    linked_advances = getattr(salary, "deducted_advances", None)
    if linked_advances is not None:
        linked_advances.filter(salary_payment__isnull=True).update(salary_payment=salary)
    return entry


def post_salary_payment(salary, *, created_by=None):
    accounts = ensure_payroll_accounts()
    amounts = _salary_breakdown_amounts(salary)
    net_pay = amounts["net_pay"]
    if net_pay <= 0:
        return None

    employee_name = getattr(salary.employee, "display_name", str(salary.employee))
    description = f"Salary paid to {employee_name} - {salary.period_month}"
    return post_journal_entry(
        entry_date=salary.payment_date or date.today(),
        reference=f"SAL-PAY-{salary.pk}",
        description=description,
        lines=[
            {"account": accounts["salary_payable"], "debit": net_pay, "memo": "Clear salary payable"},
            {"account": accounts["cash"], "credit": net_pay, "memo": "Net salary paid"},
        ],
        source=salary,
        created_by=created_by or salary.recorded_by,
        dedupe_source=False,
    )


def _month_bounds(year, month):
    year = int(year)
    month = int(month)
    if month < 1 or month > 12:
        raise ValidationError("Month must be between 1 and 12.")
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _previous_month(value):
    if value.month == 1:
        return value.year - 1, 12
    return value.year, value.month - 1


@transaction.atomic
def record_monthly_depreciation(*, year=None, month=None, created_by=None):
    from django.utils import timezone
    from .models import AccountingCode, FixedAssetAcquisition, JournalEntry

    today = timezone.localdate()
    year = int(year or today.year)
    month = int(month or today.month)
    period_start, period_end = _month_bounds(year, month)

    summary = {
        "year": year,
        "month": month,
        "posted": 0,
        "skipped": 0,
        "total_amount": Decimal("0.00"),
        "entries": [],
        "errors": [],
    }

    assets = (
        FixedAssetAcquisition.objects.select_related(
            "asset_category",
            "asset_category__asset_account",
            "asset_category__depreciation_expense_account",
            "asset_category__accumulated_depreciation_account",
            "created_by",
        )
        .filter(is_active=True, is_depreciable=True)
        .exclude(asset_category__is_land=True)
    )

    for asset in assets.order_by("in_service_date", "id"):
        if not asset.in_service_date or asset.in_service_date > period_end:
            summary["skipped"] += 1
            continue

        reference = f"DEP-FA-{asset.pk}-{year}{month:02d}"
        if JournalEntry.objects.filter(reference=reference).exists():
            summary["skipped"] += 1
            continue

        amount = _asset_period_depreciation(asset, period_start, period_end)
        if amount <= 0:
            summary["skipped"] += 1
            continue

        try:
            depreciation_account, accumulated_account, expense_category = ensure_depreciation_accounts(asset.asset_category)
        except ValidationError as exc:
            summary["errors"].append(str(exc))
            continue

        description = f"Dep of {asset.asset_category.asset_account.account_name} - {asset.asset_name}"
        expense = ExpenseTransaction.objects.create(
            expense_date=period_end,
            category=expense_category,
            account=depreciation_account,
            description=description,
            reference_no=reference,
            total_amount=amount,
            payment_method="",
            period_year=year,
            period_month=month,
            status=ExpenseTransaction.Status.APPROVED,
            created_by=created_by or asset.created_by,
        )
        AccountingCode.create_for(
            account=depreciation_account,
            content_object=expense,
            description=description,
        )
        entry = post_journal_entry(
            entry_date=period_end,
            reference=reference,
            description=description,
            lines=[
                {"account": depreciation_account, "debit": amount, "memo": description},
                {"account": accumulated_account, "credit": amount, "memo": description},
            ],
            source=expense,
            created_by=created_by or asset.created_by,
        )
        summary["posted"] += 1
        summary["total_amount"] += amount
        summary["entries"].append(
            {
                "reference": entry.reference,
                "asset": asset.asset_name,
                "amount": amount,
                "depreciation_account": depreciation_account.account_name,
                "accumulated_account": accumulated_account.account_name,
            }
        )

    summary["total_amount"] = _money(summary["total_amount"])
    return summary


def record_due_monthly_depreciation(*, as_of_date=None, created_by=None):
    from django.utils import timezone
    from .models import FixedAssetAcquisition

    today = as_of_date or timezone.localdate()
    previous_year, previous_month = _previous_month(today)
    final_month_start = date(previous_year, previous_month, 1)

    first_asset_date = (
        FixedAssetAcquisition.objects.filter(is_active=True, is_depreciable=True)
        .exclude(asset_category__is_land=True)
        .exclude(in_service_date__isnull=True)
        .filter(in_service_date__lte=date(previous_year, previous_month, monthrange(previous_year, previous_month)[1]))
        .order_by("in_service_date")
        .values_list("in_service_date", flat=True)
        .first()
    )
    if not first_asset_date:
        return {
            "posted": 0,
            "skipped": 0,
            "total_amount": Decimal("0.00"),
            "months_checked": 0,
            "errors": [],
        }

    cursor = date(first_asset_date.year, first_asset_date.month, 1)
    summary = {
        "posted": 0,
        "skipped": 0,
        "total_amount": Decimal("0.00"),
        "months_checked": 0,
        "errors": [],
    }

    while cursor <= final_month_start:
        month_summary = record_monthly_depreciation(
            year=cursor.year,
            month=cursor.month,
            created_by=created_by,
        )
        summary["posted"] += month_summary["posted"]
        summary["skipped"] += month_summary["skipped"]
        summary["total_amount"] += month_summary["total_amount"]
        summary["months_checked"] += 1
        summary["errors"].extend(month_summary["errors"])

        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    summary["total_amount"] = _money(summary["total_amount"])
    return summary


@transaction.atomic
def post_journal_entry(*, entry_date, reference, description, lines, source=None, created_by=None, dedupe_source=True):
    from .models import JournalEntry, JournalLine

    existing_reference = JournalEntry.objects.filter(reference=reference, status=JournalEntry.Status.POSTED).first()
    if existing_reference:
        return existing_reference

    if dedupe_source and source and source.pk:
        existing = JournalEntry.objects.filter(**_source_filter(source), status=JournalEntry.Status.POSTED).first()
        if existing:
            return existing

    clean_lines = []
    for line in lines:
        debit = _money(line.get("debit", 0))
        credit = _money(line.get("credit", 0))
        account = line.get("account")
        if debit < 0 or credit < 0:
            raise ValidationError("Journal amounts cannot be negative.")
        if debit and credit:
            raise ValidationError("A journal line cannot have both debit and credit.")
        if not debit and not credit:
            continue
        _require_account(account, "Every journal line needs an account.")
        clean_lines.append(
            {
                "account": account,
                "debit": debit,
                "credit": credit,
                "memo": line.get("memo", ""),
            }
        )

    total_debits = sum((line["debit"] for line in clean_lines), Decimal("0.00"))
    total_credits = sum((line["credit"] for line in clean_lines), Decimal("0.00"))
    if not clean_lines or total_debits != total_credits:
        raise ValidationError("Journal entry must balance before it can be posted.")

    kwargs = _source_filter(source) if source and source.pk else {"content_type": None, "object_id": None}
    entry = JournalEntry.objects.create(
        entry_date=entry_date,
        reference=reference,
        description=description,
        created_by=created_by,
        **kwargs,
    )
    JournalLine.objects.bulk_create(
        [
            JournalLine(
                entry=entry,
                account=line["account"],
                debit=line["debit"],
                credit=line["credit"],
                memo=line["memo"],
            )
            for line in clean_lines
        ]
    )
    return entry


def post_sale(sale_item, *, amount_paid=None, created_by=None):
    invoice = sale_item.invoice
    revenue_account = _require_account(
        sale_item.account or (sale_item.category.account if sale_item.category_id else None),
        f"Sale category is not mapped to a ledger account for {sale_item.product_name}.",
    )
    paid = _money(amount_paid if amount_paid is not None else 0)
    sale_amount = _money(getattr(sale_item, "line_total", 0))
    wht_amount = _money(getattr(invoice, "wht_amount", 0))
    customer_payable_amount = sale_amount - wht_amount
    if customer_payable_amount < 0:
        raise ValidationError("Withholding tax cannot be more than the sale amount.")
    if paid > customer_payable_amount:
        raise ValidationError("Amount paid cannot be more than the customer amount due after withholding tax.")
    customer_receivable_amount = sale_amount - wht_amount - paid
    if customer_receivable_amount < 0:
        customer_receivable_amount = Decimal("0.00")
    wht_receivable_amount = wht_amount if wht_amount > 0 else Decimal("0.00")
    receivable_amount = customer_receivable_amount + wht_receivable_amount
    if receivable_amount < 0:
        receivable_amount = Decimal("0.00")

    lines = []
    if invoice.delivery_status == invoice.DeliveryStatus.PENDING:
        if paid <= 0:
            return None
        payment_account = _require_account(
            _payment_account(invoice.payment_method),
            f"Payment method {invoice.payment_method} is not mapped to a ledger account.",
        )
        prepaid_account = _require_account(
            account_by_system_code("PREPAID_ORDERS"),
            "The built-in Prepaid Orders account is missing.",
        )
        return post_journal_entry(
            entry_date=invoice.invoice_date,
            reference=f"SALE-{invoice.invoice_no}-{sale_item.pk}-DEPOSIT",
            description=f"Prepaid order for {sale_item.product_name}",
            lines=[
                {"account": payment_account, "debit": paid, "memo": "Amount received before delivery"},
                {"account": prepaid_account, "credit": paid, "memo": "Prepaid order"},
            ],
            source=sale_item,
            created_by=created_by or invoice.created_by,
        )

    if paid > 0:
        payment_account = _require_account(
            _payment_account(invoice.payment_method),
            f"Payment method {invoice.payment_method} is not mapped to a ledger account.",
        )
        lines.append({"account": payment_account, "debit": paid, "memo": "Amount received"})
    if customer_receivable_amount > 0:
        lines.append(
            {
                "account": _require_account(
                    account_by_system_code("CREDIT_ORDERS") or _first_account_by_legacy_type("RECEIVABLE"),
                    "No credit orders or receivable account is configured.",
                ),
                "debit": customer_receivable_amount,
                "memo": "Customer receivable",
            }
        )
    if wht_receivable_amount > 0:
        lines.append(
            {
                "account": _require_account(
                    _first_account_by_code_or_name("331004", "WHT", "RECEIVABLE"),
                    "No WHT receivable account is configured.",
                ),
                "debit": wht_receivable_amount,
                "memo": "Withholding tax receivable",
            }
        )
    lines.append({"account": revenue_account, "credit": sale_amount, "memo": sale_item.product_name})
    return post_journal_entry(
        entry_date=invoice.invoice_date,
        reference=f"SALE-{invoice.invoice_no}-{sale_item.pk}",
        description=f"Sale of {sale_item.product_name}",
        lines=lines,
        source=sale_item,
        created_by=created_by or invoice.created_by,
    )


def post_sale_delivery(invoice, *, created_by=None):
    receivable_account = _require_account(
        account_by_system_code("CREDIT_ORDERS") or _first_account_by_legacy_type("RECEIVABLE"),
        "No credit orders or receivable account is configured.",
    )
    prepaid_account = _require_account(
        account_by_system_code("PREPAID_ORDERS"),
        "The built-in Prepaid Orders account is missing.",
    )
    lines = []
    paid_remaining = _money(getattr(invoice.receivable, "amount_paid", 0) if hasattr(invoice, "receivable") else 0)
    for item in invoice.items.select_related("account", "category", "category__account"):
        revenue_account = _require_account(
            item.account or (item.category.account if item.category_id else None),
            f"Sale account is missing for {item.product_name}.",
        )
        amount = _money(item.line_total)
        prepaid_amount = min(paid_remaining, amount)
        credit_amount = amount - prepaid_amount
        if prepaid_amount > 0:
            lines.append({"account": prepaid_account, "debit": prepaid_amount, "memo": item.product_name})
            paid_remaining -= prepaid_amount
        if credit_amount > 0:
            lines.append({"account": receivable_account, "debit": credit_amount, "memo": item.product_name})
        lines.append({"account": revenue_account, "credit": amount, "memo": item.product_name})

    if not lines:
        return None
    return post_journal_entry(
        entry_date=invoice.invoice_date,
        reference=f"DEL-{invoice.invoice_no}",
        description=f"Delivered order {invoice.invoice_no}",
        lines=lines,
        source=invoice,
        created_by=created_by or invoice.created_by,
    )


def post_customer_payment(payment, *, created_by=None):
    credit_account = _require_account(
        account_by_system_code("PREPAID_ORDERS")
        if payment.invoice.delivery_status == payment.invoice.DeliveryStatus.PENDING
        else account_by_system_code("CREDIT_ORDERS") or _first_account_by_legacy_type("RECEIVABLE"),
        "No prepaid orders, credit orders, or receivable account is configured.",
    )
    payment_account = _require_account(
        _payment_account(payment.method),
        f"Payment method {payment.method} is not mapped to a ledger account.",
    )
    amount = _money(payment.amount)
    return post_journal_entry(
        entry_date=payment.payment_date,
        reference=f"PAY-{payment.payment_id}",
        description=f"Customer payment for {payment.invoice.invoice_no}",
        lines=[
            {"account": payment_account, "debit": amount, "memo": payment.reference or "Customer payment"},
            {"account": credit_account, "credit": amount, "memo": payment.invoice.invoice_no},
        ],
        source=payment,
        created_by=created_by or payment.received_by,
    )


def post_expense(expense, *, created_by=None):
    expense_account = _require_account(
        expense.account or (expense.category.account if expense.category_id else None),
        f"Expense {expense.description} is not mapped to a ledger account.",
    )
    amount = _money(expense.total_amount)
    lines = [{"account": expense_account, "debit": amount, "memo": expense.description}]

    if expense.payment_method == ExpenseTransaction.PAYMENT_CREDIT:
        credit_account = _require_account(_first_account_by_legacy_type("PAYABLE"), "No payable account is configured.")
    else:
        credit_account = _require_account(
            _payment_account(expense.payment_method),
            f"Payment method {expense.payment_method} is not mapped to a ledger account.",
        )
    lines.append({"account": credit_account, "credit": amount, "memo": expense.description})
    return post_journal_entry(
        entry_date=expense.expense_date,
        reference=f"EXP-{expense.expense_id}",
        description=expense.description,
        lines=lines,
        source=expense,
        created_by=created_by or expense.created_by,
    )


def post_asset_purchase(asset, *, created_by=None):
    asset_account = _require_account(asset.asset_category.asset_account, f"Asset category {asset.asset_category} has no asset account.")
    if (asset.payment_method or "").strip().upper() == "CREDIT":
        payment_account = _require_account(_first_account_by_legacy_type("PAYABLE"), "No payable account is configured.")
    else:
        payment_account = _require_account(
            _payment_account(asset.payment_method),
            f"Payment method {asset.payment_method} is not mapped to a ledger account.",
        )
    amount = _money(asset.amount)
    return post_journal_entry(
        entry_date=asset.acquisition_date,
        reference=f"FA-{asset.pk}",
        description=f"Fixed asset purchase: {asset.asset_name}",
        lines=[
            {"account": asset_account, "debit": amount, "memo": asset.asset_name},
            {"account": payment_account, "credit": amount, "memo": asset.payment_method},
        ],
        source=asset,
        created_by=created_by or asset.created_by,
    )


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


def _statement_account_natures(statement_code, fallback):
    from .models import FinancialStatementAccountNature

    natures = list(
        FinancialStatementAccountNature.objects.filter(
            statement__code=statement_code,
            statement__is_active=True,
            is_active=True,
        ).values_list("account_nature", flat=True)
    )
    return natures or list(fallback)


def _normal_balance_amount(nature, debit, credit):
    from .models import AccountType

    debit = _as_decimal(debit)
    credit = _as_decimal(credit)
    if nature in {AccountType.AccountNature.ASSET, AccountType.AccountNature.EXPENSE}:
        return debit - credit
    return credit - debit


def _journal_lines_for_natures(natures, start_date=None, end_date=None):
    from .models import JournalEntry, JournalLine

    lines = JournalLine.objects.select_related("entry", "account", "account__account_type").filter(
        entry__status=JournalEntry.Status.POSTED,
        account__account_type__account_nature__in=natures,
    )
    if start_date:
        lines = lines.filter(entry__entry_date__gte=start_date)
    if end_date:
        lines = lines.filter(entry__entry_date__lte=end_date)
    return lines


def get_trial_balance_data(start_date=None, end_date=None):
    from .models import AccountType, ChartOfAccount

    natures = _statement_account_natures(
        "TRIAL_BALANCE",
        [
            AccountType.AccountNature.ASSET,
            AccountType.AccountNature.LIABILITY,
            AccountType.AccountNature.EQUITY,
            AccountType.AccountNature.INCOME,
            AccountType.AccountNature.EXPENSE,
        ],
    )
    accounts = ChartOfAccount.objects.select_related("account_type").filter(
        is_active=True,
        account_type__account_nature__in=natures,
    ).order_by("account_type__account_nature", "account_type__name", "code")

    totals_by_account = {}
    for line in _journal_lines_for_natures(natures, start_date, end_date):
        row = totals_by_account.setdefault(
            line.account_id,
            {"debit": Decimal("0.00"), "credit": Decimal("0.00")},
        )
        row["debit"] += _as_decimal(line.debit)
        row["credit"] += _as_decimal(line.credit)

    rows = []
    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")
    for account in accounts:
        totals = totals_by_account.get(account.pk, {"debit": Decimal("0.00"), "credit": Decimal("0.00")})
        net = _normal_balance_amount(account.account_type.account_nature, totals["debit"], totals["credit"])
        debit_nature = account.account_type.account_nature in {
            AccountType.AccountNature.ASSET,
            AccountType.AccountNature.EXPENSE,
        }
        if debit_nature:
            debit_balance = net if net >= 0 else Decimal("0.00")
            credit_balance = abs(net) if net < 0 else Decimal("0.00")
        else:
            debit_balance = abs(net) if net < 0 else Decimal("0.00")
            credit_balance = net if net >= 0 else Decimal("0.00")
        debit_balance = _money(debit_balance)
        credit_balance = _money(credit_balance)
        total_debits += debit_balance
        total_credits += credit_balance
        rows.append(
            {
                "code": account.code,
                "account_name": account.account_name,
                "account_type": account.account_type.name,
                "account_nature": account.account_type.get_account_nature_display(),
                "debit": debit_balance,
                "credit": credit_balance,
            }
        )

    return {
        "rows": rows,
        "totals": {
            "debit": _money(total_debits),
            "credit": _money(total_credits),
            "difference": _money(total_debits - total_credits),
        },
    }


def get_cash_flow_data(start_date=None, end_date=None):
    from .models import AccountType

    asset_natures = _statement_account_natures("CASH_FLOW", [AccountType.AccountNature.ASSET])
    cash_legacy_types = {"BANK_AND_CASH"}
    lines = _journal_lines_for_natures(asset_natures, start_date, end_date).filter(
        account__account_type__legacy_code__in=cash_legacy_types,
    )

    sections = {
        "inflows": {"label": "Cash In", "rows": [], "total": Decimal("0.00")},
        "outflows": {"label": "Cash Out", "rows": [], "total": Decimal("0.00")},
    }
    for line in lines.order_by("entry__entry_date", "entry__reference", "id"):
        movement = _money(line.debit - line.credit)
        if movement == 0:
            continue
        key = "inflows" if movement > 0 else "outflows"
        amount = abs(movement)
        sections[key]["rows"].append(
            {
                "date": line.entry.entry_date,
                "reference": line.entry.reference,
                "description": line.entry.description,
                "account": line.account.account_name,
                "amount": amount,
            }
        )
        sections[key]["total"] += amount

    net_cash_flow = sections["inflows"]["total"] - sections["outflows"]["total"]
    return {
        "sections": sections,
        "totals": {
            "cash_in": _money(sections["inflows"]["total"]),
            "cash_out": _money(sections["outflows"]["total"]),
            "net_cash_flow": _money(net_cash_flow),
        },
    }


def get_financial_statement_data(statement, start_date=None, end_date=None):
    from .models import AccountType, ChartOfAccount

    memberships = list(
        statement.account_natures.filter(is_active=True).order_by("display_order", "account_nature")
    )
    natures = [membership.account_nature for membership in memberships]
    nature_order = {membership.account_nature: index for index, membership in enumerate(memberships)}
    if not natures:
        return {
            "statement": statement,
            "groups": [],
            "totals": {
                "debit": Decimal("0.00"),
                "credit": Decimal("0.00"),
                "normal_balance": Decimal("0.00"),
            },
        }

    accounts = ChartOfAccount.objects.select_related("account_type").filter(
        is_active=True,
        account_type__account_nature__in=natures,
    ).order_by("account_type__account_nature", "account_type__name", "code")

    totals_by_account = {}
    for line in _journal_lines_for_natures(natures, start_date, end_date):
        row = totals_by_account.setdefault(
            line.account_id,
            {"debit": Decimal("0.00"), "credit": Decimal("0.00")},
        )
        row["debit"] += _as_decimal(line.debit)
        row["credit"] += _as_decimal(line.credit)

    groups = {}
    type_groups = {}
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    total_normal_balance = Decimal("0.00")
    for account in accounts:
        account_type = account.account_type
        nature = account_type.account_nature
        totals = totals_by_account.get(account.pk, {"debit": Decimal("0.00"), "credit": Decimal("0.00")})
        debit = _money(totals["debit"])
        credit = _money(totals["credit"])
        normal_balance = _money(_normal_balance_amount(nature, debit, credit))

        group = groups.setdefault(
            nature,
            {
                "nature": nature,
                "label": account_type.get_account_nature_display(),
                "type_groups": [],
                "debit": Decimal("0.00"),
                "credit": Decimal("0.00"),
                "normal_balance": Decimal("0.00"),
                "_order": nature_order.get(nature, 999),
            },
        )
        type_key = (nature, account_type.pk)
        type_group = type_groups.get(type_key)
        if type_group is None:
            type_group = {
                "label": account_type.name,
                "accounts": [],
                "debit": Decimal("0.00"),
                "credit": Decimal("0.00"),
                "normal_balance": Decimal("0.00"),
            }
            type_groups[type_key] = type_group
            group["type_groups"].append(type_group)

        row = {
            "code": account.code,
            "account_name": account.account_name,
            "account_type": account_type.name,
            "debit": debit,
            "credit": credit,
            "normal_balance": normal_balance,
        }
        type_group["accounts"].append(row)
        type_group["debit"] += debit
        type_group["credit"] += credit
        type_group["normal_balance"] += normal_balance
        group["debit"] += debit
        group["credit"] += credit
        group["normal_balance"] += normal_balance
        total_debit += debit
        total_credit += credit
        total_normal_balance += normal_balance

    ordered_groups = sorted(groups.values(), key=lambda row: (row["_order"], row["label"]))
    for group in ordered_groups:
        group.pop("_order", None)
        group["debit"] = _money(group["debit"])
        group["credit"] = _money(group["credit"])
        group["normal_balance"] = _money(group["normal_balance"])
        for type_group in group["type_groups"]:
            type_group["debit"] = _money(type_group["debit"])
            type_group["credit"] = _money(type_group["credit"])
            type_group["normal_balance"] = _money(type_group["normal_balance"])

    return {
        "statement": statement,
        "groups": ordered_groups,
        "totals": {
            "debit": _money(total_debit),
            "credit": _money(total_credit),
            "normal_balance": _money(total_normal_balance),
        },
    }


def _pl_bucket_for_accounting_code(code):
    if code.account_id:
        account_type = code.account.account_type
        profit_loss_natures = _statement_account_natures(
            "PROFIT_LOSS",
            [account_type.AccountNature.INCOME, account_type.AccountNature.EXPENSE],
        )
        if account_type.account_nature not in profit_loss_natures:
            return None
        if account_type.legacy_code in {"REVENUE", "OTHER_INCOME", "COST_OF_REVENUE", "DEPRECIATION", "EXPENSES", "OTHER_EXPENSES"}:
            return {
                "REVENUE": "income",
                "OTHER_INCOME": "other_income",
                "COST_OF_REVENUE": "cost_of_revenue",
                "DEPRECIATION": "depreciation",
                "EXPENSES": "expenses",
                "OTHER_EXPENSES": "other_expenses",
            }[account_type.legacy_code]
        if account_type.account_nature == account_type.AccountNature.INCOME:
            return "income"
        if account_type.account_nature == account_type.AccountNature.EXPENSE:
            return "expenses"
        return None

    if code.account_type == "REVENUE":
        return "income"
    if code.account_type == "OTHER_INCOME":
        return "other_income"
    if code.account_type == "COST_OF_REVENUE":
        return "cost_of_revenue"
    if code.account_type == "DEPRECIATION":
        return "depreciation"
    if code.account_type == "EXPENSES":
        return "expenses"
    if code.account_type == "OTHER_EXPENSES":
        return "other_expenses"
    if code.account_type == "MONTHLY_EXPENSES":
        return "other_expenses" if _is_other_expense_account_name(code.account_name) else "expenses"
    return None


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
    from .models import AccountingCode, JournalEntry, JournalLine
    
    # Get all accounting codes within the date range
    codes_query = AccountingCode.objects.select_related(
        "account",
        "account__account_type",
    )
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

    journal_lines = JournalLine.objects.select_related("entry", "account", "account__account_type").filter(
        entry__status=JournalEntry.Status.POSTED,
    )
    if start_date:
        journal_lines = journal_lines.filter(entry__entry_date__gte=start_date)
    if end_date:
        journal_lines = journal_lines.filter(entry__entry_date__lte=end_date)
    journal_lines = journal_lines.filter(
        account__account_type__account_nature__in=_statement_account_natures("PROFIT_LOSS", ["INCOME", "EXPENSE"])
    )
    for line in journal_lines.order_by("entry__entry_date", "id"):
        account_type = line.account.account_type
        nature = account_type.account_nature
        if account_type.legacy_code == "OTHER_INCOME":
            bucket = "other_income"
        elif account_type.legacy_code == "COST_OF_REVENUE":
            bucket = "cost_of_revenue"
        elif account_type.legacy_code == "DEPRECIATION":
            bucket = "depreciation"
        elif account_type.legacy_code == "OTHER_EXPENSES":
            bucket = "other_expenses"
        elif nature == account_type.AccountNature.INCOME:
            bucket = "income"
        elif nature == account_type.AccountNature.EXPENSE:
            bucket = "expenses"
        else:
            continue

        amount = (line.credit - line.debit) if nature == account_type.AccountNature.INCOME else (line.debit - line.credit)
        amount = _money(amount)
        if amount == 0:
            continue
        entry = {
            'code': line.entry.reference,
            'account_name': line.account.account_name,
            'type': account_type.name,
            'class': "INCOME" if nature == account_type.AccountNature.INCOME else "EXPENSE",
            'amount': amount,
            'date': line.entry.entry_date,
        }
        pl_data[bucket].append(entry)
        pl_data['totals'][bucket] += amount
    
    # Group by account type and aggregate amounts
    for code in codes_query.order_by('account_type', '-created_at'):
        if code.content_object and has_journal_entry(code.content_object):
            continue
        # Get amount based on content type
        amount = Decimal('0.00')
        date_obj = code.created_at.date() if code.created_at else None
        
        if code.content_object:
            amount = _transaction_amount(code.content_object)
        
        entry = {
            'code': code.code,
            'account_name': code.account_name,
            'type': code.get_account_type_display(),
            'class': (
                "INCOME"
                if code.account_id and code.account.account_type.account_nature == code.account.account_type.AccountNature.INCOME
                else _pl_class_for_account_type(code.account_type)
            ),
            'amount': amount,
            'date': date_obj,
        }

        bucket = _pl_bucket_for_accounting_code(code)
        if bucket:
            pl_data[bucket].append(entry)
            pl_data['totals'][bucket] += amount

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


def _legacy_get_bs_data(start_date=None, end_date=None):
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
                        'account_name': f'{asset.asset_category} / {asset.asset_name}',
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


def get_bs_data(start_date=None, end_date=None):
    """Retrieve Balance Sheet data from existing transaction-backed models."""
    from .models import AccountType, ChartOfAccount, FixedAssetAcquisition, JournalEntry, JournalLine, AssetConstructionProject
    from accounts.models import InvestorCapitalTransaction
    from inventory.models import InventoryTransaction
    from hr.models import WelfareRequest
    from poultry.models import ApprovalStatus, MortalityRecord, PoultryBatch
    from payroll.models import SalaryPayment
    from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem

    balance_sheet_natures = _statement_account_natures(
        "BALANCE_SHEET",
        [
            AccountType.AccountNature.ASSET,
            AccountType.AccountNature.LIABILITY,
            AccountType.AccountNature.EQUITY,
        ],
    )
    accounts_query = (
        ChartOfAccount.objects.filter(is_active=True)
        .filter(account_type__account_nature__in=balance_sheet_natures)
        .select_related("account_type")
        .order_by(
            "account_type__account_nature",
            "account_type__name",
            "code",
        )
    )
    account_amounts = {}

    def add_amount(code, amount):
        account_amounts[code] = account_amounts.get(code, Decimal("0.00")) + _as_decimal(amount)

    def period_filter(queryset, field_name):
        if start_date:
            queryset = queryset.filter(**{f"{field_name}__gte": start_date})
        if end_date:
            queryset = queryset.filter(**{f"{field_name}__lte": end_date})
        return queryset

    journal_lines = JournalLine.objects.select_related("entry", "account", "account__account_type").filter(
        entry__status=JournalEntry.Status.POSTED,
        account__account_type__account_nature__in=balance_sheet_natures,
    )
    source_backed_content_types = [
        ContentType.objects.get_for_model(SalaryPayment),
        ContentType.objects.get_for_model(WelfareRequest),
    ]
    journal_lines = journal_lines.exclude(entry__content_type__in=source_backed_content_types)
    if start_date:
        journal_lines = journal_lines.filter(entry__entry_date__gte=start_date)
    if end_date:
        journal_lines = journal_lines.filter(entry__entry_date__lte=end_date)
    for line in journal_lines:
        nature = line.account.account_type.account_nature
        if nature == AccountType.AccountNature.ASSET:
            amount = line.debit - line.credit
        else:
            amount = line.credit - line.debit
        add_amount(line.account.code, amount)

    sale_item_content_type = ContentType.objects.get_for_model(SaleItem)
    journaled_sale_item_ids = JournalEntry.objects.filter(
        content_type=sale_item_content_type,
        status=JournalEntry.Status.POSTED,
    ).values_list("object_id", flat=True)
    journaled_sale_invoice_ids = set(
        SaleItem.objects.filter(item_id__in=journaled_sale_item_ids).values_list("invoice_id", flat=True)
    )

    def remaining_prepayment_amount(amount, period_start, period_end, as_of_date):
        if not period_start or not period_end or period_end < period_start:
            return Decimal("0.00")
        amount = _as_decimal(amount)
        if as_of_date < period_start:
            return amount
        if as_of_date > period_end:
            return Decimal("0.00")
        total_days = Decimal((period_end - period_start).days + 1)
        remaining_days = Decimal((period_end - as_of_date).days + 1)
        return (amount * remaining_days / total_days).quantize(Decimal("0.01"))

    def fixed_asset_account_code(asset_category):
        return asset_category.asset_account.code if asset_category and asset_category.asset_account_id else None

    def depreciation_account_code(asset_category):
        account = asset_category.accumulated_depreciation_account if asset_category else None
        return account.code if account else None

    def cash_account_for_payment_method(method, default_bank_code="351003"):
        return {
            ExpenseTransaction.PAYMENT_CASH: "351008",
            ExpenseTransaction.PAYMENT_MOBILE: "351005",
            ExpenseTransaction.PAYMENT_BANK: default_bank_code,
            ExpenseTransaction.PAYMENT_CHECK: "351007",
            "Cash": "351008",
            "Mobile Money": "351005",
            "Bank": default_bank_code,
            "Check": "351007",
        }.get(method)

    inv_in = period_filter(
        InventoryTransaction.objects.filter(tx_type=InventoryTransaction.TxType.IN_).select_related("item", "item__category"),
        "tx_date",
    )
    inv_out = period_filter(
        InventoryTransaction.objects.filter(tx_type=InventoryTransaction.TxType.OUT).select_related("item", "item__category"),
        "tx_date",
    )
    inventory_state = {}
    for tx in inv_in.order_by("tx_date", "tx_id"):
        row = inventory_state.setdefault(
            tx.item_id,
            {
                "quantity": Decimal("0.000"),
                "unit_price": Decimal("0.00"),
                "category_code": tx.item.category.code if tx.item and tx.item.category else "",
                "item_name": tx.item.name if tx.item else "",
            },
        )
        row["quantity"] += tx.quantity
        if tx.unit_price is not None:
            row["unit_price"] = tx.unit_price
    for tx in inv_out.order_by("tx_date", "tx_id"):
        row = inventory_state.setdefault(
            tx.item_id,
            {
                "quantity": Decimal("0.000"),
                "unit_price": Decimal("0.00"),
                "category_code": tx.item.category.code if tx.item and tx.item.category else "",
                "item_name": tx.item.name if tx.item else "",
            },
        )
        row["quantity"] -= tx.quantity
        if tx.unit_price is not None:
            row["unit_price"] = tx.unit_price

    inventory_value = Decimal("0.00")
    day_old_chick_value = Decimal("0.00")
    for row in inventory_state.values():
        value = max(row["quantity"], Decimal("0.000")) * row["unit_price"]
        category_code = (row["category_code"] or "").upper()
        item_name = (row["item_name"] or "").lower()
        if category_code == "BIRDS" and ("day-old" in item_name or "day old" in item_name or "chick" in item_name):
            day_old_chick_value += value
        elif category_code != "BIRDS":
            inventory_value += value
    add_amount("321001", day_old_chick_value + inventory_value)

    batches = period_filter(PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE), "date_stocked")
    laying_birds_value = Decimal("0.00")
    for batch in batches:
        batch_cost = _as_decimal(batch.amount_paid)
        if batch_cost <= 0 or not batch.initial_quantity:
            continue
        mortality_qs = MortalityRecord.objects.filter(batch=batch, status=ApprovalStatus.APPROVED)
        if end_date:
            mortality_qs = mortality_qs.filter(record_date__lte=end_date)
        dead = mortality_qs.aggregate(total=Sum("number_dead"))["total"] or 0
        remaining = max(batch.initial_quantity - dead, 0)
        laying_birds_value += (batch_cost * Decimal(remaining) / Decimal(batch.initial_quantity)).quantize(Decimal("0.01"))
    add_amount("321002", laying_birds_value)

    acquisitions = period_filter(FixedAssetAcquisition.objects.filter(is_active=True), "acquisition_date")
    for asset in acquisitions:
        if has_journal_entry(asset):
            continue
        code = fixed_asset_account_code(asset.asset_category)
        if code:
            add_amount(code, asset.amount)

    in_progress_projects = AssetConstructionProject.objects.filter(status=AssetConstructionProject.Status.IN_PROGRESS)
    for project in in_progress_projects:
        code = fixed_asset_account_code(project.asset_category)
        if code:
            cost_lines = period_filter(project.cost_lines.all(), "cost_date")
            add_amount(code, cost_lines.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))

    as_of_date = end_date
    if as_of_date is None:
        from django.utils import timezone
        as_of_date = timezone.now().date()
    for asset in FixedAssetAcquisition.objects.filter(is_active=True, is_depreciable=True):
        dep_code = depreciation_account_code(asset.asset_category)
        if dep_code and not has_posted_depreciation(asset):
            add_amount(dep_code, _asset_accumulated_depreciation(asset, as_of_date))

    open_receivables = period_filter(
        ReceivableLedger.objects.filter(balance__gt=0)
        .exclude(invoice__status=SaleInvoice.Status.CANCELLED)
        .exclude(invoice_id__in=journaled_sale_invoice_ids)
        .select_related("invoice", "invoice__customer"),
        "invoice__invoice_date",
    )
    add_amount("331001", open_receivables.aggregate(total=Sum("balance"))["total"] or Decimal("0.00"))

    prepayment_as_of_date = end_date
    if prepayment_as_of_date is None:
        from django.utils import timezone
        prepayment_as_of_date = timezone.now().date()

    prepayment_account_codes = {
        ExpenseTransaction.PrepaymentType.ELECTRICITY: "341001",
        ExpenseTransaction.PrepaymentType.RENT: "341003",
        ExpenseTransaction.PrepaymentType.WATER: "341004",
    }
    prepayment_expenses = period_filter(
        ExpenseTransaction.objects.filter(
            is_prepayment=True,
            prepayment_start_date__isnull=False,
            prepayment_end_date__isnull=False,
        ).exclude(status=ExpenseTransaction.Status.REJECTED),
        "expense_date",
    )
    for expense in prepayment_expenses:
        if has_journal_entry(expense):
            continue
        code = prepayment_account_codes.get(expense.prepayment_type)
        if code:
            add_amount(
                code,
                remaining_prepayment_amount(
                    expense.total_amount,
                    expense.prepayment_start_date,
                    expense.prepayment_end_date,
                    prepayment_as_of_date,
                ),
            )

    salary_advances = period_filter(
        WelfareRequest.objects.filter(
            request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
            status=WelfareRequest.Status.MANAGER_APPROVED,
            salary_payment__isnull=True,
            advance_amount__gt=0,
            advance_period_start__isnull=False,
            advance_period_end__isnull=False,
        ),
        "advance_period_start",
    )
    for advance in salary_advances:
        add_amount(
            "341002",
            remaining_prepayment_amount(
                advance.advance_amount,
                advance.advance_period_start,
                advance.advance_period_end,
                prepayment_as_of_date,
            ),
        )

    wht_invoices = period_filter(
        SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED).filter(wht_amount__gt=0),
        "invoice_date",
    )
    add_amount("331004", wht_invoices.aggregate(total=Sum("wht_amount"))["total"] or Decimal("0.00"))

    customer_payments = period_filter(
        CustomerPayment.objects.exclude(invoice__status=SaleInvoice.Status.CANCELLED).exclude(invoice_id__in=journaled_sale_invoice_ids),
        "payment_date",
    )
    customer_payment_accounts = {
        CustomerPayment.Method.CASH: "351008",
        CustomerPayment.Method.MOMO: "351005",
        CustomerPayment.Method.BANK: "351002",
        CustomerPayment.Method.OTHER: "351007",
    }
    for method, code in customer_payment_accounts.items():
        add_amount(code, customer_payments.filter(method=method).aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))

    expense_outflows = period_filter(
        ExpenseTransaction.objects.exclude(status=ExpenseTransaction.Status.REJECTED),
        "expense_date",
    )
    for expense in expense_outflows:
        if has_journal_entry(expense):
            continue
        code = cash_account_for_payment_method(expense.payment_method)
        if code:
            add_amount(code, -expense.total_amount)

    for asset in acquisitions:
        if has_journal_entry(asset):
            continue
        code = cash_account_for_payment_method(asset.payment_method)
        if code:
            add_amount(code, -asset.amount)

    paid_salaries = period_filter(SalaryPayment.objects.filter(status=SalaryPayment.Status.PAID), "payment_date")
    add_amount("351008", -(paid_salaries.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")))

    credit_expenses = period_filter(
        ExpenseTransaction.objects.filter(
            payment_method=ExpenseTransaction.PAYMENT_CREDIT,
            status=ExpenseTransaction.Status.APPROVED,
        ).select_related("category"),
        "expense_date",
    )
    credit_expense_total = Decimal("0.00")
    audit_fee_total = Decimal("0.00")
    for expense in credit_expenses:
        if has_journal_entry(expense):
            continue
        credit_expense_total += expense.total_amount
        if expense.category and expense.category.code == "AUDIT_FEES":
            audit_fee_total += expense.total_amount
    credit_inventory_total = Decimal("0.00")
    for tx in inv_in.filter(payment_method=InventoryTransaction.PaymentMethod.CREDIT):
        total = (tx.quantity * tx.unit_price) if tx.unit_price is not None else Decimal("0.00")
        paid_upfront = _as_decimal(tx.credit_paid_upfront)
        credit_inventory_total += (total * (Decimal("100.00") - paid_upfront) / Decimal("100.00")).quantize(Decimal("0.01"))
    add_amount("431001", credit_expense_total + credit_inventory_total)
    add_amount("421007", audit_fee_total)

    salary_payables = period_filter(SalaryPayment.objects.filter(status=SalaryPayment.Status.PENDING), "payment_date")
    add_amount("421010", salary_payables.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))
    add_amount("421012", salary_payables.aggregate(total=Sum("paye_tax"))["total"] or Decimal("0.00"))
    nssf_employee = salary_payables.aggregate(total=Sum("nssf_employee"))["total"] or Decimal("0.00")
    nssf_employer = salary_payables.aggregate(total=Sum("nssf_employer"))["total"] or Decimal("0.00")
    add_amount("421016", nssf_employee + nssf_employer)

    capital_records = period_filter(InvestorCapitalTransaction.objects.all(), "transaction_date")
    capital_in = capital_records.exclude(
        transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    capital_out = capital_records.filter(
        transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    net_capital = capital_in - capital_out
    add_amount("511001", net_capital)
    add_amount("351008", net_capital)

    pl_data = get_pl_data(start_date, end_date)
    add_amount("521001", pl_data["totals"]["profit_loss"])
    if start_date:
        retained_data = get_pl_data(None, start_date - timedelta(days=1))
        add_amount("511003", retained_data["totals"]["profit_loss"])

    legacy_group_codes = {
        AccountType.AccountNature.ASSET: ("ASSETS", "Assets"),
        AccountType.AccountNature.LIABILITY: ("LIABILITIES", "Liabilities"),
        AccountType.AccountNature.EQUITY: ("EQUITY", "Equity"),
    }
    grouped_index = {}
    type_index = {}

    for account in accounts_query:
        account_type = account.account_type
        group_value, group_label = legacy_group_codes.get(
            account_type.account_nature,
            (account_type.account_nature, account_type.get_account_nature_display()),
        )
        group_order = balance_sheet_natures.index(account_type.account_nature) if account_type.account_nature in balance_sheet_natures else 0

        group = grouped_index.setdefault(
            group_value,
            {
                "group_label": group_label,
                "group_value": group_value,
                "type_groups": [],
                "group_total": Decimal("0.00"),
                "_order": group_order,
            },
        )

        type_value = account_type.legacy_code or account_type.name.upper().replace(" ", "_")
        type_key = (group_value, type_value)
        type_group = type_index.get(type_key)
        if type_group is None:
            type_group = {
                "type_label": account_type.name,
                "type_value": type_value,
                "accounts": [],
                "type_total": Decimal("0.00"),
                "_order": group_order,
            }
            type_index[type_key] = type_group
            group["type_groups"].append(type_group)

        amount = account_amounts.get(account.code, Decimal("0.00"))
        type_group["accounts"].append(
            {
                "code": account.code,
                "account_name": account.account_name,
                "type": account.get_account_type_display(),
                "amount": amount,
                "is_active": account.is_active,
            }
        )
        type_group["type_total"] += amount
        group["group_total"] += amount

    grouped_accounts = sorted(grouped_index.values(), key=lambda row: (row["_order"], row["group_label"]))
    for group in grouped_accounts:
        group["type_groups"].sort(key=lambda row: (row["_order"], row["type_label"]))
        for type_group in group["type_groups"]:
            type_group.pop("_order", None)
        group.pop("_order", None)

    return {"grouped_accounts": grouped_accounts}
