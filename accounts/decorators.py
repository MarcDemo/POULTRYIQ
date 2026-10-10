from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def _redirect_for_role(role_code):
    if role_code == "WORKER":
        return redirect("workersdash")
    if role_code == "SUPERVISOR":
        return redirect("supdash")
    if role_code in ("OWNER", "INVESTOR"):
        return redirect("investor")
    if role_code == "MANAGER":
        return redirect("managerdash")
    return redirect("login")


def roles_required(*allowed_role_codes, message="You do not have permission to access this page."):
    """Require authentication and one of the supplied role codes."""
    allowed = {code.upper() for code in allowed_role_codes}

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")

            role_code = (getattr(request.user.role, "code", "") or "").upper()
            if role_code not in allowed:
                messages.error(request, message)
                return _redirect_for_role(role_code)

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


manager_required = roles_required(
    "MANAGER",
    "OWNER",
    message="Access denied: Managers only.",
)

sales_access_required = roles_required(
    "SUPERVISOR",
    "MANAGER",
    "OWNER",
    message="Access denied: Sales team only.",
)

field_operations_required = roles_required(
    "WORKER",
    "SUPERVISOR",
    message="Access denied: Field operations staff only.",
)


def worker_required(view_func):
    """
    Allows access only to authenticated users whose role code is WORKER.
    Unauthenticated users are sent to login.
    Wrong-role users get an error message and are redirected to their dashboard.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")

        role_code = (getattr(request.user.role, "code", "") or "").upper()
        if role_code != "WORKER":
            messages.error(request, "Access denied: Workers only.")
            if role_code == "SUPERVISOR":
                return redirect("supdash")
            if role_code in ("OWNER", "INVESTOR"):
                return redirect("investor")
            if role_code == "MANAGER":
                return redirect("managerdash")
            return redirect("login")

        return view_func(request, *args, **kwargs)

    return _wrapped


def supervisor_required(view_func):
    """
    Allows access only to authenticated users whose role code is SUPERVISOR.
    Unauthenticated users are sent to login.
    Wrong-role users get an error message and are redirected to their dashboard.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")

        role_code = (getattr(request.user.role, "code", "") or "").upper()
        if role_code not in ("SUPERVISOR", "MANAGER", "OWNER"):
            messages.error(request, "Access denied: Supervisors only.")
            if role_code == "WORKER":
                return redirect("workersdash")
            if role_code in ("OWNER", "INVESTOR"):
                return redirect("investor")
            if role_code == "MANAGER":
                return redirect("managerdash")
            return redirect("login")

        return view_func(request, *args, **kwargs)

    return _wrapped
