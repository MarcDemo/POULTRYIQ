from django.urls import path
from . import views
urlpatterns = [
    path('staff/', views.staff_profiles, name='staff_profiles'),
    path('staff/<int:pk>/', views.staff_profile_detail, name='staff_profile_detail'),
    path('staff/<int:pk>/edit/', views.edit_staff_profile, name='edit_staff_profile'),
    path('staff/<int:pk>/documents/upload/', views.upload_employee_document, name='upload_employee_document'),
    path('staff/<int:pk>/unpaid-absences/', views.record_unpaid_absence, name='record_unpaid_absence'),
    path('staff/<int:pk>/contract-renewals/', views.renew_staff_contract, name='renew_staff_contract'),
    path('welfare/', views.worker_welfare, name='worker_welfare'),
    path('welfare/supervisor/', views.supervisor_welfare, name='supervisor_welfare'),
    path('welfare/supervisor/<int:pk>/review/', views.supervisor_review_welfare, name='supervisor_review_welfare'),
    path('welfare/manager/', views.manager_welfare, name='manager_welfare'),
    path('welfare/manager/<int:pk>/review/', views.manager_review_welfare, name='manager_review_welfare'),
    path('salary-advances/', views.advance_register, name='advance_register'),
    path('salary-advances/<int:pk>/disburse/', views.disburse_salary_advance, name='disburse_salary_advance'),
    path('manage_users/', views.manage_users, name='manage_users'),
    path('manage_users/<int:pk>/reset-password/', views.reset_user_password, name='reset_user_password'),
    path('add_user/', views.add_user, name='add_user'),
    path('users/<int:pk>/edit/', views.edit_user, name='edit_user'),
]
