from django.db import migrations
from django.utils import timezone


def backfill_house_bird_ledger(apps, schema_editor):
    PoultryBatch = apps.get_model("poultry", "PoultryBatch")
    MortalityRecord = apps.get_model("poultry", "MortalityRecord")
    HouseBirdMovement = apps.get_model("poultry", "HouseBirdMovement")
    SaleItem = apps.get_model("sales", "SaleItem")

    balances = {}

    def add_balance(batch_id, quantity):
        balances[batch_id] = balances.get(batch_id, 0) + quantity

    for batch in PoultryBatch.objects.all().iterator():
        if batch.initial_quantity <= 0:
            continue
        HouseBirdMovement.objects.create(
            house_id=batch.house_id,
            batch_id=batch.pk,
            occurred_on=batch.date_stocked,
            direction="IN",
            movement_type="OPENING",
            quantity=batch.initial_quantity,
            operator_id=batch.created_by_id,
            notes=f"Opening balance from historical batch {batch.batch_code}",
        )
        add_balance(batch.pk, batch.initial_quantity)

    for mortality in MortalityRecord.objects.filter(status="APPROVED").iterator():
        if mortality.number_dead <= 0:
            continue
        batch = PoultryBatch.objects.get(pk=mortality.batch_id)
        HouseBirdMovement.objects.create(
            house_id=batch.house_id,
            batch_id=batch.pk,
            occurred_on=mortality.record_date,
            direction="OUT",
            movement_type="MORTALITY",
            quantity=mortality.number_dead,
            operator_id=mortality.reviewed_by_id or mortality.reported_by_id,
            mortality_record_id=mortality.pk,
            notes=mortality.notes,
        )
        add_balance(batch.pk, -mortality.number_dead)

    delivered_sales = SaleItem.objects.filter(
        batch_id__isnull=False,
        invoice__delivery_status="DELIVERED",
    ).select_related("invoice")
    for sale_item in delivered_sales.iterator():
        label = (sale_item.product_name or "").lower()
        unit = (sale_item.unit or "").lower()
        is_bird_sale = (
            unit in {"bird", "birds"}
            or "off layer" in label
            or "off-layer" in label
            or "bird" in label
        )
        if not is_bird_sale or sale_item.quantity <= 0:
            continue
        if sale_item.quantity != sale_item.quantity.to_integral_value():
            raise RuntimeError(f"Bird sale item {sale_item.pk} has a fractional quantity.")
        batch = PoultryBatch.objects.get(pk=sale_item.batch_id)
        quantity = int(sale_item.quantity)
        HouseBirdMovement.objects.create(
            house_id=batch.house_id,
            batch_id=batch.pk,
            occurred_on=sale_item.invoice.invoice_date,
            direction="OUT",
            movement_type="SALE",
            quantity=quantity,
            operator_id=sale_item.invoice.created_by_id,
            sale_item_id=sale_item.pk,
            notes=f"Historical delivered sale {sale_item.invoice.invoice_no}",
        )
        add_balance(batch.pk, -quantity)

    negative = [batch_id for batch_id, balance in balances.items() if balance < 0]
    if negative:
        details = ", ".join(
            f"{batch.batch_code}={balances[batch.pk]}"
            for batch in PoultryBatch.objects.filter(pk__in=negative)
        )
        raise RuntimeError(f"Bird ledger backfill produced negative batch balances: {details}")

    for batch in PoultryBatch.objects.filter(status="CLOSED").iterator():
        remaining = balances.get(batch.pk, 0)
        if remaining <= 0:
            continue
        HouseBirdMovement.objects.create(
            house_id=batch.house_id,
            batch_id=batch.pk,
            occurred_on=timezone.localdate(),
            direction="OUT",
            movement_type="LEGACY_CLOSE",
            quantity=remaining,
            operator_id=batch.created_by_id,
            notes="Migration reconciliation for a batch already marked closed",
        )
        balances[batch.pk] = 0


class Migration(migrations.Migration):
    dependencies = [
        ("poultry", "0017_poultrybatch_origin_batch_birdtransfer_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_house_bird_ledger, migrations.RunPython.noop),
    ]
