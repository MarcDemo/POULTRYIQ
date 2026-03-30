from django.shortcuts import render

# Create your views here.
def reports(request):
    return render(request, 'reports.html')

def end_of_day(request):
    # Sample data for demonstration
    eggs = 2000
    sales = 10000000
    expenses = 500000
    deaths = 10
    profit = sales - expenses

    context = {
        'eggs': eggs,
        'sales': sales,
        'expenses': expenses,
        'deaths': deaths,
        'profit': profit,
    }
    return render(request, 'end_of_day.html', context)