from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import Role, User
from poultry.models import PoultryHouse


class UserModelTests(TestCase):
    def setUp(self):
        self.supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Supervisor",
        )
        self.worker_role = Role.objects.create(
            code=Role.RoleCode.WORKER,
            name="Farm Worker",
        )
        self.owner_role = Role.objects.create(
            code=Role.RoleCode.OWNER,
            name="Business Owner",
        )
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Farm Manager",
        )

    def test_display_name_uses_full_name_when_available(self):
        user = User.objects.create_user(
            username="manager1",
            password="StrongPass1",
            first_name="Grace",
            last_name="Auma",
            role=self.owner_role,
        )

        self.assertEqual(user.display_name, "Grace Auma")
        self.assertEqual(str(user), "Grace Auma")

    def test_role_code_and_authentication_state(self):
        user = User.objects.create_user(
            username="manager2",
            password="StrongPass1",
            role=self.owner_role,
            is_locked=True,
        )

        self.assertEqual(user.role_code, Role.RoleCode.OWNER)
        self.assertFalse(user.can_authenticate)

    def test_worker_and_supervisor_roles_require_house_assignment(self):
        self.assertTrue(User(role=self.worker_role).requires_house_assignment)
        self.assertTrue(User(role=self.supervisor_role).requires_house_assignment)
        self.assertFalse(User(role=self.manager_role).requires_house_assignment)
        self.assertFalse(User(role=self.owner_role).requires_house_assignment)


class SignupViewTests(TestCase):
    def setUp(self):
        self.worker_role = Role.objects.create(
            code=Role.RoleCode.WORKER,
            name="Farm Worker",
        )
        self.supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Supervisor",
        )
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Farm Manager",
        )
        self.house = PoultryHouse.objects.create(
            house_code="HSE-02",
            name="Brooder House",
            capacity=800,
        )

    def test_signup_rejects_worker_without_house(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "worker1",
                "email": "worker1@example.com",
                "first_name": "Mary",
                "last_name": "Atim",
                "password": "StrongPass1",
                "password_confirm": "StrongPass1",
                "phone_number": "0700000000",
                "role": str(self.worker_role.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Workers and supervisors must be assigned to at least one poultry house.",
        )
        self.assertFalse(User.objects.filter(username="worker1").exists())

    def test_signup_rejects_supervisor_without_house(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "supervisor1",
                "email": "supervisor1@example.com",
                "first_name": "Paul",
                "last_name": "Okello",
                "password": "StrongPass1",
                "password_confirm": "StrongPass1",
                "phone_number": "0700000001",
                "role": str(self.supervisor_role.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Workers and supervisors must be assigned to at least one poultry house.",
        )
        self.assertFalse(User.objects.filter(username="supervisor1").exists())

    def test_signup_assigns_selected_house_to_worker(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "worker2",
                "email": "worker2@example.com",
                "first_name": "Jane",
                "last_name": "Anyango",
                "password": "StrongPass1",
                "password_confirm": "StrongPass1",
                "phone_number": "0700000002",
                "role": str(self.worker_role.id),
                "houses": [str(self.house.pk)],
            },
        )

        self.assertRedirects(response, reverse("login"))

        user = User.objects.get(username="worker2")
        self.assertEqual(user.role, self.worker_role)
        self.assertQuerySetEqual(
            user.houses.order_by("house_code"),
            [self.house],
            transform=lambda item: item,
        )

    def test_signup_allows_manager_without_house(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "manager1",
                "email": "manager1@example.com",
                "first_name": "Joel",
                "last_name": "Otim",
                "password": "StrongPass1",
                "password_confirm": "StrongPass1",
                "phone_number": "0700000003",
                "role": str(self.manager_role.id),
            },
        )

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(username="manager1")
        self.assertEqual(user.role, self.manager_role)
        self.assertEqual(user.houses.count(), 0)


class TemplateSyntaxSmokeTests(SimpleTestCase):
    """Smoke test to fail fast on template syntax regressions."""

    @staticmethod
    def _collect_template_names(template_dir: Path) -> set[str]:
        if not template_dir.exists():
            return set()

        return {
            str(path.relative_to(template_dir)).replace("\\", "/")
            for path in template_dir.rglob("*.html")
        }

    def _project_template_names(self) -> list[str]:
        names: set[str] = set()

        for template_dir in settings.TEMPLATES[0].get("DIRS", []):
            names.update(self._collect_template_names(Path(template_dir)))

        for app_config in apps.get_app_configs():
            app_template_dir = Path(app_config.path) / "templates"
            names.update(self._collect_template_names(app_template_dir))

        return sorted(names)

    def test_all_templates_compile(self):
        failures = []

        for template_name in self._project_template_names():
            try:
                get_template(template_name)
            except TemplateSyntaxError as exc:
                failures.append(f"{template_name}: {exc}")

        self.assertFalse(
            failures,
            "Template syntax smoke test failures:\n" + "\n".join(failures),
        )


class LoginRedirectTests(TestCase):
    def setUp(self):
        self.worker_role = Role.objects.create(
            code=Role.RoleCode.WORKER,
            name="Farm Worker",
        )
        self.supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Supervisor",
        )
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Farm Manager",
        )
        self.owner_role = Role.objects.create(
            code=Role.RoleCode.OWNER,
            name="Investor",
        )
        self.house = PoultryHouse.objects.create(
            house_code="HSE-03",
            name="Production House",
            capacity=1000,
        )

    def test_worker_login_redirects_to_workers_dashboard(self):
        user = User.objects.create_user(
            username="worker-login",
            password="StrongPass1",
            role=self.worker_role,
        )
        user.houses.set([self.house])

        response = self.client.post(
            reverse("login"),
            {
                "username": "worker-login",
                "password": "StrongPass1",
            },
        )

        self.assertRedirects(response, reverse("workersdash"))

    def test_supervisor_login_redirects_to_supervisor_dashboard(self):
        user = User.objects.create_user(
            username="supervisor-login",
            password="StrongPass1",
            role=self.supervisor_role,
        )
        user.houses.set([self.house])

        response = self.client.post(
            reverse("login"),
            {
                "username": "supervisor-login",
                "password": "StrongPass1",
            },
        )

        self.assertRedirects(response, reverse("supdash"))

    def test_manager_login_redirects_to_main_dashboard(self):
        user = User.objects.create_user(
            username="manager-login",
            password="StrongPass1",
            role=self.manager_role,
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "manager-login",
                "password": "StrongPass1",
            },
        )

        self.assertRedirects(response, reverse("dashboard"))

    def test_investor_login_redirects_to_investor_dashboard(self):
        User.objects.create_user(
            username="investor-login",
            password="StrongPass1",
            role=self.owner_role,
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "investor-login",
                "password": "StrongPass1",
            },
        )

        self.assertRedirects(response, reverse("investor"))


class NavSidebarVisibilityTests(TestCase):
    """Assert that each role sees exactly its own nav/sidebar chrome."""

    def setUp(self):
        self.house = PoultryHouse.objects.create(
            house_code="HSE-NAV",
            name="Nav Test House",
            capacity=500,
        )
        self.worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        self.supervisor_role = Role.objects.create(code=Role.RoleCode.SUPERVISOR, name="Supervisor")
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.owner_role = Role.objects.create(code=Role.RoleCode.OWNER, name="Investor")

        self.worker = User.objects.create_user(username="nav-worker", password="Pass1234!", role=self.worker_role)
        self.worker.houses.set([self.house])

        self.supervisor = User.objects.create_user(username="nav-sup", password="Pass1234!", role=self.supervisor_role)
        self.supervisor.houses.set([self.house])

        self.manager = User.objects.create_user(username="nav-manager", password="Pass1234!", role=self.manager_role)
        self.investor = User.objects.create_user(username="nav-investor", password="Pass1234!", role=self.owner_role)

    # ------------------------------------------------------------------ worker
    def test_worker_sees_worker_sidebar_on_workersdash(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse("workersdash"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="workerSidebar"')
        self.assertContains(response, "Worker Panel")

    def test_worker_does_not_see_other_sidebars(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse("workersdash"))

        self.assertNotContains(response, 'id="supervisorSidebar"')
        self.assertNotContains(response, 'id="managerSidebar"')
        self.assertNotContains(response, 'id="investorNavbar"')
        self.assertNotContains(response, "Supervisor Panel")

    # -------------------------------------------------------------- supervisor
    def test_supervisor_sees_supervisor_sidebar_on_supdash(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supdash"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="supervisorSidebar"')
        self.assertContains(response, "Supervisor Panel")

    def test_supervisor_does_not_see_other_sidebars(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supdash"))

        self.assertNotContains(response, 'id="workerSidebar"')
        self.assertNotContains(response, 'id="managerSidebar"')
        self.assertNotContains(response, 'id="investorNavbar"')
        self.assertNotContains(response, "Worker Panel")

    # --------------------------------------------------------------- manager
    def test_manager_sees_manager_sidebar_on_dashboard(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="managerSidebar"')

    def test_manager_does_not_see_other_sidebars(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard"))

        self.assertNotContains(response, 'id="workerSidebar"')
        self.assertNotContains(response, 'id="supervisorSidebar"')
        self.assertNotContains(response, 'id="investorNavbar"')
        self.assertNotContains(response, "Worker Panel")
        self.assertNotContains(response, "Supervisor Panel")

    # --------------------------------------------------------------- investor
    def test_investor_sees_investor_navbar_on_investor_page(self):
        self.client.force_login(self.investor)
        response = self.client.get(reverse("investor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="investorNavbar"')
        self.assertContains(response, "PoultryIQ Investor")

    def test_investor_does_not_see_sidebars(self):
        self.client.force_login(self.investor)
        response = self.client.get(reverse("investor"))

        self.assertNotContains(response, 'id="workerSidebar"')
        self.assertNotContains(response, 'id="supervisorSidebar"')
        self.assertNotContains(response, 'id="managerSidebar"')
        self.assertNotContains(response, "Worker Panel")
        self.assertNotContains(response, "Supervisor Panel")
