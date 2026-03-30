from django.shortcuts import render

# Create your views here.
def mortality(request):
    return render(request, 'mortality.html')

def treatment(request):
    return render(request, 'treatment.html')

def vaccination(request):
    return render(request, 'vaccination.html')

def vaccine_report(request):
    vaccinations = [
        {'date': '5th april', 'house': 'house B', 'vaccine': 'newcastle vaccine', 'status': 'scheduled'},
        {'date': '4th april', 'house': 'house A', 'vaccine': 'gumboro vaccine', 'status': 'done'},
    ]
    return render(request, 'vaccine_report.html', {'vaccinations': vaccinations})