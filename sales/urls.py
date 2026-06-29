from django.urls import path
from . import views
urlpatterns = [
    path('customers/', views.customers, name='customers'),
    path('customers/<int:pk>/', views.customer_profile, name='customer_profile'),
    path('sales/', views.sales, name='sales'),
    path('orders/view/', views.orderview, name='orderview'),
    path('orders/', views.orders, name='orders'),
]
