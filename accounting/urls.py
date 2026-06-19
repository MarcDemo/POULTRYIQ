from django.urls import path
from . import views

urlpatterns = [
    path('chart_of_accounts/', views.chart_of_accounts, name='chart_of_accounts'),
]
