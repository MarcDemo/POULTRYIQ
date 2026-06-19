from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class SalariesViewTests(TestCase):
    def test_salaries_page_renders(self):
        user = get_user_model().objects.create_user(
            username="salary-user",
            password="pass1234",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("salaries"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "payroll/salaries.html")
        self.assertContains(response, "Salary Management")
