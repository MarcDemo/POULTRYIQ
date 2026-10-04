from django.core.management.base import BaseCommand
from django.db.models import Case, F, IntegerField, Sum, When

from poultry.models import HouseBirdMovement, PoultryBatch
from sales.models import SaleInvoice, SaleItem


class Command(BaseCommand):
    help = "Report negative bird-ledger balances and delivered bird sales that have no source batch."

    def handle(self, *args, **options):
        unlinked_sales = SaleItem.objects.filter(
            batch__isnull=True,
            invoice__delivery_status=SaleInvoice.DeliveryStatus.DELIVERED,
        ).filter(
            models_filter_for_birds()
        ).select_related("invoice")

        self.stdout.write("Delivered bird sales without a source batch:")
        if not unlinked_sales.exists():
            self.stdout.write(self.style.SUCCESS("  None"))
        else:
            for item in unlinked_sales:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Item {item.pk}: {item.invoice.invoice_no}, {item.quantity} {item.unit}, {item.product_name}"
                    )
                )

        balances = (
            HouseBirdMovement.objects.values("batch_id")
            .annotate(
                balance=Sum(
                    Case(
                        When(direction=HouseBirdMovement.Direction.IN, then=F("quantity")),
                        default=-F("quantity"),
                        output_field=IntegerField(),
                    )
                )
            )
            .filter(balance__lt=0)
        )
        self.stdout.write("Negative batch balances:")
        if not balances.exists():
            self.stdout.write(self.style.SUCCESS("  None"))
        else:
            batches = {batch.pk: batch for batch in PoultryBatch.objects.filter(pk__in=[row["batch_id"] for row in balances])}
            for row in balances:
                batch = batches[row["batch_id"]]
                self.stdout.write(self.style.ERROR(f"  {batch.batch_code}: {row['balance']}"))


def models_filter_for_birds():
    from django.db.models import Q

    return (
        Q(unit__iexact="bird")
        | Q(unit__iexact="birds")
        | Q(product_name__icontains="bird")
        | Q(product_name__icontains="off layer")
        | Q(product_name__icontains="off-layer")
    )
