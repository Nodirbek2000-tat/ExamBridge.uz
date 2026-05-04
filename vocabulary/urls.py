from django.urls import path
from . import views

urlpatterns = [
    path('', views.vocabulary_view, name='vocabulary'),
    path('review/<int:word_id>/', views.review_word_view, name='review_word'),
]
