from django.urls import path
from accounts import views as account_views
from . import views
urlpatterns = [
    path('', account_views.login_view, name='login'),
    path('birds/', views.birds, name='birds'),
    path('record_feed/', views.record_feed, name='record_feed'),
    path('feed-mixtures/', views.feed_mixtures, name='feed_mixtures'),
    path('feed-formulas/<int:pk>/archive/', views.archive_feed_formula, name='archive_feed_formula'),
    path('record_egg/', views.record_egg, name='record_egg'),
    path('record_cleaning/', views.record_cleaning, name='record_cleaning'),
    path('workersdash/', views.workersdash, name='workersdash'),
    path('supdash/', views.supdash, name='supdash'),
    path('supapproval/', views.supapproval, name='supapproval'),
    path('sup/approve/eggs/<int:pk>/', views.sup_approve_eggs, name='sup_approve_eggs'),
    path('sup/approve/feed/<int:pk>/', views.sup_approve_feed, name='sup_approve_feed'),
    path('sup/approve/cleaning/<int:pk>/', views.sup_approve_cleaning, name='sup_approve_cleaning'),
    path('sup/approve/mortality/<int:pk>/', views.sup_approve_mortality, name='sup_approve_mortality'),
    path('login/', account_views.login_view, name='login'),
    path('managerdashboard/', views.dashboard, name='managerdash'),
    path('dashboard/', views.dashboard, name='dashboard_legacy'),
    path('eggrec/', views.eggrec, name='eggrec'),
    path('feedrec/', views.feedrec, name='feedrec'),
    path('investor/', views.investor, name='investor'),
    path('investor/analysis/', views.investor_analysis, name='investor_analysis'),
    path('investor/targets/', views.investor_targets, name='investor_targets'),
    path('add_batch/', views.add_batch, name='add_batch'),
]
