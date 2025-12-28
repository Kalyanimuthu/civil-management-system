from .utils import render_to_pdf
from collections import defaultdict
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from io import BytesIO
from django.utils.timezone import now
from datetime import date, timedelta, datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from django.contrib import messages

from .models import (
    Site, Team, Department,
    CivilDailyWork, DepartmentWork,
    TeamRate, DefaultRate, CivilAdvance, MaterialEntry
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


def calculate_civil_labour(team, mf, hf, mh, hh, work_date):
    rate = get_team_rate(team, work_date)
    if not rate:
        return 0
    return (
        mf * rate.mason_full_rate +
        hf * rate.helper_full_rate +
        mh * (rate.mason_full_rate / 2) +
        hh * (rate.helper_full_rate / 2)
    )

# =========================================================
# DASHBOARD
# =========================================================

def dashboard(request):
    today = date.today()
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)
    week_end = week_start + timedelta(days=6)

    sites = Site.objects.all()
    data = []

    for site in sites:

        # ================= TODAY =================
        civil_today = CivilDailyWork.objects.filter(
            site=site, date=today
        ).aggregate(labour=Sum("labour_amount"))

        dept_today = DepartmentWork.objects.filter(
            site=site, date=today
        ).aggregate(
            labour=Sum("labour_amount"),
            advance=Sum("advance_amount")
        )

        civil_advance_today = CivilAdvance.objects.filter(
            site=site, date=today
        ).aggregate(total=Sum("amount"))

        material_today = MaterialEntry.objects.filter(
            site=site, date=today
        ).aggregate(total=Sum("total"))

        today_labour = (civil_today["labour"] or 0) + (dept_today["labour"] or 0)
        today_advance = (civil_advance_today["total"] or 0) + (dept_today["advance"] or 0)
        today_material = material_today["total"] or 0

        today_total = today_labour + today_material - today_advance


        # ================= WEEK =================
        civil_week = CivilDailyWork.objects.filter(
            site=site,
            date__range=[week_start, week_end]
        ).aggregate(labour=Sum("labour_amount"))

        dept_week = DepartmentWork.objects.filter(
            site=site,
            date__range=[week_start, week_end]
        ).aggregate(
            labour=Sum("labour_amount"),
            advance=Sum("advance_amount")
        )

        civil_adv_week = CivilAdvance.objects.filter(
            site=site,
            date__range=[week_start, week_end]
        ).aggregate(total=Sum("amount"))

        material_week = MaterialEntry.objects.filter(
            site=site,
            date__range=[week_start, week_end]
        ).aggregate(total=Sum("total"))

        week_labour = (civil_week["labour"] or 0) + (dept_week["labour"] or 0)
        week_advance = (civil_adv_week["total"] or 0) + (dept_week["advance"] or 0)
        week_material = material_week["total"] or 0

        weekly_total = week_labour + week_material - week_advance


        # ================= MONTH =================
        civil_month = CivilDailyWork.objects.filter(
            site=site,
            date__year=today.year,
            date__month=today.month
        ).aggregate(labour=Sum("labour_amount"))

        dept_month = DepartmentWork.objects.filter(
            site=site,
            date__year=today.year,
            date__month=today.month
        ).aggregate(
            labour=Sum("labour_amount"),
            advance=Sum("advance_amount")
        )

        civil_adv_month = CivilAdvance.objects.filter(
            site=site,
            date__year=today.year,
            date__month=today.month
        ).aggregate(total=Sum("amount"))

        material_month = MaterialEntry.objects.filter(
            site=site,
            date__year=today.year,
            date__month=today.month
        ).aggregate(total=Sum("total"))

        month_labour = (civil_month["labour"] or 0) + (dept_month["labour"] or 0)
        month_advance = (civil_adv_month["total"] or 0) + (dept_month["advance"] or 0)
        month_material = material_month["total"] or 0

        month_total = month_labour + month_material - month_advance


        data.append({
            "site": site,
            "today_total": today_total,
            "weekly_total": weekly_total,
            "month_total": month_total,
            "today_advance": today_advance,
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

    # ---------------- DATE ----------------
    raw_date = request.GET.get("date") or request.POST.get("date")
    try:
        work_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except:
        work_date = date.today()

    teams = Team.objects.all()
    departments = Department.objects.exclude(name="Civil")

    # ================= SAVE =================
    if request.method == "POST":

        # ---------- CIVIL ----------

        for team in teams:
            mf = to_int(request.POST.get(f"mason_full_{team.id}"))
            hf = to_int(request.POST.get(f"helper_full_{team.id}"))
            mh = to_int(request.POST.get(f"mason_half_{team.id}"))
            hh = to_int(request.POST.get(f"helper_half_{team.id}"))

            # Get advance safely
            adv_raw = request.POST.get(f"advance_{team.id}")

            existing_advance = CivilAdvance.objects.filter(
                site=site,
                team=team,
                date=work_date
            ).first()

            if adv_raw not in [None, ""]:
                adv = float(adv_raw)

                CivilAdvance.objects.update_or_create(
                    site=site,
                    team=team,
                    date=work_date,
                    defaults={"amount": adv}
                )
            else:
                adv = existing_advance.amount if existing_advance else 0

            # Calculate labour
            labour = calculate_civil_labour(team, mf, hf, mh, hh, work_date)

            # Calculate total
            total = labour - adv

            if mf or hf or mh or hh or adv:
                CivilDailyWork.objects.update_or_create(
                    site=site,
                    team=team,
                    date=work_date,
                    defaults={
                        "mason_full": mf,
                        "helper_full": hf,
                        "mason_half": mh,
                        "helper_half": hh,
                        "total_amount": total,
                        "labour_amount": labour,
                        
                    }
                )
            else:
                CivilDailyWork.objects.filter(
                    site=site, team=team, date=work_date
                ).delete()

        # ---------- DEPARTMENT ----------
        for dept in departments:
            full = to_int(request.POST.get(f"dept_full_{dept.id}"))
            half = to_int(request.POST.get(f"dept_half_{dept.id}"))
            adv_raw = request.POST.get(f"dept_advance_{dept.id}")

            advance = float(adv_raw) if adv_raw not in [None, ""] else 0

            if full or half or advance:
                rate = DefaultRate.objects.get(department=dept)

                labour = (full * rate.full_day_rate) + (half * rate.full_day_rate / 2)
                total = labour - advance

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
                        
                        "total_amount": total,   # ✅ FIX
                    }
                )
            else:
                DepartmentWork.objects.filter(
                    site=site,
                    department=dept,
                    date=work_date
                ).delete()

        # ---------- MATERIAL ----------

        
        MaterialEntry.objects.filter(site=site, date=work_date).delete()

        i = 0
        while True:
            name = request.POST.get(f"material_name_{i}")
            if not name:
                break
            
            qty = float(request.POST.get(f"material_qty_{i}", 0))
            unit = request.POST.get(f"material_unit_{i}", "")
            rate = float(request.POST.get(f"material_rate_{i}", 0))
            agent = request.POST.get(f"agent_name_{i}", "")

            MaterialEntry.objects.create(
                site=site,
                date=work_date,
                name=name,
                agent_name=agent,  # ✅ saved
                quantity=qty,
                unit=unit,
                rate=rate,
                total=qty * rate
            )
            i += 1



    # ================= DISPLAY =================
    civil_qs = CivilDailyWork.objects.filter(site=site, date=work_date)
    civil_map = {c.team_id: c for c in civil_qs}

    advance_qs = CivilAdvance.objects.filter(site=site, date=work_date)
    advance_map = {a.team_id: a.amount for a in advance_qs}

    dept_qs = DepartmentWork.objects.filter(site=site, date=work_date)
    dept_map = {d.department_id: d for d in dept_qs}


    civil_rows = []
    for team in teams:
        rate = get_team_rate(team, work_date) or get_team_rate(team, date.today())

        if not rate:
            continue

        work = civil_map.get(team.id)
        labour = work.labour_amount if work else 0
        advance = advance_map.get(team.id, 0)
        total = work.total_amount if work else 0

        civil_rows.append({
            "team": team,
            "rate": rate,
            "work": work,
            "labour": labour,
            "advance": advance,
            "total": total,
        })

    dept_qs = DepartmentWork.objects.filter(site=site, date=work_date)
    dept_map = {d.department_id: d for d in dept_qs}

    materials = MaterialEntry.objects.filter(site=site, date=work_date)

    default_rate_map = {
        r.department_id: r.full_day_rate
        for r in DefaultRate.objects.all()
    }

    return render(request, "site_detail.html", {
        "site": site,
        "today": work_date,
        "civil_rows": civil_rows,
        "dept_map": dept_map,
        "materials": materials,
        "other_depts": departments,
        "default_rates": default_rate_map,
    })

# =========================================================
# SITE EDIT
# =========================================================

def site_edit(request, site_id):
    site = get_object_or_404(Site, id=site_id)

    # ---------------- DATE ----------------
    raw_date = request.POST.get("date") or request.GET.get("date")
    try:
        work_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except:
        work_date = date.today()

    teams = Team.objects.all()
    departments = Department.objects.exclude(name="Civil")

    # ================= SAVE =================
    if request.method == "POST":

        # -------- CIVIL --------
        for team in teams:
            mf = to_int(request.POST.get(f"mason_full_{team.id}"))
            hf = to_int(request.POST.get(f"helper_full_{team.id}"))
            mh = to_int(request.POST.get(f"mason_half_{team.id}"))
            hh = to_int(request.POST.get(f"helper_half_{team.id}"))
            adv = to_int(request.POST.get(f"advance_{team.id}"))

            # Save advance
            CivilAdvance.objects.update_or_create(
                site=site,
                team=team,
                date=work_date,
                defaults={"amount": adv}
            )

            labour = calculate_civil_labour(team, mf, mh, hf, hh, work_date)

            if mf or hf or mh or hh:
                CivilDailyWork.objects.update_or_create(
                    site=site,
                    team=team,
                    date=work_date,
                    defaults={
                        "mason_full": mf,
                        "helper_full": hf,
                        "mason_half": mh,
                        "helper_half": hh,
                        "labour_amount": labour,
                    }
                )
            else:
                CivilDailyWork.objects.filter(
                    site=site, team=team, date=work_date
                ).delete()

        # -------- DEPARTMENT --------
        for dept in departments:
            full = to_int(request.POST.get(f"dept_full_{dept.id}"))
            half = to_int(request.POST.get(f"dept_half_{dept.id}"))

            if full or half:
                rate = DefaultRate.objects.get(department=dept)
                labour = (full * rate.full_day_rate) + (half * rate.half_day_rate)

                DepartmentWork.objects.update_or_create(
                    site=site,
                    department=dept,
                    date=work_date,
                    defaults={
                        "full_day_count": full,
                        "half_day_count": half,
                        "labour_amount": labour,
                    }
                )
            else:
                DepartmentWork.objects.filter(
                    site=site, department=dept, date=work_date
                ).delete()

        # -------- MATERIAL --------
        MaterialEntry.objects.filter(site=site, date=work_date).delete()

        i = 0
        while True:
            name = request.POST.get(f"material_name_{i}")
            if not name:
                break

            qty = float(request.POST.get(f"material_qty_{i}", 0))
            rate = float(request.POST.get(f"material_rate_{i}", 0))
            unit = request.POST.get(f"material_unit_{i}", "")
            agent = request.POST.get(f"agent_name_{i}", "")

            MaterialEntry.objects.create(
                site=site,
                date=work_date,
                name=name,
                agent_name=agent,
                quantity=qty,
                rate=rate,
                unit=unit,
                total=qty * rate
            )
            i += 1

        # 🔁 stay on same date after save
        return redirect(f"/site/{site.id}/?date={work_date}")

    # ================= DISPLAY =================
    civil_qs = CivilDailyWork.objects.filter(site=site, date=work_date)
    civil_map = {c.team_id: c for c in civil_qs}

    dept_qs = DepartmentWork.objects.filter(site=site, date=work_date)
    dept_map = {d.department_id: d for d in dept_qs}

    materials = MaterialEntry.objects.filter(site=site, date=work_date)
    rate_map = {r.department_id: r for r in DefaultRate.objects.all()}

    return render(request, "site_edit.html", {
        "site": site,
        "date": work_date,
        "teams": teams,
        "departments": departments,
        "civil_map": civil_map,
        "dept_map": dept_map,
        "rate_map": rate_map, 
        "materials": materials,
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
# RESET
# =========================================================

def reset_site_today(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    today = date.today()

    CivilDailyWork.objects.filter(site=site, date=today).delete()
    DepartmentWork.objects.filter(site=site, date=today).delete()
    CivilAdvance.objects.filter(site=site, date=today).delete()
    MaterialEntry.objects.filter(site=site, date=today).delete()

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

    CivilAdvance.objects.filter(
        site=site,
        date__year=today.year,
        date__month=today.month
    ).delete()

    MaterialEntry.objects.filter(
        site=site,
        date__year=today.year,
        date__month=today.month
    ).delete()

    return redirect("site_detail", site_id=site.id)

def reset_site_all(request, site_id):
    site = get_object_or_404(Site, id=site_id)

    CivilDailyWork.objects.filter(site=site).delete()
    DepartmentWork.objects.filter(site=site).delete()
    CivilAdvance.objects.filter(site=site).delete()
    MaterialEntry.objects.filter(site=site).delete()
    
    return redirect("site_detail", site_id=site.id)

def reports(request):
    today = date.today()

    from_date = parse_date(request.GET.get("from_date")) or today
    to_date = parse_date(request.GET.get("to_date")) or today

    site_id = request.GET.get("site")
    team_id = request.GET.get("team")
    dept_id = request.GET.get("department")
    material_only = request.GET.get("material") == "yes"

    sites = Site.objects.all()
    teams = Team.objects.all()
    departments = Department.objects.all()

    rows = []

    total_labour = 0
    total_material = 0
    total_advance = 0

    # ===================== CIVIL =====================
    if not material_only and not dept_id:
        civil_qs = CivilDailyWork.objects.filter(date__range=[from_date, to_date])

        if site_id:
            civil_qs = civil_qs.filter(site_id=site_id)
        if team_id:
            civil_qs = civil_qs.filter(team_id=team_id)

        advance_map = {
            (a.team_id, a.date): a.amount
            for a in CivilAdvance.objects.filter(date__range=[from_date, to_date])
        }

        for r in civil_qs:
            adv = advance_map.get((r.team_id, r.date), 0)
            total = r.labour_amount - adv

            rows.append({
                "date": r.date,
                "site": r.site,
                "department": "Civil",
                "team": r.team.name,
                "labour": r.labour_amount,
                "material": 0,
                "advance": adv,
                "total": total,
            })

            total_labour += r.labour_amount
            total_advance += adv

    # ===================== DEPARTMENT =====================
    if not material_only and not team_id:
        dept_qs = DepartmentWork.objects.filter(date__range=[from_date, to_date])

        if site_id:
            dept_qs = dept_qs.filter(site_id=site_id)
        if dept_id:
            dept_qs = dept_qs.filter(department_id=dept_id)

        for d in dept_qs:
            total = d.labour_amount - (d.advance_amount or 0)

            rows.append({
                "date": d.date,
                "site": d.site,
                "department": d.department.name,
                "team": "-",
                "labour": d.labour_amount,
                "material": 0,
                "advance": d.advance_amount or 0,
                "total": total,
            })

            total_labour += d.labour_amount
            total_advance += d.advance_amount or 0

    # ===================== MATERIAL =====================
    if material_only or (not team_id and not dept_id):
        material_qs = MaterialEntry.objects.filter(date__range=[from_date, to_date])

        if site_id:
            material_qs = material_qs.filter(site_id=site_id)

        for m in material_qs:
            rows.append({
                "date": m.date,
                "site": m.site,
                "department": "Material",
                "team": m.name if hasattr(m, "name") else "-",
                "labour": 0,
                "material": m.total,
                "advance": 0,
                "total": m.total,
            })

            total_material += m.total

    # ================= SORT =================
    rows = sorted(rows, key=lambda x: x["date"], reverse=True)

    grand_total = total_labour + total_material - total_advance

    # ================= SUMMARY =================
    from collections import defaultdict
    team_site_totals = defaultdict(lambda: defaultdict(float))
    dept_site_totals = defaultdict(lambda: defaultdict(float))
    material_site_totals = defaultdict(lambda: defaultdict(float))

    for r in rows:
        site = r["site"].name

        if r["department"] == "Civil":
            team_site_totals[r["team"]][site] += r["total"]

        elif r["department"] == "Material":
            material_site_totals["Material"][site] += r["total"]

        else:
            dept_site_totals[r["department"]][site] += r["total"]

    return render(request, "reports.html", {
        "sites": sites,
        "teams": teams,
        "departments": departments,
        "rows": rows,

        "total_labour": total_labour,
        "total_material": total_material,
        "total_advance": total_advance,
        "grand_total": grand_total,

        "team_site_totals": dict(team_site_totals),
        "dept_site_totals": dict(dept_site_totals),
        "material_site_totals": dict(material_site_totals),

        "from_date": from_date,
        "to_date": to_date,
        "selected_site": site_id,
        "selected_team": team_id,
        "selected_department": dept_id,
        "selected_material": request.GET.get("material"),
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

def parse_date(val):
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except:
        return date.today()

def reset_site_date(request, site_id):
    site = get_object_or_404(Site, id=site_id)

    date_str = request.GET.get("date")
    if not date_str:
        return redirect("site_detail", site_id=site.id)

    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except:
        return redirect("site_detail", site_id=site.id)

    # 🔥 DELETE EVERYTHING FOR THAT DATE
    CivilDailyWork.objects.filter(site=site, date=selected_date).delete()
    DepartmentWork.objects.filter(site=site, date=selected_date).delete()
    MaterialEntry.objects.filter(site=site, date=selected_date).delete()
    CivilAdvance.objects.filter(site=site, date=selected_date).delete()

    return redirect(f"/site/{site.id}/?date={selected_date}")

def report_pdf(request):
    today = date.today()

    from_date = parse_date(request.GET.get("from_date")) or today
    to_date = parse_date(request.GET.get("to_date")) or today

    site_id = request.GET.get("site")
    team_id = request.GET.get("team")
    dept_id = request.GET.get("department")

    rows = []
    total_labour = total_material = total_advance = 0

    # ---------------- CIVIL ----------------
    civil_qs = CivilDailyWork.objects.filter(date__range=[from_date, to_date])
    if site_id:
        civil_qs = civil_qs.filter(site_id=site_id)
    if team_id:
        civil_qs = civil_qs.filter(team_id=team_id)

    advance_map = {
        (a.team_id, a.date): a.amount
        for a in CivilAdvance.objects.filter(date__range=[from_date, to_date])
    }

    for r in civil_qs:
        adv = advance_map.get((r.team_id, r.date), 0)
        total = r.labour_amount - adv

        rows.append({
            "date": r.date,
            "site": r.site.name,
            "department": "Civil",
            "team": r.team.name,
            "labour": r.labour_amount,
            "material": 0,
            "advance": adv,
            "total": total,
        })

        total_labour += r.labour_amount
        total_advance += adv

    # ---------------- DEPARTMENT ----------------
    dept_qs = DepartmentWork.objects.filter(date__range=[from_date, to_date])
    if site_id:
        dept_qs = dept_qs.filter(site_id=site_id)
    if dept_id:
        dept_qs = dept_qs.filter(department_id=dept_id)

    for d in dept_qs:
        rows.append({
            "date": d.date,
            "site": d.site.name,
            "department": d.department.name,
            "team": "-",
            "labour": d.labour_amount,
            "material": 0,
            "advance": d.advance_amount or 0,
            "total": d.labour_amount - (d.advance_amount or 0),
        })

        total_labour += d.labour_amount
        total_advance += d.advance_amount or 0

    # ---------------- MATERIAL ----------------
    material_qs = MaterialEntry.objects.filter(date__range=[from_date, to_date])
    if site_id:
        material_qs = material_qs.filter(site_id=site_id)

    for m in material_qs:
        rows.append({
            "date": m.date,
            "site": m.site.name,
            "department": "Material",
            "team": "-",
            "labour": 0,
            "material": m.total,
            "advance": 0,
            "total": m.total,
        })

        total_material += m.total

    grand_total = total_labour + total_material - total_advance

    context = {
        "rows": rows,
        "from_date": from_date,
        "to_date": to_date,
        "total_labour": total_labour,
        "total_material": total_material,
        "total_advance": total_advance,
        "grand_total": grand_total,
    }

    return render_to_pdf("reports_pdf.html", context)
