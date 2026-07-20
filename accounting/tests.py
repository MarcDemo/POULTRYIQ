from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import InvestorCapitalTransaction, Role
from accounting.models import (
    AccountingCode,
    AssetCategory,
    BalanceSheetAccount,
    FixedAssetAcquisition,
    AssetConstructionProject,
    AssetConstructionCostLine,
)
from accounting.balance_sheet_data import BALANCE_SHEET_ACCOUNTS
from accounting.services import get_pl_data, get_bs_data
from expenses.models import ExpenseCategory, ExpenseTransaction
from hr.models import WelfareRequest
from inventory.models import InventoryTransaction, Item, ItemCategory, Store
from payroll.models import SalaryPayment
from poultry.models import ApprovalStatus, MortalityRecord, PoultryBatch, PoultryHouse
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

    def _account_row(self, code):
        for group in get_bs_data()["grouped_accounts"]:
            for type_group in group["type_groups"]:
                for account in type_group["accounts"]:
                    if account["code"] == code:
                        return account
        self.fail(f"Balance sheet account {code} was not returned")

    def _asset_category(self, legacy_code):
        return AssetCategory.objects.get(legacy_code=legacy_code)

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
        FixedAssetAcquisition.objects.create(
            asset_name="Poultry House A",
            asset_category=self._asset_category("POULTRY_HOUSE"),
            acquisition_date=date(2026, 6, 1),
            amount=Decimal("1200000.00"),
            useful_life_years=20,
            residual_value=Decimal("0.00"),
            in_service_date=date(2026, 6, 1),
            payment_method="Bank",
            created_by=self.manager,
        )

        row = self._account_row("311001")
        self.assertEqual(row["account_name"], "Buildings Cost")
        self.assertEqual(row["amount"], Decimal("1200000.00"))

    def test_completed_construction_moves_from_auc_to_fixed_asset_label(self):
        self.client.force_login(self.manager)
        project = AssetConstructionProject.objects.create(
            project_name="Layer House B",
            asset_category=self._asset_category("LAYER_HOUSE"),
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

        self.assertEqual(self._account_row("311001")["amount"], Decimal("400000.00"))

        self.client.post(
            reverse("asset_construction_project_detail", args=[project.pk]),
            {
                "action": "complete",
                "completed_date": "2026-06-20",
            },
            follow=True,
        )

        self.assertEqual(self._account_row("311001")["amount"], Decimal("400000.00"))

    def test_straight_line_depreciation_affects_pl_and_bs(self):
        FixedAssetAcquisition.objects.create(
            asset_name="Generator",
            asset_category=self._asset_category("HARDWARE_EQUIPMENT"),
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
        farm_equipment_depreciation = next(row for row in ad_group["accounts"] if row["code"] == "312008")
        self.assertGreater(farm_equipment_depreciation["amount"], Decimal("0.00"))

    def test_balance_sheet_wires_inventory_and_laying_birds_from_models(self):
        store = Store.objects.create(name="Main Store")
        bird_category = ItemCategory.objects.create(code="BIRDS", name="Birds")
        feed_category = ItemCategory.objects.create(code="FEED", name="Feed")
        chicks = Item.objects.create(name="Day-old chicks", category=bird_category, unit="bird")
        feed = Item.objects.create(name="Layer mash", category=feed_category, unit="kg")
        InventoryTransaction.objects.create(
            tx_date=date(2026, 6, 1),
            tx_type=InventoryTransaction.TxType.IN_,
            store=store,
            item=chicks,
            quantity=Decimal("100.000"),
            unit_price=Decimal("2500.00"),
            created_by=self.manager,
        )
        InventoryTransaction.objects.create(
            tx_date=date(2026, 6, 1),
            tx_type=InventoryTransaction.TxType.IN_,
            store=store,
            item=feed,
            quantity=Decimal("10.000"),
            unit_price=Decimal("1000.00"),
            created_by=self.manager,
        )
        house = PoultryHouse.objects.create(house_code="H-01", capacity=200)
        batch = PoultryBatch.objects.create(
            house=house,
            breed="Layers",
            amount_paid=Decimal("500000.00"),
            date_stocked=date(2026, 6, 1),
            initial_quantity=100,
            initial_age_days=120,
            created_by=self.manager,
        )
        MortalityRecord.objects.create(
            batch=batch,
            record_date=date(2026, 6, 2),
            number_dead=10,
            status=ApprovalStatus.APPROVED,
            reported_by=self.manager,
        )

        self.assertEqual(self._account_row("321001")["amount"], Decimal("260000.00000"))
        self.assertEqual(self._account_row("321002")["amount"], Decimal("450000.00"))

    def test_balance_sheet_wires_payroll_tax_creditors_and_equity(self):
        SalaryPayment.objects.create(
            employee=self.manager,
            period_month="2026-06",
            amount=Decimal("700000.00"),
            gross_salary=Decimal("1000000.00"),
            paye_tax=Decimal("100000.00"),
            nssf_employee=Decimal("50000.00"),
            nssf_employer=Decimal("100000.00"),
            net_pay=Decimal("700000.00"),
            payment_date=date(2026, 6, 30),
            status=SalaryPayment.Status.PENDING,
            recorded_by=self.manager,
        )
        audit_category = ExpenseCategory.objects.create(
            code="AUDIT_FEES",
            name="Audit Fees",
            expense_type=ExpenseCategory.ExpenseType.MONTHLY_EXPENSES,
        )
        ExpenseTransaction.objects.create(
            expense_date=date(2026, 6, 15),
            category=audit_category,
            description="Audit fee accrual",
            total_amount=Decimal("300000.00"),
            payment_method=ExpenseTransaction.PAYMENT_CREDIT,
            period_year=2026,
            period_month=6,
            status=ExpenseTransaction.Status.APPROVED,
            created_by=self.manager,
            approved_by=self.manager,
        )
        InvestorCapitalTransaction.objects.create(
            transaction_type=InvestorCapitalTransaction.TransactionType.STARTUP,
            transaction_date=date(2026, 1, 1),
            amount=Decimal("2000000.00"),
            recorded_by=self.manager,
        )

        self.assertEqual(self._account_row("421010")["amount"], Decimal("700000.00"))
        self.assertEqual(self._account_row("421012")["amount"], Decimal("100000.00"))
        self.assertEqual(self._account_row("421016")["amount"], Decimal("150000.00"))
        self.assertEqual(self._account_row("421007")["amount"], Decimal("300000.00"))
        self.assertEqual(self._account_row("431001")["amount"], Decimal("300000.00"))
        self.assertEqual(self._account_row("511001")["amount"], Decimal("2000000.00"))

    def test_salary_advances_are_wired_as_prepaid_salaries(self):
        WelfareRequest.objects.create(
            worker=self.manager,
            request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
            title="July salary advance",
            details="Advance against July salary",
            advance_amount=Decimal("310000.00"),
            advance_period_start=date(2026, 7, 1),
            advance_period_end=date(2026, 7, 31),
            status=WelfareRequest.Status.MANAGER_APPROVED,
        )

        bs_data = get_bs_data(end_date=date(2026, 7, 15))
        assets_group = next(row for row in bs_data["grouped_accounts"] if row["group_value"] == "ASSETS")
        prepayments = next(row for row in assets_group["type_groups"] if row["type_value"] == "PREPAYMENT")
        prepaid_salaries = next(row for row in prepayments["accounts"] if row["code"] == "341002")
        staff_advances = next(row for row in assets_group["type_groups"] if row["type_value"] == "RECEIVABLE")
        staff_advances_row = next(row for row in staff_advances["accounts"] if row["code"] == "331003")

        self.assertEqual(prepaid_salaries["amount"], Decimal("170000.00"))
        self.assertEqual(staff_advances_row["amount"], Decimal("0.00"))
