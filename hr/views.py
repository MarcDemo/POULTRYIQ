from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
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
from accounting.services import post_salary_advance
from .models import StaffProfile, WelfareRequest
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


def _is_staff_profile_user(user):
    return user.is_active and not user.is_investor


def _staff_queryset():
    return (
        User.objects.select_related("role", "staff_profile")
        .filter(is_active=True)
        .exclude(Q(role__code__in=["OWNER", "INVESTOR"]) | Q(role__name__icontains="investor"))
        .order_by("first_name", "username")
    )


def _ensure_staff_profile(user):
    profile, _ = StaffProfile.objects.get_or_create(user=user)
    return profile


def _profile_form_data(profile, user):
    return {
        "full_name": user.get_full_name().strip() or user.username,
        "phone_number": user.phone_number,
        "email": user.email,
        "age": profile.age or "",
        "job_title": profile.job_title,
        "employee_number": profile.employee_number,
        "tin_number": profile.tin_number,
        "nssf_number": profile.nssf_number,
        "national_id": profile.national_id,
        "next_of_kin_name": profile.next_of_kin_name,
        "next_of_kin_contact": profile.next_of_kin_contact,
        "physical_address": profile.physical_address,
        "emergency_contact": profile.emergency_contact,
        "monthly_salary": profile.monthly_salary,
        "pay_nssf": "True" if profile.pay_nssf else "False",
        "pay_paye": "True" if profile.pay_paye else "False",
        "employment_status": profile.employment_status,
        "hire_date": profile.hire_date.isoformat() if profile.hire_date else "",
        "notes": profile.notes,
    }


def _staff_profile_context(target_user, can_edit=False, form_data=None):
    profile = _ensure_staff_profile(target_user)
    leave_history = WelfareRequest.objects.filter(
        worker=target_user,
        request_type=WelfareRequest.RequestType.LEAVE,
    ).select_related("supervisor", "manager")[:20]
    advance_history = WelfareRequest.objects.filter(
        worker=target_user,
        request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
    ).select_related("supervisor", "manager", "salary_payment")[:20]
    salary_history = SalaryPayment.objects.filter(employee=target_user).select_related("recorded_by")[:20]
    return {
        "staff_user": target_user,
        "profile": profile,
        "can_edit": can_edit,
        "form_data": form_data or _profile_form_data(profile, target_user),
        "leave_history": leave_history,
        "advance_history": advance_history,
        "salary_history": salary_history,
        "employment_status_choices": StaffProfile.EmploymentStatus.choices,
    }


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
    form_data = form_data or {}
    form_data.setdefault("age", "")
    form_data.setdefault("job_title", "")
    form_data.setdefault("employee_number", "")
    form_data.setdefault("tin_number", "")
    form_data.setdefault("nssf_number", "")
    form_data.setdefault("national_id", "")
    form_data.setdefault("next_of_kin_name", "")
    form_data.setdefault("next_of_kin_contact", "")
    form_data.setdefault("physical_address", "")
    form_data.setdefault("emergency_contact", "")
    form_data.setdefault("monthly_salary", "0.00")
    form_data.setdefault("pay_nssf", "True")
    form_data.setdefault("pay_paye", "True")
    form_data.setdefault("employment_status", StaffProfile.EmploymentStatus.ACTIVE)
    form_data.setdefault("hire_date", "")
    form_data.setdefault("notes", "")
    return {
        "form_data": form_data,
        "selected_house_ids": selected_house_ids or [],
        "editing_user": editing_user,
        "roles": _editable_roles(),
        "houses": PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name"),
        "employment_status_choices": StaffProfile.EmploymentStatus.choices,
    }


def _profile_data_from_request(request):
    pay_nssf = request.POST.get("pay_nssf") == "on"
    pay_paye = request.POST.get("pay_paye") == "on"
    return {
        "age": request.POST.get("age", "").strip(),
        "job_title": request.POST.get("job_title", "").strip(),
        "employee_number": request.POST.get("employee_number", "").strip(),
        "tin_number": request.POST.get("tin_number", "").strip(),
        "nssf_number": request.POST.get("nssf_number", "").strip(),
        "national_id": request.POST.get("national_id", "").strip(),
        "next_of_kin_name": request.POST.get("next_of_kin_name", "").strip(),
        "next_of_kin_contact": request.POST.get("next_of_kin_contact", "").strip(),
        "physical_address": request.POST.get("physical_address", "").strip(),
        "emergency_contact": request.POST.get("emergency_contact", "").strip(),
        "monthly_salary": request.POST.get("monthly_salary", "0").strip(),
        "pay_nssf": "True" if pay_nssf else "False",
        "pay_paye": "True" if pay_paye else "False",
        "employment_status": request.POST.get("employment_status", StaffProfile.EmploymentStatus.ACTIVE).strip(),
        "hire_date": request.POST.get("hire_date", "").strip(),
        "notes": request.POST.get("notes", "").strip(),
    }


def _validate_user_profile_data(form_data):
    errors = []
    age = None
    hire_date = None
    monthly_salary = Decimal("0.00")

    try:
        age = int(form_data["age"]) if form_data["age"] else None
        if age is not None and age <= 0:
            errors.append("Age must be greater than zero.")
    except ValueError:
        errors.append("Please enter a valid age.")

    if form_data["hire_date"]:
        try:
            hire_date = date.fromisoformat(form_data["hire_date"])
        except ValueError:
            errors.append("Please enter a valid hire date.")

    try:
        monthly_salary = Decimal(form_data["monthly_salary"] or "0")
        if monthly_salary < 0:
            errors.append("Gross salary cannot be negative.")
    except (InvalidOperation, ValueError):
        errors.append("Please enter a valid gross salary.")

    if form_data["employment_status"] not in dict(StaffProfile.EmploymentStatus.choices):
        errors.append("Please select a valid employment status.")

    return errors, age, hire_date, monthly_salary


def _save_user_profile(user, form_data, age, hire_date, monthly_salary, profile_photo=None):
    profile, _ = StaffProfile.objects.get_or_create(user=user)
    profile.age = age
    profile.job_title = form_data["job_title"]
    profile.employee_number = form_data["employee_number"]
    profile.tin_number = form_data["tin_number"]
    profile.nssf_number = form_data["nssf_number"]
    profile.national_id = form_data["national_id"]
    profile.next_of_kin_name = form_data["next_of_kin_name"]
    profile.next_of_kin_contact = form_data["next_of_kin_contact"]
    profile.physical_address = form_data["physical_address"]
    profile.emergency_contact = form_data["emergency_contact"]
    profile.monthly_salary = monthly_salary
    profile.pay_nssf = form_data["pay_nssf"] != "False"
    profile.pay_paye = form_data["pay_paye"] != "False"
    profile.employment_status = form_data["employment_status"]
    profile.hire_date = hire_date
    profile.notes = form_data["notes"]
    if profile_photo:
        profile.profile_photo = profile_photo
    profile.save()
    return profile


@login_required(login_url="login")
def staff_profiles(request):
    if request.user.is_investor:
        messages.error(request, "Staff profiles are not available to investors.")
        return redirect(get_post_login_redirect(request.user))

    if _is_manager(request.user):
        query = request.GET.get("q", "").strip()
        users = _staff_queryset()
        if query:
            users = users.filter(
                Q(username__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone_number__icontains=query)
                | Q(staff_profile__employee_number__icontains=query)
                | Q(staff_profile__job_title__icontains=query)
            )
        page_obj, querystring = paginate(request, users, per_page=20)
        return render(
            request,
            "staff_profiles.html",
            {
                "staff_users": page_obj,
                "page_obj": page_obj,
                "querystring": querystring,
                "q": query,
                "can_manage": True,
            },
        )

    if not _is_staff_profile_user(request.user):
        messages.error(request, "Staff profile not available.")
        return redirect(get_post_login_redirect(request.user))
    return redirect("staff_profile_detail", pk=request.user.pk)


@login_required(login_url="login")
def staff_profile_detail(request, pk):
    target_user = get_object_or_404(_staff_queryset(), pk=pk)
    if not _is_manager(request.user) and target_user.pk != request.user.pk:
        messages.error(request, "You can only view your own staff profile.")
        return redirect("staff_profile_detail", pk=request.user.pk)

    return render(
        request,
        "staff_profile_detail.html",
        _staff_profile_context(target_user, can_edit=_is_manager(request.user)),
    )


@login_required(login_url="login")
def edit_staff_profile(request, pk):
    denied = _manager_only(request)
    if denied:
        return denied

    target_user = get_object_or_404(_staff_queryset(), pk=pk)
    profile = _ensure_staff_profile(target_user)

    if request.method == "POST":
        full_name = request.POST.get("full_name", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        email = request.POST.get("email", "").strip()
        age_raw = request.POST.get("age", "").strip()
        hire_date_raw = request.POST.get("hire_date", "").strip()
        monthly_salary_raw = request.POST.get("monthly_salary", "0").strip()
        pay_nssf = request.POST.get("pay_nssf") == "on"
        pay_paye = request.POST.get("pay_paye") == "on"
        form_data = {
            "full_name": full_name,
            "phone_number": phone_number,
            "email": email,
            "age": age_raw,
            "job_title": request.POST.get("job_title", "").strip(),
            "employee_number": request.POST.get("employee_number", "").strip(),
            "tin_number": request.POST.get("tin_number", "").strip(),
            "nssf_number": request.POST.get("nssf_number", "").strip(),
            "national_id": request.POST.get("national_id", "").strip(),
            "next_of_kin_name": request.POST.get("next_of_kin_name", "").strip(),
            "next_of_kin_contact": request.POST.get("next_of_kin_contact", "").strip(),
            "physical_address": request.POST.get("physical_address", "").strip(),
            "emergency_contact": request.POST.get("emergency_contact", "").strip(),
            "monthly_salary": monthly_salary_raw,
            "pay_nssf": "True" if pay_nssf else "False",
            "pay_paye": "True" if pay_paye else "False",
            "employment_status": request.POST.get("employment_status", StaffProfile.EmploymentStatus.ACTIVE).strip(),
            "hire_date": hire_date_raw,
            "notes": request.POST.get("notes", "").strip(),
        }
        errors = []
        age = None
        hire_date = None
        monthly_salary = None

        if not full_name:
            errors.append("Full name is required.")
        try:
            age = int(age_raw) if age_raw else None
            if age is not None and age <= 0:
                errors.append("Age must be greater than zero.")
        except ValueError:
            errors.append("Please enter a valid age.")
        if hire_date_raw:
            try:
                hire_date = date.fromisoformat(hire_date_raw)
            except ValueError:
                errors.append("Please enter a valid hire date.")
        try:
            monthly_salary = Decimal(monthly_salary_raw or "0")
            if monthly_salary < 0:
                errors.append("Monthly salary cannot be negative.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid monthly salary.")
        if form_data["employment_status"] not in dict(StaffProfile.EmploymentStatus.choices):
            errors.append("Please select a valid employment status.")

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(
                request,
                "staff_profile_edit.html",
                _staff_profile_context(target_user, can_edit=True, form_data=form_data),
            )

        first_name, last_name = _split_full_name(full_name)
        target_user.first_name = first_name
        target_user.last_name = last_name
        target_user.phone_number = phone_number
        target_user.email = email
        target_user.save(update_fields=["first_name", "last_name", "phone_number", "email"])

        profile.age = age
        profile.job_title = form_data["job_title"]
        profile.employee_number = form_data["employee_number"]
        profile.tin_number = form_data["tin_number"]
        profile.nssf_number = form_data["nssf_number"]
        profile.national_id = form_data["national_id"]
        profile.next_of_kin_name = form_data["next_of_kin_name"]
        profile.next_of_kin_contact = form_data["next_of_kin_contact"]
        profile.physical_address = form_data["physical_address"]
        profile.emergency_contact = form_data["emergency_contact"]
        profile.monthly_salary = monthly_salary
        profile.pay_nssf = pay_nssf
        profile.pay_paye = pay_paye
        profile.employment_status = form_data["employment_status"]
        profile.hire_date = hire_date
        profile.notes = form_data["notes"]
        if request.FILES.get("profile_photo"):
            profile.profile_photo = request.FILES["profile_photo"]
        profile.save()

        messages.success(request, f"Profile updated for {target_user.display_name}.")
        return redirect("staff_profile_detail", pk=target_user.pk)

    return render(
        request,
        "staff_profile_edit.html",
        _staff_profile_context(target_user, can_edit=True),
    )


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
        advance_period_start_raw = request.POST.get("advance_period_start", "").strip()
        advance_period_end_raw = request.POST.get("advance_period_end", "").strip()

        errors = []
        leave_start = None
        leave_end = None
        advance_amount = None
        advance_period_start = None
        advance_period_end = None

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
            try:
                advance_period_start = date.fromisoformat(advance_period_start_raw)
            except ValueError:
                errors.append("Please provide the salary advance period start date.")
            try:
                advance_period_end = date.fromisoformat(advance_period_end_raw)
            except ValueError:
                errors.append("Please provide the salary advance period end date.")
            if advance_period_start and advance_period_end and advance_period_end < advance_period_start:
                errors.append("Salary advance period end date cannot be before the start date.")

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
                advance_period_start=advance_period_start,
                advance_period_end=advance_period_end,
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

    try:
        with transaction.atomic():
            welfare_request.manager = request.user
            welfare_request.manager_notes = notes
            welfare_request.manager_reviewed_at = timezone.now()
            welfare_request.save(update_fields=["status", "manager", "manager_notes", "manager_reviewed_at", "updated_at"])
            if action == "approve" and welfare_request.request_type == WelfareRequest.RequestType.SALARY_ADVANCE:
                post_salary_advance(welfare_request, created_by=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        return redirect("manager_welfare")

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
        profile_data = _profile_data_from_request(request)
        first_name, last_name = _split_full_name(full_name)

        form_data = {
            "username": username,
            "full_name": full_name,
            "email": email,
            "phone_number": phone_number,
            "role": role_id,
            "is_active": "True" if is_active else "False",
            **profile_data,
        }
        errors = []
        role = _editable_roles().filter(pk=role_id).first() if role_id else None
        houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
        selected_houses = list(houses.filter(pk__in=selected_house_ids))
        profile_errors, age, hire_date, monthly_salary = _validate_user_profile_data(form_data)

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
        errors.extend(profile_errors)
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
        _save_user_profile(user, form_data, age, hire_date, monthly_salary, request.FILES.get("profile_photo"))
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
        profile_data = _profile_data_from_request(request)
        first_name, last_name = _split_full_name(full_name)

        form_data = {
            "username": username,
            "full_name": full_name,
            "email": email,
            "phone_number": phone_number,
            "role": role_id,
            "is_active": "True" if is_active else "False",
            **profile_data,
        }
        errors = []
        role = _editable_roles().filter(pk=role_id).first() if role_id else None
        houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
        selected_houses = list(houses.filter(pk__in=selected_house_ids))
        profile_errors, age, hire_date, monthly_salary = _validate_user_profile_data(form_data)

        if not username:
            errors.append("Username is required.")
        elif User.objects.filter(username__iexact=username).exclude(pk=target_user.pk).exists():
            errors.append("Username already exists.")
        if not role:
            errors.append("Please select a valid editable role.")
        errors.extend(profile_errors)
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
        _save_user_profile(target_user, form_data, age, hire_date, monthly_salary, request.FILES.get("profile_photo"))
        messages.success(request, f"User {target_user.display_name} updated.")
        return redirect("manage_users")

    full_name = target_user.get_full_name().strip() or target_user.username
    profile = getattr(target_user, "staff_profile", None)
    form_data = {
        "username": target_user.username,
        "full_name": full_name,
        "email": target_user.email,
        "phone_number": target_user.phone_number,
        "role": str(target_user.role_id or ""),
        "is_active": "True" if target_user.is_active else "False",
        "monthly_salary": profile.monthly_salary if profile else "0.00",
        "pay_nssf": "True" if not profile or profile.pay_nssf else "False",
        "pay_paye": "True" if not profile or profile.pay_paye else "False",
        "age": profile.age if profile and profile.age else "",
        "job_title": profile.job_title if profile else "",
        "employee_number": profile.employee_number if profile else "",
        "tin_number": profile.tin_number if profile else "",
        "nssf_number": profile.nssf_number if profile else "",
        "national_id": profile.national_id if profile else "",
        "next_of_kin_name": profile.next_of_kin_name if profile else "",
        "next_of_kin_contact": profile.next_of_kin_contact if profile else "",
        "physical_address": profile.physical_address if profile else "",
        "emergency_contact": profile.emergency_contact if profile else "",
        "employment_status": profile.employment_status if profile else StaffProfile.EmploymentStatus.ACTIVE,
        "hire_date": profile.hire_date.isoformat() if profile and profile.hire_date else "",
        "notes": profile.notes if profile else "",
    }
    selected_house_ids = [str(pk) for pk in target_user.houses.values_list("pk", flat=True)]
    return render(
        request,
        "add_user.html",
        _user_form_context(form_data, selected_house_ids, editing_user=target_user),
    )
