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


def valuation(request):
    # Sample data for demonstration
    invested = 1000000
    withdrawn = 200000
    profit = 500000
    assets = 1500000
    liabilities = 300000
    business_value = assets - liabilities
    share = profit
    roi = (profit / invested * 100) if invested != 0 else 0

    context = {
        'invested': invested,
        'withdrawn': withdrawn,
        'profit': profit,
        'assets': assets,
        'liabilities': liabilities,
        'business_value': business_value,
        'share': share,
        'roi': roi,
    }
    return render(request, 'valuation.html', context)


def investor_reports(request):
    return render(request, 'investor_reports.html')