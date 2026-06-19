from django.urls import path
from . import views

urlpatterns = [
    path('suppliers/', views.suppliers, name='suppliers'),
    path('suppliers/tin-lookup/', views.tin_lookup, name='tin_lookup'),
    path('inventory-management/', views.inventory_management, name='inventory_management'),
    path('requisitions/', views.supervisor_requisitions, name='supervisor_requisitions'),
    path('requisitions/manager/', views.manager_requisitions, name='manager_requisitions'),
    path('requisitions/manager/<int:pk>/review/', views.manager_review_requisition, name='manager_review_requisition'),
    path('store/', views.store_out, name='store'),
]
