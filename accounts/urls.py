from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),
    path('reports/', views.reports, name='reports'),
    path('end_of_day/', views.end_of_day, name='end_of_day'),
    path('valuation/', views.valuation, name='valuation'),
    path('investor_reports/', views.investor_reports, name='investor_reports'),

]