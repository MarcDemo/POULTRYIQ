from django.urls import path
from . import views
urlpatterns = [
    path('manage_users/', views.manage_users, name='manage_users'),
    path('manage_users/<int:pk>/reset-password/', views.reset_user_password, name='reset_user_password'),
    path('add_user/', views.add_user, name='add_user'),
    path('users/<int:pk>/edit/', views.edit_user, name='edit_user'),
]
