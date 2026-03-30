from django.urls import path
from . import views
urlpatterns = [
    path('manage_users/', views.manage_users, name='manage_users'),
   path('add_user/', views.add_user, name='add_user'),
]