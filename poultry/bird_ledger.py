from datetime import date
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Sum, When

from .models import (
    ApprovalStatus,
    BirdTransfer,
    BirdTransferAllocation,
    HouseBirdMovement,
    PoultryBatch,
    PoultryHouse,
)


def _movement_total(queryset):
    return queryset.aggregate(
        total=Sum(
            Case(
                When(direction=HouseBirdMovement.Direction.IN, then=F("quantity")),
                default=-F("quantity"),
                output_field=IntegerField(),
            )
        )
    )["total"] or 0


def house_bird_count(house, *, as_of=None):
    queryset = HouseBirdMovement.objects.filter(house=house)
    if as_of is not None:
        queryset = queryset.filter(occurred_on__lte=as_of)
    return int(_movement_total(queryset))


def batch_bird_count(batch, *, as_of=None):
    queryset = HouseBirdMovement.objects.filter(batch=batch)
    if as_of is not None:
        queryset = queryset.filter(occurred_on__lte=as_of)
    return int(_movement_total(queryset))


def farm_bird_count(*, as_of=None):
    queryset = HouseBirdMovement.objects.all()
    if as_of is not None:
        queryset = queryset.filter(occurred_on__lte=as_of)
    return int(_movement_total(queryset))


def house_bird_counts(*, as_of=None):
    queryset = HouseBirdMovement.objects.all()
    if as_of is not None:
        queryset = queryset.filter(occurred_on__lte=as_of)
    rows = queryset.values("house_id").annotate(
        balance=Sum(
            Case(
                When(direction=HouseBirdMovement.Direction.IN, then=F("quantity")),
                default=-F("quantity"),
                output_field=IntegerField(),
            )
        )
    )
    return {row["house_id"]: int(row["balance"] or 0) for row in rows}


def record_batch_stocking(batch, *, operator=None, movement_type=None):
    """Create the single opening movement for a newly stocked, non-transfer batch."""
    if batch.origin_batch_id:
        return None
    movement_type = movement_type or HouseBirdMovement.MovementType.STOCK_IN
    movement, _ = HouseBirdMovement.objects.get_or_create(
        batch=batch,
        house=batch.house,
        movement_type=movement_type,
        defaults={
            "occurred_on": batch.date_stocked,
            "direction": HouseBirdMovement.Direction.IN,
            "quantity": batch.initial_quantity,
            "operator": operator or batch.created_by,
            "notes": f"Stocking for {batch.batch_code}",
        },
    )
    return movement


def record_approved_mortality(record, *, operator):
    if record.status != ApprovalStatus.APPROVED:
        raise ValidationError("Only approved mortality can reduce the bird ledger.")
    if record.number_dead <= 0:
        raise ValidationError("Approved mortality must contain at least one dead bird.")
    if hasattr(record, "bird_movement"):
        return record.bird_movement

    with transaction.atomic():
        batch = PoultryBatch.objects.select_for_update().select_related("house").get(pk=record.batch_id)
        available = batch_bird_count(batch)
        if record.number_dead > available:
            raise ValidationError(
                f"Cannot approve {record.number_dead} deaths; {available} birds remain in {batch.batch_code}."
            )
        return HouseBirdMovement.objects.create(
            house=batch.house,
            batch=batch,
            occurred_on=record.record_date,
            direction=HouseBirdMovement.Direction.OUT,
            movement_type=HouseBirdMovement.MovementType.MORTALITY,
            quantity=record.number_dead,
            operator=operator,
            mortality_record=record,
            notes=record.notes,
        )


def is_bird_sale_item(sale_item):
    label = (sale_item.product_name or "").lower()
    unit = (sale_item.unit or "").lower()
    return unit in {"bird", "birds"} or "off layer" in label or "off-layer" in label or "bird" in label


def record_delivered_bird_sale(sale_item, *, operator):
    from sales.models import SaleInvoice

    if not is_bird_sale_item(sale_item):
        return None
    if sale_item.invoice.delivery_status != SaleInvoice.DeliveryStatus.DELIVERED:
        return None
    if not sale_item.batch_id:
        raise ValidationError("Select the source flock batch before delivering a bird sale.")
    if hasattr(sale_item, "bird_movement"):
        return sale_item.bird_movement
    if sale_item.quantity != sale_item.quantity.to_integral_value() or sale_item.quantity <= 0:
        raise ValidationError("Bird sale quantities must be positive whole birds.")

    quantity = int(sale_item.quantity)
    with transaction.atomic():
        batch = PoultryBatch.objects.select_for_update().select_related("house").get(pk=sale_item.batch_id)
        available = batch_bird_count(batch)
        if quantity > available:
            raise ValidationError(
                f"Cannot deliver {quantity} birds; {available} birds remain in {batch.batch_code}."
            )
        movement = HouseBirdMovement.objects.create(
            house=batch.house,
            batch=batch,
            occurred_on=sale_item.invoice.invoice_date,
            direction=HouseBirdMovement.Direction.OUT,
            movement_type=HouseBirdMovement.MovementType.SALE,
            quantity=quantity,
            operator=operator,
            sale_item=sale_item,
            notes=f"Delivered on {sale_item.invoice.invoice_no}",
        )
        if batch_bird_count(batch) == 0 and batch.status != PoultryBatch.Status.CLOSED:
            batch.status = PoultryBatch.Status.CLOSED
            batch.save(update_fields=["status"])
        return movement


def _role_code(user):
    return (getattr(getattr(user, "role", None), "code", "") or "").upper()


def create_bird_transfer(
    *,
    actor,
    source_batch_id,
    transfer_date,
    allocations,
    notes="",
    capacity_warning_acknowledged=False,
):
    if _role_code(actor) != "SUPERVISOR":
        raise PermissionDenied("Only supervisors can create bird transfers.")
    if transfer_date > date.today():
        raise ValidationError("Transfer date cannot be in the future.")
    if not allocations:
        raise ValidationError("Add at least one destination house.")

    normalized = []
    seen_houses = set()
    for allocation in allocations:
        try:
            house_id = int(allocation["house_id"])
            quantity = int(allocation["quantity"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError("Each destination needs a valid house and whole-bird quantity.")
        if quantity <= 0:
            raise ValidationError("Transfer quantities must be greater than zero.")
        if house_id in seen_houses:
            raise ValidationError("Each destination house can appear only once.")
        seen_houses.add(house_id)
        normalized.append((house_id, quantity))

    with transaction.atomic():
        try:
            source = (
                PoultryBatch.objects.select_for_update()
                .select_related("house", "origin_batch")
                .get(pk=source_batch_id)
            )
        except (PoultryBatch.DoesNotExist, TypeError, ValueError):
            raise ValidationError("Select a valid source flock batch.")
        if source.status != PoultryBatch.Status.ACTIVE:
            raise ValidationError("Only active batches can be transferred.")
        if not actor.houses.filter(pk=source.house_id, is_active=True).exists():
            raise PermissionDenied("You can transfer birds only from an assigned source house.")
        if source.house_id in seen_houses:
            raise ValidationError("The destination house must differ from the source house.")

        houses = {
            house.pk: house
            for house in PoultryHouse.objects.select_for_update().filter(pk__in=seen_houses, is_active=True)
        }
        if len(houses) != len(seen_houses):
            raise ValidationError("Every destination must be an active poultry house.")

        total_quantity = sum(quantity for _, quantity in normalized)
        available_then = batch_bird_count(source, as_of=transfer_date)
        available_now = batch_bird_count(source)
        if total_quantity > available_then or total_quantity > available_now:
            raise ValidationError(
                f"Cannot transfer {total_quantity} birds; {min(available_then, available_now)} are available."
            )

        house_counts = house_bird_counts()
        projections = {
            house_id: house_counts.get(house_id, 0) + quantity
            for house_id, quantity in normalized
        }
        exceeds_capacity = any(projections[house_id] > houses[house_id].capacity for house_id, _ in normalized)
        if exceeds_capacity and not capacity_warning_acknowledged:
            raise ValidationError("A destination exceeds capacity. Acknowledge the warning to continue.")

        transfer = BirdTransfer.objects.create(
            source_batch=source,
            transfer_date=transfer_date,
            notes=notes.strip(),
            capacity_warning_acknowledged=bool(capacity_warning_acknowledged),
            created_by=actor,
        )
        origin_batch = source.origin_batch or source
        source_age = max(source.initial_age_days + (transfer_date - source.date_stocked).days, 0)

        for line_number, (house_id, quantity) in enumerate(normalized, start=1):
            house = houses[house_id]
            projected = projections[house_id]
            allocation = BirdTransferAllocation.objects.create(
                transfer=transfer,
                destination_house=house,
                quantity=quantity,
                projected_house_birds=max(projected, 0),
                exceeded_capacity=projected > house.capacity,
            )
            destination_batch = PoultryBatch.objects.create(
                batch_code=f"TR-{transfer_date:%Y%m%d}-{transfer.pk}-{line_number}",
                house=house,
                breed=source.breed,
                supplier_name=source.supplier_name,
                amount_paid=None,
                date_stocked=transfer_date,
                initial_quantity=quantity,
                initial_age_days=source_age,
                expected_lay_start=source.expected_lay_start,
                status=PoultryBatch.Status.ACTIVE,
                notes=f"Transferred from {source.batch_code}. {notes}".strip(),
                created_by=actor,
                origin_batch=origin_batch,
            )
            allocation.destination_batch = destination_batch
            allocation.save(update_fields=["destination_batch"])
            HouseBirdMovement.objects.create(
                house=source.house,
                batch=source,
                occurred_on=transfer_date,
                direction=HouseBirdMovement.Direction.OUT,
                movement_type=HouseBirdMovement.MovementType.TRANSFER_OUT,
                quantity=quantity,
                operator=actor,
                transfer_allocation=allocation,
                notes=f"Transfer {transfer.pk} to {house.house_code}",
            )
            HouseBirdMovement.objects.create(
                house=house,
                batch=destination_batch,
                occurred_on=transfer_date,
                direction=HouseBirdMovement.Direction.IN,
                movement_type=HouseBirdMovement.MovementType.TRANSFER_IN,
                quantity=quantity,
                operator=actor,
                transfer_allocation=allocation,
                notes=f"Transfer {transfer.pk} from {source.house.house_code}",
            )

        if batch_bird_count(source) == 0:
            source.status = PoultryBatch.Status.CLOSED
            source.save(update_fields=["status"])
        return transfer


def reverse_bird_transfer(*, actor, transfer_id, reason):
    if not actor.is_authenticated or not actor.is_superuser:
        raise PermissionDenied("Only Django superusers can reverse bird transfers.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reversal reason is required.")

    with transaction.atomic():
        original = (
            BirdTransfer.objects.select_for_update()
            .select_related("source_batch__house")
            .prefetch_related("allocations__destination_batch__house", "allocations__movements")
            .get(pk=transfer_id)
        )
        if original.status != BirdTransfer.Status.COMPLETED or original.reversal_of_id:
            raise ValidationError("Only an unreversed original transfer can be reversed.")

        allocations = list(original.allocations.all())
        for allocation in allocations:
            destination_batch = PoultryBatch.objects.select_for_update().get(pk=allocation.destination_batch_id)
            if batch_bird_count(destination_batch) < allocation.quantity:
                raise ValidationError(
                    f"{destination_batch.batch_code} no longer has all {allocation.quantity} transferred birds."
                )
            downstream = HouseBirdMovement.objects.filter(
                batch=destination_batch,
                direction=HouseBirdMovement.Direction.OUT,
            ).exists()
            if downstream:
                raise ValidationError(
                    f"{destination_batch.batch_code} has mortality, sales, or later transfers and cannot be reversed."
                )

        for allocation in allocations:
            destination_batch = allocation.destination_batch
            reverse_transfer = BirdTransfer.objects.create(
                source_batch=destination_batch,
                transfer_date=date.today(),
                notes=f"Reversal of transfer {original.pk}",
                capacity_warning_acknowledged=True,
                reversal_of=original,
                reversal_reason=reason,
                created_by=actor,
            )
            reverse_allocation = BirdTransferAllocation.objects.create(
                transfer=reverse_transfer,
                destination_house=original.source_batch.house,
                quantity=allocation.quantity,
                projected_house_birds=max(
                    house_bird_count(original.source_batch.house) + allocation.quantity,
                    0,
                ),
                exceeded_capacity=(
                    house_bird_count(original.source_batch.house) + allocation.quantity
                    > original.source_batch.house.capacity
                ),
            )
            returned_batch = PoultryBatch.objects.create(
                batch_code=f"TR-{date.today():%Y%m%d}-{reverse_transfer.pk}-1",
                house=original.source_batch.house,
                breed=destination_batch.breed,
                supplier_name=destination_batch.supplier_name,
                amount_paid=None,
                date_stocked=date.today(),
                initial_quantity=allocation.quantity,
                initial_age_days=destination_batch.current_age_days,
                expected_lay_start=destination_batch.expected_lay_start,
                status=PoultryBatch.Status.ACTIVE,
                notes=f"Reversal of transfer {original.pk}: {reason}",
                created_by=actor,
                origin_batch=destination_batch.origin_batch or destination_batch,
            )
            reverse_allocation.destination_batch = returned_batch
            reverse_allocation.save(update_fields=["destination_batch"])

            original_out = allocation.movements.get(
                movement_type=HouseBirdMovement.MovementType.TRANSFER_OUT
            )
            original_in = allocation.movements.get(
                movement_type=HouseBirdMovement.MovementType.TRANSFER_IN
            )
            HouseBirdMovement.objects.create(
                house=destination_batch.house,
                batch=destination_batch,
                occurred_on=date.today(),
                direction=HouseBirdMovement.Direction.OUT,
                movement_type=HouseBirdMovement.MovementType.REVERSAL_OUT,
                quantity=allocation.quantity,
                operator=actor,
                transfer_allocation=reverse_allocation,
                reversal_of=original_in,
                notes=reason,
            )
            HouseBirdMovement.objects.create(
                house=original.source_batch.house,
                batch=returned_batch,
                occurred_on=date.today(),
                direction=HouseBirdMovement.Direction.IN,
                movement_type=HouseBirdMovement.MovementType.REVERSAL_IN,
                quantity=allocation.quantity,
                operator=actor,
                transfer_allocation=reverse_allocation,
                reversal_of=original_out,
                notes=reason,
            )
            destination_batch.status = PoultryBatch.Status.CLOSED
            destination_batch.save(update_fields=["status"])

        original.status = BirdTransfer.Status.REVERSED
        original.save(update_fields=["status"])
        return original
