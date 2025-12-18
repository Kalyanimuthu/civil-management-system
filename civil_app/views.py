from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from django.contrib import messages

from .models import (
    Site, Team, Department,
    CivilDailyWork, DepartmentWork,
    TeamRate, DefaultRate
)

# =========================================================
# HELPERS
# =========================================================

def to_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def get_team_rate(team, work_date):
    return (
        TeamRate.objects
        .filter(team=team, from_date__lte=work_date)
        .order_by("-is_locked", "-from_date")
        .first()
    )


def calculate_civil_labour(team, mf, mh, hf, hh, work_date):
    rate = get_team_rate(team, work_date)
    if not rate:
        return 0
    return (
        mf * rate.mason_full_rate +
        mh * (rate.mason_full_rate / 2) +
        hf * rate.helper_full_rate +
        hh * (rate.helper_full_rate / 2)
    )

# =========================================================
# DASHBOARD
# =========================================================

from datetime import date, timedelta
from django.db.models import Sum
from django.shortcuts import render
from .models import Site, CivilDailyWork, DepartmentWork


def dashboard(request):
    today = date.today()

    # Week range (Monday → Today)
    week_start = today - timedelta(days=today.weekday())

    sites = Site.objects.all()
    data = []

    for site in sites:

        # ================= TODAY TOTAL =================
        civil_today = (
            CivilDailyWork.objects
            .filter(site=site, date=today)
            .aggregate(total=Sum("labour_amount") + Sum("material_amount"))
            ["total"] or 0
        )

        dept_today = (
            DepartmentWork.objects
            .filter(site=site, date=today)
            .aggregate(total=Sum("labour_amount") + Sum("material_amount"))
            ["total"] or 0
        )

        today_total = civil_today + dept_today

        # ================= WEEK TOTAL =================
        civil_week = (
            CivilDailyWork.objects
            .filter(site=site, date__gte=week_start, date__lte=today)
            .aggregate(total=Sum("labour_amount") + Sum("material_amount"))
            ["total"] or 0
        )

        dept_week = (
            DepartmentWork.objects
            .filter(site=site, date__gte=week_start, date__lte=today)
            .aggregate(total=Sum("labour_amount") + Sum("material_amount"))
            ["total"] or 0
        )

        weekly_total = civil_week + dept_week

        # ================= MONTH TOTAL =================
        civil_month = (
            CivilDailyWork.objects
            .filter(
                site=site,
                date__year=today.year,
                date__month=today.month
            )
            .aggregate(total=Sum("labour_amount") + Sum("material_amount"))
            ["total"] or 0
        )

        dept_month = (
            DepartmentWork.objects
            .filter(
                site=site,
                date__year=today.year,
                date__month=today.month
            )
            .aggregate(total=Sum("labour_amount") + Sum("material_amount"))
            ["total"] or 0
        )

        month_total = civil_month + dept_month

        # ================= STATUS =================
        status = "Done" if today_total > 0 else "Pending"

        data.append({
            "site": site,
            "today_total": today_total,
            "weekly_total": weekly_total,
            "month_total": month_total,
            "status": status,
        })

    return render(request, "dashboard.html", {
        "sites": data
    })


# =========================================================
# SITE MANAGEMENT
# =========================================================

def site_manage(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            Site.objects.create(name=name)
        return redirect("site_manage")

    return render(request, "site_manage.html", {
        "sites": Site.objects.all()
    })


def delete_site(request, id):
    Site.objects.filter(id=id).delete()
    return redirect("site_manage")

# =========================================================
# DAILY ENTRY (SITE DETAIL)
# =========================================================

def site_detail(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    work_date = date.today()

    teams = Team.objects.all()
    departments = Department.objects.exclude(name="Civil")

    # ---------------- SAVE ----------------
    if request.method == "POST":
        work_date = request.POST.get("date") or work_date

        # ---- CIVIL ----
        for team in teams:
            mf = to_int(request.POST.get(f"mason_full_{team.id}"))
            mh = to_int(request.POST.get(f"mason_half_{team.id}"))
            hf = to_int(request.POST.get(f"helper_full_{team.id}"))
            hh = to_int(request.POST.get(f"helper_half_{team.id}"))
            material = to_int(request.POST.get(f"material_{team.id}"))

            labour = calculate_civil_labour(team, mf, mh, hf, hh, work_date)

            CivilDailyWork.objects.update_or_create(
                site=site,
                team=team,
                date=work_date,
                defaults={
                    "mason_full": mf,
                    "mason_half": mh,
                    "helper_full": hf,
                    "helper_half": hh,
                    "labour_amount": labour,
                    "material_amount": material,
                }
            )

        # ---- DEPARTMENTS ----
        for dept in departments:
            full = to_int(request.POST.get(f"dept_full_{dept.id}"))
            half = to_int(request.POST.get(f"dept_half_{dept.id}"))
            material = to_int(request.POST.get(f"dept_material_{dept.id}"))

            rate = DefaultRate.objects.get(department=dept)
            labour = (full * rate.full_day_rate) + (half * rate.half_day_rate)

            DepartmentWork.objects.update_or_create(
                site=site,
                department=dept,
                date=work_date,
                defaults={
                    "full_day_count": full,
                    "half_day_count": half,
                    "full_day_rate": rate.full_day_rate,
                    "half_day_rate": rate.half_day_rate,
                    "labour_amount": labour,
                    "material_amount": material,
                }
            )

        return redirect("site_detail", site_id=site.id)

    # ---------------- DISPLAY ----------------
    civil_qs = CivilDailyWork.objects.filter(site=site, date=work_date)
    civil_map = {c.team_id: c for c in civil_qs}

    civil_rows = []
    for team in teams:
        civil_rows.append({
            "team": team,
            "rate": get_team_rate(team, work_date),
            "work": civil_map.get(team.id),
        })

    return render(request, "site_detail.html", {
        "site": site,
        "civil_rows": civil_rows,
        "other_depts": departments,
        "default_rates": DefaultRate.objects.all(),
        "today": work_date
    })

# =========================================================
# SITE EDIT
# =========================================================

def site_edit(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    work_date = request.GET.get("date") or date.today()

    teams = Team.objects.all()
    departments = Department.objects.exclude(name="Civil")

    civil_qs = CivilDailyWork.objects.filter(site=site, date=work_date)
    dept_qs = DepartmentWork.objects.filter(site=site, date=work_date)

    civil_map = {c.team_id: c for c in civil_qs}
    dept_map = {d.department_id: d for d in dept_qs}

    if request.method == "POST":
        work_date = request.POST.get("date")

        for team in teams:
            mf = to_int(request.POST.get(f"mason_full_{team.id}"))
            mh = to_int(request.POST.get(f"mason_half_{team.id}"))
            hf = to_int(request.POST.get(f"helper_full_{team.id}"))
            hh = to_int(request.POST.get(f"helper_half_{team.id}"))
            material = to_int(request.POST.get(f"material_{team.id}"))

            labour = calculate_civil_labour(team, mf, mh, hf, hh, work_date)

            CivilDailyWork.objects.update_or_create(
                site=site,
                team=team,
                date=work_date,
                defaults={
                    "mason_full": mf,
                    "mason_half": mh,
                    "helper_full": hf,
                    "helper_half": hh,
                    "labour_amount": labour,
                    "material_amount": material,
                }
            )

        for dept in departments:
            full = to_int(request.POST.get(f"dept_full_{dept.id}"))
            half = to_int(request.POST.get(f"dept_half_{dept.id}"))
            material = to_int(request.POST.get(f"dept_material_{dept.id}"))

            rate = DefaultRate.objects.get(department=dept)
            labour = (full * rate.full_day_rate) + (half * rate.half_day_rate)

            DepartmentWork.objects.update_or_create(
                site=site,
                department=dept,
                date=work_date,
                defaults={
                    "full_day_count": full,
                    "half_day_count": half,
                    "full_day_rate": rate.full_day_rate,
                    "half_day_rate": rate.half_day_rate,
                    "labour_amount": labour,
                    "material_amount": material,
                }
            )

        return redirect("site_detail", site_id=site.id)

    return render(request, "site_edit.html", {
        "site": site,
        "date": work_date,
        "teams": teams,
        "departments": departments,
        "civil_map": civil_map,
        "dept_map": dept_map,
    })

# =========================================================
# PAYMENTS
# =========================================================

def default_payment(request):
    if request.method == "POST":
        pay_type = request.POST.get("pay_type")

        # ================= DEPARTMENT PAYMENT =================
        if pay_type == "dept":
            department_id = request.POST.get("department")
            full = to_int(request.POST.get("full"))

            if department_id and full > 0:
                DefaultRate.objects.update_or_create(
                    department_id=department_id,
                    defaults={
                        "full_day_rate": full,   # ✅ ONLY THIS
                    }
                )

        # ================= CIVIL TEAM PAYMENT =================
        if pay_type == "civil":
            team_id = request.POST.get("team")
            mason = to_int(request.POST.get("mason"))
            helper = to_int(request.POST.get("helper"))

            if team_id and mason > 0 and helper > 0:
                TeamRate.objects.update_or_create(
                    team_id=team_id,
                    defaults={
                        "mason_full_rate": mason,
                        "helper_full_rate": helper,
                        "from_date": date.today(),
                        "is_locked": False,
                    }
                )

        return redirect("default_payment")

    return render(request, "default_payment.html", {
        "departments": Department.objects.exclude(name="Civil"),
        "rates": DefaultRate.objects.all(),
        "teams": Team.objects.all(),
        "team_rates": TeamRate.objects.select_related("team").order_by("team__name"),
    })

# =========================================================
# REPORTS
# =========================================================

def date_report(request):
    today = date.today()
    from_date = request.GET.get("from") or today
    to_date = request.GET.get("to") or today

    civil_entries = CivilDailyWork.objects.filter(date__range=[from_date, to_date])
    dept_entries = DepartmentWork.objects.filter(date__range=[from_date, to_date])

    return render(request, "date_report.html", {
        "civil_entries": civil_entries,
        "dept_entries": dept_entries,
        "total_labour": sum(r.labour_amount for r in civil_entries) + sum(r.labour_amount for r in dept_entries),
        "total_material": sum(r.material_amount for r in civil_entries) + sum(r.material_amount for r in dept_entries),
        "grand_total": (
            sum(r.labour_amount for r in civil_entries) +
            sum(r.material_amount for r in civil_entries) +
            sum(r.labour_amount for r in dept_entries) +
            sum(r.material_amount for r in dept_entries)
        ),
        "from": from_date,
        "to": to_date
    })


def detailed_report(request):
    today = date.today()
    from_date = request.GET.get("from") or today
    to_date = request.GET.get("to") or today

    team_report = (
        CivilDailyWork.objects
        .filter(date__range=[from_date, to_date])
        .values("team__name")
        .annotate(
            labour=Sum("labour_amount"),
            material=Sum("material_amount"),
            total=Sum("labour_amount") + Sum("material_amount")
        )
    )

    dept_report = (
        DepartmentWork.objects
        .filter(date__range=[from_date, to_date])
        .values("department__name")
        .annotate(
            labour=Sum("labour_amount"),
            material=Sum("material_amount"),
            total=Sum("labour_amount") + Sum("material_amount")
        )
    )

    return render(request, "detailed_report.html", {
        "team_report": team_report,
        "dept_report": dept_report,
        "from": from_date,
        "to": to_date
    })

# =========================================================
# RESET
# =========================================================

def reset_site_today(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    today = date.today()

    CivilDailyWork.objects.filter(site=site, date=today).delete()
    DepartmentWork.objects.filter(site=site, date=today).delete()

    return redirect("site_detail", site_id=site.id)


def reset_site_month(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    today = date.today()

    CivilDailyWork.objects.filter(
        site=site,
        date__year=today.year,
        date__month=today.month
    ).delete()

    DepartmentWork.objects.filter(
        site=site,
        date__year=today.year,
        date__month=today.month
    ).delete()

    return redirect("site_detail", site_id=site.id)


def reset_site_all(request, site_id):
    site = get_object_or_404(Site, id=site_id)

    CivilDailyWork.objects.filter(site=site).delete()
    DepartmentWork.objects.filter(site=site).delete()

    return redirect("site_detail", site_id=site.id)

def reports(request):
    today = date.today()

    from_date = request.GET.get("from") or today
    to_date = request.GET.get("to") or today

    site_id = request.GET.get("site")
    team_id = request.GET.get("team")
    dept_id = request.GET.get("department")

    sites = Site.objects.all()
    teams = Team.objects.all()
    departments = Department.objects.all()  # ✅ include Civil

    civil_qs = CivilDailyWork.objects.filter(
        date__range=[from_date, to_date]
    ).select_related("site", "team")

    dept_qs = DepartmentWork.objects.filter(
        date__range=[from_date, to_date]
    ).select_related("site", "department")

    # ---------- SITE FILTER ----------
    if site_id:
        civil_qs = civil_qs.filter(site_id=site_id)
        dept_qs = dept_qs.filter(site_id=site_id)

    # ---------- DEPARTMENT FILTER ----------
    if dept_id:
        department = Department.objects.get(id=dept_id)

        if department.name.lower() == "civil":
            dept_qs = DepartmentWork.objects.none()
            civil_qs = civil_qs
        else:
            dept_qs = dept_qs.filter(department_id=dept_id)
            civil_qs = CivilDailyWork.objects.none()

    # ---------- TEAM FILTER ----------
    if team_id:
        civil_qs = civil_qs.filter(team_id=team_id)
        dept_qs = DepartmentWork.objects.none()

    # ---------- TOTALS ----------
    total_labour = (
        sum(r.labour_amount for r in civil_qs) +
        sum(r.labour_amount for r in dept_qs)
    )
    total_material = (
        sum(r.material_amount for r in civil_qs) +
        sum(r.material_amount for r in dept_qs)
    )

    return render(request, "reports.html", {
        "sites": sites,
        "teams": teams,
        "departments": departments,

        "civil_entries": civil_qs,
        "dept_entries": dept_qs,

        "total_labour": total_labour,
        "total_material": total_material,
        "grand_total": total_labour + total_material,

        "from": from_date,
        "to": to_date,
        "selected_site": site_id,
        "selected_team": team_id,
        "selected_department": dept_id,
    })

def masters(request):
    if request.method == "POST":
        form_type = request.POST.get("form_type")
        name = request.POST.get("name", "").strip()

        if name:
            if form_type == "department":
                Department.objects.get_or_create(name=name)

            elif form_type == "team":
                Team.objects.get_or_create(name=name)

        return redirect("masters")  # 🔥 VERY IMPORTANT

    return render(request, "masters.html", {
        "departments": Department.objects.all().order_by("name"),
        "teams": Team.objects.all().order_by("name"),
    })


def delete_team(request, team_id):
    if request.method == "POST":
        team = get_object_or_404(Team, id=team_id)

        if CivilDailyWork.objects.filter(team=team).exists() or \
           TeamRate.objects.filter(team=team).exists():
            messages.error(request, "Team already used. Cannot delete.")
        else:
            team.delete()
            messages.success(request, "Team deleted successfully.")

    return redirect("masters")   # 🔥 ALWAYS back to masters

def delete_department(request, dept_id):
    if request.method == "POST":
        department = get_object_or_404(Department, id=dept_id)

        if DepartmentWork.objects.filter(department=department).exists():
            messages.error(request, "Department already used. Cannot delete.")
        else:
            department.delete()
            messages.success(request, "Department deleted successfully.")

    return redirect("masters")   # 🔥 ALWAYS back to masters
