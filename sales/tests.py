from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import InventoryTransaction, Item, ItemCategory, Store
from poultry.models import ApprovalStatus, PoultryBatch, PoultryHouse, egg_collection
from accounting.models import AccountingCode
from accounting.services import get_bs_data
from sales.models import Customer, CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem


class SalesPageTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			username="sales-user",
			password="pass1234",
		)

		self.house = PoultryHouse.objects.create(
			house_code="HSE-SALES",
			name="Sales House",
			capacity=500,
		)
		self.batch = PoultryBatch.objects.create(
			batch_code="BATCH-SALES-1",
			house=self.house,
			breed="Layers",
			supplier_name="Source",
			date_stocked=date(2026, 5, 1),
			initial_quantity=300,
			initial_age_days=120,
			status=PoultryBatch.Status.ACTIVE,
			created_by=self.user,
		)

		egg_collection.objects.create(
			batch=self.batch,
			collection_date=date(2026, 5, 8),
			eggs_collected=120,
			eggs_rejected=10,
			status=ApprovalStatus.APPROVED,
			collected_by=self.user,
		)

		self.store = Store.objects.create(name="General Store")
		manure_cat, _ = ItemCategory.objects.get_or_create(code="MANURE", defaults={"name": "Manure"})
		birds_cat, _ = ItemCategory.objects.get_or_create(code="OFF_LAYER", defaults={"name": "Off Layers"})
		self.manure_item = Item.objects.create(name="Dry Manure", category=manure_cat, unit="kg")
		self.offlayer_item = Item.objects.create(name="Off Layer Bird", category=birds_cat, unit="birds")

		InventoryTransaction.objects.create(
			tx_date=date(2026, 5, 8),
			tx_type=InventoryTransaction.TxType.IN_,
			store=self.store,
			item=self.manure_item,
			quantity=Decimal("50.000"),
			created_by=self.user,
		)
		InventoryTransaction.objects.create(
			tx_date=date(2026, 5, 9),
			tx_type=InventoryTransaction.TxType.OUT,
			store=self.store,
			item=self.manure_item,
			quantity=Decimal("5.000"),
			created_by=self.user,
		)
		InventoryTransaction.objects.create(
			tx_date=date(2026, 5, 8),
			tx_type=InventoryTransaction.TxType.IN_,
			store=self.store,
			item=self.offlayer_item,
			quantity=Decimal("20.000"),
			created_by=self.user,
		)

		customer = Customer.objects.create(name="Walk In")
		invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-0001",
			customer=customer,
			invoice_date=date(2026, 5, 8),
			subtotal=Decimal("1000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("1000.00"),
			status=SaleInvoice.Status.PAID,
			delivery_status=SaleInvoice.DeliveryStatus.DELIVERED,
			created_by=self.user,
		)
		SaleItem.objects.create(
			invoice=invoice,
			product_name="Eggs",
			quantity=Decimal("20.000"),
			unit="eggs",
			unit_price=Decimal("500.00"),
			line_total=Decimal("10000.00"),
		)

	def test_sales_page_uses_live_db_stock_values(self):
		self.client.force_login(self.user)
		response = self.client.get(reverse("sales"))

		cards = {c["key"]: c for c in response.context["stock_cards"]}

		self.assertEqual(cards["eggs"]["unit"], "trays")
		self.assertEqual(cards["eggs"]["source_qty"], Decimal("3.667"))
		self.assertEqual(cards["eggs"]["sold_qty"], Decimal("0.667"))
		self.assertEqual(cards["eggs"]["available_qty"], Decimal("3.000"))
		self.assertEqual(cards["eggs"]["available_display"], "3 trays")

		self.assertEqual(cards["manure"]["source_qty"], Decimal("45.000"))
		self.assertEqual(cards["manure"]["available_qty"], Decimal("45.000"))

		self.assertEqual(cards["off_layers"]["source_qty"], Decimal("20.000"))
		self.assertEqual(cards["off_layers"]["available_qty"], Decimal("20.000"))

	def test_sales_page_uses_customer_dropdown_from_db(self):
		Customer.objects.create(name="Dropdown Buyer", is_active=True)
		Customer.objects.create(name="Inactive Buyer", is_active=False)
		self.client.force_login(self.user)

		response = self.client.get(reverse("sales"))

		self.assertContains(response, '<select class="form-select" name="customer" id="customerName" required>', html=False)
		self.assertContains(response, '<option value="Dropdown Buyer">Dropdown Buyer</option>', html=False)
		self.assertNotContains(response, '<option value="Inactive Buyer">Inactive Buyer</option>', html=False)

	def test_post_sale_creates_invoice_item_ledger_and_payment(self):
		Customer.objects.create(name="City Buyer")
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("sales"),
			{
				"customer": "City Buyer",
				"phone": "0700000000",
				"product": "eggs",
				"quantity": "2",
				"price": "5000",
				"deposit": "3000",
				"payment_method": "CASH",
				"sale_type": "instant",
				"notes": "Morning pickup",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice = SaleInvoice.objects.order_by("-invoice_id").first()
		item = SaleItem.objects.filter(invoice=invoice).first()
		ledger = ReceivableLedger.objects.get(invoice=invoice)
		payment = CustomerPayment.objects.get(invoice=invoice)

		self.assertEqual(invoice.customer.name, "City Buyer")
		self.assertEqual(item.product_name, "Eggs")
		self.assertEqual(item.unit, "trays")
		self.assertEqual(item.quantity, Decimal("2"))
		self.assertEqual(invoice.total_amount, Decimal("10000.00"))
		self.assertEqual(invoice.status, SaleInvoice.Status.ISSUED)
		self.assertEqual(ledger.amount_paid, Decimal("3000.00"))
		self.assertEqual(ledger.balance, Decimal("7000.00"))
		self.assertEqual(payment.amount, Decimal("3000.00"))
		self.assertTrue(
			AccountingCode.objects.filter(
				prefix="SE",
				account_type="REVENUE",
				content_type__model="saleitem",
				object_id=item.pk,
			).exists()
		)

	def test_customer_profile_can_save_payment_preferences(self):
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("customers"),
			{
				"name": "Regular Buyer",
				"contact_person": "Amina",
				"phone_number": "0700000111",
				"email": "amina@example.com",
				"address": "Kampala",
				"preferred_payment_method": "MOMO",
				"momo_receiving_number": "0777000111",
				"allow_credit": "on",
				"pay_wht": "on",
				"credit_limit": "500000",
				"credit_days": "14",
				"is_active": "on",
			},
		)

		customer = Customer.objects.get(name="Regular Buyer")
		self.assertRedirects(response, reverse("customer_profile", args=[customer.pk]))
		self.assertEqual(customer.preferred_payment_method, Customer.PaymentMethod.MOMO)
		self.assertEqual(customer.momo_receiving_number, "0777000111")
		self.assertTrue(customer.pay_wht)
		self.assertEqual(customer.credit_limit, Decimal("500000.00"))
		self.assertEqual(customer.credit_days, 14)

	def test_wht_customer_sale_records_wht_receivable_on_balance_sheet(self):
		Customer.objects.create(
			name="WHT Buyer",
			phone_number="0700999888",
			pay_wht=True,
		)
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("sales"),
			{
				"customer": "WHT Buyer",
				"phone": "0700999888",
				"product": "eggs",
				"quantity": "1",
				"price": "10000",
				"deposit": "0",
				"payment_method": "CASH",
				"sale_type": "instant",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice = SaleInvoice.objects.order_by("-invoice_id").first()
		ledger = ReceivableLedger.objects.get(invoice=invoice)

		self.assertEqual(invoice.customer.name, "WHT Buyer")
		self.assertEqual(invoice.total_amount, Decimal("10000.00"))
		self.assertEqual(invoice.wht_amount, Decimal("600.00"))
		self.assertEqual(ledger.amount_due, Decimal("9400.00"))
		self.assertEqual(ledger.balance, Decimal("9400.00"))

		bs_data = get_bs_data()
		assets_group = next(row for row in bs_data["grouped_accounts"] if row["group_value"] == "ASSETS")
		receivables = next(row for row in assets_group["type_groups"] if row["type_value"] == "RECEIVABLE")
		wht_row = next(row for row in receivables["accounts"] if row["code"] == f"WHT-{invoice.invoice_no}")

		self.assertEqual(wht_row["account_name"], f"WHT Receivable / {invoice.invoice_no} - WHT Buyer")
		self.assertEqual(wht_row["amount"], Decimal("600.00"))

	def test_sale_method_can_override_customer_default_payment_method(self):
		Customer.objects.create(
			name="Profile Buyer",
			phone_number="0700444555",
			preferred_payment_method=Customer.PaymentMethod.MOMO,
			momo_receiving_number="0777444555",
		)
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("sales"),
			{
				"customer": "Profile Buyer",
				"phone": "0700444555",
				"product": "eggs",
				"quantity": "1",
				"price": "5000",
				"deposit": "5000",
				"payment_method": "BANK",
				"sale_type": "instant",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice = SaleInvoice.objects.order_by("-invoice_id").first()
		payment = CustomerPayment.objects.get(invoice=invoice)
		self.assertEqual(invoice.payment_method, Customer.PaymentMethod.BANK)
		self.assertEqual(payment.method, CustomerPayment.Method.BANK)

	def test_post_off_layer_sale_uses_off_layer_accounting_prefix(self):
		Customer.objects.create(name="Bird Buyer")
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("sales"),
			{
				"customer": "Bird Buyer",
				"phone": "0700333444",
				"product": "off_layers",
				"quantity": "2",
				"price": "15000",
				"deposit": "30000",
				"payment_method": "CASH",
				"sale_type": "instant",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice = SaleInvoice.objects.order_by("-invoice_id").first()
		item = SaleItem.objects.get(invoice=invoice)

		self.assertEqual(item.product_name, "Off Layer Birds")
		self.assertTrue(
			AccountingCode.objects.filter(
				prefix="SO",
				account_type="REVENUE",
				content_type__model="saleitem",
				object_id=item.pk,
			).exists()
		)

	def test_post_sale_overpayment_creates_negative_balance(self):
		Customer.objects.create(name="Credit Buyer")
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("sales"),
			{
				"customer": "Credit Buyer",
				"phone": "0700111222",
				"product": "eggs",
				"quantity": "1",
				"price": "2000",
				"deposit": "3000",
				"payment_method": "CASH",
				"sale_type": "instant",
				"notes": "Overpaid intentionally",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice = SaleInvoice.objects.order_by("-invoice_id").first()
		ledger = ReceivableLedger.objects.get(invoice=invoice)
		payment = CustomerPayment.objects.get(invoice=invoice)

		self.assertEqual(invoice.total_amount, Decimal("2000.00"))
		self.assertEqual(invoice.status, SaleInvoice.Status.PAID)
		self.assertEqual(ledger.amount_paid, Decimal("3000.00"))
		self.assertEqual(ledger.balance, Decimal("-1000.00"))
		self.assertEqual(payment.amount, Decimal("3000.00"))

	def test_booking_without_payment_is_saved_as_draft(self):
		Customer.objects.create(name="Booking Buyer")
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("sales"),
			{
				"customer": "Booking Buyer",
				"phone": "0700222333",
				"product": "eggs",
				"quantity": "1",
				"price": "5000",
				"deposit": "0",
				"payment_method": "CASH",
				"sale_type": "booking",
				"delivery_date": "2026-05-12",
				"notes": "Booked for later pickup",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice = SaleInvoice.objects.order_by("-invoice_id").first()
		self.assertEqual(invoice.status, SaleInvoice.Status.DRAFT)
		self.assertContains(response, "Booked")

	def test_pending_orders_metrics_show_in_sales_and_orders_pages(self):
		pending_customer = Customer.objects.create(name="Pending Buyer")
		pending_invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-0999",
			customer=pending_customer,
			invoice_date=date(2026, 5, 9),
			due_date=date(2026, 5, 10),
			subtotal=Decimal("7000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("7000.00"),
			status=SaleInvoice.Status.ISSUED,
			delivery_status=SaleInvoice.DeliveryStatus.PENDING,
			created_by=self.user,
		)
		SaleItem.objects.create(
			invoice=pending_invoice,
			product_name="Eggs",
			quantity=Decimal("14.000"),
			unit="eggs",
			unit_price=Decimal("500.00"),
			line_total=Decimal("7000.00"),
		)
		ReceivableLedger.objects.create(
			invoice=pending_invoice,
			amount_due=Decimal("7000.00"),
			amount_paid=Decimal("2000.00"),
			balance=Decimal("5000.00"),
		)

		self.client.force_login(self.user)
		sales_response = self.client.get(reverse("sales"))
		orders_response = self.client.get(reverse("orders"))

		self.assertEqual(sales_response.status_code, 200)
		self.assertEqual(orders_response.status_code, 200)
		self.assertEqual(sales_response.context["pending_orders_count"], 1)
		self.assertEqual(orders_response.context["pending_orders_count"], 1)
		self.assertEqual(orders_response.context["pending_orders_outstanding_value"], Decimal("5000"))
		self.assertContains(sales_response, "Upcoming Orders")
		self.assertContains(orders_response, "Pending Buyer")

	def test_orders_delivered_action_closes_pending_invoice(self):
		customer = Customer.objects.create(name="Deliver Buyer")
		invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-1000",
			customer=customer,
			invoice_date=date(2026, 5, 9),
			due_date=date(2026, 5, 11),
			subtotal=Decimal("9000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("9000.00"),
			status=SaleInvoice.Status.ISSUED,
			delivery_status=SaleInvoice.DeliveryStatus.PENDING,
			created_by=self.user,
		)
		ReceivableLedger.objects.create(
			invoice=invoice,
			amount_due=Decimal("9000.00"),
			amount_paid=Decimal("9000.00"),
			balance=Decimal("0.00"),
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse("orders"),
			{"action": "deliver", "invoice_id": str(invoice.invoice_id)},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice.refresh_from_db()
		ledger = ReceivableLedger.objects.get(invoice=invoice)

		self.assertEqual(invoice.status, SaleInvoice.Status.PAID)
		self.assertEqual(invoice.delivery_status, SaleInvoice.DeliveryStatus.DELIVERED)
		self.assertEqual(ledger.amount_paid, Decimal("9000.00"))
		self.assertEqual(ledger.balance, Decimal("0.00"))

	def test_orders_delivered_action_rejects_unpaid_invoice(self):
		customer = Customer.objects.create(name="Unpaid Deliver Buyer")
		invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-1002",
			customer=customer,
			invoice_date=date(2026, 5, 9),
			subtotal=Decimal("6000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("6000.00"),
			status=SaleInvoice.Status.ISSUED,
			delivery_status=SaleInvoice.DeliveryStatus.PENDING,
			created_by=self.user,
		)
		ReceivableLedger.objects.create(
			invoice=invoice,
			amount_due=Decimal("6000.00"),
			amount_paid=Decimal("0.00"),
			balance=Decimal("6000.00"),
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse("orders"),
			{"action": "deliver", "invoice_id": str(invoice.invoice_id)},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice.refresh_from_db()
		self.assertEqual(invoice.status, SaleInvoice.Status.ISSUED)
		self.assertEqual(invoice.delivery_status, SaleInvoice.DeliveryStatus.PENDING)

	def test_egg_tray_display_in_orders_items(self):
		customer = Customer.objects.create(name="Tray Text Buyer")
		invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-1001",
			customer=customer,
			invoice_date=date(2026, 5, 9),
			subtotal=Decimal("10000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("10000.00"),
			status=SaleInvoice.Status.ISSUED,
			delivery_status=SaleInvoice.DeliveryStatus.PENDING,
			created_by=self.user,
		)
		SaleItem.objects.create(
			invoice=invoice,
			product_name="Eggs",
			quantity=Decimal("13.733"),
			unit="trays",
			unit_price=Decimal("10000.00"),
			line_total=Decimal("137330.00"),
		)
		ReceivableLedger.objects.create(
			invoice=invoice,
			amount_due=Decimal("137330.00"),
			amount_paid=Decimal("0.00"),
			balance=Decimal("137330.00"),
		)

		self.client.force_login(self.user)
		response = self.client.get(reverse("orders"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "13 trays and 22 eggs")

	def test_orders_record_payment_updates_balance_and_status(self):
		customer = Customer.objects.create(name="Pay Buyer")
		invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-1003",
			customer=customer,
			invoice_date=date(2026, 5, 9),
			subtotal=Decimal("12000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("12000.00"),
			status=SaleInvoice.Status.DRAFT,
			delivery_status=SaleInvoice.DeliveryStatus.PENDING,
			notes="Sale type: Booking",
			created_by=self.user,
		)
		ReceivableLedger.objects.create(
			invoice=invoice,
			amount_due=Decimal("12000.00"),
			amount_paid=Decimal("0.00"),
			balance=Decimal("12000.00"),
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse("orders"),
			{
				"action": "record_payment",
				"invoice_id": str(invoice.invoice_id),
				"payment_amount": "5000",
				"payment_method": "CASH",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice.refresh_from_db()
		ledger = ReceivableLedger.objects.get(invoice=invoice)
		payment = CustomerPayment.objects.filter(invoice=invoice).latest("payment_id")

		self.assertEqual(invoice.status, SaleInvoice.Status.ISSUED)
		self.assertEqual(ledger.amount_paid, Decimal("5000.00"))
		self.assertEqual(ledger.balance, Decimal("7000.00"))
		self.assertEqual(payment.amount, Decimal("5000.00"))

	def test_orders_cancel_action_marks_invoice_cancelled(self):
		customer = Customer.objects.create(name="Cancel Buyer")
		invoice = SaleInvoice.objects.create(
			invoice_no="INV-2026-1004",
			customer=customer,
			invoice_date=date(2026, 5, 9),
			subtotal=Decimal("3000.00"),
			discount_amount=Decimal("0.00"),
			total_amount=Decimal("3000.00"),
			status=SaleInvoice.Status.ISSUED,
			delivery_status=SaleInvoice.DeliveryStatus.PENDING,
			created_by=self.user,
		)
		ReceivableLedger.objects.create(
			invoice=invoice,
			amount_due=Decimal("3000.00"),
			amount_paid=Decimal("0.00"),
			balance=Decimal("3000.00"),
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse("orders"),
			{
				"action": "cancel",
				"invoice_id": str(invoice.invoice_id),
				"cancel_reason": "Customer postponed",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		invoice.refresh_from_db()
		self.assertEqual(invoice.status, SaleInvoice.Status.CANCELLED)
		self.assertEqual(invoice.delivery_status, SaleInvoice.DeliveryStatus.CANCELLED)
