from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import date
from decimal import Decimal, InvalidOperation

from accounts.models import Role, User, validate_house_assignment
from accounts.views import get_post_login_redirect
from accounts.decorators import supervisor_required
from payroll.models import SalaryPayment
from .models import WelfareRequest
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


def _is_manager(user):
    return _role_code(user) in ("MANAGER", "OWNER")


def _worker_salary_summary(user):
    salary_records = SalaryPayment.objects.filter(employee=user).order_by("-period_month", "-recorded_at")
    paid_total = salary_records.filter(status=SalaryPayment.Status.PAID).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    pending_total = salary_records.filter(status=SalaryPayment.Status.PENDING).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return salary_records[:12], paid_total, pending_total


def _scope_welfare_to_supervisor(queryset, supervisor):
    return queryset.filter(worker__houses__in=supervisor.houses.all()).distinct()


def _welfare_self_service_allowed(request):
    if not request.user.is_authenticated:
        return redirect("login")
    role_code = _role_code(request.user)
    if role_code not in ("WORKER", "SUPERVISOR"):
        messages.error(request, "Access denied.")
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


def worker_welfare(request):
    denied = _welfare_self_service_allowed(request)
    if denied:
        return denied
    role_code = _role_code(request.user)

    if request.method == "POST":
        request_type = request.POST.get("request_type", "").strip()
        title = request.POST.get("title", "").strip()
        details = request.POST.get("details", "").strip()
        leave_start_raw = request.POST.get("leave_start", "").strip()
        leave_end_raw = request.POST.get("leave_end", "").strip()
        advance_amount_raw = request.POST.get("advance_amount", "").strip()

        errors = []
        leave_start = None
        leave_end = None
        advance_amount = None

        if request_type not in dict(WelfareRequest.RequestType.choices):
            errors.append("Please select a valid welfare request type.")
        if not title:
            errors.append("Please add a short request title.")
        if not details:
            errors.append("Please describe what you need.")

        if request_type == WelfareRequest.RequestType.LEAVE:
            try:
                leave_start = date.fromisoformat(leave_start_raw)
            except ValueError:
                errors.append("Please provide a valid leave start date.")
            try:
                leave_end = date.fromisoformat(leave_end_raw)
            except ValueError:
                errors.append("Please provide a valid leave end date.")
            if leave_start and leave_end and leave_end < leave_start:
                errors.append("Leave end date cannot be before the start date.")

        if request_type == WelfareRequest.RequestType.SALARY_ADVANCE:
            try:
                advance_amount = Decimal(advance_amount_raw)
                if advance_amount <= 0:
                    errors.append("Salary advance amount must be greater than zero.")
            except (InvalidOperation, ValueError):
                errors.append("Please enter a valid salary advance amount.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            initial_status = (
                WelfareRequest.Status.SUPERVISOR_SUBMITTED
                if role_code == "SUPERVISOR"
                else WelfareRequest.Status.SUBMITTED
            )
            WelfareRequest.objects.create(
                worker=request.user,
                request_type=request_type,
                title=title,
                details=details,
                leave_start=leave_start,
                leave_end=leave_end,
                advance_amount=advance_amount,
                status=initial_status,
            )
            messages.success(
                request,
                "Welfare request sent to the manager." if role_code == "SUPERVISOR" else "Welfare request sent to your supervisor.",
            )
            return redirect("worker_welfare")

    welfare_requests = WelfareRequest.objects.filter(worker=request.user).select_related(
        "supervisor", "manager"
    )[:20]
    salary_records, paid_total, pending_total = _worker_salary_summary(request.user)
    approved_advances = WelfareRequest.objects.filter(
        worker=request.user,
        request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
        status=WelfareRequest.Status.MANAGER_APPROVED,
    ).aggregate(total=Sum("advance_amount"))["total"] or Decimal("0")

    return render(
        request,
        "worker_welfare.html",
        {
            "request_types": WelfareRequest.RequestType.choices,
            "welfare_requests": welfare_requests,
            "salary_records": salary_records,
            "paid_total": paid_total,
            "pending_total": pending_total,
            "approved_advances": approved_advances,
            "today": timezone.localdate(),
            "is_supervisor_requester": role_code == "SUPERVISOR",
        },
    )


@supervisor_required
def supervisor_welfare(request):
    queryset = WelfareRequest.objects.select_related("worker", "supervisor", "manager").filter(
        status=WelfareRequest.Status.SUBMITTED
    )
    if _role_code(request.user) == "SUPERVISOR":
        queryset = _scope_welfare_to_supervisor(queryset, request.user)

    page_obj, querystring = paginate(request, queryset, per_page=20)
    return render(
        request,
        "supervisor_welfare.html",
        {
            "requests": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
        },
    )


@supervisor_required
def supervisor_review_welfare(request, pk):
    if request.method != "POST":
        return redirect("supervisor_welfare")

    queryset = WelfareRequest.objects.filter(status=WelfareRequest.Status.SUBMITTED)
    if _role_code(request.user) == "SUPERVISOR":
        queryset = _scope_welfare_to_supervisor(queryset, request.user)
    welfare_request = get_object_or_404(queryset, pk=pk)

    action = request.POST.get("action", "").strip()
    notes = request.POST.get("notes", "").strip()
    if action == "approve":
        welfare_request.status = WelfareRequest.Status.SUPERVISOR_APPROVED
        message = "Welfare request sent to the manager."
    elif action == "reject":
        welfare_request.status = WelfareRequest.Status.SUPERVISOR_REJECTED
        message = "Welfare request rejected."
    else:
        messages.error(request, "Unknown welfare review action.")
        return redirect("supervisor_welfare")

    welfare_request.supervisor = request.user
    welfare_request.supervisor_notes = notes
    welfare_request.supervisor_reviewed_at = timezone.now()
    welfare_request.save(update_fields=["status", "supervisor", "supervisor_notes", "supervisor_reviewed_at", "updated_at"])
    messages.success(request, message)
    return redirect("supervisor_welfare")


@login_required(login_url="login")
def manager_welfare(request):
    denied = _manager_only(request)
    if denied:
        return denied

    queryset = WelfareRequest.objects.select_related("worker", "supervisor", "manager").filter(
        status__in=[
            WelfareRequest.Status.SUPERVISOR_APPROVED,
            WelfareRequest.Status.SUPERVISOR_SUBMITTED,
        ]
    )
    page_obj, querystring = paginate(request, queryset, per_page=20)
    return render(
        request,
        "manager_welfare.html",
        {
            "requests": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
        },
    )


@login_required(login_url="login")
def manager_review_welfare(request, pk):
    denied = _manager_only(request)
    if denied:
        return denied
    if request.method != "POST":
        return redirect("manager_welfare")

    welfare_request = get_object_or_404(
        WelfareRequest.objects.filter(
            status__in=[
                WelfareRequest.Status.SUPERVISOR_APPROVED,
                WelfareRequest.Status.SUPERVISOR_SUBMITTED,
            ]
        ),
        pk=pk,
    )
    action = request.POST.get("action", "").strip()
    notes = request.POST.get("notes", "").strip()
    if action == "approve":
        welfare_request.status = WelfareRequest.Status.MANAGER_APPROVED
        message = "Welfare request approved."
    elif action == "reject":
        welfare_request.status = WelfareRequest.Status.MANAGER_REJECTED
        message = "Welfare request rejected."
    else:
        messages.error(request, "Unknown welfare review action.")
        return redirect("manager_welfare")

    welfare_request.manager = request.user
    welfare_request.manager_notes = notes
    welfare_request.manager_reviewed_at = timezone.now()
    welfare_request.save(update_fields=["status", "manager", "manager_notes", "manager_reviewed_at", "updated_at"])
    messages.success(request, message)
    return redirect("manager_welfare")


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
