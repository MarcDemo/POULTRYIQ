from django.urls import path
from accounts import views as account_views
from . import views
urlpatterns = [
    path('', account_views.login_view, name='home'),
    path('birds/', views.birds, name='birds'),
    path('record_feed/', views.record_feed, name='record_feed'),
    path('record_egg/', views.record_egg, name='record_egg'),
    path('record_cleaning/', views.record_cleaning, name='record_cleaning'),
    path('workersdash/', views.workersdash, name='workersdash'),
    path('supdash/', views.supdash, name='supdash'),
    path('supapproval/', views.supapproval, name='supapproval'),  
    path('login/', account_views.login_view, name='login'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('eggrec/', views.eggrec, name='eggrec'),
    path('feedrec/', views.feedrec, name='feedrec'),
    path('investor/', views.investor, name='investor'),
]
