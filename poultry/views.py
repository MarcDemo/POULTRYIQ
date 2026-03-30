from django.shortcuts import render

# Create your views here.
def dashboard(request):
    return render(request, 'dashboard.html')

def birds(request):
    return render(request, 'birds.html')

def record_feed(request):
    return render(request, 'record_feed.html')

def record_egg(request):
    return render(request, 'record_egg.html')

def record_cleaning(request):
    return render(request, 'record_cleaning.html')

def workersdash(request):
    return render(request, 'workersdash.html')

def supdash(request):
    return render(request, 'supdash.html')

def supapproval(request):
    return render(request, 'supapproval.html')

def login(request):
    return render(request, 'login.html')

def eggrec(request):
    return render(request, 'eggrec.html')

def feedrec(request):
    return render(request, 'feed_rec.html')


def investor(request):
    return render(request, 'investor.html')