from django.urls import path
from . import views
urlpatterns = [
    path('expense_form/', views.expense_form, name='expense_form'),
]