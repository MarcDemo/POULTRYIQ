from django.db import migrations


PAYMENT_ACCOUNTS = [
    ("PAYMENT_CASH", "Cash"),
    ("PAYMENT_BANK", "Bank"),
    ("PAYMENT_MOBILE_MONEY", "Mobile Money"),
    ("PAYMENT_CHECK", "Check / Clearing"),
]


def unique_prefix(AccountType, seed):
    prefix = seed
    suffix = 2
    while AccountType.objects.filter(prefix=prefix).exists():
        prefix = f"{seed}{suffix}"
        suffix += 1
    return prefix


def next_account_code(ChartOfAccount, prefix):
    last_account = ChartOfAccount.objects.filter(code__startswith=prefix).order_by("-code").first()
    next_sequence = 1
    if last_account:
        suffix = last_account.code.replace(prefix, "", 1)
        if suffix.isdigit():
            next_sequence = int(suffix) + 1
    code = f"{prefix}{next_sequence:04d}"
    while ChartOfAccount.objects.filter(code=code).exists():
        next_sequence += 1
        code = f"{prefix}{next_sequence:04d}"
    return code


def seed_builtin_payment_accounts(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")

    account_type = AccountType.objects.filter(legacy_code="BANK_AND_CASH").first()
    if not account_type:
        account_type = AccountType.objects.filter(name__iexact="Bank and Cash").first()
    if account_type:
        updates = []
        if account_type.legacy_code != "BANK_AND_CASH":
            account_type.legacy_code = "BANK_AND_CASH"
            updates.append("legacy_code")
        if account_type.account_nature != "ASSET":
            account_type.account_nature = "ASSET"
            updates.append("account_nature")
        if not account_type.is_active:
            account_type.is_active = True
            updates.append("is_active")
        if updates:
            account_type.save(update_fields=updates)
    else:
        account_type = AccountType.objects.create(
            name="Bank and Cash",
            prefix=unique_prefix(AccountType, "BC"),
            account_nature="ASSET",
            legacy_code="BANK_AND_CASH",
            is_active=True,
        )

    for system_code, account_name in PAYMENT_ACCOUNTS:
        account = ChartOfAccount.objects.filter(system_code=system_code).first()
        if not account:
            account = ChartOfAccount.objects.filter(account_name__iexact=account_name).first()
        if account:
            updates = []
            if account.system_code != system_code:
                account.system_code = system_code
                updates.append("system_code")
            if account.account_type_id != account_type.id:
                account.account_type = account_type
                updates.append("account_type")
            if not account.is_active:
                account.is_active = True
                updates.append("is_active")
            if updates:
                account.save(update_fields=updates)
        else:
            ChartOfAccount.objects.create(
                system_code=system_code,
                code=next_account_code(ChartOfAccount, account_type.prefix),
                account_name=account_name,
                account_type=account_type,
                allow_reconciliation=True,
                is_active=True,
            )


def unseed_builtin_payment_accounts(apps, schema_editor):
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")
    ChartOfAccount.objects.filter(system_code__in=[row[0] for row in PAYMENT_ACCOUNTS]).update(system_code=None)


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0019_chartofaccount_system_code_seed_requested_defaults"),
    ]

    operations = [
        migrations.RunPython(seed_builtin_payment_accounts, unseed_builtin_payment_accounts),
    ]
