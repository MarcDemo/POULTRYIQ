from django.urls import path
from . import views

urlpatterns = [
    path('mortality/', views.mortality, name='mortality'),
    path('treatment/', views.treatment, name='treatment'),
    path('vaccination/', views.vaccination, name='vaccination'),
    path('vaccine_report/', views.vaccine_report, name='vaccine_report'),
]
