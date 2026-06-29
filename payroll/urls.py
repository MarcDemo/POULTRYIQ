from django.urls import path
from . import views

urlpatterns = [
    path('salaries/', views.salaries, name='salaries'),
    path('salaries/<int:pk>/edit/', views.edit_salary, name='edit_salary'),
]
