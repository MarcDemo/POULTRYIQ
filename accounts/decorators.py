from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


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
