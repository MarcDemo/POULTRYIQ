from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.utils.timezone import now

from expenses.models import ExpenseAllocation, ExpenseTransaction
from hr.models import WagePayment
from payroll.models import SalaryPayment
from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem

from poultry.models import (
    ApprovalStatus,
    FeedRecord,
    InvestorKpiTarget,
    MortalityRecord,
    PoultryBatch,
    PoultryHouse,
    egg_collection,
)


THEME_META = {
    "profitability": {"label": "Profitability", "icon": "bi-bank"},
    "cash_flow": {"label": "Cash Flow", "icon": "bi-wallet2"},
    "production_cost": {"label": "Cost of Production", "icon": "bi-coin"},
    "sales_pricing": {"label": "Sales & Pricing", "icon": "bi-cart-check"},
    "batch_performance": {"label": "Batch / House Performance", "icon": "bi-house-gear"},
    "risk_loss": {"label": "Risk & Loss Drivers", "icon": "bi-exclamation-triangle"},
}

DIMENSIONS = {
    "period": {"label": "Trend over time", "icon": "bi-calendar3"},
    "house": {"label": "House", "icon": "bi-house"},
    "batch": {"label": "Batch", "icon": "bi-collection"},
    "product": {"label": "Product", "icon": "bi-basket"},
    "customer": {"label": "Customer", "icon": "bi-person-lines-fill"},
    "expense_category": {"label": "Expense category", "icon": "bi-receipt"},
    "supplier": {"label": "Supplier", "icon": "bi-truck"},
    "payment_method": {"label": "Payment method", "icon": "bi-credit-card"},
    "capital_type": {"label": "Capital type", "icon": "bi-safe"},
    "feed_type": {"label": "Feed type", "icon": "bi-bag"},
    "mortality_cause": {"label": "Mortality cause", "icon": "bi-exclamation-triangle"},
    "health_status": {"label": "Health status", "icon": "bi-clipboard-pulse"},
    "inventory_item": {"label": "Inventory item", "icon": "bi-box"},
    "store": {"label": "Store", "icon": "bi-shop"},
    "staff": {"label": "Staff", "icon": "bi-people"},
    "attendance_status": {"label": "Attendance status", "icon": "bi-person-check"},
}


def _metric(key, label, theme, unit, value_format, dimensions, featured=False, source="Farm records"):
    unit_group = {
        "currency": "currency",
        "percent": "percent",
        "kg": "mass",
        "ratio": "ratio",
        "decimal": "decimal",
    }.get(value_format, "count")
    return {
        "key": key,
        "label": label,
        "theme": theme,
        "unit": unit,
        "format": value_format,
        "unitGroup": unit_group,
        "dimensions": list(dimensions),
        "featured": featured,
        "source": source,
        "definition": f"{label} calculated from approved PoultryIQ {source.lower()}.",
        "comparisonSupported": key not in {
            "receivables_outstanding", "active_batches", "live_birds", "house_capacity",
            "house_utilization", "inventory_balance_qty", "inventory_value", "low_stock_rules",
            "active_workers", "treatments_due", "vaccinations_overdue",
        },
        "additive": value_format in {"currency", "kg", "number", "decimal"} and key not in {
            "cost_per_egg", "feed_cost_per_egg", "revenue_per_egg", "average_invoice_value",
            "average_egg_weight", "house_utilization",
        },
    }


_RAW_METRICS = [
    ("sales_revenue", "Sales revenue", "sales_pricing", "UGX", "currency", "period product customer house batch", True),
    ("cash_received", "Cash received", "cash_flow", "UGX", "currency", "period customer payment_method", True),
    ("total_expenses", "Total expenses", "profitability", "UGX", "currency", "period expense_category supplier", True),
    ("profit", "Profit", "profitability", "UGX", "currency", "period house batch", True),
    ("profit_margin", "Profit margin", "profitability", "%", "percent", "period house batch", True),
    ("expense_ratio", "Expense ratio", "profitability", "%", "percent", "period house batch", False),
    ("feed_cost", "Feed cost", "production_cost", "UGX", "currency", "period expense_category supplier house batch", True),
    ("labour_cost", "Labour cost", "production_cost", "UGX", "currency", "period staff house batch", True),
    ("cost_per_egg", "Cost per saleable egg", "production_cost", "UGX", "currency", "period house batch", True),
    ("feed_cost_per_egg", "Feed cost per saleable egg", "production_cost", "UGX", "currency", "period house batch", False),
    ("revenue_per_egg", "Egg sales revenue per saleable egg", "sales_pricing", "UGX", "currency", "period house batch", False),
    ("rejected_egg_loss", "Rejected egg revenue loss estimate", "risk_loss", "UGX", "currency", "period house batch", True),
    ("mortality_loss_estimate", "Mortality loss estimate", "risk_loss", "UGX", "currency", "period house batch", False),
    ("batch_revenue", "Batch revenue", "batch_performance", "UGX", "currency", "period house batch", True),
    ("batch_allocated_expenses", "Batch allocated expenses", "batch_performance", "UGX", "currency", "period house batch", True),
    ("batch_profit", "Batch profit", "batch_performance", "UGX", "currency", "period house batch", True),
    ("collection_rate", "Collection rate", "cash_flow", "%", "percent", "period customer", True),
    ("receivables_outstanding", "Receivables outstanding", "cash_flow", "UGX", "currency", "customer", False),
    ("invoices_issued", "Invoices issued", "cash_flow", "records", "number", "period customer", False),
    ("average_invoice_value", "Average invoice value", "sales_pricing", "UGX", "currency", "period customer", False),
    ("manure_sales", "Manure sales", "sales_pricing", "UGX", "currency", "period customer house batch", False),
    ("manure_quantity_sold", "Manure quantity sold", "sales_pricing", "units", "decimal", "period customer house batch", False),
    ("capital_invested", "Capital invested", "profitability", "UGX", "currency", "period capital_type", False),
    ("owner_withdrawals", "Owner withdrawals", "profitability", "UGX", "currency", "period capital_type", False),
    ("eggs_collected", "Eggs collected", "batch_performance", "eggs", "number", "period house batch", True),
    ("rejected_eggs", "Broken / rejected eggs", "batch_performance", "eggs", "number", "period house batch", False),
    ("net_eggs", "Net eggs", "batch_performance", "eggs", "number", "period house batch", True),
    ("egg_rejection_rate", "Egg rejection rate", "batch_performance", "%", "percent", "period house batch", False),
    ("average_egg_weight", "Average egg weight", "batch_performance", "g", "decimal", "period house batch", False),
    ("active_batches", "Active batches", "batch_performance", "batches", "number", "house", False),
    ("live_birds", "Live birds", "batch_performance", "birds", "number", "house batch", True),
    ("birds_stocked", "Birds stocked", "batch_performance", "birds", "number", "house batch", False),
    ("house_capacity", "House capacity", "batch_performance", "birds", "number", "house", False),
    ("house_utilization", "House utilization", "batch_performance", "%", "percent", "period house", False),
    ("batch_purchase_cost", "Batch purchase cost", "batch_performance", "UGX", "currency", "batch", False),
    ("feed_used_kg", "Feed used", "production_cost", "kg", "kg", "period house batch feed_type", True),
    ("feed_per_egg", "Feed per egg", "production_cost", "kg", "ratio", "period house batch", True),
    ("feed_mixture_kg", "Feed mixed", "production_cost", "kg", "kg", "period staff", False),
    ("feed_records", "Feed records", "production_cost", "records", "number", "period house batch feed_type", False),
    ("feed_inventory_out", "Feed stock issued", "production_cost", "kg", "kg", "period inventory_item store", False),
    ("feed_purchase_cost", "Feed purchase cost", "production_cost", "UGX", "currency", "period expense_category supplier", False),
    ("deaths", "Deaths", "risk_loss", "birds", "number", "period house batch mortality_cause", True),
    ("mortality_rate", "Mortality rate", "risk_loss", "%", "percent", "period house batch", True),
    ("sickness_cases", "Sickness cases", "risk_loss", "cases", "number", "period house health_status", True),
    ("birds_affected", "Birds affected", "risk_loss", "birds", "number", "period house health_status", False),
    ("treatments_due", "Treatments due", "risk_loss", "items", "number", "period staff", False),
    ("treatments_given", "Treatments given", "risk_loss", "items", "number", "period staff", False),
    ("vaccinations_scheduled", "Vaccinations scheduled", "risk_loss", "schedules", "number", "period health_status house", False),
    ("vaccinations_administered", "Vaccinations administered", "risk_loss", "birds", "number", "period house", False),
    ("vaccinations_overdue", "Vaccinations overdue", "risk_loss", "schedules", "number", "period house", False),
    ("health_events", "Health events", "risk_loss", "events", "number", "period house", False),
    ("sickbay_cleanings", "Sickbay cleanings", "risk_loss", "records", "number", "period", False),
    ("cleaning_records", "Cleaning records", "risk_loss", "records", "number", "period house batch", False),
    ("houses_cleaned", "House cleaned checks", "risk_loss", "checks", "number", "period house", False),
    ("disinfections_done", "Disinfections done", "risk_loss", "checks", "number", "period house", False),
    ("water_changes", "Water changes", "risk_loss", "checks", "number", "period house", False),
    ("inventory_stock_in", "Stock in", "risk_loss", "units", "decimal", "period inventory_item store", False),
    ("inventory_stock_out", "Stock out", "risk_loss", "units", "decimal", "period inventory_item store", False),
    ("inventory_adjustments", "Stock adjustments", "risk_loss", "units", "decimal", "period inventory_item store", False),
    ("inventory_balance_qty", "Stock balance quantity", "risk_loss", "units", "decimal", "inventory_item store", False),
    ("inventory_value", "Inventory value estimate", "risk_loss", "UGX", "currency", "inventory_item store", False),
    ("low_stock_rules", "Low stock alerts", "risk_loss", "items", "number", "inventory_item store", False),
    ("active_workers", "Active workers", "production_cost", "workers", "number", "staff", False),
    ("attendance_present", "Attendance present", "production_cost", "records", "number", "period staff attendance_status", False),
    ("attendance_absent", "Attendance absent", "production_cost", "records", "number", "period staff attendance_status", False),
    ("hours_worked", "Hours worked", "production_cost", "hours", "decimal", "period staff", False),
    ("wages_paid", "Wages paid", "production_cost", "UGX", "currency", "period staff house batch", False),
    ("salary_paid", "Salaries paid", "production_cost", "UGX", "currency", "period staff", False),
]

METRIC_CATALOG = {
    row[0]: _metric(row[0], row[1], row[2], row[3], row[4], row[5].split(), row[6])
    for row in _RAW_METRICS
}

_LOWER_IS_BETTER = {
    "total_expenses", "expense_ratio", "feed_cost", "labour_cost", "cost_per_egg",
    "feed_cost_per_egg", "rejected_egg_loss", "mortality_loss_estimate",
    "batch_allocated_expenses", "receivables_outstanding", "rejected_eggs",
    "egg_rejection_rate", "feed_per_egg", "deaths", "mortality_rate",
}
_HIGHER_IS_BETTER = {
    "sales_revenue", "cash_received", "profit", "profit_margin", "revenue_per_egg",
    "batch_revenue", "batch_profit", "collection_rate", "eggs_collected", "net_eggs",
    "live_birds", "house_utilization",
}
for key, metric in METRIC_CATALOG.items():
    metric["performanceDirection"] = (
        "lower" if key in _LOWER_IS_BETTER else "higher" if key in _HIGHER_IS_BETTER else "neutral"
    )
    metric["permittedViews"] = (
        ["bar", "line", "area", "table"] if "period" in metric["dimensions"] else ["bar", "table"]
    )
    if metric["additive"]:
        metric["permittedViews"].append("pie")

TARGET_DIRECTIONS = {
    "profit_margin": InvestorKpiTarget.Direction.MINIMUM,
    "expense_ratio": InvestorKpiTarget.Direction.MAXIMUM,
    "collection_rate": InvestorKpiTarget.Direction.MINIMUM,
    "cost_per_egg": InvestorKpiTarget.Direction.MAXIMUM,
    "feed_cost_per_egg": InvestorKpiTarget.Direction.MAXIMUM,
    "feed_per_egg": InvestorKpiTarget.Direction.MAXIMUM,
    "revenue_per_egg": InvestorKpiTarget.Direction.MINIMUM,
    "egg_rejection_rate": InvestorKpiTarget.Direction.MAXIMUM,
    "mortality_rate": InvestorKpiTarget.Direction.MAXIMUM,
    "house_utilization": InvestorKpiTarget.Direction.MINIMUM,
}
for key, direction in TARGET_DIRECTIONS.items():
    METRIC_CATALOG[key]["targetable"] = True
    METRIC_CATALOG[key]["direction"] = direction
for metric in METRIC_CATALOG.values():
    metric.setdefault("targetable", False)
    metric.setdefault("direction", None)

GUIDED_QUESTIONS = {
    "loss_drivers": {
        "label": "Why am I making a loss?",
        "description": "Connect revenue, expenses, profit, margin, and cost pressure.",
        "theme": "profitability",
        "icon": "bi-graph-down-arrow",
        "metrics": ["sales_revenue", "total_expenses", "profit", "profit_margin", "expense_ratio"],
        "dimension": "period",
    },
    "high_costs": {
        "label": "Which costs are too high?",
        "description": "Compare total, feed, labour, and unit production costs.",
        "theme": "production_cost",
        "icon": "bi-receipt-cutoff",
        "metrics": ["total_expenses", "feed_cost", "labour_cost", "cost_per_egg", "feed_cost_per_egg"],
        "dimension": "period",
    },
    "batch_profitability": {
        "label": "Which batch or house is most profitable?",
        "description": "Rank linked revenue, allocated costs, profit, output, and feed.",
        "theme": "batch_performance",
        "icon": "bi-house-check",
        "metrics": ["batch_revenue", "batch_allocated_expenses", "batch_profit", "eggs_collected", "feed_used_kg"],
        "dimension": "batch",
    },
    "feed_margin": {
        "label": "Is feed cost hurting profit?",
        "description": "Relate feed spend and usage to egg output and profit.",
        "theme": "production_cost",
        "icon": "bi-bag-heart",
        "metrics": ["feed_cost", "feed_used_kg", "feed_per_egg", "feed_cost_per_egg", "profit"],
        "dimension": "period",
    },
    "customer_cash": {
        "label": "Which customers owe the farm?",
        "description": "Compare invoiced sales, cash received, receivables, and collection.",
        "theme": "cash_flow",
        "icon": "bi-person-exclamation",
        "metrics": ["sales_revenue", "cash_received", "receivables_outstanding", "collection_rate", "invoices_issued"],
        "dimension": "customer",
    },
    "expand_safely": {
        "label": "Can I expand safely?",
        "description": "Test profitability, collections, unit cost, mortality, and utilization.",
        "theme": "profitability",
        "icon": "bi-arrows-angle-expand",
        "metrics": ["profit", "profit_margin", "collection_rate", "cost_per_egg", "mortality_rate", "house_utilization"],
        "dimension": "period",
    },
}

FOCUSED_METRICS = set().union(*(question["metrics"] for question in GUIDED_QUESTIONS.values())) | set(TARGET_DIRECTIONS)


def catalogue_payload():
    themes = []
    for key, meta in THEME_META.items():
        themes.append({
            "key": key,
            **meta,
            "count": sum(1 for metric in METRIC_CATALOG.values() if metric["theme"] == key),
        })
    return {
        "version": 2,
        "themes": themes,
        "dimensions": [{"key": key, **value} for key, value in DIMENSIONS.items()],
        "questions": [{"key": key, **value} for key, value in GUIDED_QUESTIONS.items()],
        "metrics": list(METRIC_CATALOG.values()),
        "targetMetrics": [
            {**METRIC_CATALOG[key], "direction": direction}
            for key, direction in TARGET_DIRECTIONS.items()
        ],
    }


def previous_period(start_date, end_date):
    duration = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    return previous_end - timedelta(days=duration - 1), previous_end


def _decimal(value):
    return Decimal(str(value or 0))


def _number(value):
    return float(value) if value is not None else None


def _pct(numerator, denominator):
    denominator = _decimal(denominator)
    if denominator == 0:
        return None
    return _decimal(numerator) / denominator * Decimal("100")


def _combine_points(first, second, operation):
    first_map = {item["label"]: _decimal(item["value"]) for item in first}
    second_map = {item["label"]: _decimal(item["value"]) for item in second}
    labels = list(dict.fromkeys([item["label"] for item in first + second]))
    values = []
    for label in labels:
        value = operation(first_map.get(label, Decimal("0")), second_map.get(label, Decimal("0")))
        values.append({"label": label, "value": _number(value)})
    return values


class AnalysisContext:
    def __init__(self, start_date, end_date, group_by, scope_type="farm", scope_id=None):
        self.start_date = start_date
        self.end_date = end_date
        self.group_by = group_by
        self.scope_type = scope_type
        self.scope_id = int(scope_id) if scope_id else None
        self._cache = {}

    def _scope_batch_filter(self, prefix="batch"):
        if self.scope_type == "batch":
            return {f"{prefix}_id": self.scope_id}
        if self.scope_type == "house":
            return {f"{prefix}__house_id": self.scope_id}
        return {}

    def _period_label(self, value):
        if self.group_by == "day":
            return value.strftime("%d %b %Y")
        if self.group_by == "week":
            week_start = value - timedelta(days=value.weekday())
            return f"Week of {week_start.strftime('%d %b %Y')}"
        return value.strftime("%b %Y")

    def _period_points(self, queryset, date_field, value_field=None, aggregate="sum"):
        if aggregate == "count":
            rows = queryset.values(date_field).annotate(total=Count("pk")).order_by(date_field)
        elif aggregate == "avg":
            rows = queryset.values(date_field).annotate(total=Avg(value_field)).order_by(date_field)
        else:
            rows = queryset.values(date_field).annotate(total=Sum(value_field)).order_by(date_field)
        grouped = defaultdict(Decimal)
        for row in rows:
            row_date = row.get(date_field)
            if row_date is not None:
                grouped[self._period_label(row_date)] += _decimal(row["total"])
        return [{"label": label, "value": _number(value)} for label, value in grouped.items()]

    def _group_points(self, queryset, fields, value_field=None, aggregate="sum", labeler=None):
        fields = [fields] if isinstance(fields, str) else fields
        if aggregate == "count":
            rows = queryset.values(*fields).annotate(total=Count("pk")).order_by()
        elif aggregate == "avg":
            rows = queryset.values(*fields).annotate(total=Avg(value_field)).order_by()
        else:
            rows = queryset.values(*fields).annotate(total=Sum(value_field)).order_by()
        points = []
        for row in rows:
            label = labeler(row) if labeler else row.get(fields[0])
            points.append({"label": str(label or "Unassigned"), "value": _number(row["total"] or 0)})
        points.sort(key=lambda item: abs(item["value"] or 0), reverse=True)
        return points[:15]

    def _sales_items(self):
        key = "sales_items"
        if key not in self._cache:
            queryset = SaleItem.objects.filter(
                invoice__invoice_date__range=(self.start_date, self.end_date),
            ).exclude(invoice__status=SaleInvoice.Status.CANCELLED)
            self._cache[key] = queryset.filter(**self._scope_batch_filter("batch"))
        return self._cache[key]

    def _sales_invoices(self):
        return SaleInvoice.objects.filter(
            invoice_date__range=(self.start_date, self.end_date),
        ).exclude(status=SaleInvoice.Status.CANCELLED)

    def _expense_allocations(self, category_codes=None):
        queryset = ExpenseAllocation.objects.filter(
            expense__expense_date__range=(self.start_date, self.end_date),
        ).exclude(expense__status=ExpenseTransaction.Status.REJECTED)
        if self.scope_type != "farm":
            queryset = queryset.filter(**self._scope_batch_filter("batch"))
        if category_codes:
            category_filter = Q()
            for code in category_codes:
                category_filter |= Q(expense__category__code__iexact=code)
            queryset = queryset.filter(category_filter)
        return queryset

    def _expenses(self, category_codes=None):
        queryset = ExpenseTransaction.objects.filter(
            expense_date__range=(self.start_date, self.end_date),
        ).exclude(status=ExpenseTransaction.Status.REJECTED)
        if category_codes:
            category_filter = Q()
            for code in category_codes:
                category_filter |= Q(category__code__iexact=code)
            queryset = queryset.filter(category_filter)
        return queryset

    def _expense_result(self, dimension, category_codes=None):
        if self.scope_type == "farm":
            queryset = self._expenses(category_codes)
            value = queryset.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
            if dimension == "period":
                points = self._period_points(queryset, "expense_date", "total_amount")
            elif dimension == "expense_category":
                points = self._group_points(queryset, "category__name", "total_amount")
            elif dimension == "supplier":
                points = self._group_points(queryset, "supplier_name", "total_amount")
            else:
                points = [{"label": "Selected period", "value": _number(value)}]
            return value, points, queryset.count()

        queryset = self._expense_allocations(category_codes)
        value = queryset.aggregate(total=Sum("amount_allocated"))["total"] or Decimal("0")
        if dimension == "period":
            points = self._period_points(queryset, "expense__expense_date", "amount_allocated")
        elif dimension == "batch":
            points = self._group_points(queryset, "batch__batch_code", "amount_allocated")
        elif dimension == "house":
            points = self._group_points(queryset, ["batch__house__house_code", "batch__house__name"], "amount_allocated", labeler=lambda row: row.get("batch__house__name") or row.get("batch__house__house_code"))
        else:
            points = [{"label": "Selected period", "value": _number(value)}]
        return value, points, queryset.count()

    def _allocated_expense_result(self, dimension, category_codes=None):
        queryset = self._expense_allocations(category_codes)
        value = queryset.aggregate(total=Sum("amount_allocated"))["total"] or Decimal("0")
        if dimension == "period":
            points = self._period_points(queryset, "expense__expense_date", "amount_allocated")
        elif dimension == "batch":
            points = self._group_points(queryset, "batch__batch_code", "amount_allocated")
        elif dimension == "house":
            points = self._group_points(
                queryset,
                ["batch__house__house_code", "batch__house__name"],
                "amount_allocated",
                labeler=lambda row: row.get("batch__house__name") or row.get("batch__house__house_code"),
            )
        else:
            points = [{"label": "Selected period", "value": _number(value)}]
        return value, points, queryset.count()

    def _revenue_result(self, dimension):
        if self.scope_type == "farm" and dimension in {"period", "customer"}:
            queryset = self._sales_invoices()
            value = queryset.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
            if dimension == "period":
                points = self._period_points(queryset, "invoice_date", "total_amount")
            else:
                points = self._group_points(queryset, "customer__name", "total_amount")
            return value, points, queryset.count()
        queryset = self._sales_items()
        value = queryset.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
        dimension_map = {
            "period": ("invoice__invoice_date", None),
            "customer": ("invoice__customer__name", None),
            "product": ("product_name", None),
            "batch": ("batch__batch_code", None),
            "house": (["batch__house__house_code", "batch__house__name"], lambda row: row.get("batch__house__name") or row.get("batch__house__house_code")),
        }
        if dimension == "period":
            points = self._period_points(queryset, "invoice__invoice_date", "line_total")
        elif dimension in dimension_map:
            fields, labeler = dimension_map[dimension]
            points = self._group_points(queryset, fields, "line_total", labeler=labeler)
        else:
            points = [{"label": "Selected period", "value": _number(value)}]
        return value, points, queryset.count()

    def _invoice_scope_ratios(self):
        if self.scope_type == "farm":
            return None
        if "invoice_scope_ratios" in self._cache:
            return self._cache["invoice_scope_ratios"]
        all_rows = SaleItem.objects.values("invoice_id").annotate(total=Sum("line_total"))
        scoped_rows = SaleItem.objects.filter(**self._scope_batch_filter("batch")).values("invoice_id").annotate(total=Sum("line_total"))
        totals = {row["invoice_id"]: _decimal(row["total"]) for row in all_rows}
        self._cache["invoice_scope_ratios"] = {
            row["invoice_id"]: (_decimal(row["total"]) / totals[row["invoice_id"]])
            for row in scoped_rows
            if totals.get(row["invoice_id"])
        }
        return self._cache["invoice_scope_ratios"]

    def _cash_result(self, dimension):
        queryset = CustomerPayment.objects.filter(payment_date__range=(self.start_date, self.end_date)).select_related("customer")
        ratios = self._invoice_scope_ratios()
        if ratios is None:
            value = queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            if dimension == "period":
                points = self._period_points(queryset, "payment_date", "amount")
            elif dimension == "customer":
                points = self._group_points(queryset, "customer__name", "amount")
            elif dimension == "payment_method":
                points = self._group_points(queryset, "method", "amount")
            else:
                points = [{"label": "Selected period", "value": _number(value)}]
            return value, points, queryset.count()

        grouped = defaultdict(Decimal)
        value = Decimal("0")
        records = 0
        for payment in queryset.filter(invoice_id__in=ratios):
            allocated = payment.amount * ratios[payment.invoice_id]
            value += allocated
            records += 1
            if dimension == "period":
                label = self._period_label(payment.payment_date)
            elif dimension == "customer":
                label = payment.customer.name
            else:
                label = "Selected period"
            grouped[label] += allocated
        return value, [{"label": label, "value": _number(amount)} for label, amount in grouped.items()], records

    def _receivables_result(self, dimension):
        queryset = ReceivableLedger.objects.select_related("invoice__customer")
        ratios = self._invoice_scope_ratios()
        grouped = defaultdict(Decimal)
        value = Decimal("0")
        records = 0
        for ledger in queryset if ratios is None else queryset.filter(invoice_id__in=ratios):
            amount = ledger.balance if ratios is None else ledger.balance * ratios[ledger.invoice_id]
            value += amount
            records += 1
            label = ledger.invoice.customer.name if dimension == "customer" else "Current snapshot"
            grouped[label] += amount
        return value, [{"label": label, "value": _number(amount)} for label, amount in grouped.items()], records

    def _egg_result(self, dimension, field):
        queryset = egg_collection.objects.filter(
            collection_date__range=(self.start_date, self.end_date),
            status=ApprovalStatus.APPROVED,
            **self._scope_batch_filter("batch"),
        )
        value = queryset.aggregate(total=Sum(field))["total"] or Decimal("0")
        if dimension == "period":
            points = self._period_points(queryset, "collection_date", field)
        elif dimension == "batch":
            points = self._group_points(queryset, "batch__batch_code", field)
        elif dimension == "house":
            points = self._group_points(queryset, ["batch__house__house_code", "batch__house__name"], field, labeler=lambda row: row.get("batch__house__name") or row.get("batch__house__house_code"))
        else:
            points = [{"label": "Selected period", "value": _number(value)}]
        return value, points, queryset.count()

    def _feed_result(self, dimension):
        queryset = FeedRecord.objects.filter(
            record_date__range=(self.start_date, self.end_date),
            status=ApprovalStatus.APPROVED,
            **self._scope_batch_filter("batch"),
        )
        value = queryset.aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0")
        if dimension == "period":
            points = self._period_points(queryset, "record_date", "quantity_kg")
        elif dimension == "batch":
            points = self._group_points(queryset, "batch__batch_code", "quantity_kg")
        elif dimension == "house":
            points = self._group_points(queryset, ["batch__house__house_code", "batch__house__name"], "quantity_kg", labeler=lambda row: row.get("batch__house__name") or row.get("batch__house__house_code"))
        elif dimension == "feed_type":
            points = self._group_points(queryset, "feed_type", "quantity_kg")
        else:
            points = [{"label": "Selected period", "value": _number(value)}]
        return value, points, queryset.count()

    def _deaths_result(self, dimension):
        queryset = MortalityRecord.objects.filter(
            record_date__range=(self.start_date, self.end_date),
            status=ApprovalStatus.APPROVED,
            **self._scope_batch_filter("batch"),
        )
        value = queryset.aggregate(total=Sum("number_dead"))["total"] or Decimal("0")
        if dimension == "period":
            points = self._period_points(queryset, "record_date", "number_dead")
        elif dimension == "batch":
            points = self._group_points(queryset, "batch__batch_code", "number_dead")
        elif dimension == "house":
            points = self._group_points(queryset, ["batch__house__house_code", "batch__house__name"], "number_dead", labeler=lambda row: row.get("batch__house__name") or row.get("batch__house__house_code"))
        elif dimension == "mortality_cause":
            points = self._group_points(queryset, "cause__name", "number_dead")
        else:
            points = [{"label": "Selected period", "value": _number(value)}]
        return value, points, queryset.count()

    def _live_birds(self, dimension):
        batches = PoultryBatch.objects.filter(date_stocked__lte=self.end_date, **self._scope_batch_filter("pk"))
        if self.scope_type == "house":
            batches = PoultryBatch.objects.filter(date_stocked__lte=self.end_date, house_id=self.scope_id)
        elif self.scope_type == "batch":
            batches = PoultryBatch.objects.filter(date_stocked__lte=self.end_date, pk=self.scope_id)
        rows = []
        total = Decimal("0")
        for batch in batches.select_related("house"):
            deaths = MortalityRecord.objects.filter(
                batch=batch,
                record_date__lte=self.end_date,
                status=ApprovalStatus.APPROVED,
            ).aggregate(total=Sum("number_dead"))["total"] or 0
            live = max(Decimal(batch.initial_quantity) - Decimal(deaths), Decimal("0"))
            total += live
            label = batch.batch_code if dimension == "batch" else (batch.house.name or batch.house.house_code)
            rows.append({"label": label, "value": _number(live)})
        return total, rows, batches.count()

    def resolve(self, key, dimension):
        cache_key = (key, dimension)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if key in {"sales_revenue", "batch_revenue"}:
            value, points, records = self._revenue_result(dimension)
        elif key == "total_expenses":
            value, points, records = self._expense_result(dimension)
        elif key == "batch_allocated_expenses":
            value, points, records = self._allocated_expense_result(dimension)
        elif key == "feed_cost":
            value, points, records = self._expense_result(dimension, {"FEED", "FEEDS"})
        elif key == "labour_cost":
            expense_value, expense_points, expense_records = self._expense_result(dimension, {"LABOUR", "WAGES", "SALARIES"})
            wages = WagePayment.objects.filter(payment_date__range=(self.start_date, self.end_date))
            wages = wages.filter(**self._scope_batch_filter("batch"))
            wage_value = wages.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            salary_value = Decimal("0")
            if self.scope_type == "farm":
                salary_value = SalaryPayment.objects.filter(
                    payment_date__range=(self.start_date, self.end_date)
                ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            value = expense_value + wage_value + salary_value
            points = expense_points
            records = expense_records + wages.count()
        elif key == "cash_received":
            value, points, records = self._cash_result(dimension)
        elif key == "receivables_outstanding":
            value, points, records = self._receivables_result(dimension)
        elif key == "invoices_issued":
            if self.scope_type == "farm":
                queryset = self._sales_invoices()
            else:
                queryset = SaleInvoice.objects.filter(items__in=self._sales_items()).distinct()
            value = queryset.count()
            if dimension == "period":
                points = self._period_points(queryset, "invoice_date", aggregate="count")
            elif dimension == "customer":
                points = self._group_points(queryset, "customer__name", aggregate="count")
            else:
                points = [{"label": "Selected period", "value": value}]
            records = value
        elif key in {"eggs_collected", "rejected_eggs"}:
            field = "eggs_collected" if key == "eggs_collected" else "eggs_rejected"
            value, points, records = self._egg_result(dimension, field)
        elif key == "net_eggs":
            collected = self.resolve("eggs_collected", dimension)
            rejected = self.resolve("rejected_eggs", dimension)
            value = _decimal(collected["value"]) - _decimal(rejected["value"])
            points = _combine_points(collected["points"], rejected["points"], lambda first, second: max(first - second, Decimal("0")))
            records = collected["records"]
        elif key == "egg_rejection_rate":
            collected = self.resolve("eggs_collected", dimension)
            rejected = self.resolve("rejected_eggs", dimension)
            value = _pct(rejected["value"], _decimal(collected["value"]) + _decimal(rejected["value"]))
            points = _combine_points(
                rejected["points"],
                collected["points"],
                lambda rejected_value, collected_value: _pct(rejected_value, rejected_value + collected_value),
            )
            records = collected["records"]
        elif key == "feed_used_kg":
            value, points, records = self._feed_result(dimension)
        elif key == "deaths":
            value, points, records = self._deaths_result(dimension)
        elif key == "live_birds":
            value, points, records = self._live_birds(dimension)
        elif key == "house_utilization":
            live = self.resolve("live_birds", "house")
            houses = PoultryHouse.objects.filter(is_active=True)
            if self.scope_type == "house":
                houses = houses.filter(pk=self.scope_id)
            elif self.scope_type == "batch":
                houses = houses.filter(batches__pk=self.scope_id)
            capacities = {house.name or house.house_code: Decimal(house.capacity) for house in houses}
            live_map = {item["label"]: _decimal(item["value"]) for item in live["points"]}
            points = [
                {"label": label, "value": _number(_pct(live_map.get(label, 0), capacity))}
                for label, capacity in capacities.items()
            ]
            total_capacity = sum(capacities.values(), Decimal("0"))
            value = _pct(live["value"], total_capacity)
            records = len(capacities)
            if dimension != "house":
                points = [{"label": "Current snapshot", "value": _number(value)}]
        elif key == "mortality_rate":
            deaths = self.resolve("deaths", dimension)
            live = self.resolve("live_birds", dimension if dimension in {"house", "batch"} else "batch")
            value = _pct(deaths["value"], _decimal(deaths["value"]) + _decimal(live["value"]))
            points = _combine_points(
                deaths["points"],
                live["points"],
                lambda death_value, live_value: _pct(death_value, death_value + live_value),
            ) if dimension in {"house", "batch"} else [{"label": "Selected period", "value": _number(value)}]
            records = deaths["records"]
        elif key in {"profit", "batch_profit"}:
            revenue = self.resolve("batch_revenue" if key == "batch_profit" else "sales_revenue", dimension)
            expenses = self.resolve("batch_allocated_expenses" if key == "batch_profit" else "total_expenses", dimension)
            value = _decimal(revenue["value"]) - _decimal(expenses["value"])
            points = _combine_points(revenue["points"], expenses["points"], lambda first, second: first - second)
            records = revenue["records"] + expenses["records"]
        elif key in {"profit_margin", "expense_ratio"}:
            revenue = self.resolve("sales_revenue", dimension)
            component = self.resolve("profit" if key == "profit_margin" else "total_expenses", dimension)
            value = _pct(component["value"], revenue["value"])
            points = _combine_points(component["points"], revenue["points"], _pct)
            records = revenue["records"] + component["records"]
        elif key == "collection_rate":
            cash = self.resolve("cash_received", dimension)
            revenue = self.resolve("sales_revenue", dimension)
            value = _pct(cash["value"], revenue["value"])
            points = _combine_points(cash["points"], revenue["points"], _pct)
            records = cash["records"]
        elif key in {"cost_per_egg", "feed_cost_per_egg", "revenue_per_egg", "feed_per_egg"}:
            numerator_key = {
                "cost_per_egg": "total_expenses",
                "feed_cost_per_egg": "feed_cost",
                "revenue_per_egg": "sales_revenue",
                "feed_per_egg": "feed_used_kg",
            }[key]
            numerator = self.resolve(numerator_key, dimension)
            eggs = self.resolve("net_eggs", dimension)
            value = (_decimal(numerator["value"]) / _decimal(eggs["value"])) if _decimal(eggs["value"]) else None
            points = _combine_points(
                numerator["points"],
                eggs["points"],
                lambda first, second: (first / second) if second else None,
            )
            records = eggs["records"]
        else:
            return None

        result = {
            "value": _number(value),
            "points": points,
            "records": records,
            "hasData": records > 0 and value is not None,
        }
        self._cache[cache_key] = result
        return result

    def coverage(self):
        all_lines = SaleItem.objects.filter(
            invoice__invoice_date__range=(self.start_date, self.end_date)
        ).exclude(invoice__status=SaleInvoice.Status.CANCELLED)
        total_line_revenue = all_lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
        linked_revenue = all_lines.exclude(batch__isnull=True).aggregate(total=Sum("line_total"))["total"] or Decimal("0")
        all_expenses = self._expenses().aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
        allocated_expenses = ExpenseAllocation.objects.filter(
            expense__expense_date__range=(self.start_date, self.end_date)
        ).exclude(expense__status=ExpenseTransaction.Status.REJECTED).aggregate(total=Sum("amount_allocated"))["total"] or Decimal("0")
        return {
            "revenueAllocationPercent": _number(_pct(linked_revenue, total_line_revenue)),
            "expenseAllocationPercent": _number(_pct(allocated_expenses, all_expenses)),
        }


def active_targets(end_date, metric_keys):
    targets = InvestorKpiTarget.objects.filter(
        metric_key__in=metric_keys,
        effective_from__lte=end_date,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=end_date)).order_by("metric_key", "-effective_from")
    return {target.metric_key: target for target in targets}


def target_payload(metric_key, value, target):
    if target is None:
        return {"configured": False, "label": "No target set"}
    difference = None if value is None else _decimal(value) - target.target_value
    if value is None:
        status = "unavailable"
    elif target.direction == InvestorKpiTarget.Direction.MINIMUM:
        status = "met" if difference >= 0 else "missed"
    else:
        status = "met" if difference <= 0 else "missed"
    return {
        "configured": True,
        "value": _number(target.target_value),
        "direction": target.direction,
        "status": status,
        "difference": _number(difference),
        "effectiveFrom": target.effective_from.isoformat(),
        "label": "Target met" if status == "met" else "Target needs attention" if status == "missed" else "Target unavailable",
    }


def build_report(*, mode, question_key, metric_keys, start_date, end_date, group_by, scope_type, scope_id, dimension, requested_view):
    if mode == "guided":
        question = GUIDED_QUESTIONS[question_key]
        metric_keys = question["metrics"]
        dimension = question["dimension"]
        title = question["label"]
    else:
        question = None
        metric_keys = metric_keys[:6]
        title = "Analyst report"

    previous_start, previous_end = previous_period(start_date, end_date)
    current_context = AnalysisContext(start_date, end_date, group_by, scope_type, scope_id)
    previous_context = AnalysisContext(previous_start, previous_end, group_by, scope_type, scope_id)
    targets = active_targets(end_date, metric_keys)
    metrics = []
    unresolved = []

    for key in metric_keys:
        metadata = METRIC_CATALOG[key]
        current = current_context.resolve(key, dimension)
        if current is None:
            unresolved.append(key)
            continue
        previous = previous_context.resolve(key, dimension) if metadata["comparisonSupported"] else None
        previous_value = previous["value"] if previous else None
        absolute_change = None
        percent_change = None
        variance_state = "unavailable"
        if current["value"] is not None and previous_value is not None:
            absolute_change = _decimal(current["value"]) - _decimal(previous_value)
            if _decimal(previous_value) != 0:
                percent_change = absolute_change / abs(_decimal(previous_value)) * Decimal("100")
            if absolute_change == 0 or metadata["performanceDirection"] == "neutral":
                variance_state = "neutral"
            elif metadata["performanceDirection"] == "higher":
                variance_state = "favorable" if absolute_change > 0 else "unfavorable"
            else:
                variance_state = "favorable" if absolute_change < 0 else "unfavorable"
        metrics.append({
            **metadata,
            **current,
            "previousValue": previous_value,
            "absoluteChange": _number(absolute_change),
            "percentChange": _number(percent_change),
            "varianceState": variance_state,
            "target": target_payload(key, current["value"], targets.get(key)),
        })

    supported_sets = [set(METRIC_CATALOG[key]["dimensions"]) for key in metric_keys if key in METRIC_CATALOG]
    valid_dimensions = set.intersection(*supported_sets) if supported_sets else {"period"}
    if dimension not in valid_dimensions and mode == "analyst":
        dimension = "period" if "period" in valid_dimensions else sorted(valid_dimensions)[0]

    valid_views = ["bar", "table"]
    if dimension == "period":
        valid_views.extend(["line", "area"])
    if len(metrics) == 1 and metrics[0]["additive"] and all((point["value"] or 0) >= 0 for point in metrics[0]["points"]) and len(metrics[0]["points"]) <= 8:
        valid_views.append("pie")
    selected_view = requested_view if requested_view in valid_views else ("line" if dimension == "period" else "bar")

    sections = []
    grouped_metrics = defaultdict(list)
    for metric in metrics:
        grouped_metrics[metric["unitGroup"]].append(metric["key"])
    for unit_group, keys in grouped_metrics.items():
        sections.append({
            "key": unit_group,
            "title": {
                "currency": "Financial values",
                "percent": "Rates and margins",
                "mass": "Feed quantities",
                "ratio": "Efficiency ratios",
                "count": "Operational volumes",
                "decimal": "Measured values",
            }.get(unit_group, "Measures"),
            "metricKeys": keys,
        })

    highlights = []
    for metric in metrics:
        if metric["target"]["configured"] and metric["target"]["status"] == "missed":
            highlights.append(f"{metric['label']} is outside its active farm target.")
        elif metric["percentChange"] is not None and abs(metric["percentChange"]) >= 10:
            highlights.append(f"{metric['label']} changed {abs(metric['percentChange']):.1f}% from the previous period.")

    return {
        "meta": {
            "title": title,
            "mode": mode,
            "questionKey": question_key if question else None,
            "generatedAt": now().isoformat(),
            "range": {"start": start_date.isoformat(), "end": end_date.isoformat(), "groupBy": group_by},
            "previousRange": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
            "scope": {"type": scope_type, "id": scope_id},
            "dimension": dimension,
            "view": selected_view,
            "validViews": valid_views,
            "validDimensions": [key for key in DIMENSIONS if key in valid_dimensions],
            "viewAdjustment": (
                None
                if requested_view == selected_view
                else f"{requested_view or 'Requested view'} is not compatible with this selection; {selected_view} was used."
            ),
        },
        "metrics": metrics,
        "sections": sections,
        "coverage": current_context.coverage(),
        "highlights": highlights[:5],
        "unresolvedMetricKeys": unresolved,
    }


def create_target_version(*, metric_key, target_value, effective_from, user):
    if metric_key not in TARGET_DIRECTIONS:
        raise ValueError("This indicator does not support farm targets.")
    if InvestorKpiTarget.objects.filter(metric_key=metric_key, effective_from=effective_from).exists():
        raise ValueError("A target already starts on this date.")

    previous = InvestorKpiTarget.objects.filter(
        metric_key=metric_key,
        effective_from__lt=effective_from,
    ).order_by("-effective_from").first()
    next_target = InvestorKpiTarget.objects.filter(
        metric_key=metric_key,
        effective_from__gt=effective_from,
    ).order_by("effective_from").first()
    if previous and (previous.effective_to is None or previous.effective_to >= effective_from):
        previous.effective_to = effective_from - timedelta(days=1)
        previous.save(update_fields=["effective_to"])

    return InvestorKpiTarget.objects.create(
        metric_key=metric_key,
        target_value=target_value,
        direction=TARGET_DIRECTIONS[metric_key],
        effective_from=effective_from,
        effective_to=(next_target.effective_from - timedelta(days=1)) if next_target else None,
        created_by=user,
    )
