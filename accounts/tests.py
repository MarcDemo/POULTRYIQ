from django.test import TestCase
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
