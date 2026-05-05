from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from poultry.models import PoultryBatch, PoultryHouse

from .models import SickbayCleaningRecord, SicknessReport


class SicknessFlowTests(TestCase):
    def setUp(self):
        self.worker_role = Role.objects.create(
            code=Role.RoleCode.WORKER,
            name="Worker",
        )
        self.supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Supervisor",
        )
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Manager",
        )

        self.manager = User.objects.create_user(
            username="manager",
            password="pass1234",
            role=self.manager_role,
        )
        self.worker = User.objects.create_user(
            username="worker",
            password="pass1234",
            role=self.worker_role,
        )
        self.supervisor = User.objects.create_user(
            username="supervisor",
            password="pass1234",
            role=self.supervisor_role,
        )

        self.house_a = PoultryHouse.objects.create(
            house_code="HSE-A",
            name="House A",
            capacity=500,
        )
        self.house_b = PoultryHouse.objects.create(
            house_code="HSE-B",
            name="House B",
            capacity=500,
        )

        self.worker.houses.add(self.house_a)
        self.supervisor.houses.add(self.house_a)

        self.batch_a = PoultryBatch.objects.create(
            batch_code="BT-ACTIVE-A",
            house=self.house_a,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date(2026, 4, 1),
            initial_quantity=200,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )
        self.batch_b = PoultryBatch.objects.create(
            batch_code="BT-ACTIVE-B",
            house=self.house_b,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date(2026, 4, 1),
            initial_quantity=220,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )

    def test_worker_sickness_report_links_house_batch_and_isolation(self):
        self.client.force_login(self.worker)

        response = self.client.post(
            reverse("report_sickness"),
            {
                "date": "2026-05-04",
                "house": str(self.house_a.pk),
                "disease": "Newcastle",
                "affected": "12",
                "action": "isolate",
                "notes": "Birds separated quickly.",
            },
        )

        self.assertRedirects(response, reverse("report_sickness"))
        report = SicknessReport.objects.get()
        self.assertEqual(report.house_ref, self.house_a)
        self.assertEqual(report.batch, self.batch_a)
        self.assertTrue(report.isolated)
        self.assertEqual(report.isolation_name, "House A-Isolation(Newcastle)")

    def test_worker_can_record_sickbay_cleaning_from_isolated_report(self):
        report = SicknessReport.objects.create(
            date=date(2026, 5, 4),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            disease="Coryza",
            affected=8,
            action="sickbay",
            notes="Moved to sickbay.",
            reported_by=self.worker,
        )
        self.client.force_login(self.worker)

        response = self.client.post(
            reverse("record_sickbay_cleaning"),
            {
                "sickness_report": str(report.pk),
                "record_date": "2026-05-04",
                "sickbay_cleaned": "on",
                "disinfected": "on",
                "notes": "Area cleaned after treatment round.",
            },
        )

        self.assertRedirects(response, reverse("record_sickbay_cleaning"))
        cleaning = SickbayCleaningRecord.objects.get()
        self.assertEqual(cleaning.sickness_report, report)
        self.assertTrue(cleaning.sickbay_cleaned)
        self.assertTrue(cleaning.disinfection_done)
        self.assertEqual(cleaning.recorded_by, self.worker)

    def test_supervisor_dashboard_only_shows_sickness_from_assigned_houses(self):
        SicknessReport.objects.create(
            date=date(2026, 5, 4),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            disease="Newcastle",
            affected=10,
            action="isolate",
            reported_by=self.worker,
        )
        SicknessReport.objects.create(
            date=date(2026, 5, 4),
            house="House B",
            house_ref=self.house_b,
            batch=self.batch_b,
            disease="Coccidiosis",
            affected=7,
            action="isolate",
            reported_by=self.manager,
        )

        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supdash"))

        recent_reports = list(response.context["recent_sickness_reports"])
        self.assertEqual(len(recent_reports), 1)
        self.assertEqual(recent_reports[0].house_ref, self.house_a)
        self.assertEqual(response.context["isolated_sickness_cases"], 1)

    def test_manager_sickness_view_exposes_cleaning_summary(self):
        report = SicknessReport.objects.create(
            date=date(2026, 5, 4),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            disease="Newcastle",
            affected=10,
            action="isolate",
            reported_by=self.worker,
        )
        SickbayCleaningRecord.objects.create(
            sickness_report=report,
            record_date=date(2026, 5, 4),
            sickbay_cleaned=True,
            recorded_by=self.worker,
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("view_sickness"))

        report_row = list(response.context["reports"])[0]
        self.assertEqual(response.context["total_cases"], 1)
        self.assertEqual(response.context["total_affected"], 10)
        self.assertEqual(response.context["isolated_cases"], 1)
        self.assertEqual(report_row.cleaning_sessions, 1)
        self.assertEqual(report_row.last_cleaned_at, date(2026, 5, 4))
