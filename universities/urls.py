from django.urls import path
from . import views

urlpatterns = [
    path('', views.university_list_view, name='university_list'),
]
