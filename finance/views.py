from django.shortcuts import render

# Create your views here.
def expense_form(request):
    return render(request, 'expense_form.html')

def salaries(request):
    return render(request, 'salaries.html')