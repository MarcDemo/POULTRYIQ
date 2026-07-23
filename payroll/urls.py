from django.urls import path
from . import views

urlpatterns = [
    path('salaries/', views.salaries, name='salaries'),
    path('salaries/history/', views.salary_history, name='salary_history'),
    path('salaries/bonuses/', views.salary_bonuses, name='salary_bonuses'),
    path('salaries/<str:period_month>/pending/', views.pending_salaries, name='pending_salaries'),
    path('salaries/<int:pk>/edit/', views.edit_salary, name='edit_salary'),
]
