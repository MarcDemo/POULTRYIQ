from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Role, User, validate_house_assignment
from accounts.views import get_post_login_redirect
from poultry.models import PoultryHouse
from poultryiq.pagination import paginate

# Create your views here.
def _role_code(user) -> str:
    return (getattr(user.role, "code", "") or "").upper()


def _manager_only(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if _role_code(request.user) != "MANAGER":
        messages.error(request, "Access denied: Managers only.")
        return redirect(get_post_login_redirect(request.user))
    return None


def _can_manager_reset_password(target_user):
    role_code = _role_code(target_user)
    return role_code != "MANAGER" and not target_user.is_investor


def _can_manager_edit_user(target_user):
    return _can_manager_reset_password(target_user)


def _split_full_name(full_name):
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _editable_roles():
    return (
        Role.objects.filter(is_active=True)
        .exclude(Q(code__in=["MANAGER", "OWNER", "INVESTOR"]) | Q(name__icontains="investor"))
        .order_by("name")
    )


def _user_form_context(form_data=None, selected_house_ids=None, editing_user=None):
    return {
        "form_data": form_data or {},
        "selected_house_ids": selected_house_ids or [],
        "editing_user": editing_user,
        "roles": _editable_roles(),
        "houses": PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name"),
    }


@login_required(login_url="login")
def manage_users(request):
    denied = _manager_only(request)
    if denied:
        return denied

    query = request.GET.get("q", "").strip()
    users = User.objects.select_related("role").order_by("first_name", "username")
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone_number__icontains=query)
            | Q(role__name__icontains=query)
            | Q(role__code__icontains=query)
        )

    page_obj, querystring = paginate(request, users, per_page=20)
    for user in page_obj.object_list:
        user.can_manager_reset_password = _can_manager_reset_password(user)
        user.can_manager_edit_user = _can_manager_edit_user(user)

    return render(request, 'manage_users.html', {
        "users": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "q": query,
    })


@login_required(login_url="login")
def reset_user_password(request, pk):
    denied = _manager_only(request)
    if denied:
        return denied
    if request.method != "POST":
        return redirect("manage_users")

    target_user = get_object_or_404(User.objects.select_related("role"), pk=pk)
    if not _can_manager_reset_password(target_user):
        messages.error(request, "Managers cannot reset passwords for managers or investors.")
        return redirect("manage_users")

    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")

    if new_password != confirm_password:
        messages.error(request, f"Passwords do not match for {target_user.display_name}.")
        return redirect("manage_users")

    try:
        validate_password(new_password, user=target_user)
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
        return redirect("manage_users")

    target_user.set_password(new_password)
    target_user.save(update_fields=["password"])
    messages.success(request, f"Password reset for {target_user.display_name}.")
    return redirect("manage_users")


@login_required(login_url="login")
def add_user(request):
    denied = _manager_only(request)
    if denied:
        return denied

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")
        role_id = request.POST.get("role", "").strip()
        is_active = request.POST.get("is_active", "True") == "True"
        selected_house_ids = request.POST.getlist("houses")
        first_name, last_name = _split_full_name(full_name)

        form_data = {
            "username": username,
            "full_name": full_name,
            "email": email,
            "phone_number": phone_number,
            "role": role_id,
            "is_active": "True" if is_active else "False",
        }
        errors = []
        role = _editable_roles().filter(pk=role_id).first() if role_id else None
        houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
        selected_houses = list(houses.filter(pk__in=selected_house_ids))

        if not username:
            errors.append("Username is required.")
        elif User.objects.filter(username__iexact=username).exists():
            errors.append("Username already exists.")
        if not role:
            errors.append("Please select a valid editable role.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        try:
            validate_password(password)
        except ValidationError as exc:
            errors.extend(exc.messages)
        if len(selected_houses) != len(set(selected_house_ids)):
            errors.append("Please select valid poultry houses.")
        if role:
            try:
                validate_house_assignment(role, selected_houses)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, "add_user.html", _user_form_context(form_data, selected_house_ids))

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            role=role,
            is_active=is_active,
        )
        user.houses.set(selected_houses)
        messages.success(request, f"User {user.display_name} created.")
        return redirect("manage_users")

    return render(request, 'add_user.html', _user_form_context())


@login_required(login_url="login")
def edit_user(request, pk):
    denied = _manager_only(request)
    if denied:
        return denied

    target_user = get_object_or_404(User.objects.select_related("role").prefetch_related("houses"), pk=pk)
    if not _can_manager_edit_user(target_user):
        messages.error(request, "Managers cannot edit manager or investor accounts.")
        return redirect("manage_users")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        role_id = request.POST.get("role", "").strip()
        is_active = request.POST.get("is_active", "True") == "True"
        selected_house_ids = request.POST.getlist("houses")
        first_name, last_name = _split_full_name(full_name)

        form_data = {
            "username": username,
            "full_name": full_name,
            "email": email,
            "phone_number": phone_number,
            "role": role_id,
            "is_active": "True" if is_active else "False",
        }
        errors = []
        role = _editable_roles().filter(pk=role_id).first() if role_id else None
        houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
        selected_houses = list(houses.filter(pk__in=selected_house_ids))

        if not username:
            errors.append("Username is required.")
        elif User.objects.filter(username__iexact=username).exclude(pk=target_user.pk).exists():
            errors.append("Username already exists.")
        if not role:
            errors.append("Please select a valid editable role.")
        if len(selected_houses) != len(set(selected_house_ids)):
            errors.append("Please select valid poultry houses.")
        if role:
            try:
                validate_house_assignment(role, selected_houses)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(
                request,
                "add_user.html",
                _user_form_context(form_data, selected_house_ids, editing_user=target_user),
            )

        target_user.username = username
        target_user.first_name = first_name
        target_user.last_name = last_name
        target_user.email = email
        target_user.phone_number = phone_number
        target_user.role = role
        target_user.is_active = is_active
        target_user.save(update_fields=["username", "first_name", "last_name", "email", "phone_number", "role", "is_active"])
        target_user.houses.set(selected_houses)
        messages.success(request, f"User {target_user.display_name} updated.")
        return redirect("manage_users")

    full_name = target_user.get_full_name().strip() or target_user.username
    form_data = {
        "username": target_user.username,
        "full_name": full_name,
        "email": target_user.email,
        "phone_number": target_user.phone_number,
        "role": str(target_user.role_id or ""),
        "is_active": "True" if target_user.is_active else "False",
    }
    selected_house_ids = [str(pk) for pk in target_user.houses.values_list("pk", flat=True)]
    return render(
        request,
        "add_user.html",
        _user_form_context(form_data, selected_house_ids, editing_user=target_user),
    )
