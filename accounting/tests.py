from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounting.models import (
    AccountingCode,
    BalanceSheetAccount,
    FixedAssetAcquisition,
    AssetConstructionProject,
    AssetConstructionCostLine,
)
from accounting.balance_sheet_data import BALANCE_SHEET_ACCOUNTS
from accounting.services import get_pl_data, get_bs_data
from sales.models import Customer, SaleInvoice, SaleItem


class ProfitAndLossDataTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="accounting-user",
            password="pass1234",
        )
        self.customer = Customer.objects.create(name="P&L Buyer")

    def _create_sale_item(self, invoice_no, line_total):
        invoice = SaleInvoice.objects.create(
            invoice_no=invoice_no,
            customer=self.customer,
            invoice_date=date(2026, 6, 19),
            subtotal=line_total,
            discount_amount=Decimal("0.00"),
            total_amount=line_total,
            status=SaleInvoice.Status.PAID,
            delivery_status=SaleInvoice.DeliveryStatus.DELIVERED,
            created_by=self.user,
        )
        return SaleItem.objects.create(
            invoice=invoice,
            product_name="Eggs",
            quantity=Decimal("2.000"),
            unit="trays",
            unit_price=(line_total / Decimal("2.000")).quantize(Decimal("0.01")),
            line_total=line_total,
        )

    def test_sale_item_line_total_is_used_as_revenue_amount(self):
        item = self._create_sale_item("INV-2026-PL01", Decimal("10000.00"))
        AccountingCode.create_or_get_accounting_code(
            prefix="SE",
            account_type="REVENUE",
            account_name="sale_of_eggs",
            content_object=item,
        )

        pl_data = get_pl_data()

        self.assertEqual(pl_data["revenue"][0]["amount"], Decimal("10000.00"))
        self.assertEqual(pl_data["totals"]["revenue"], Decimal("10000.00"))

    def test_transaction_backed_codes_are_not_reused_by_account_name(self):
        first_item = self._create_sale_item("INV-2026-PL02", Decimal("10000.00"))
        second_item = self._create_sale_item("INV-2026-PL03", Decimal("15000.00"))

        first_code = AccountingCode.create_or_get_accounting_code(
            prefix="SE",
            account_type="REVENUE",
            account_name="sale_of_eggs",
            content_object=first_item,
        )
        second_code = AccountingCode.create_or_get_accounting_code(
            prefix="SE",
            account_type="REVENUE",
            account_name="sale_of_eggs",
            content_object=second_item,
        )

        self.assertNotEqual(first_code.pk, second_code.pk)
        self.assertEqual(AccountingCode.objects.filter(account_name="sale_of_eggs").count(), 2)
        self.assertEqual(get_pl_data()["totals"]["revenue"], Decimal("25000.00"))


class BalanceSheetAccountTests(TestCase):
    def setUp(self):
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.manager = get_user_model().objects.create_user(
            username="balance-manager",
            password="pass1234",
            role=self.manager_role,
        )

    def test_seeded_balance_sheet_accounts_include_required_groups(self):
        required_types = {
            BalanceSheetAccount.AccountType.FIXED_ASSET,
            BalanceSheetAccount.AccountType.ACCUMULATED_DEPRECIATION,
            BalanceSheetAccount.AccountType.CURRENT_ASSET,
            BalanceSheetAccount.AccountType.RECEIVABLE,
            BalanceSheetAccount.AccountType.PREPAYMENT,
            BalanceSheetAccount.AccountType.BANK_AND_CASH,
            BalanceSheetAccount.AccountType.NON_CURRENT_LIABILITY,
            BalanceSheetAccount.AccountType.CURRENT_LIABILITY,
            BalanceSheetAccount.AccountType.PAYABLE,
            BalanceSheetAccount.AccountType.CURRENT_YEAR_EARNINGS,
        }

        self.assertTrue(required_types.issubset(set(BalanceSheetAccount.objects.values_list("account_type", flat=True))))
        self.assertEqual(
            BalanceSheetAccount.objects.count(),
            len(BALANCE_SHEET_ACCOUNTS),
        )
        self.assertEqual(
            set(BalanceSheetAccount.objects.values_list("code", flat=True)),
            {row[0] for row in BALANCE_SHEET_ACCOUNTS},
        )

    def test_manager_can_view_and_create_balance_sheet_account(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("balance_sheet"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounting/balance_sheet.html")
        self.assertContains(response, "ASSETS")
        # BS page now shows amounts in a read-only report format
        self.assertContains(response, "Amount")

    def test_manager_can_filter_balance_sheet_by_date(self):
        self.client.force_login(self.manager)

        # Test without filters
        response = self.client.get(reverse("balance_sheet"))
        self.assertEqual(response.status_code, 200)

        # Test with date filters
        response = self.client.get(
            reverse("balance_sheet"),
            {"start_date": "2026-01-01", "end_date": "2026-12-31"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-01-01")  # start_date value in form
        self.assertContains(response, "2026-12-31")  # end_date value in form

    def test_balance_sheet_model_auto_generates_next_code(self):
        first = BalanceSheetAccount.objects.create(
            account_name="Main Bank",
            group=BalanceSheetAccount.Group.ASSETS,
            account_type=BalanceSheetAccount.AccountType.BANK_AND_CASH,
        )
        second = BalanceSheetAccount.objects.create(
            account_name="Petty Cash",
            group=BalanceSheetAccount.Group.ASSETS,
            account_type=BalanceSheetAccount.AccountType.BANK_AND_CASH,
        )

        self.assertRegex(first.code, r"^BC\d{4}$")
        self.assertRegex(second.code, r"^BC\d{4}$")
        self.assertNotEqual(first.code, second.code)

    def test_fixed_asset_acquisition_is_included_in_balance_sheet(self):
        self.client.force_login(self.manager)
        FixedAssetAcquisition.objects.create(
            asset_name="Poultry House A",
            asset_category="POULTRY_HOUSE",
            acquisition_date=date(2026, 6, 1),
            amount=Decimal("1200000.00"),
            useful_life_years=20,
            residual_value=Decimal("0.00"),
            in_service_date=date(2026, 6, 1),
            payment_method="Bank",
            created_by=self.manager,
        )

        response = self.client.get(reverse("balance_sheet"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Poultry House / Poultry House A")
        self.assertContains(response, "1200000.00")

    def test_completed_construction_moves_from_auc_to_fixed_asset_label(self):
        self.client.force_login(self.manager)
        project = AssetConstructionProject.objects.create(
            project_name="Layer House B",
            asset_category="LAYER_HOUSE",
            start_date=date(2026, 6, 1),
            useful_life_years=20,
            residual_value=Decimal("0.00"),
            in_service_date=date(2026, 6, 21),
            status=AssetConstructionProject.Status.IN_PROGRESS,
            created_by=self.manager,
        )
        AssetConstructionCostLine.objects.create(
            project=project,
            cost_date=date(2026, 6, 3),
            cost_item_name="Cement",
            amount=Decimal("400000.00"),
            created_by=self.manager,
        )

        response = self.client.get(reverse("balance_sheet"))
        self.assertContains(response, "Asset Construction / Layer House B")

        self.client.post(
            reverse("asset_construction_project_detail", args=[project.pk]),
            {
                "action": "complete",
                "completed_date": "2026-06-20",
            },
            follow=True,
        )

        response = self.client.get(reverse("balance_sheet"))
        self.assertNotContains(response, "Asset Construction / Layer House B")
        self.assertContains(response, "Layer House / Layer House B")

    def test_straight_line_depreciation_affects_pl_and_bs(self):
        FixedAssetAcquisition.objects.create(
            asset_name="Generator",
            asset_category="HARDWARE_EQUIPMENT",
            acquisition_date=date(2026, 1, 1),
            in_service_date=date(2026, 1, 1),
            amount=Decimal("1200.00"),
            residual_value=Decimal("0.00"),
            useful_life_years=1,
            is_depreciable=True,
            created_by=self.manager,
        )

        pl_data = get_pl_data(start_date=date(2026, 2, 1), end_date=date(2026, 2, 28))
        self.assertGreater(pl_data["totals"]["depreciation"], Decimal("0.00"))

        bs_data = get_bs_data(end_date=date(2026, 2, 28))
        assets_group = next(row for row in bs_data["grouped_accounts"] if row["group_value"] == "ASSETS")
        ad_group = next(row for row in assets_group["type_groups"] if row["type_value"] == "ACCUMULATED_DEPRECIATION")
        self.assertGreater(ad_group["type_total"], Decimal("0.00"))
        self.assertTrue(
            any(row["account_name"] == "Accumulated depreciation-Generator" for row in ad_group["accounts"])
        )
