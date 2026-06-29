import io
import re
import textwrap
import zipfile
from xml.sax.saxutils import escape as xml_escape

from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from expenses.models import ExpenseTransaction
from inventory.models import InventoryTransaction, ReorderRule
from poultry.models import ApprovalStatus, FeedRecord, MortalityRecord, PoultryBatch, PoultryHouse, egg_collection
from inventory.models import InventoryTransaction
from poultry.models import (
    ApprovalStatus,
    FeedRecord,
    MortalityRecord,
    PoultryHouse,
    egg_collection,
)
from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem
from accounting.services import get_pl_data, get_bs_data

from .models import InvestorCapitalTransaction, Role, User, validate_house_assignment

# Create your views here.


REPORT_CARDS = [
    {
        "key": "egg_production",
        "title": "Egg Production",
        "subtitle": "Daily, house & trend details",
        "icon": "bi-basket",
        "border": "success",
        "metric_label": "eggs collected",
    },
    {
        "key": "sales",
        "title": "Sales Report",
        "subtitle": "Revenue & transactions",
        "icon": "bi-graph-up-arrow",
        "border": "primary",
        "metric_label": "sales revenue",
    },
    {
        "key": "expenses",
        "title": "Expense Report",
        "subtitle": "Spending analysis",
        "icon": "bi-cash-coin",
        "border": "danger",
        "metric_label": "expenses",
    },
    {
        "key": "profit_loss",
        "title": "Profit & Loss",
        "subtitle": "Revenue vs expenses",
        "icon": "bi-bar-chart-line",
        "border": "dark",
        "metric_label": "net profit",
    },
    {
        "key": "mortality",
        "title": "Mortality Report",
        "subtitle": "Deaths & causes",
        "icon": "bi-exclamation-triangle",
        "border": "warning",
        "metric_label": "deaths",
    },
    {
        "key": "feed_usage",
        "title": "Feed Usage",
        "subtitle": "Consumption tracking",
        "icon": "bi-bag-fill",
        "border": "info",
        "metric_label": "feed used",
    },
    {
        "key": "stock",
        "title": "Stock Report",
        "subtitle": "Monthly movement",
        "icon": "bi-boxes",
        "border": "secondary",
        "metric_label": "stock records",
    },
    {
        "key": "creditors",
        "title": "Creditors",
        "subtitle": "Outstanding balances",
        "icon": "bi-receipt-cutoff",
        "border": "danger",
        "metric_label": "outstanding",
    },
    {
        "key": "bookings",
        "title": "Bookings",
        "subtitle": "Future orders",
        "icon": "bi-calendar2-check",
        "border": "primary",
        "metric_label": "pending bookings",
    },
    {
        "key": "staff_activity",
        "title": "Staff Activity",
        "subtitle": "Monthly user actions",
        "icon": "bi-people",
        "border": "dark",
        "metric_label": "actions logged",
    },
]


def _next_month_start(month_start):
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


def _month_window(period_key):
    today = timezone.localdate()
    current_month_start = today.replace(day=1)

    if period_key == "previous":
        month_end = current_month_start - timedelta(days=1)
        month_start = month_end.replace(day=1)
        label = "Previous Month"
    else:
        month_start = current_month_start
        month_end = _next_month_start(month_start) - timedelta(days=1)
        period_key = "current"
        label = "Current Month"

    return {
        "key": period_key,
        "label": label,
        "month_label": month_start.strftime("%B %Y"),
        "start": month_start,
        "end": month_end,
        "today": today,
    }


def _report_period_options():
    current = _month_window("current")
    previous = _month_window("previous")
    return [
        {
            "key": current["key"],
            "label": "Current Month",
            "month_label": current["month_label"],
        },
        {
            "key": previous["key"],
            "label": "Previous Month",
            "month_label": previous["month_label"],
        },
    ]


def _money(value, currency="UGX"):
    value = value if value is not None else Decimal("0.00")
    return f"{currency} {Decimal(value):,.2f}"


def _number(value, places=0, suffix=""):
    value = Decimal(str(value or 0))
    if places == 0:
        formatted = f"{value:,.0f}"
    else:
        formatted = f"{value:,.{places}f}"
    return f"{formatted}{suffix}"


def _percent(value):
    return f"{Decimal(str(value or 0)):,.1f}%"


def _decimal_total(queryset, field_name, max_digits=14, decimal_places=2):
    return queryset.aggregate(
        total=Coalesce(
            Sum(field_name),
            Value(0),
            output_field=DecimalField(max_digits=max_digits, decimal_places=decimal_places),
        )
    )["total"]


def _int_total(queryset, field_name):
    return queryset.aggregate(total=Coalesce(Sum(field_name), Value(0)))["total"] or 0


def _house_label_from_values(row):
    return row.get("batch__house__name") or row.get("batch__house__house_code") or "Unassigned"


def _user_label_from_values(row, field_prefix):
    first_name = row.get(f"{field_prefix}__first_name") or ""
    last_name = row.get(f"{field_prefix}__last_name") or ""
    full_name = f"{first_name} {last_name}".strip()
    return full_name or row.get(f"{field_prefix}__username") or "Unknown"


def _report_section(title, headers, rows, empty_text):
    return {
        "title": title,
        "headers": headers,
        "rows": rows,
        "empty_text": empty_text,
    }


def _report_period_text(report_period):
    return (
        f"{report_period['label']}: "
        f"{report_period['start'].strftime('%d %b %Y')} - "
        f"{report_period['end'].strftime('%d %b %Y')}"
    )


def _report_export_rows(selected_report, report_period):
    rows = [
        [selected_report["title"]],
        [selected_report["summary"]],
        ["Period", _report_period_text(report_period)],
        [],
        ["Summary"],
        ["Metric", "Value", "Note"],
    ]

    for stat in selected_report["stat_cards"]:
        rows.append([stat["label"], stat["value"], stat["hint"]])

    for section in selected_report["sections"]:
        rows.extend([[], [section["title"]]])
        if section["rows"]:
            rows.append(section["headers"])
            rows.extend(section["rows"])
        else:
            rows.append([section["empty_text"]])

    return rows


def _clean_xml_text(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))


def _xlsx_column_name(column_number):
    name = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _build_xlsx_bytes(rows):
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cell_ref = f"{_xlsx_column_name(column_index)}{row_index}"
            text = xml_escape(_clean_xml_text(value))
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)

    return output.getvalue()


def _pdf_escape(value):
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_color(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[index:index + 2], 16) / 255 for index in (0, 2, 4))


def _pdf_wrap_text(value, max_width, font_size):
    text = str(value or "").strip()
    if not text:
        return [""]
    avg_char_width = max(font_size * 0.48, 1)
    wrap_width = max(8, int(max_width / avg_char_width))
    wrapped = []
    for line in text.splitlines() or [text]:
        wrapped.extend(textwrap.wrap(line, width=wrap_width) or [""])
    return wrapped


def _pdf_truncate_lines(lines, max_lines):
    if len(lines) <= max_lines:
        return lines
    truncated = lines[:max_lines]
    truncated[-1] = (truncated[-1][: max(0, len(truncated[-1]) - 3)] + "...").strip()
    return truncated


class _ReportPdfBuilder:
    width = 595
    height = 842
    margin_x = 36
    bottom_margin = 52
    content_width = width - (margin_x * 2)

    def __init__(self, selected_report, report_period):
        self.report = selected_report
        self.report_period = report_period
        self.period_text = _report_period_text(report_period)
        self.pages = []
        self.commands = []
        self.y = 0
        self._new_page(first=True)

    def _new_page(self, first=False):
        if self.commands:
            self.pages.append(self.commands)
        self.commands = []
        self._rect(0, 0, self.width, self.height, fill="#ffffff")
        if first:
            self._hero_header()
            self.y = 706
        else:
            self._compact_header()
            self.y = 758

    def _finish(self):
        if self.commands:
            self.pages.append(self.commands)
            self.commands = []

        page_count = len(self.pages)
        for index, page in enumerate(self.pages, start=1):
            page.extend(self._footer_commands(index, page_count))

        return self._build_pdf()

    def _set_fill(self, color):
        r, g, b = _pdf_color(color)
        self.commands.append(f"{r:.3f} {g:.3f} {b:.3f} rg")

    def _set_stroke(self, color):
        r, g, b = _pdf_color(color)
        self.commands.append(f"{r:.3f} {g:.3f} {b:.3f} RG")

    def _rect(self, x, y, width, height, fill=None, stroke=None, line_width=1):
        if fill:
            self._set_fill(fill)
        if stroke:
            self._set_stroke(stroke)
            self.commands.append(f"{line_width:.2f} w")
        mode = "B" if fill and stroke else "f" if fill else "S"
        self.commands.append(f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re {mode}")

    def _line(self, x1, y1, x2, y2, color="#d7ded9", line_width=1):
        self._set_stroke(color)
        self.commands.append(f"{line_width:.2f} w")
        self.commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def _text(self, x, y, text, size=10, font="F1", color="#111827"):
        self._set_fill(color)
        self.commands.append(
            f"BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_pdf_escape(text)}) Tj ET"
        )

    def _wrapped_text(self, x, y, text, max_width, size=10, font="F1", color="#111827", line_height=None):
        line_height = line_height or (size + 3)
        lines = _pdf_wrap_text(text, max_width, size)
        for line in lines:
            self._text(x, y, line, size=size, font=font, color=color)
            y -= line_height
        return y

    def _ensure_space(self, required_height):
        if self.y - required_height < self.bottom_margin:
            self._new_page(first=False)

    def _hero_header(self):
        self._rect(0, 736, self.width, 106, fill="#14532d")
        self._rect(0, 736, self.width, 6, fill="#22c55e")
        self._rect(36, 786, 34, 34, fill="#dcfce7")
        self._text(46, 797, "PIQ", size=8.8, font="F2", color="#166534")
        self._text(78, 808, "POULTRYIQ", size=9, font="F2", color="#bbf7d0")
        self._text(78, 779, self.report["title"], size=22, font="F2", color="#ffffff")
        self._text(78, 760, self.report.get("summary", ""), size=9.5, font="F1", color="#dcfce7")

        pill_text = self.period_text
        pill_width = min(210, max(130, len(pill_text) * 4.3))
        pill_x = self.width - self.margin_x - pill_width
        self._rect(pill_x, 782, pill_width, 24, fill="#f0fdf4", stroke="#86efac", line_width=.7)
        self._text(pill_x + 10, 790, pill_text, size=8, font="F2", color="#166534")
        self._text(self.width - self.margin_x - 110, 748, "Manager Reports", size=8.5, font="F1", color="#bbf7d0")

    def _compact_header(self):
        self._rect(0, 786, self.width, 56, fill="#14532d")
        self._rect(0, 786, self.width, 5, fill="#22c55e")
        self._text(36, 811, "PoultryIQ", size=10, font="F2", color="#bbf7d0")
        self._text(36, 794, self.report["title"], size=14, font="F2", color="#ffffff")
        self._text(368, 798, self.period_text, size=8.3, font="F2", color="#dcfce7")

    def _footer_commands(self, page_number, page_count):
        footer = []
        old_commands = self.commands
        self.commands = footer
        self._line(36, 34, self.width - 36, 34, color="#d7ded9", line_width=.7)
        self._text(36, 20, "PoultryIQ Reports Center", size=8, font="F2", color="#166534")
        self._text(self.width - 96, 20, f"Page {page_number} of {page_count}", size=8, font="F1", color="#6b7280")
        self.commands = old_commands
        return footer

    def draw_stats(self):
        stats = self.report.get("stat_cards", [])
        if not stats:
            return
        self._ensure_space(148)
        self._text(self.margin_x, self.y, "Summary", size=13, font="F2", color="#14532d")
        self.y -= 20

        gap = 10
        card_width = (self.content_width - gap) / 2
        card_height = 54
        for index, stat in enumerate(stats[:4]):
            row = index // 2
            column = index % 2
            x = self.margin_x + column * (card_width + gap)
            y = self.y - row * (card_height + gap) - card_height
            self._rect(x, y, card_width, card_height, fill="#f8faf9", stroke="#d7ded9", line_width=.8)
            self._rect(x, y + card_height - 5, card_width, 5, fill="#22c55e")
            self._text(x + 12, y + 35, stat["label"].upper(), size=7.2, font="F2", color="#166534")
            value_size = 11 if len(str(stat["value"])) <= 22 else 9
            self._text(x + 12, y + 19, stat["value"], size=value_size, font="F2", color="#111827")
            self._text(x + 12, y + 7, stat["hint"], size=7.3, font="F1", color="#6b7280")

        self.y -= (2 * card_height) + gap + 24

    def draw_sections(self):
        for section in self.report.get("sections", []):
            self._draw_section(section)

    def _draw_section(self, section):
        self._ensure_space(70)
        self._line(self.margin_x, self.y + 7, self.width - self.margin_x, self.y + 7, color="#edf2ee", line_width=.8)
        self._text(self.margin_x, self.y - 10, section["title"], size=12, font="F2", color="#14532d")
        self.y -= 28

        if not section["rows"]:
            self._ensure_space(36)
            self._rect(self.margin_x, self.y - 28, self.content_width, 30, fill="#f8faf9", stroke="#cfd8d3", line_width=.7)
            self._text(self.margin_x + 12, self.y - 17, section["empty_text"], size=8.8, font="F1", color="#6b7280")
            self.y -= 46
            return

        headers = section["headers"]
        column_count = max(len(headers), 1)
        column_widths = self._column_widths(column_count)
        self._draw_table_header(headers, column_widths)
        for index, row in enumerate(section["rows"]):
            self._draw_table_row(row, headers, column_widths, shaded=index % 2 == 1)
        self.y -= 12

    def _column_widths(self, column_count):
        weights = [1] * column_count
        if column_count >= 6:
            weights = [1.05] * column_count
            weights[0] = 1.15
            weights[-1] = 1.2
        total_weight = sum(weights)
        return [self.content_width * weight / total_weight for weight in weights]

    def _draw_table_header(self, headers, column_widths):
        self._ensure_space(34)
        header_height = 24
        self._rect(self.margin_x, self.y - header_height, self.content_width, header_height, fill="#e9f7ef", stroke="#bfe6cd", line_width=.7)
        x = self.margin_x
        for header, width in zip(headers, column_widths):
            label = _pdf_truncate_lines(_pdf_wrap_text(header, width - 8, 7.2), 1)[0]
            self._text(x + 4, self.y - 15, label.upper(), size=7.2, font="F2", color="#166534")
            x += width
        self.y -= header_height

    def _draw_table_row(self, row, headers, column_widths, shaded=False):
        font_size = 7.2 if len(column_widths) >= 6 else 8
        line_height = font_size + 2
        wrapped_cells = []
        max_lines = 1
        for cell, width in zip(row, column_widths):
            lines = _pdf_truncate_lines(_pdf_wrap_text(cell, width - 8, font_size), 3)
            wrapped_cells.append(lines)
            max_lines = max(max_lines, len(lines))

        row_height = max(22, 10 + max_lines * line_height)
        if self.y - row_height < self.bottom_margin:
            self._new_page(first=False)
            self._draw_table_header(headers, column_widths)

        fill = "#ffffff" if not shaded else "#f8faf9"
        self._rect(self.margin_x, self.y - row_height, self.content_width, row_height, fill=fill, stroke="#e5e7eb", line_width=.45)
        x = self.margin_x
        for lines, width in zip(wrapped_cells, column_widths):
            text_y = self.y - 13
            for line in lines:
                self._text(x + 4, text_y, line, size=font_size, font="F1", color="#374151")
                text_y -= line_height
            x += width
        self.y -= row_height

    def _build_pdf(self):
        objects = [
            b"",
            b"",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        ]
        page_ids = []
        for page_commands in self.pages:
            stream = "\n".join(page_commands).encode("latin-1", "replace")
            page_id = len(objects) + 1
            content_id = page_id + 1
            page_ids.append(page_id)
            objects.append(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.width} {self.height}] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
                ).encode("latin-1")
            )
            objects.append(
                b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
                + stream
                + b"\nendstream"
            )

        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

        pdf = bytearray(b"%PDF-1.4\n")
        offsets = []
        for object_id, obj in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
            pdf.extend(obj)
            pdf.extend(b"\nendobj\n")

        xref_start = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        pdf.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_start}\n%%EOF"
            ).encode("ascii")
        )
        return bytes(pdf)


def _build_pdf_bytes(selected_report, report_period):
    builder = _ReportPdfBuilder(selected_report, report_period)
    builder.draw_stats()
    builder.draw_sections()
    return builder._finish()


def _report_download_response(selected_report, report_period, download_format):
    file_stem = slugify(f"{selected_report['title']} {report_period['month_label']}") or "report"

    if download_format == "pdf":
        content = _build_pdf_bytes(selected_report, report_period)
        content_type = "application/pdf"
        extension = "pdf"
    else:
        rows = _report_export_rows(selected_report, report_period)
        content = _build_xlsx_bytes(rows)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{file_stem}.{extension}"'
    return response


def _build_report_cards(report_metrics):
    cards = []
    for card in REPORT_CARDS:
        cards.append({**card, "metric": report_metrics.get(card["key"], "0")})
    return cards


def _report_metrics(month_start, month_end):
    eggs_qs = egg_collection.objects.filter(
        collection_date__range=(month_start, month_end),
        sickness_report__isnull=True,
    )
    sales_qs = SaleInvoice.objects.filter(invoice_date__range=(month_start, month_end)).exclude(
        status=SaleInvoice.Status.CANCELLED
    )
    expenses_qs = ExpenseTransaction.objects.filter(expense_date__range=(month_start, month_end)).exclude(
        status=ExpenseTransaction.Status.REJECTED
    )
    mortality_qs = MortalityRecord.objects.filter(record_date__range=(month_start, month_end))
    feed_qs = FeedRecord.objects.filter(record_date__range=(month_start, month_end))
    stock_qs = InventoryTransaction.objects.filter(tx_date__range=(month_start, month_end))
    creditors_qs = ReceivableLedger.objects.filter(
        invoice__invoice_date__range=(month_start, month_end),
        balance__gt=0,
    ).exclude(invoice__status=SaleInvoice.Status.CANCELLED)
    bookings_qs = SaleInvoice.objects.filter(
        invoice_date__range=(month_start, month_end),
        delivery_status=SaleInvoice.DeliveryStatus.PENDING,
    ).exclude(status=SaleInvoice.Status.CANCELLED)

    total_sales = _decimal_total(sales_qs, "total_amount")
    total_expenses = _decimal_total(expenses_qs, "total_amount")

    staff_actions = (
        SaleInvoice.objects.filter(created_at__date__range=(month_start, month_end)).count()
        + ExpenseTransaction.objects.filter(created_at__date__range=(month_start, month_end)).count()
        + InventoryTransaction.objects.filter(created_at__date__range=(month_start, month_end)).count()
        + egg_collection.objects.filter(collected_at__date__range=(month_start, month_end)).count()
        + FeedRecord.objects.filter(created_at__date__range=(month_start, month_end)).count()
        + MortalityRecord.objects.filter(reported_at__date__range=(month_start, month_end)).count()
    )

    return {
        "egg_production": _number(_int_total(eggs_qs, "eggs_collected")),
        "sales": _money(total_sales),
        "expenses": _money(total_expenses),
        "profit_loss": _money(total_sales - total_expenses),
        "mortality": _number(_int_total(mortality_qs, "number_dead")),
        "feed_usage": _number(_decimal_total(feed_qs, "quantity_kg", 14, 2), 2, " kg"),
        "stock": _number(stock_qs.count()),
        "creditors": _money(_decimal_total(creditors_qs, "balance")),
        "bookings": _number(bookings_qs.count()),
        "staff_activity": _number(staff_actions),
    }


def _build_egg_production_report(month_start, month_end):
    egg_qs = (
        egg_collection.objects.filter(
            collection_date__range=(month_start, month_end),
            sickness_report__isnull=True,
        )
        .select_related("batch__house", "collected_by")
        .order_by("-collection_date", "-collection_id")
    )
    total_eggs = _int_total(egg_qs, "eggs_collected")
    rejected_eggs = _int_total(egg_qs, "eggs_rejected")
    net_eggs = total_eggs - rejected_eggs

    by_house = []
    for row in egg_qs.values("batch__house__house_code", "batch__house__name").annotate(
        records=Count("collection_id"),
        eggs=Coalesce(Sum("eggs_collected"), Value(0)),
        rejected=Coalesce(Sum("eggs_rejected"), Value(0)),
    ).order_by("batch__house__house_code"):
        by_house.append([
            _house_label_from_values(row),
            _number(row["records"]),
            _number(row["eggs"]),
            _number(row["rejected"]),
            _number((row["eggs"] or 0) - (row["rejected"] or 0)),
        ])

    recent_rows = []
    for record in egg_qs[:15]:
        recent_rows.append([
            record.collection_date.strftime("%d %b %Y"),
            record.batch.house.name or record.batch.house.house_code,
            record.batch.batch_code,
            _number(record.eggs_collected),
            _number(record.eggs_rejected),
            record.get_status_display(),
            record.collected_by.display_name,
        ])

    return {
        "title": "Egg Production Report",
        "summary": "Approved and pending egg collection records captured during the current month.",
        "stat_cards": [
            {"label": "Total Eggs", "value": _number(total_eggs), "hint": "Collected this month"},
            {"label": "Rejected Eggs", "value": _number(rejected_eggs), "hint": "Damaged or rejected"},
            {"label": "Net Eggs", "value": _number(net_eggs), "hint": "Collected minus rejected"},
            {"label": "Records", "value": _number(egg_qs.count()), "hint": "Collection entries"},
        ],
        "sections": [
            _report_section(
                "Production By House",
                ["House", "Records", "Eggs", "Rejected", "Net Eggs"],
                by_house,
                "No egg production records for the current month.",
            ),
            _report_section(
                "Recent Egg Collections",
                ["Date", "House", "Batch", "Eggs", "Rejected", "Status", "Collected By"],
                recent_rows,
                "No egg collections have been recorded this month.",
            ),
        ],
    }


def _build_sales_report(month_start, month_end):
    invoice_qs = (
        SaleInvoice.objects.filter(invoice_date__range=(month_start, month_end))
        .exclude(status=SaleInvoice.Status.CANCELLED)
        .select_related("customer", "receivable")
        .order_by("-invoice_date", "-invoice_id")
    )
    payment_qs = (
        CustomerPayment.objects.filter(payment_date__range=(month_start, month_end))
        .select_related("customer", "invoice")
        .order_by("-payment_date", "-payment_id")
    )
    receivable_qs = ReceivableLedger.objects.filter(
        invoice__invoice_date__range=(month_start, month_end)
    ).exclude(invoice__status=SaleInvoice.Status.CANCELLED)

    revenue = _decimal_total(invoice_qs, "total_amount")
    cash_received = _decimal_total(payment_qs, "amount")
    outstanding = _decimal_total(receivable_qs, "balance")

    by_product = []
    product_qs = SaleItem.objects.filter(
        invoice__invoice_date__range=(month_start, month_end)
    ).exclude(invoice__status=SaleInvoice.Status.CANCELLED)
    for row in product_qs.values("product_name", "unit").annotate(
        quantity=Coalesce(
            Sum("quantity"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        total=Coalesce(
            Sum("line_total"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("product_name", "unit"):
        by_product.append([
            row["product_name"],
            _number(row["quantity"], 3),
            row["unit"],
            _money(row["total"]),
        ])

    invoice_rows = []
    for invoice in invoice_qs[:15]:
        receivable = getattr(invoice, "receivable", None)
        invoice_rows.append([
            invoice.invoice_date.strftime("%d %b %Y"),
            invoice.invoice_no,
            invoice.customer.name,
            invoice.get_status_display(),
            invoice.get_delivery_status_display(),
            _money(invoice.total_amount),
            _money(receivable.amount_paid if receivable else 0),
            _money(receivable.balance if receivable else 0),
        ])

    payment_rows = []
    for payment in payment_qs[:10]:
        payment_rows.append([
            payment.payment_date.strftime("%d %b %Y"),
            payment.customer.name,
            payment.invoice.invoice_no,
            payment.get_method_display(),
            _money(payment.amount),
        ])

    return {
        "title": "Sales Report",
        "summary": "Invoices, products sold, collections, and balances for the current month.",
        "stat_cards": [
            {"label": "Revenue", "value": _money(revenue), "hint": "Non-cancelled invoices"},
            {"label": "Cash Received", "value": _money(cash_received), "hint": "Payments received"},
            {"label": "Invoices", "value": _number(invoice_qs.count()), "hint": "Sales records"},
            {"label": "Outstanding", "value": _money(outstanding), "hint": "Customer balances"},
        ],
        "sections": [
            _report_section("Sales By Product", ["Product", "Quantity", "Unit", "Revenue"], by_product, "No products sold this month."),
            _report_section(
                "Recent Invoices",
                ["Date", "Invoice", "Customer", "Status", "Delivery", "Total", "Paid", "Balance"],
                invoice_rows,
                "No sales invoices for the current month.",
            ),
            _report_section(
                "Recent Payments",
                ["Date", "Customer", "Invoice", "Method", "Amount"],
                payment_rows,
                "No customer payments received this month.",
            ),
        ],
    }


def _build_expense_report(month_start, month_end):
    expense_qs = (
        ExpenseTransaction.objects.filter(expense_date__range=(month_start, month_end))
        .exclude(status=ExpenseTransaction.Status.REJECTED)
        .select_related("category", "created_by")
        .order_by("-expense_date", "-expense_id")
    )
    total_expenses = _decimal_total(expense_qs, "total_amount")
    largest = expense_qs.order_by("-total_amount").first()

    by_category = []
    for row in expense_qs.values("category__name").annotate(
        records=Count("expense_id"),
        total=Coalesce(
            Sum("total_amount"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("-total"):
        by_category.append([
            row["category__name"] or "Uncategorised",
            _number(row["records"]),
            _money(row["total"]),
        ])

    by_payment = []
    for row in expense_qs.values("payment_method").annotate(
        records=Count("expense_id"),
        total=Coalesce(
            Sum("total_amount"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("payment_method"):
        by_payment.append([row["payment_method"] or "Not set", _number(row["records"]), _money(row["total"])])

    expense_rows = []
    for expense in expense_qs[:15]:
        expense_rows.append([
            expense.expense_date.strftime("%d %b %Y"),
            expense.category.name,
            expense.description,
            expense.payment_method or "Not set",
            expense.get_status_display(),
            _money(expense.total_amount),
            expense.created_by.display_name,
        ])

    return {
        "title": "Expense Report",
        "summary": "All non-rejected expenses recorded during the current month.",
        "stat_cards": [
            {"label": "Total Expenses", "value": _money(total_expenses), "hint": "Non-rejected spending"},
            {"label": "Expense Records", "value": _number(expense_qs.count()), "hint": "Entries captured"},
            {"label": "Categories", "value": _number(len(by_category)), "hint": "Spending groups"},
            {
                "label": "Largest Expense",
                "value": _money(largest.total_amount if largest else 0),
                "hint": largest.description[:60] if largest else "No expense yet",
            },
        ],
        "sections": [
            _report_section("Expenses By Category", ["Category", "Records", "Amount"], by_category, "No expenses recorded this month."),
            _report_section("Expenses By Payment Method", ["Method", "Records", "Amount"], by_payment, "No payment method data this month."),
            _report_section(
                "Recent Expenses",
                ["Date", "Category", "Description", "Payment", "Status", "Amount", "Recorded By"],
                expense_rows,
                "No expense entries for the current month.",
            ),
        ],
    }


def _build_profit_loss_report(month_start, month_end):
    sales_qs = SaleInvoice.objects.filter(invoice_date__range=(month_start, month_end)).exclude(
        status=SaleInvoice.Status.CANCELLED
    )
    expense_qs = ExpenseTransaction.objects.filter(expense_date__range=(month_start, month_end)).exclude(
        status=ExpenseTransaction.Status.REJECTED
    )
    payment_qs = CustomerPayment.objects.filter(payment_date__range=(month_start, month_end))

    revenue = _decimal_total(sales_qs, "total_amount")
    expenses = _decimal_total(expense_qs, "total_amount")
    cash_received = _decimal_total(payment_qs, "amount")
    profit = revenue - expenses
    profit_margin = (profit / revenue * 100) if revenue else Decimal("0")
    expense_ratio = (expenses / revenue * 100) if revenue else Decimal("0")

    sales_by_product = []
    product_qs = SaleItem.objects.filter(
        invoice__invoice_date__range=(month_start, month_end)
    ).exclude(invoice__status=SaleInvoice.Status.CANCELLED)
    for row in product_qs.values("product_name").annotate(
        total=Coalesce(
            Sum("line_total"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).order_by("-total"):
        sales_by_product.append([row["product_name"], _money(row["total"])])

    expenses_by_category = []
    for row in expense_qs.values("category__name").annotate(
        total=Coalesce(
            Sum("total_amount"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).order_by("-total"):
        expenses_by_category.append([row["category__name"] or "Uncategorised", _money(row["total"])])

    summary_rows = [
        ["Sales Revenue", _money(revenue), "Non-cancelled invoices"],
        ["Expenses", _money(expenses), "Non-rejected expense records"],
        ["Net Profit", _money(profit), "Revenue minus expenses"],
        ["Profit Margin", _percent(profit_margin), "Net profit as a share of revenue"],
        ["Expense Ratio", _percent(expense_ratio), "Expenses as a share of revenue"],
        ["Cash Received", _money(cash_received), "Customer payments collected this month"],
    ]

    return {
        "title": "Profit & Loss Report",
        "summary": "Current month revenue, expenses, profit, and cash collection.",
        "stat_cards": [
            {"label": "Revenue", "value": _money(revenue), "hint": "Sales this month"},
            {"label": "Expenses", "value": _money(expenses), "hint": "Spending this month"},
            {"label": "Net Profit", "value": _money(profit), "hint": "Revenue minus expenses"},
            {"label": "Margin", "value": _percent(profit_margin), "hint": "Profitability"},
        ],
        "sections": [
            _report_section("Profit & Loss Summary", ["Metric", "Value", "Meaning"], summary_rows, "No financial records this month."),
            _report_section("Revenue By Product", ["Product", "Revenue"], sales_by_product, "No product revenue this month."),
            _report_section("Expense Breakdown", ["Category", "Amount"], expenses_by_category, "No expense records this month."),
        ],
    }


def _build_mortality_report(month_start, month_end):
    mortality_qs = (
        MortalityRecord.objects.filter(record_date__range=(month_start, month_end))
        .select_related("batch__house", "cause", "reported_by")
        .order_by("-record_date", "-mortality_id")
    )
    total_deaths = _int_total(mortality_qs, "number_dead")
    approved_deaths = _int_total(mortality_qs.filter(status=ApprovalStatus.APPROVED), "number_dead")

    by_cause = []
    for row in mortality_qs.values("cause__name").annotate(
        records=Count("mortality_id"),
        deaths=Coalesce(Sum("number_dead"), Value(0)),
    ).order_by("-deaths"):
        by_cause.append([row["cause__name"] or "Unspecified", _number(row["records"]), _number(row["deaths"])])

    by_house = []
    for row in mortality_qs.values("batch__house__house_code", "batch__house__name").annotate(
        records=Count("mortality_id"),
        deaths=Coalesce(Sum("number_dead"), Value(0)),
    ).order_by("-deaths"):
        by_house.append([_house_label_from_values(row), _number(row["records"]), _number(row["deaths"])])

    mortality_rows = []
    for record in mortality_qs[:15]:
        mortality_rows.append([
            record.record_date.strftime("%d %b %Y"),
            record.batch.house.name or record.batch.house.house_code,
            record.batch.batch_code,
            _number(record.number_dead),
            record.cause.name if record.cause else "Unspecified",
            record.get_status_display(),
            record.reported_by.display_name,
        ])

    return {
        "title": "Mortality Report",
        "summary": "Mortality records submitted during the current month.",
        "stat_cards": [
            {"label": "Total Deaths", "value": _number(total_deaths), "hint": "All submitted records"},
            {"label": "Approved Deaths", "value": _number(approved_deaths), "hint": "Approved records only"},
            {"label": "Reports", "value": _number(mortality_qs.count()), "hint": "Mortality entries"},
            {"label": "Pending Reviews", "value": _number(mortality_qs.filter(status=ApprovalStatus.PENDING).count()), "hint": "Awaiting approval"},
        ],
        "sections": [
            _report_section("Deaths By Cause", ["Cause", "Records", "Deaths"], by_cause, "No mortality causes recorded this month."),
            _report_section("Deaths By House", ["House", "Records", "Deaths"], by_house, "No mortality by house this month."),
            _report_section(
                "Recent Mortality Records",
                ["Date", "House", "Batch", "Deaths", "Cause", "Status", "Reported By"],
                mortality_rows,
                "No mortality records for the current month.",
            ),
        ],
    }


def _build_feed_usage_report(month_start, month_end):
    feed_qs = (
        FeedRecord.objects.filter(record_date__range=(month_start, month_end))
        .select_related("batch__house", "recorded_by")
        .order_by("-record_date", "-feed_id")
    )
    total_feed = _decimal_total(feed_qs, "quantity_kg", 14, 2)
    approved_feed = _decimal_total(feed_qs.filter(status=ApprovalStatus.APPROVED), "quantity_kg", 14, 2)

    by_feed_type = []
    feed_type_labels = dict(FeedRecord.FeedType.choices)
    for row in feed_qs.values("feed_type").annotate(
        records=Count("feed_id"),
        total=Coalesce(
            Sum("quantity_kg"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("-total"):
        by_feed_type.append([feed_type_labels.get(row["feed_type"], row["feed_type"]), _number(row["records"]), _number(row["total"], 2, " kg")])

    by_house = []
    for row in feed_qs.values("batch__house__house_code", "batch__house__name").annotate(
        records=Count("feed_id"),
        total=Coalesce(
            Sum("quantity_kg"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("-total"):
        by_house.append([_house_label_from_values(row), _number(row["records"]), _number(row["total"], 2, " kg")])

    feed_rows = []
    for record in feed_qs[:15]:
        feed_rows.append([
            record.record_date.strftime("%d %b %Y"),
            record.batch.house.name or record.batch.house.house_code,
            record.batch.batch_code,
            record.get_feed_type_display(),
            _number(record.quantity_kg, 2, " kg"),
            record.get_status_display(),
            record.recorded_by.display_name,
        ])

    return {
        "title": "Feed Usage Report",
        "summary": "Feed consumption records captured during the current month.",
        "stat_cards": [
            {"label": "Total Feed", "value": _number(total_feed, 2, " kg"), "hint": "All submitted records"},
            {"label": "Approved Feed", "value": _number(approved_feed, 2, " kg"), "hint": "Approved records only"},
            {"label": "Records", "value": _number(feed_qs.count()), "hint": "Feed entries"},
            {"label": "Pending Reviews", "value": _number(feed_qs.filter(status=ApprovalStatus.PENDING).count()), "hint": "Awaiting approval"},
        ],
        "sections": [
            _report_section("Feed By Type", ["Feed Type", "Records", "Quantity"], by_feed_type, "No feed usage recorded this month."),
            _report_section("Feed By House", ["House", "Records", "Quantity"], by_house, "No feed usage by house this month."),
            _report_section(
                "Recent Feed Records",
                ["Date", "House", "Batch", "Feed Type", "Quantity", "Status", "Recorded By"],
                feed_rows,
                "No feed records for the current month.",
            ),
        ],
    }


def _build_stock_report(month_start, month_end):
    tx_qs = (
        InventoryTransaction.objects.filter(tx_date__range=(month_start, month_end))
        .select_related("store", "item__category", "batch", "created_by")
        .order_by("-tx_date", "-tx_id")
    )
    stock_in = _decimal_total(tx_qs.filter(tx_type=InventoryTransaction.TxType.IN_), "quantity", 14, 3)
    stock_out = _decimal_total(tx_qs.filter(tx_type=InventoryTransaction.TxType.OUT), "quantity", 14, 3)
    adjustments = _decimal_total(tx_qs.filter(tx_type=InventoryTransaction.TxType.ADJUST), "quantity", 14, 3)

    by_item = []
    for row in tx_qs.values("item__name", "item__unit", "item__category__name").annotate(
        stock_in=Coalesce(
            Sum("quantity", filter=Q(tx_type=InventoryTransaction.TxType.IN_)),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        stock_out=Coalesce(
            Sum("quantity", filter=Q(tx_type=InventoryTransaction.TxType.OUT)),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        adjustments=Coalesce(
            Sum("quantity", filter=Q(tx_type=InventoryTransaction.TxType.ADJUST)),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        records=Count("tx_id"),
    ).order_by("item__name"):
        by_item.append([
            row["item__name"],
            row["item__category__name"],
            _number(row["stock_in"], 3),
            _number(row["stock_out"], 3),
            _number(row["adjustments"], 3),
            row["item__unit"],
            _number(row["records"]),
        ])

    tx_rows = []
    for tx in tx_qs[:15]:
        tx_rows.append([
            tx.tx_date.strftime("%d %b %Y"),
            tx.item.name,
            tx.item.category.name,
            tx.get_tx_type_display(),
            _number(tx.quantity, 3),
            tx.item.unit,
            tx.store.name,
            tx.created_by.display_name,
        ])

    return {
        "title": "Stock Report",
        "summary": "Inventory movements recorded during the current month.",
        "stat_cards": [
            {"label": "Stock In", "value": _number(stock_in, 3), "hint": "Units added"},
            {"label": "Stock Out", "value": _number(stock_out, 3), "hint": "Units removed"},
            {"label": "Adjustments", "value": _number(adjustments, 3), "hint": "Manual corrections"},
            {"label": "Records", "value": _number(tx_qs.count()), "hint": "Inventory transactions"},
        ],
        "sections": [
            _report_section(
                "Stock Movement By Item",
                ["Item", "Category", "In", "Out", "Adjust", "Unit", "Records"],
                by_item,
                "No stock movements recorded this month.",
            ),
            _report_section(
                "Recent Stock Transactions",
                ["Date", "Item", "Category", "Type", "Quantity", "Unit", "Store", "Recorded By"],
                tx_rows,
                "No inventory transactions for the current month.",
            ),
        ],
    }


def _build_creditors_report(month_start, month_end):
    ledger_qs = (
        ReceivableLedger.objects.filter(
            invoice__invoice_date__range=(month_start, month_end),
            balance__gt=0,
        )
        .exclude(invoice__status=SaleInvoice.Status.CANCELLED)
        .select_related("invoice__customer")
        .order_by("-balance")
    )
    total_due = _decimal_total(ledger_qs, "amount_due")
    total_paid = _decimal_total(ledger_qs, "amount_paid")
    total_balance = _decimal_total(ledger_qs, "balance")

    by_customer = []
    for row in ledger_qs.values("invoice__customer__name").annotate(
        invoices=Count("receivable_id"),
        amount_due=Coalesce(
            Sum("amount_due"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        amount_paid=Coalesce(
            Sum("amount_paid"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        balance=Coalesce(
            Sum("balance"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("-balance"):
        by_customer.append([
            row["invoice__customer__name"],
            _number(row["invoices"]),
            _money(row["amount_due"]),
            _money(row["amount_paid"]),
            _money(row["balance"]),
        ])

    ledger_rows = []
    for ledger in ledger_qs[:15]:
        ledger_rows.append([
            ledger.invoice.invoice_date.strftime("%d %b %Y"),
            ledger.invoice.invoice_no,
            ledger.invoice.customer.name,
            _money(ledger.amount_due),
            _money(ledger.amount_paid),
            _money(ledger.balance),
            ledger.last_payment_date.strftime("%d %b %Y") if ledger.last_payment_date else "No payment yet",
        ])

    return {
        "title": "Creditors Report",
        "summary": "Customer credit and outstanding balances from current month invoices.",
        "stat_cards": [
            {"label": "Amount Due", "value": _money(total_due), "hint": "Invoice totals on credit"},
            {"label": "Amount Paid", "value": _money(total_paid), "hint": "Payments received"},
            {"label": "Outstanding", "value": _money(total_balance), "hint": "Unpaid balance"},
            {"label": "Accounts", "value": _number(ledger_qs.count()), "hint": "Invoices with balances"},
        ],
        "sections": [
            _report_section(
                "Outstanding By Customer",
                ["Customer", "Invoices", "Amount Due", "Paid", "Balance"],
                by_customer,
                "No outstanding customer balances from current month invoices.",
            ),
            _report_section(
                "Outstanding Invoice Details",
                ["Date", "Invoice", "Customer", "Due", "Paid", "Balance", "Last Payment"],
                ledger_rows,
                "No current month invoice balances to show.",
            ),
        ],
    }


def _build_bookings_report(month_start, month_end):
    bookings_qs = (
        SaleInvoice.objects.filter(
            invoice_date__range=(month_start, month_end),
            delivery_status=SaleInvoice.DeliveryStatus.PENDING,
        )
        .exclude(status=SaleInvoice.Status.CANCELLED)
        .select_related("customer", "receivable")
        .order_by("due_date", "-invoice_date", "-invoice_id")
    )
    booking_value = _decimal_total(bookings_qs, "total_amount")
    paid_value = bookings_qs.aggregate(
        total=Coalesce(
            Sum("receivable__amount_paid"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]
    due_this_month = bookings_qs.filter(due_date__range=(month_start, month_end)).count()

    by_due_date = []
    for row in bookings_qs.values("due_date").annotate(
        bookings=Count("invoice_id"),
        total=Coalesce(
            Sum("total_amount"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).order_by("due_date"):
        by_due_date.append([
            row["due_date"].strftime("%d %b %Y") if row["due_date"] else "No delivery date",
            _number(row["bookings"]),
            _money(row["total"]),
        ])

    booking_rows = []
    for invoice in bookings_qs[:15]:
        receivable = getattr(invoice, "receivable", None)
        booking_rows.append([
            invoice.invoice_date.strftime("%d %b %Y"),
            invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "No date set",
            invoice.invoice_no,
            invoice.customer.name,
            invoice.get_status_display(),
            _money(invoice.total_amount),
            _money(receivable.amount_paid if receivable else 0),
            _money(receivable.balance if receivable else 0),
        ])

    return {
        "title": "Bookings Report",
        "summary": "Pending delivery orders created during the current month.",
        "stat_cards": [
            {"label": "Bookings", "value": _number(bookings_qs.count()), "hint": "Pending deliveries"},
            {"label": "Booked Value", "value": _money(booking_value), "hint": "Order value"},
            {"label": "Deposits Paid", "value": _money(paid_value), "hint": "Payments received"},
            {"label": "Due This Month", "value": _number(due_this_month), "hint": "Delivery dates in month"},
        ],
        "sections": [
            _report_section("Bookings By Delivery Date", ["Delivery Date", "Bookings", "Value"], by_due_date, "No pending bookings this month."),
            _report_section(
                "Booking Details",
                ["Booked On", "Delivery Date", "Invoice", "Customer", "Status", "Total", "Paid", "Balance"],
                booking_rows,
                "No booking details for the current month.",
            ),
        ],
    }


def _add_activity(activity, queryset, user_field, count_field, bucket):
    for row in queryset.values(
        f"{user_field}_id",
        f"{user_field}__first_name",
        f"{user_field}__last_name",
        f"{user_field}__username",
    ).annotate(total=Count(count_field)):
        user_id = row[f"{user_field}_id"] or "unknown"
        current = activity.setdefault(
            user_id,
            {
                "user": _user_label_from_values(row, user_field),
                "egg": 0,
                "feed": 0,
                "sales": 0,
                "expenses": 0,
                "inventory": 0,
                "mortality": 0,
            },
        )
        current[bucket] += row["total"]


def _build_staff_activity_report(month_start, month_end):
    activity = {}
    _add_activity(
        activity,
        egg_collection.objects.filter(collected_at__date__range=(month_start, month_end)),
        "collected_by",
        "collection_id",
        "egg",
    )
    _add_activity(
        activity,
        FeedRecord.objects.filter(created_at__date__range=(month_start, month_end)),
        "recorded_by",
        "feed_id",
        "feed",
    )
    _add_activity(
        activity,
        SaleInvoice.objects.filter(created_at__date__range=(month_start, month_end)),
        "created_by",
        "invoice_id",
        "sales",
    )
    _add_activity(
        activity,
        ExpenseTransaction.objects.filter(created_at__date__range=(month_start, month_end)),
        "created_by",
        "expense_id",
        "expenses",
    )
    _add_activity(
        activity,
        InventoryTransaction.objects.filter(created_at__date__range=(month_start, month_end)),
        "created_by",
        "tx_id",
        "inventory",
    )
    _add_activity(
        activity,
        MortalityRecord.objects.filter(reported_at__date__range=(month_start, month_end)),
        "reported_by",
        "mortality_id",
        "mortality",
    )

    activity_rows = []
    for row in activity.values():
        total = row["egg"] + row["feed"] + row["sales"] + row["expenses"] + row["inventory"] + row["mortality"]
        activity_rows.append([
            row["user"],
            _number(row["egg"]),
            _number(row["feed"]),
            _number(row["sales"]),
            _number(row["expenses"]),
            _number(row["inventory"]),
            _number(row["mortality"]),
            _number(total),
        ])
    activity_rows.sort(key=lambda item: int(item[-1].replace(",", "")), reverse=True)

    source_rows = [
        ["Egg Collection", _number(sum(row["egg"] for row in activity.values()))],
        ["Feed Records", _number(sum(row["feed"] for row in activity.values()))],
        ["Sales Invoices", _number(sum(row["sales"] for row in activity.values()))],
        ["Expenses", _number(sum(row["expenses"] for row in activity.values()))],
        ["Inventory Transactions", _number(sum(row["inventory"] for row in activity.values()))],
        ["Mortality Reports", _number(sum(row["mortality"] for row in activity.values()))],
    ]
    total_actions = sum(int(row[1].replace(",", "")) for row in source_rows)

    return {
        "title": "Staff Activity Report",
        "summary": "User actions recorded across production, sales, expenses, inventory, and mortality this month.",
        "stat_cards": [
            {"label": "Active Staff", "value": _number(len(activity_rows)), "hint": "Users with activity"},
            {"label": "Total Actions", "value": _number(total_actions), "hint": "Records created"},
            {"label": "Production Actions", "value": _number(sum(row["egg"] + row["feed"] for row in activity.values())), "hint": "Egg and feed entries"},
            {"label": "Business Actions", "value": _number(sum(row["sales"] + row["expenses"] + row["inventory"] for row in activity.values())), "hint": "Sales, expenses, stock"},
        ],
        "sections": [
            _report_section(
                "Activity By Staff",
                ["Staff", "Egg", "Feed", "Sales", "Expenses", "Inventory", "Mortality", "Total"],
                activity_rows,
                "No staff activity recorded this month.",
            ),
            _report_section("Activity By Source", ["Source", "Records"], source_rows, "No source activity this month."),
        ],
    }


REPORT_BUILDERS = {
    "egg_production": _build_egg_production_report,
    "sales": _build_sales_report,
    "expenses": _build_expense_report,
    "profit_loss": _build_profit_loss_report,
    "mortality": _build_mortality_report,
    "feed_usage": _build_feed_usage_report,
    "stock": _build_stock_report,
    "creditors": _build_creditors_report,
    "bookings": _build_bookings_report,
    "staff_activity": _build_staff_activity_report,
}


def get_post_login_redirect(user):
    if not user.role:
        return "login"

    role_code = (user.role.code or "").upper()
    role_name = (user.role.name or "").strip().lower()

    if role_code == "WORKER":
        return "workersdash"
    if role_code == "SUPERVISOR":
        return "supdash"
    if role_code == "MANAGER":
        return "managerdash"
    if role_code in {"OWNER", "INVESTOR"} or "investor" in role_name:
        return "investor"

    return "login"


def login_view(request):
    """Handle user login."""
    if request.user.is_authenticated:
        redirect_target = get_post_login_redirect(request.user)
        if redirect_target == "login":
            logout(request)
            messages.error(request, "Your account has no assigned role. Please contact administrator.")
            return render(request, "login.html")
        return redirect(redirect_target)
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        
        if not username or not password:
            messages.error(request, 'Please provide both username and password.')
            return render(request, 'login.html')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            if user.is_active:
                if user.is_locked:
                    messages.error(request, 'Your account has been locked. Please contact administrator.')
                    return render(request, 'login.html')

                if not user.role:
                    messages.error(request, 'Your account has no assigned role. Please contact administrator.')
                    return render(request, 'login.html')
                
                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                return redirect(get_post_login_redirect(user))
            else:
                messages.error(request, 'Your account has been disabled.')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'login.html')


def signup_view(request):
    """Handle user registration."""
    if request.user.is_authenticated:
        redirect_target = get_post_login_redirect(request.user)
        if redirect_target == "login":
            logout(request)
            messages.error(request, "Your account has no assigned role. Please contact administrator.")
            return redirect("login")
        return redirect(redirect_target)
    
    roles = Role.objects.filter(is_active=True).order_by("name")
    houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
    form_data = {}
    selected_house_ids = []
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        phone_number = request.POST.get('phone_number', '').strip()
        role_id = request.POST.get('role')
        selected_house_ids = request.POST.getlist('houses')

        form_data = {
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
            "role": role_id,
        }
        
        # Validation
        errors = []
        role = None
        selected_houses = list(houses.filter(pk__in=selected_house_ids))
        
        if not all([username, email, first_name, password, password_confirm, role_id]):
            errors.append('All fields are required.')
        
        if len(username) < 4:
            errors.append('Username must be at least 4 characters long.')
        
        if User.objects.filter(username=username).exists():
            errors.append('Username already exists.')
        
        if User.objects.filter(email=email).exists():
            errors.append('Email already exists.')
        
        if len(password) < 8:
            errors.append('Password must be at least 8 characters long.')
        
        if password != password_confirm:
            errors.append('Passwords do not match.')
        
        if not any(char.isupper() for char in password):
            errors.append('Password must contain at least one uppercase letter.')
        
        if not any(char.isdigit() for char in password):
            errors.append('Password must contain at least one digit.')

        if role_id:
            role = Role.objects.filter(id=role_id, is_active=True).first()
            if role is None:
                errors.append('Invalid role selected.')

        if len(selected_houses) != len(set(selected_house_ids)):
            errors.append('Please select valid poultry house assignments.')

        if role is not None:
            try:
                validate_house_assignment(role, selected_houses)
            except ValidationError as exc:
                errors.extend(exc.messages)
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(
                request,
                'signup.html',
                {
                    'roles': roles,
                    'houses': houses,
                    'form_data': form_data,
                    'selected_house_ids': selected_house_ids,
                },
            )
        
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                role=role
            )
            if selected_houses:
                user.houses.set(selected_houses)
            messages.success(request, 'Account created successfully! Please log in.')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return render(
                request,
                'signup.html',
                {
                    'roles': roles,
                    'houses': houses,
                    'form_data': form_data,
                    'selected_house_ids': selected_house_ids,
                },
            )
    
    context = {
        'roles': roles,
        'houses': houses,
        'form_data': form_data,
        'selected_house_ids': selected_house_ids,
    }
    return render(request, 'signup.html', context)


def logout_view(request):
    """Handle user logout."""
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('login')


@login_required(login_url='login')
def reports(request):
    period_key = request.GET.get("period", "current")
    if period_key not in {"current", "previous"}:
        period_key = "current"

    report_period = _month_window(period_key)
    month_start = report_period["start"]
    month_end = report_period["end"]

    selected_key = request.GET.get("report", REPORT_CARDS[0]["key"])
    if selected_key not in REPORT_BUILDERS:
        selected_key = REPORT_CARDS[0]["key"]

    selected_meta = next(card for card in REPORT_CARDS if card["key"] == selected_key)
    selected_report = REPORT_BUILDERS[selected_key](month_start, month_end)
    selected_report = {**selected_report, **selected_meta}

    download_format = request.GET.get("download", "").lower()
    if download_format in {"pdf", "excel", "xlsx"}:
        return _report_download_response(selected_report, report_period, download_format)

    context = {
        "today": report_period["today"],
        "month_start": month_start,
        "month_end": month_end,
        "month_label": report_period["month_label"],
        "period_label": report_period["label"],
        "selected_period": report_period["key"],
        "period_options": _report_period_options(),
        "report_cards": _build_report_cards(_report_metrics(month_start, month_end)),
        "selected_report": selected_report,
    }
    return render(request, 'reports.html', context)


@login_required(login_url='login')
def end_of_day(request):
    # Sample data for demonstration
    eggs = 2000
    sales = 10000000
    expenses = 500000
    deaths = 10
    profit = sales - expenses

    context = {
        'eggs': eggs,
        'sales': sales,
        'expenses': expenses,
        'deaths': deaths,
        'profit': profit,
    }
    return render(request, 'end_of_day.html', context)


@login_required(login_url='login')
def valuation(request):
    if request.method == "POST":
        transaction_type = request.POST.get("transaction_type", "").strip()
        transaction_date_raw = request.POST.get("transaction_date", "").strip()
        amount_raw = request.POST.get("amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        transaction_date = None
        amount = None

        valid_types = dict(InvestorCapitalTransaction.TransactionType.choices)
        if transaction_type not in valid_types:
            errors.append("Please select a valid capital transaction type.")

        if not transaction_date_raw:
            errors.append("Transaction date is required.")
        else:
            try:
                transaction_date = date.fromisoformat(transaction_date_raw)
            except ValueError:
                errors.append("Please enter a valid transaction date.")

        try:
            amount = Decimal(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid amount.")

        if not errors and transaction_date and amount:
            InvestorCapitalTransaction.objects.create(
                transaction_type=transaction_type,
                transaction_date=transaction_date,
                amount=amount,
                notes=notes,
                recorded_by=request.user,
            )
            messages.success(request, "Investor capital record saved.")
            return redirect("valuation")

        for error in errors:
            messages.error(request, error)

    def decimal_total(queryset, field_name):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"]

    capital_records = InvestorCapitalTransaction.objects.select_related("recorded_by")
    startup_capital = decimal_total(
        capital_records.filter(transaction_type=InvestorCapitalTransaction.TransactionType.STARTUP),
        "amount",
    )
    additional_capital = decimal_total(
        capital_records.filter(transaction_type=InvestorCapitalTransaction.TransactionType.ADDITION),
        "amount",
    )
    withdrawn = decimal_total(
        capital_records.filter(transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL),
        "amount",
    )
    invested = startup_capital + additional_capital
    net_owner_capital = invested - withdrawn

    sales = decimal_total(
        SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED),
        "total_amount",
    )
    cash_received = decimal_total(CustomerPayment.objects.all(), "amount")
    expenses = decimal_total(
        ExpenseTransaction.objects.exclude(status=ExpenseTransaction.Status.REJECTED),
        "total_amount",
    )
    profit = sales - expenses
    outstanding_receivables = decimal_total(ReceivableLedger.objects.all(), "balance")

    inventory_rows = {}
    for tx in InventoryTransaction.objects.select_related("item").order_by("item_id", "tx_date", "tx_id"):
        row = inventory_rows.setdefault(
            tx.item_id,
            {"quantity": Decimal("0.000"), "unit_price": Decimal("0.00")},
        )
        if tx.tx_type == InventoryTransaction.TxType.IN_:
            row["quantity"] += tx.quantity
            if tx.unit_price is not None:
                row["unit_price"] = tx.unit_price
        elif tx.tx_type == InventoryTransaction.TxType.OUT:
            row["quantity"] -= tx.quantity
        else:
            row["quantity"] += tx.quantity
    inventory_value = sum(
        max(row["quantity"], Decimal("0.000")) * row["unit_price"]
        for row in inventory_rows.values()
    )

    cash_position = net_owner_capital + cash_received - expenses
    assets = cash_position + outstanding_receivables + inventory_value
    liabilities = Decimal("0.00")
    business_value = assets - liabilities
    owner_equity = business_value
    owner_gain = owner_equity - net_owner_capital
    roi = (owner_gain / invested * 100) if invested > 0 else Decimal("0")

    capital_mix = [
        {"label": "Startup Capital", "value": float(startup_capital)},
        {"label": "Additional Capital", "value": float(additional_capital)},
        {"label": "Withdrawals", "value": float(withdrawn)},
    ]

    asset_mix = [
        {"label": "Cash Position", "value": float(cash_position)},
        {"label": "Receivables", "value": float(outstanding_receivables)},
        {"label": "Inventory Estimate", "value": float(inventory_value)},
    ]

    valuation_notes = []
    if invested <= 0:
        valuation_notes.append("Add startup capital first so ROI and owner equity can be measured properly.")
    if cash_position < 0:
        valuation_notes.append("Cash position is negative. The farm may need cash collection, cost control, or additional capital.")
    if outstanding_receivables > cash_received and cash_received > 0:
        valuation_notes.append("Receivables are higher than collected cash. Follow up customer balances before adding more capital.")
    if profit < 0:
        valuation_notes.append("The farm is carrying a loss. Review expenses and selling prices before expansion.")
    if not valuation_notes:
        valuation_notes.append("Valuation is stable based on the current records. Keep capital additions separated from operating revenue.")

    context = {
        "today": date.today(),
        "startup_capital": startup_capital,
        "additional_capital": additional_capital,
        'invested': invested,
        'withdrawn': withdrawn,
        "net_owner_capital": net_owner_capital,
        "cash_received": cash_received,
        "sales": sales,
        "expenses": expenses,
        'profit': profit,
        'assets': assets,
        'liabilities': liabilities,
        'business_value': business_value,
        "owner_equity": owner_equity,
        "owner_gain": owner_gain,
        'roi': roi,
        "cash_position": cash_position,
        "outstanding_receivables": outstanding_receivables,
        "inventory_value": inventory_value,
        "capital_records": capital_records[:12],
        "capital_mix": capital_mix,
        "asset_mix": asset_mix,
        "valuation_notes": valuation_notes,
        "capital_transaction_types": InvestorCapitalTransaction.TransactionType.choices,
    }
    return render(request, 'valuation.html', context)


@login_required(login_url='login')
def investor_reports(request):
    start_date_str = request.GET.get("start_date", "").strip()
    end_date_str = request.GET.get("end_date", "").strip()

    if start_date_str:
        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            start_date = None
            messages.error(request, "Invalid start date. Please use YYYY-MM-DD.")
    else:
        start_date = None

    if end_date_str:
        try:
            end_date = date.fromisoformat(end_date_str)
        except ValueError:
            end_date = None
            messages.error(request, "Invalid end date. Please use YYYY-MM-DD.")
    else:
        end_date = None

    pl_data = get_pl_data(start_date, end_date)
    bs_data = get_bs_data(start_date, end_date)

    fixed_assets_total = Decimal("0.00")
    accumulated_depreciation_total = Decimal("0.00")

    for group in bs_data.get("grouped_accounts", []):
        for type_group in group.get("type_groups", []):
            if type_group.get("type_value") == "FIXED_ASSET":
                fixed_assets_total += type_group.get("type_total", Decimal("0.00"))
            if type_group.get("type_value") == "ACCUMULATED_DEPRECIATION":
                accumulated_depreciation_total += type_group.get("type_total", Decimal("0.00"))

    net_book_value = fixed_assets_total - accumulated_depreciation_total

    context = {
        "start_date": start_date_str,
        "end_date": end_date_str,
        "pl_data": pl_data,
        "bs_data": bs_data,
        "fixed_assets_total": fixed_assets_total,
        "accumulated_depreciation_total": accumulated_depreciation_total,
        "net_book_value": net_book_value,
    }

    return render(request, 'investor_reports.html', context)
