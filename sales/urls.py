from django.urls import path
from . import views
urlpatterns = [
    path('customers/', views.customers, name='customers'),
    path('customers/<int:pk>/', views.customer_profile, name='customer_profile'),
    path('receivables/', views.receivables, name='receivables'),
    path('receivables/<int:receivable_id>/payment/', views.record_receivable_payment, name='record_receivable_payment'),
    path('sales/', views.sales, name='sales'),
    path('orders/view/', views.orderview, name='orderview'),
    path('orders/', views.orders, name='orders'),
]
