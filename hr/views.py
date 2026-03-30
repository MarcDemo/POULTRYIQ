from django.shortcuts import render

# Create your views here.
def manage_users(request):
    return render(request, 'manage_users.html')

def add_user(request):
    return render(request, 'add_user.html')