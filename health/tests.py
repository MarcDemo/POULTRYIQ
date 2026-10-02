from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from alerts.models import Alert, AlertType
from poultry.models import PoultryBatch, PoultryHouse

from .models import SickbayCleaningRecord, SicknessReport, TreatmentPlanItem


class SupervisorSicknessWorkflowTests(TestCase):
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

    def test_supervisor_records_single_bird_sickness_case(self):
        self.client.force_login(self.supervisor)

        response = self.client.post(
            reverse("report_sickness"),
            {
                "date": "2026-05-05",
                "house": str(self.house_a.pk),
                        "bird_identifier": "BIRD-001",
                "affected": "1",
                "symptoms": "Coughing and low appetite",
                "action": "sickbay",
                "notes": "Moved out for observation.",
            },
        )

        self.assertRedirects(response, reverse("report_sickness"))
        report = SicknessReport.objects.get()
        self.assertEqual(report.house_ref, self.house_a)
        self.assertEqual(report.batch, self.batch_a)
        self.assertEqual(report.bird_identifier, "BIRD-001")
        self.assertEqual(report.symptoms, "Coughing and low appetite")
        self.assertEqual(report.affected, 1)
        self.assertEqual(report.case_status, SicknessReport.CaseStatus.REPORTED)
        self.assertTrue(report.isolated)

    def test_supervisor_can_record_grouped_sickness_case(self):
        self.client.force_login(self.supervisor)

        response = self.client.post(
            reverse("report_sickness"),
            {
                "date": "2026-05-05",
                "house": str(self.house_a.pk),
                        "bird_identifier": "",
                "affected": "10",
                "symptoms": "Sneezing and watery eyes in one pen",
                "action": "crowd",
                "notes": "Observed as one grouped case before vet review.",
            },
        )

        self.assertRedirects(response, reverse("report_sickness"))
        report = SicknessReport.objects.get()
        self.assertEqual(report.affected, 10)
        self.assertEqual(report.case_label, "10 birds")
        self.assertEqual(report.case_status, SicknessReport.CaseStatus.REPORTED)
        self.assertFalse(report.isolated)

    def test_supervisor_records_diagnosis_and_dosage_creates_persistent_alert(self):
        report = SicknessReport.objects.create(
            date=date(2026, 5, 5),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            bird_identifier="BIRD-002",
            symptoms="Swollen eyes",
            affected=1,
            action="sickbay",
            reported_by=self.supervisor,
            case_status=SicknessReport.CaseStatus.REPORTED,
        )

        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("treatment"),
            {
                "form_action": "record_treatment_plan",
                "sickness_report": str(report.pk),
                "vet_visit_date": "2026-05-05",
                "vet_name": "Dr. Nakato",
                "diagnosis": "Coryza",
                "medicine_name": "Tylosin",
                "dosage": "2ml twice daily",
                "administration_route": "Oral",
                "scheduled_for": "2026-05-05",
                "instructions": "Give after feeding.",
                "diagnosis_notes": "Continue for 3 days.",
            },
        )

        self.assertRedirects(response, reverse("treatment"))
        report.refresh_from_db()
        plan_item = TreatmentPlanItem.objects.get(sickness_report=report)

        self.assertEqual(report.disease, "Coryza")
        self.assertEqual(report.vet_name, "Dr. Nakato")
        self.assertEqual(report.case_status, SicknessReport.CaseStatus.DIAGNOSED)
        self.assertEqual(plan_item.medicine_name, "Tylosin")
        self.assertEqual(plan_item.dosage, "2ml twice daily")
        self.assertIsNotNone(plan_item.alert)
        self.assertTrue(plan_item.alert.persist_until_resolved)
        self.assertEqual(plan_item.alert.status, Alert.Status.UNREAD)

    def test_marking_dose_as_given_resolves_alert_and_completes_case(self):
        health_type = AlertType.objects.create(
            code=AlertType.AlertTypeCode.HEALTH,
            name="Health Alert",
        )
        report = SicknessReport.objects.create(
            date=date(2026, 5, 5),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            bird_identifier="BIRD-003",
            symptoms="Weak legs",
            disease="Vitamin deficiency",
            vet_name="Dr. Nakato",
            vet_visit_date=date(2026, 5, 5),
            affected=1,
            action="crowd",
            reported_by=self.supervisor,
            case_status=SicknessReport.CaseStatus.DIAGNOSED,
        )
        alert = Alert.objects.create(
            alert_type=health_type,
            title="Treatment due for 1 bird",
            message="Give vitamins",
            receiver=self.supervisor,
            priority=Alert.Priority.HIGH,
            persist_until_resolved=True,
        )
        plan_item = TreatmentPlanItem.objects.create(
            sickness_report=report,
            medicine_name="Vitamin mix",
            dosage="5ml in water",
            scheduled_for=date(2026, 5, 5),
            created_by=self.supervisor,
            alert=alert,
        )

        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("treatment"),
            {
                "form_action": "mark_given",
                "plan_id": str(plan_item.pk),
            },
        )

        self.assertRedirects(response, reverse("treatment"))
        plan_item.refresh_from_db()
        alert.refresh_from_db()
        report.refresh_from_db()

        self.assertTrue(plan_item.is_given)
        self.assertIsNotNone(plan_item.given_at)
        self.assertEqual(plan_item.marked_given_by, self.supervisor)
        self.assertEqual(alert.status, Alert.Status.RESOLVED)
        self.assertEqual(report.case_status, SicknessReport.CaseStatus.TREATMENT_COMPLETED)
        self.assertIsNotNone(report.treatment_completed_at)

    def test_supervisor_dashboard_scopes_sickness_and_treatments_to_assigned_houses(self):
        report_a = SicknessReport.objects.create(
            date=date(2026, 5, 5),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            bird_identifier="BIRD-A",
            symptoms="Sneezing",
            affected=1,
            action="sickbay",
            reported_by=self.supervisor,
        )
        report_b = SicknessReport.objects.create(
            date=date(2026, 5, 5),
            house="House B",
            house_ref=self.house_b,
            batch=self.batch_b,
            bird_identifier="BIRD-B",
            symptoms="Drooping wings",
            affected=1,
            action="sickbay",
            reported_by=self.manager,
        )
        TreatmentPlanItem.objects.create(
            sickness_report=report_a,
            medicine_name="Med A",
            dosage="1ml",
            created_by=self.supervisor,
        )
        TreatmentPlanItem.objects.create(
            sickness_report=report_b,
            medicine_name="Med B",
            dosage="2ml",
            created_by=self.manager,
        )

        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supdash"))

        recent_reports = list(response.context["recent_sickness_reports"])
        recent_treatments = list(response.context["recent_pending_treatments"])
        self.assertEqual(len(recent_reports), 1)
        self.assertEqual(recent_reports[0].house_ref, self.house_a)
        self.assertEqual(response.context["pending_treatment_doses"], 1)
        self.assertEqual(len(recent_treatments), 1)
        self.assertEqual(recent_treatments[0].sickness_report.house_ref, self.house_a)

    def test_supervisor_can_open_scoped_sickness_report_list(self):
        visible_report = SicknessReport.objects.create(
            date=date(2026, 5, 5),
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            bird_identifier="BIRD-A",
            symptoms="Sneezing",
            affected=1,
            action="sickbay",
            reported_by=self.supervisor,
        )
        SicknessReport.objects.create(
            date=date(2026, 5, 5),
            house="House B",
            house_ref=self.house_b,
            batch=self.batch_b,
            bird_identifier="BIRD-B",
            symptoms="Drooping wings",
            affected=1,
            action="sickbay",
            reported_by=self.manager,
        )

        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("view_sickness"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["reports"]), [visible_report])
        self.assertContains(response, "BIRD-A")
        self.assertNotContains(response, "BIRD-B")

    def test_worker_can_record_sickbay_cleaning_from_sickbay_report(self):
        today = date.today()
        report = SicknessReport.objects.create(
            date=today,
            house="House A",
            house_ref=self.house_a,
            batch=self.batch_a,
            bird_identifier="BIRD-004",
            symptoms="Lethargic",
            affected=1,
            action="sickbay",
            notes="Moved to sickbay.",
            reported_by=self.supervisor,
        )
        self.client.force_login(self.worker)

        response = self.client.post(
            reverse("record_sickbay_cleaning"),
            {
                "sickness_report": str(report.pk),
                "record_date": today.isoformat(),
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
