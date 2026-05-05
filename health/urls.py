from django.urls import path
from . import views

urlpatterns = [
    path('mortality/', views.mortality, name='mortality'),
    path('treatment/', views.treatment, name='treatment'),
    path('vaccination/', views.vaccination, name='vaccination'),
    path('vaccine_report/', views.vaccine_report, name='vaccine_report'),
    path('report-sickness/', views.report_sickness, name='report_sickness'),
    path('record-sickbay-cleaning/', views.record_sickbay_cleaning, name='record_sickbay_cleaning'),
    path('sickbay/record_egg/', views.sick_record_egg, name='sick_record_egg'),
    path('sickbay/record_feed/', views.sick_record_feed, name='sick_record_feed'),
    path('sickbay/record_cleaning/', views.sick_record_cleaning, name='sick_record_cleaning'),
    path('sickbay/mortality/', views.sick_mortality, name='sick_mortality'),
    path('view-sickness-reports/', views.view_sickness_reports, name='view_sickness'),
    path('sickbay/', views.sickbay, name='sickbay'),
]
