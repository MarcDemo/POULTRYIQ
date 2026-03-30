from django.urls import path
from . import views
urlpatterns = [
    path('reports/', views.reports, name='reports'),
    path('end_of_day/', views.end_of_day, name='end_of_day'),
]