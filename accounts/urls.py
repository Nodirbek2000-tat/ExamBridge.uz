from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('complete-profile/', views.complete_profile_view, name='complete_profile'),
    path('profile/', views.profile_view, name='profile'),
]
