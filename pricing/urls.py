from django.urls import path
from . import views

urlpatterns = [
    path('', views.pricing_view, name='pricing'),
    path('subscribe/<str:plan_type>/', views.subscribe_view, name='subscribe'),
]
