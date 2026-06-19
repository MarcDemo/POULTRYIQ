from django.urls import path
from . import views
urlpatterns = [
    path('welfare/', views.worker_welfare, name='worker_welfare'),
    path('welfare/supervisor/', views.supervisor_welfare, name='supervisor_welfare'),
    path('welfare/supervisor/<int:pk>/review/', views.supervisor_review_welfare, name='supervisor_review_welfare'),
    path('welfare/manager/', views.manager_welfare, name='manager_welfare'),
    path('welfare/manager/<int:pk>/review/', views.manager_review_welfare, name='manager_review_welfare'),
    path('manage_users/', views.manage_users, name='manage_users'),
    path('manage_users/<int:pk>/reset-password/', views.reset_user_password, name='reset_user_password'),
    path('add_user/', views.add_user, name='add_user'),
    path('users/<int:pk>/edit/', views.edit_user, name='edit_user'),
]
