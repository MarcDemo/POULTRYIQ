from django.contrib import messages
from django.shortcuts import redirect, render

from .models import Supplier

# Create your views here.
def suppliers(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        location = request.POST.get("location", "").strip()
        product = request.POST.get("product", "").strip()

        if not name:
            messages.error(request, "Supplier name is required.")
        else:
            supplier, created = Supplier.objects.update_or_create(
                name__iexact=name,
                defaults={
                    "name": name,
                    "phone": phone,
                    "location": location,
                    "product": product,
                    "is_active": True,
                },
            )
            if created:
                messages.success(request, "Supplier saved successfully.")
            else:
                messages.success(request, "Supplier details updated.")
            return redirect("suppliers")

    return render(request, 'suppliers.html', {"suppliers": Supplier.objects.filter(is_active=True)})


def inventory_management(request):
    inventory = [
        {'item': 'Maize bran', 'quantity': 1000, 'unit': 'kg'},
        {'item': 'Soybean meal', 'quantity': 500, 'unit': 'kg'},
        {'item': 'Fish meal', 'quantity': 200, 'unit': 'kg'},
        {'item': 'Vitamin premix', 'quantity': 20, 'unit': 'kg'},
    ]
    return render(request, 'inventory_management.html', {'inventory': inventory})
