from django.urls import path
from . import views
urlpatterns = [
    path('sales/', views.sales, name='sales'),
    path('orders/view/', views.orderview, name='orderview'),
    path('orders/', views.orders, name='orders'),
]
