from django.urls import path
from . import views

urlpatterns = [
    path('chart_of_accounts/', views.chart_of_accounts, name='chart_of_accounts'),
    path('balance_sheet/', views.balance_sheet, name='balance_sheet'),
    path('trial-balance/', views.trial_balance, name='trial_balance'),
    path('cash-flow/', views.cash_flow, name='cash_flow'),
    path('statements/<str:statement_code>/', views.financial_statement, name='financial_statement'),
    path('journals/', views.journal_entries, name='journal_entries'),
    path('budgets/', views.budgets, name='budgets'),
    path('fixed-assets/', views.fixed_assets, name='fixed_assets'),
    path('fixed-assets/depreciation-run/', views.depreciation_run, name='depreciation_run'),
    path('fixed-assets/<int:asset_id>/dispose/', views.asset_disposal, name='asset_disposal'),
    path('fixed-assets/<int:asset_id>/revalue/', views.asset_revaluation, name='asset_revaluation'),
    path('asset-construction/', views.asset_construction_projects, name='asset_construction_projects'),
    path('asset-construction/<int:project_id>/', views.asset_construction_project_detail, name='asset_construction_project_detail'),
]
