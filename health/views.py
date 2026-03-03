from django.shortcuts import render

# Create your views here.
def mortality(request):
    return render(request, 'mortality.html')