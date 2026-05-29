from django.shortcuts import render
from accounts.models import User
from poultryiq.pagination import paginate

# Create your views here.
def manage_users(request):
    users = User.objects.select_related("role").order_by("first_name", "username")
    page_obj, querystring = paginate(request, users, per_page=20)
    return render(request, 'manage_users.html', {
        "users": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
    })

def add_user(request):
    return render(request, 'add_user.html')
