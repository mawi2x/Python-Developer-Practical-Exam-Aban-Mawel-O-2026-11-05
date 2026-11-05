from django.urls import path

from .views import (
    CarCreateView, CarDeleteView, CarDetailView, CarListView, CarMoveView, CarUpdateView,
)

app_name = 'cars'

urlpatterns = [
    path('', CarListView.as_view(), name='list'),
    path('cars/add/', CarCreateView.as_view(), name='create'),
    path('cars/<int:pk>/', CarDetailView.as_view(), name='detail'),
    path('cars/<int:pk>/edit/', CarUpdateView.as_view(), name='edit'),
    path('cars/<int:pk>/delete/', CarDeleteView.as_view(), name='delete'),
    path('cars/<int:pk>/move/', CarMoveView.as_view(), name='move'),
]
