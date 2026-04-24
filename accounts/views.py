from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError

from poultry.models import PoultryHouse

from .models import Role, User, validate_house_assignment

# Create your views here.


def get_post_login_redirect(user):
    if not user.role:
        return "dashboard"

    role_code = (user.role.code or "").upper()
    role_name = (user.role.name or "").strip().lower()

    if role_code == "WORKER":
        return "workersdash"
    if role_code == "SUPERVISOR":
        return "supdash"
    if role_code == "MANAGER":
        return "dashboard"
    if role_code in {"OWNER", "INVESTOR"} or "investor" in role_name:
        return "investor"

    return "dashboard"


def login_view(request):
    """Handle user login."""
    if request.user.is_authenticated:
        return redirect(get_post_login_redirect(request.user))
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        
        if not username or not password:
            messages.error(request, 'Please provide both username and password.')
            return render(request, 'login.html')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            if user.is_active:
                if user.is_locked:
                    messages.error(request, 'Your account has been locked. Please contact administrator.')
                    return render(request, 'login.html')
                
                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                return redirect(get_post_login_redirect(user))
            else:
                messages.error(request, 'Your account has been disabled.')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'login.html')


def signup_view(request):
    """Handle user registration."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    roles = Role.objects.filter(is_active=True).order_by("name")
    houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
    form_data = {}
    selected_house_ids = []
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        phone_number = request.POST.get('phone_number', '').strip()
        role_id = request.POST.get('role')
        selected_house_ids = request.POST.getlist('houses')

        form_data = {
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
            "role": role_id,
        }
        
        # Validation
        errors = []
        role = None
        selected_houses = list(houses.filter(pk__in=selected_house_ids))
        
        if not all([username, email, first_name, password, password_confirm, role_id]):
            errors.append('All fields are required.')
        
        if len(username) < 4:
            errors.append('Username must be at least 4 characters long.')
        
        if User.objects.filter(username=username).exists():
            errors.append('Username already exists.')
        
        if User.objects.filter(email=email).exists():
            errors.append('Email already exists.')
        
        if len(password) < 8:
            errors.append('Password must be at least 8 characters long.')
        
        if password != password_confirm:
            errors.append('Passwords do not match.')
        
        if not any(char.isupper() for char in password):
            errors.append('Password must contain at least one uppercase letter.')
        
        if not any(char.isdigit() for char in password):
            errors.append('Password must contain at least one digit.')

        if role_id:
            role = Role.objects.filter(id=role_id, is_active=True).first()
            if role is None:
                errors.append('Invalid role selected.')

        if len(selected_houses) != len(set(selected_house_ids)):
            errors.append('Please select valid poultry house assignments.')

        if role is not None:
            try:
                validate_house_assignment(role, selected_houses)
            except ValidationError as exc:
                errors.extend(exc.messages)
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(
                request,
                'signup.html',
                {
                    'roles': roles,
                    'houses': houses,
                    'form_data': form_data,
                    'selected_house_ids': selected_house_ids,
                },
            )
        
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                role=role
            )
            if selected_houses:
                user.houses.set(selected_houses)
            messages.success(request, 'Account created successfully! Please log in.')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return render(
                request,
                'signup.html',
                {
                    'roles': roles,
                    'houses': houses,
                    'form_data': form_data,
                    'selected_house_ids': selected_house_ids,
                },
            )
    
    context = {
        'roles': roles,
        'houses': houses,
        'form_data': form_data,
        'selected_house_ids': selected_house_ids,
    }
    return render(request, 'signup.html', context)


def logout_view(request):
    """Handle user logout."""
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('login')


@login_required(login_url='login')
def reports(request):
    return render(request, 'reports.html')


@login_required(login_url='login')
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


@login_required(login_url='login')
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


@login_required(login_url='login')
def investor_reports(request):
    return render(request, 'investor_reports.html')
