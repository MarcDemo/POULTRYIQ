from django.urls import path
from . import views

urlpatterns = [
    path('chart_of_accounts/', views.chart_of_accounts, name='chart_of_accounts'),
    path('balance_sheet/', views.balance_sheet, name='balance_sheet'),
    path('trial-balance/', views.trial_balance, name='trial_balance'),
    path('cash-flow/', views.cash_flow, name='cash_flow'),
    path('statements/<str:statement_code>/', views.financial_statement, name='financial_statement'),
    path('fixed-assets/', views.fixed_assets, name='fixed_assets'),
    path('asset-construction/', views.asset_construction_projects, name='asset_construction_projects'),
    path('asset-construction/<int:project_id>/', views.asset_construction_project_detail, name='asset_construction_project_detail'),
]
