from django.urls import path
from . import views
urlpatterns = [
    path('reports/', views.reports, name='reports'),
    path('end_of_day/', views.end_of_day, name='end_of_day'),
    path('valuation/', views.valuation, name='valuation'),
    path('investor_reports/', views.investor_reports, name='investor_reports'),
]