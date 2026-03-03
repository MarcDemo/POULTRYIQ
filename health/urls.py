from django.urls import path
from . import views

urlpatterns = [
    path('mortality/', views.mortality, name='mortality'),
]
