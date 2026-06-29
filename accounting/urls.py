from django.urls import path
from . import views

urlpatterns = [
    path('chart_of_accounts/', views.chart_of_accounts, name='chart_of_accounts'),
    path('balance_sheet/', views.balance_sheet, name='balance_sheet'),
    path('fixed-assets/', views.fixed_assets, name='fixed_assets'),
    path('asset-construction/', views.asset_construction_projects, name='asset_construction_projects'),
    path('asset-construction/<int:project_id>/', views.asset_construction_project_detail, name='asset_construction_project_detail'),
]
