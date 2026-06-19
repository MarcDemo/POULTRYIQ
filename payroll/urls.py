from django.urls import path
from . import views

urlpatterns = [
    path('salaries/', views.salaries, name='salaries'),
]
