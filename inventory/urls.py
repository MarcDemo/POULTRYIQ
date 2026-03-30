from django.urls import path
from . import views

urlpatterns = [
    path('suppliers/', views.suppliers, name='suppliers'),
    path('inventory-management/', views.inventory_management, name='inventory_management'),
]