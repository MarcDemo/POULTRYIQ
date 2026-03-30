from django.shortcuts import render

# Create your views here.
def suppliers(request):
    return render(request, 'suppliers.html')


def inventory_management(request):
    inventory = [
        {'item': 'Maize bran', 'quantity': 1000, 'unit': 'kg'},
        {'item': 'Soybean meal', 'quantity': 500, 'unit': 'kg'},
        {'item': 'Fish meal', 'quantity': 200, 'unit': 'kg'},
        {'item': 'Vitamin premix', 'quantity': 20, 'unit': 'kg'},
    ]
    return render(request, 'inventory_management.html', {'inventory': inventory})