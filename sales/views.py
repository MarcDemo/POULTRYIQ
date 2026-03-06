from django.shortcuts import render


# Create your views here.
def sales(request):
    return render(request, 'sales.html')

def orders(request):
    return render(request, 'orders.html')
