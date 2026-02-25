from django.db.models.functions import Coalesce
from civil_app.utils.pdf import render_to_pdf_weasy
from django.utils import timezone
from django.db import transaction
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.dateparse import parse_date
from collections import defaultdict
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.utils.timezone import now
from datetime import date, timedelta, datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum, Value, DecimalField, CharField
from django.contrib import messages
from .models import (
    Site, Team, Department,
    CivilDailyWork, DepartmentWork,
    TeamRate, DefaultRate, CivilAdvance, MaterialEntry, BillPayment, SiteDailyNote, OtherExpense, Owner, OwnerCashEntry
    )

def staff_required(view_func):
    return user_passes_test(lambda u: u.is_staff, login_url="login")(view_func)

def admin_required(view_func):
    return user_passes_test(lambda u: u.is_superuser, login_url="login")(view_func)



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

# ===== SAFE GET PARAM HELPER =====
def clean_id(val):
    if not val or val in ["None", "null", ""]:
        return None
    return val
# =========================================================
# DASHBOARD
# =========================================================
@login_required
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
        ).aggregate(
            total=Sum("total"),
            advance=Sum("advance"),
        )

        # ================= EXPENSE TODAY =================
        expense_today = OtherExpense.objects.filter(
            site=site,
            date=today
        ).aggregate(total=Sum("amount"))

        today_labour = (civil_today["labour"] or 0) + (dept_today["labour"] or 0)
        today_advance = (civil_advance_today["total"] or 0) + (dept_today["advance"] or 0)
        today_material = material_today["total"] or 0
        material_adv_today = material_today["advance"] or 0
        expense_today_total = expense_today["total"] or 0

        today_total = (
            today_labour
            + today_material
            + expense_today_total
            - (today_advance + material_adv_today)
        )


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
        ).aggregate(
            total=Sum("total"),
            advance=Sum("advance"),
        )

        # ================= EXPENSE WEEK =================
        expense_week = OtherExpense.objects.filter(
            site=site,
            date__range=[week_start, week_end]
        ).aggregate(total=Sum("amount"))

        week_labour = (civil_week["labour"] or 0) + (dept_week["labour"] or 0)
        week_advance = (civil_adv_week["total"] or 0) + (dept_week["advance"] or 0)
        week_material = material_week["total"] or 0
        material_adv_week = material_week["advance"] or 0
        expense_week_total = expense_week["total"] or 0
        weekly_advance = week_advance + material_adv_week

        weekly_total = (
            week_labour
            + week_material
            + expense_week_total
            - (week_advance + material_adv_week)
        )

        data.append({
            "site": site,
            "today_total": today_total,
            "weekly_total": weekly_total,
            "today_advance": today_advance,
            "weekly_advance": weekly_advance,
            "today_expense": expense_today_total,
        })

    return render(request, "dashboard.html", {
        "sites": data
    })

# =========================================================
# SITE MANAGEMENT
# =========================================================
@login_required
@admin_required
def site_manage(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            Site.objects.create(name=name)
        return redirect("site_manage")

    return render(request, "site_manage.html", {
        "sites": Site.objects.all()
    })

@login_required
@staff_required
def delete_site(request, id):
    Site.objects.filter(id=id).delete()
    return redirect("site_manage")

# =========================================================
# DAILY ENTRY (SITE DETAIL)
# =========================================================
@login_required
@staff_required
def site_detail(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    sites = Site.objects.all().order_by("name")

    # ---------------- DATE ----------------
    raw_date = request.GET.get("date") or request.POST.get("date")
    if isinstance(raw_date, str) and raw_date:
        work_date = parse_date(raw_date)
    else:
        work_date = None

    work_date = work_date or date.today()

    teams = Team.objects.all()
    departments = Department.objects.exclude(name="Civil")

    # ================= SAVE =================
    if request.method == "POST":
        desc = request.POST.get("daily_description", "").strip()

        if desc:
            # ✅ create or update
            SiteDailyNote.objects.update_or_create(
                site=site,
                date=work_date,
                defaults={"description": desc}
            )
        else:
            # ✅ delete if user cleared
            SiteDailyNote.objects.filter(
                site=site,
                date=work_date
            ).delete()



        # =================================================
        # ================= CIVIL =========================
        # =================================================
        for team in teams:
            mf = to_int(request.POST.get(f"mason_full_{team.id}"))
            hf = to_int(request.POST.get(f"helper_full_{team.id}"))
            mh = to_int(request.POST.get(f"mason_half_{team.id}"))
            hh = to_int(request.POST.get(f"helper_half_{team.id}"))

            adv_raw = request.POST.get(f"advance_{team.id}")
            adv = float(adv_raw) if adv_raw not in [None, ""] else 0

            # Save advance separately
            if adv_raw not in [None, ""]:
                CivilAdvance.objects.update_or_create(
                    site=site,
                    team=team,
                    date=work_date,
                    defaults={"amount": adv}
                )

            labour = calculate_civil_labour(team, mf, hf, mh, hh, work_date)
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
                        "labour_amount": labour,
                        "total_amount": total,
                    }
                )
            else:
                CivilDailyWork.objects.filter(
                    site=site,
                    team=team,
                    date=work_date
                ).delete()

        # =================================================
        # =============== OTHER DEPARTMENTS ===============
        # =================================================
        for dept in departments:
            full = to_int(request.POST.get(f"dept_full_{dept.id}"))
            half = to_int(request.POST.get(f"dept_half_{dept.id}"))

            adv_raw = request.POST.get(f"dept_advance_{dept.id}")
            adv = float(adv_raw) if adv_raw not in [None, ""] else 0

            rate = DefaultRate.objects.filter(department=dept).first()
            if not rate:
                continue  # safety

            rate_input = request.POST.get(f"dept_rate_{dept.id}")
            try:
                rate_val = float(rate_input) if rate_input else rate.full_day_rate
            except ValueError:
                rate_val = rate.full_day_rate


            # ✅ USE rate_val (not rate.full_day_rate)
            labour = (full * rate_val) + (half * rate_val / 2)
            total = labour - adv

            # 🔥 ALWAYS persist if ANY value exists
            if full or half or adv:
                DepartmentWork.objects.update_or_create(
                    site=site,
                    department=dept,
                    date=work_date,
                    defaults={
                        "full_day_count": full,
                        "half_day_count": half,
                        "full_day_rate": rate_val,
                        "half_day_rate": rate.half_day_rate,
                        "labour_amount": labour,
                        "advance_amount": adv,   # ✅ critical
                        "total_amount": total,
                    }
                )
            else:
                DepartmentWork.objects.filter(
                    site=site,
                    department=dept,
                    date=work_date
                ).delete()

        # =================================================
        # ================= MATERIAL ======================
        # =================================================
        MaterialEntry.objects.filter(site=site, date=work_date).delete()

        i = 0
        while True:
            name = request.POST.get(f"material_name_{i}")
            if not name:
                break

            qty = float(request.POST.get(f"material_qty_{i}", 0))
            rate = float(request.POST.get(f"material_rate_{i}", 0))
            advance = float(request.POST.get(f"material_advance_{i}", 0) or 0)
            unit = request.POST.get(f"material_unit_{i}", "")
            agent = request.POST.get(f"agent_name_{i}", "")

            MaterialEntry.objects.create(
                site=site,
                date=work_date,
                name=name,
                agent_name=agent,
                quantity=qty,
                unit=unit,
                rate=rate,
                advance=advance,
                total=qty * rate,
            )
            i += 1

        # ================= OTHER EXPENSE =================
        
        OtherExpense.objects.filter(site=site, date=work_date).delete()

        i = 0
        while True:
            title = request.POST.get(f"expense_title_{i}")
            if title is None:
                break

            owner_id = request.POST.get(f"expense_owner_{i}")
            amount = request.POST.get(f"expense_amount_{i}") or 0
            notes = request.POST.get(f"expense_notes_{i}") or ""

            # ✅ CONVERT OWNER
            owner_obj = None
            if owner_id:
                try:
                    owner_obj = Owner.objects.get(id=owner_id)
                except Owner.DoesNotExist:
                    owner_obj = None

            # ✅ SAVE
            if title.strip():
                OtherExpense.objects.create(
                    site=site,
                    date=work_date,
                    title=title.strip(),
                    owner=owner_obj,   # ⭐ VERY IMPORTANT
                    amount=float(amount or 0),
                    notes=notes.strip(),
                )

            i += 1
    # ================= DISPLAY =================

    civil_map = {
        c.team_id: c
        for c in CivilDailyWork.objects.filter(site=site, date=work_date)
    }

    advance_map = {
        a.team_id: a.amount
        for a in CivilAdvance.objects.filter(site=site, date=work_date)
    }

    civil_rows = []
    for team in teams:
        rate = get_team_rate(team, work_date)
        if not rate:
            continue

        work = civil_map.get(team.id)

        civil_rows.append({
            "team": team,
            "rate": rate,
            "work": work,
            "labour": work.labour_amount if work else 0,
            "advance": advance_map.get(team.id, 0),
            "total": work.total_amount if work else 0,
        })

    dept_map = {
        d.department_id: d
        for d in DepartmentWork.objects.filter(site=site, date=work_date)
    }

    materials = MaterialEntry.objects.filter(site=site, date=work_date)

    default_rates = {
        r.department_id: r.full_day_rate
        for r in DefaultRate.objects.all()
    }

    note_obj = SiteDailyNote.objects.filter(
        site=site,
        date=work_date
    ).first()

    existing_description = note_obj.description if note_obj else ""
    other_expenses = OtherExpense.objects.filter(
        site=site,
        date=work_date
    ).select_related("owner")
    owners = OwnerCashEntry.objects.select_related("owner").order_by("-date")
    owner_cash_entries = OwnerCashEntry.objects.select_related("owner").order_by("-date")

    return render(request, "site_detail.html", {
        
        "site": site,
        "sites": sites,
        "work_date": work_date,
        "civil_rows": civil_rows,
        "dept_map": dept_map,
        "materials": materials,
        "other_depts": departments,
        "default_rates": default_rates,
        "daily_description": existing_description,
        "other_expenses": other_expenses,
        "owners": owners,
        "owner_cash_entries": owner_cash_entries,
    })


# =========================================================
# RESET
# =========================================================
@login_required
@staff_required
def reset_site_today(request, site_id):
    site = get_object_or_404(Site, id=site_id)
    today = date.today()

    CivilDailyWork.objects.filter(site=site, date=today).delete()
    DepartmentWork.objects.filter(site=site, date=today).delete()
    CivilAdvance.objects.filter(site=site, date=today).delete()
    MaterialEntry.objects.filter(site=site, date=today).delete()
    SiteDailyNote.objects.filter(site=site, date=today).delete()
    OtherExpense.objects.filter(site=site, date=today).delete()

    return redirect("site_detail", site_id=site.id)

@login_required
@staff_required
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

    SiteDailyNote.objects.filter(
        site=site,
        date__year=today.year,
        date__month=today.month
    ).delete()

    OtherExpense.objects.filter(
        site=site,
        date__year=today.year,
        date__month=today.month
    ).delete()

    return redirect("site_detail", site_id=site.id)

@login_required
@staff_required
def reset_site_all(request, site_id):
    site = get_object_or_404(Site, id=site_id)

    CivilDailyWork.objects.filter(site=site).delete()
    DepartmentWork.objects.filter(site=site).delete()
    CivilAdvance.objects.filter(site=site).delete()
    MaterialEntry.objects.filter(site=site).delete()
    SiteDailyNote.objects.filter(site=site).delete()
    OtherExpense.objects.filter(site=site).delete()
    
    return redirect("site_detail", site_id=site.id)


@login_required
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

        
        advance_qs = (
            CivilAdvance.objects
            .filter(date__range=[from_date, to_date])
            .values("team_id", "date")
            .annotate(total_advance=Sum("amount"))
        )
        
        advance_map = {
            (a["site_id"], a["team_id"], a["date"]): a["total_advance"]
            for a in (
                CivilAdvance.objects
                .filter(date__range=[from_date, to_date])
                .values("site_id", "team_id", "date")
                .annotate(total_advance=Sum("amount"))
            )
        }

        for r in civil_qs:
            adv = advance_map.get((r.site_id, r.team_id, r.date), 0)
            total = (r.labour_amount or 0) - (adv or 0)

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
            adv = m.advance or 0
            net = (m.total or 0) - adv

            rows.append({
                "date": m.date,
                "site": m.site,
                "department": "Material",
                "team": m.agent_name or "-",
                "labour": 0,
                "material": m.total,
                "advance": adv,
                "total": net,
            })

            total_material += m.total
            total_advance += adv

    # ===================== EXPENSE =====================
    if not material_only and not team_id and not dept_id:
        expense_qs = OtherExpense.objects.filter(
            date__range=[from_date, to_date]
        )

        if site_id:
            expense_qs = expense_qs.filter(site_id=site_id)

        for e in expense_qs:
            rows.append({
                "date": e.date,
                "site": e.site,
                "department": "Expense",
                "team": e.title or "-",
                "labour": 0,
                "material": 0,
                "advance": 0,
                "total": e.amount or 0,
            })

            # IMPORTANT
            total_material += e.amount or 0

    # ================= SORT =================
    rows = sorted(
        rows,
        key=lambda x: (
            x["date"],
            (x["site"].name if x["site"] else ""),
            (x["department"] or ""),
            (x["team"] or ""),
        )
    )

    grand_total = total_labour + total_material - total_advance

    # ================= SUMMARY =================
    
    team_site_totals = defaultdict(lambda: defaultdict(float))
    dept_site_totals = defaultdict(lambda: defaultdict(float))
    material_site_totals = defaultdict(lambda: defaultdict(float))

    for r in rows:
        site = r["site"].name

        if r["department"] == "Civil":
            team_site_totals[r["team"]][site] += r["total"]

        elif r["department"] == "Material":
            material_site_totals["Material"][site] += r["total"]

        elif r["department"] == "Expense":
            material_site_totals["Expense"][site] += r["total"]

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

@login_required
@admin_required
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

    
    CivilDailyWork.objects.filter(site=site, date=selected_date).delete()
    DepartmentWork.objects.filter(site=site, date=selected_date).delete()
    MaterialEntry.objects.filter(site=site, date=selected_date).delete()
    CivilAdvance.objects.filter(site=site, date=selected_date).delete()
    OtherExpense.objects.filter(site=site, date=selected_date).delete()

    return redirect(f"/site/{site.id}/?date={selected_date}")

def report_pdf(request):
    today = date.today()

    from_date = parse_date(request.GET.get("from_date")) or today
    to_date = parse_date(request.GET.get("to_date")) or today

    site_id = clean_id(request.GET.get("site"))
    team_id = clean_id(request.GET.get("team"))
    dept_id = clean_id(request.GET.get("department"))

    rows = []
    total_labour = total_material = total_advance = 0

    # ---------------- CIVIL ----------------
    civil_qs = CivilDailyWork.objects.filter(date__range=[from_date, to_date])
    if site_id:
        civil_qs = civil_qs.filter(site_id=site_id)
    if team_id:
        civil_qs = civil_qs.filter(team_id=team_id)

    advance_map = {
        (a["site_id"], a["team_id"], a["date"]): a["total_advance"]
        for a in (
            CivilAdvance.objects
            .filter(date__range=[from_date, to_date])
            .values("site_id", "team_id", "date")
            .annotate(total_advance=Sum("amount"))
        )
    }

    for r in civil_qs:
        adv = advance_map.get((r.site_id, r.team_id, r.date), 0)
        labour_amt = r.labour_amount or 0
        total = labour_amt - adv

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

        total_labour += labour_amt
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

        lab = d.labour_amount or 0
        adv = d.advance_amount or 0

    # ---------------- MATERIAL ----------------
    material_qs = MaterialEntry.objects.filter(date__range=[from_date, to_date])
    if site_id:
        material_qs = material_qs.filter(site_id=site_id)

    for m in material_qs:
        adv = m.advance or 0
        net = (m.total or 0) - adv

        rows.append({
            "date": m.date,
            "site": m.site.name,
            "department": "Material",
            "team": m.agent_name or "-",
            "labour": 0,
            "material": m.total,
            "advance": adv,
            "total": net,
        })

        mat_total = m.total or 0
        adv = m.advance or 0
    
    # ---------------- EXPENSE ----------------
    expense_qs = OtherExpense.objects.filter(date__range=[from_date, to_date])

    if site_id:
        expense_qs = expense_qs.filter(site_id=site_id)

    for e in expense_qs:
        amt = e.amount or 0

        rows.append({
            "date": e.date,
            "site": e.site.name,
            "department": "Expense",
            "team": e.title or "-",
            "labour": 0,
            "material": amt,
            "advance": 0,
            "total": amt,
        })

        total_material += amt

    rows = sorted(
        rows,
        key=lambda x: (
            x.get("date"),
            str(x.get("site")),
            str(x.get("department")),
            str(x.get("team")),
        )
    )

    grand_total = total_labour + total_material - total_advance

    context = {
        "rows": rows,
        "from_date": from_date,
        "to_date": to_date,
        "total_labour": total_labour,
        "total_material": total_material,
        "total_advance": total_advance,
        "grand_total": grand_total,
        "now": timezone.now(),
    }

    return render_to_pdf_weasy("reports_pdf.html", context)

@login_required
def team_bill(request, team_id):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    team = get_object_or_404(Team, id=team_id)

    works = CivilDailyWork.objects.filter(
        team=team,
        date__range=[from_date, to_date]
    )

    advances = CivilAdvance.objects.filter(
        team=team,
        date__range=[from_date, to_date]
    )

    rows = []
    total = 0

    for w in works:
        adv = advances.filter(date=w.date).first()
        adv_amt = adv.amount if adv else 0

        amount = w.labour_amount - adv_amt
        total += amount

        rows.append({
            "date": w.date,
            "site": w.site.name,
            "labour": w.labour_amount,
            "advance": adv_amt,
            "total": amount,
        })

    # PDF
    if request.GET.get("pdf"):
        return render_to_pdf_weasy("team_bill_pdf.html", {
            "team": team,
            "rows": rows,
            "from_date": from_date,
            "to_date": to_date,
            "total": total,
        })

    return render(request, "team_bill.html", {
        "team": team,
        "rows": rows,
        "from_date": from_date,
        "to_date": to_date,
        "total": total,
    })

@login_required
def agent_bill(request, agent_name):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    materials = MaterialEntry.objects.filter(
        agent_name=agent_name,
        date__range=[from_date, to_date]
    )

    rows = []
    total = 0

    for m in materials:
        rows.append({
            "date": m.date,
            "site": m.site.name,
            "material": m.name,
            "qty": m.quantity,
            "rate": m.rate,
            "total": m.total,
        })
        total += m.total

    # PDF
    if request.GET.get("pdf"):
        return render_to_pdf_weasy("agent_bill_pdf.html", {
            "agent": agent_name,
            "rows": rows,
            "from_date": from_date,
            "to_date": to_date,
            "total": total,
        })

    return render(request, "agent_bill.html", {
        "agent": agent_name,
        "rows": rows,
        "from_date": from_date,
        "to_date": to_date,
        "total": total,
    })

@login_required
def department_bill(request, department_id):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    department = get_object_or_404(Department, id=department_id)

    works = DepartmentWork.objects.filter(
        department=department,
        date__range=[from_date, to_date]
    )

    rows = []
    total = 0

    for w in works:
        amount = w.total_amount
        total += amount

        rows.append({
            "date": w.date.strftime("%Y-%m-%d"),
            "site": w.site.name,
            "advance": w.advance_amount or 0,
            "total": amount,
        })

    # JSON for modal
    return JsonResponse(rows, safe=False)

@login_required
def all_bills(request):
    from_date = request.GET.get("from_date")
    to_date = request.GET.get("to_date")

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    # =================================================
    # ================= CIVIL =========================
    # =================================================

    civil_totals = (
        CivilDailyWork.objects
        .filter(date__range=[from_date, to_date])
        .values("team_id", "team__name")
        .annotate(
            total_amount=Sum("total_amount"),
        )
    )

    civil_advance = (
        CivilAdvance.objects
        .filter(date__range=[from_date, to_date])
        .values("team_id")
        .annotate(total_advance=Sum("amount"))
    )

    # map advances
    advance_map = {
        a["team_id"]: a["total_advance"]
        for a in civil_advance
    }

    civil_bills = []
    for c in civil_totals:
        civil_bills.append({
            "team__id": c["team_id"],
            "team__name": c["team__name"],
            "total_amount": c["total_amount"] or 0,
            "total_advance": advance_map.get(c["team_id"], 0),
        })

    # =================================================
    # ================= DEPARTMENT ====================
    # =================================================

    dept_bills = (
        DepartmentWork.objects
        .filter(date__range=[from_date, to_date])
        .values("department_id", "department__name")
        .annotate(
            total_amount=Sum("total_amount"),
            total_advance=Sum("advance_amount"),
        )
    )

    # =================================================
    # ================= MATERIAL ======================
    # =================================================

    material_bills = (
        MaterialEntry.objects
        .filter(date__range=[from_date, to_date])
        .values("agent_name")
        .annotate(
            total_amount=Sum("total"),
            total_advance=Sum("advance"),
        )
    )
    
    expense_bills = (
        OtherExpense.objects
        .filter(date__range=[from_date, to_date])
        .values("title")
        .annotate(
            total_amount=Coalesce(
                Sum("amount"),
                Value(0),
                output_field=DecimalField()
            )
        )
        
    )
    # =================================================
    # ================= GRAND TOTAL ===================
    # =================================================

    grand_total = (
        sum(c["total_amount"] for c in civil_bills) +
        sum(d["total_amount"] for d in dept_bills) +
        sum((m["total_amount"] or 0) - (m.get("total_advance") or 0) for m in material_bills)

    )

    return render(request, "all_bills.html", {
        "civil_bills": civil_bills,
        "dept_bills": dept_bills,
        "material_bills": material_bills,
        "expense_bills": expense_bills,
        "from_date": from_date,
        "to_date": to_date,
        "grand_total": grand_total,
    })

@login_required
def all_bills_pdf(request):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    # ================= CIVIL =================
    civil_totals = (
        CivilDailyWork.objects
        .filter(date__range=[from_date, to_date])
        .values("team__name")
        .annotate(
            total_amount=Sum("labour_amount"),
        )
    )

    civil_advances = (
        CivilAdvance.objects
        .filter(date__range=[from_date, to_date])
        .values("team__name")
        .annotate(total_advance=Sum("amount"))
    )

    advance_map = {
        a["team__name"]: a["total_advance"]
        for a in civil_advances
    }

    civil_rows = []
    for c in civil_totals:
        civil_rows.append({
            "name": c["team__name"],
            "advance": advance_map.get(c["team__name"], 0),
            "total": c["total_amount"] or 0,
        })

    # ================= DEPARTMENT =================
    dept_rows = (
        DepartmentWork.objects
        .filter(date__range=[from_date, to_date])
        .values("department__name")
        .annotate(
            total=Sum("total_amount"),
            advance=Sum("advance_amount"),
        )
    )

    # ================= MATERIAL =================
    material_rows = (
        MaterialEntry.objects
        .filter(date__range=[from_date, to_date])
        .values("agent_name")
        .annotate(
            total=Sum("total"),
            advance=Sum("advance"),
        )
    )


    expense_rows = (
        OtherExpense.objects
        .filter(date__range=[from_date, to_date])
        .values("title")
        .annotate(
            owner_name=Coalesce(
                "owner__name",
                Value("—"),
                output_field=CharField()
            ),
            advance=Sum("amount"),
            total=Sum("amount"),
        )
        .order_by("title")
    )

    civil_sum = sum(row["total"] or 0 for row in civil_rows)
    dept_sum = sum((d["total"] or 0) for d in dept_rows)
    material_sum = sum((m["total"] or 0) for m in material_rows)

    total_expense = (
        OtherExpense.objects
        .filter(date__range=[from_date, to_date])
        .aggregate(total=Sum("amount"))["total"] or 0
    )

    grand_total = (
        civil_sum
        + dept_sum
        + material_sum
        + total_expense
    )
    

    return render_to_pdf_weasy("all_bills_pdf.html", {
        "from_date": from_date,
        "to_date": to_date,
        "civil_rows": civil_rows,
        "dept_rows": dept_rows,
        "material_rows": material_rows,
        "expense_rows": expense_rows,
        "grand_total": grand_total,
        "total_expense": total_expense,
        "now": timezone.now(),
    })

def bill_civil_detail(request, team_id):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    rows = CivilDailyWork.objects.filter(
        team_id=team_id,
        date__range=[from_date, to_date]
    ).select_related("site")

    advance_map = {
        a.date: a.amount
        for a in CivilAdvance.objects.filter(
            team_id=team_id,
            date__range=[from_date, to_date]
        )
    }

    data = []
    for r in rows:
        data.append({
            "date": r.date,
            "site": r.site.name,
            "advance": advance_map.get(r.date, 0),
            "total": r.total_amount,
        })

    return JsonResponse(data, safe=False)

def bill_department_detail(request, department_id):
    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    # SAFETY FALLBACK
    if not from_date or not to_date:
        from_date = to_date = date.today()

    department = get_object_or_404(Department, id=department_id)

    works = DepartmentWork.objects.filter(
        department=department,
        date__range=[from_date, to_date]
    )

    rows = []
    for w in works:
        rows.append({
            "date": w.date.strftime("%Y-%m-%d"),
            "site": w.site.name,
            "advance": w.advance_amount or 0,
            "total": w.total_amount,
        })

    return JsonResponse(rows, safe=False)

def bill_material_detail(request, agent_name):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    rows = MaterialEntry.objects.filter(
        agent_name=agent_name,
        date__range=[from_date, to_date]
    ).select_related("site")

    data = [{
        "date": r.date,
        "site": r.site.name,
        "advance": r.advance or 0,
        "total": (r.total or 0) - (r.advance or 0),
    } for r in rows]

    return JsonResponse(data, safe=False)

@login_required
def payment_receipt(request, payment_id):
    payment = get_object_or_404(BillPayment, id=payment_id)
    return render_to_pdf_weasy("receipt_pdf.html", {
        "payment": payment
    })

def api_civil_bill(request, team_id):
    from_date = request.GET.get("from_date") or date.today()
    to_date = request.GET.get("to_date") or date.today()

    qs = CivilDailyWork.objects.filter(
        team_id=team_id,
        date__range=[from_date, to_date]
    ).select_related("site")

    data = []
    for r in qs:
        advance = CivilAdvance.objects.filter(
            team_id=team_id, date=r.date
        ).first()

        adv_amt = advance.amount if advance else 0

        data.append({
            "date": r.date.strftime("%Y-%m-%d"),
            "site": r.site.name,
            "advance": adv_amt,
            "total": r.total_amount,
        })

    return JsonResponse(data, safe=False)


@login_required
@admin_required
def masters_and_payments(request):

    if request.method == "POST":
        action = request.POST.get("action")

        # ================= ADD DEPARTMENT + PAYMENT =================
        if action == "add_department":
            name = request.POST.get("name", "").strip()
            full = to_int(request.POST.get("full"))

            if name and full > 0:
                dept, _ = Department.objects.get_or_create(name=name)
                DefaultRate.objects.update_or_create(
                    department=dept,
                    defaults={"full_day_rate": full}
                )

        # ================= UPDATE DEPARTMENT =================
        elif action == "update_department":
            rate_id = request.POST.get("rate_id")
            full = to_int(request.POST.get("full"))

            if rate_id and full > 0:
                DefaultRate.objects.filter(id=rate_id).update(
                    full_day_rate=full
                )

        # ================= DELETE DEPARTMENT =================
        elif action == "delete_department":
            rate_id = request.POST.get("rate_id")
            DefaultRate.objects.filter(id=rate_id).delete()

        # ================= ADD TEAM + PAYMENT =================
        elif action == "add_team":
            name = request.POST.get("name", "").strip()
            mason = to_int(request.POST.get("mason"))
            helper = to_int(request.POST.get("helper"))

            if name and mason > 0 and helper > 0:
                team, _ = Team.objects.get_or_create(name=name)
                TeamRate.objects.update_or_create(
                    team=team,
                    defaults={
                        "mason_full_rate": mason,
                        "helper_full_rate": helper,
                        "from_date": date.today(),
                        "is_locked": False,
                    }
                )

        # ================= UPDATE TEAM =================
        elif action == "update_team":
            rate_id = request.POST.get("rate_id")
            mason = to_int(request.POST.get("mason"))
            helper = to_int(request.POST.get("helper"))

            if rate_id and mason > 0 and helper > 0:
                TeamRate.objects.filter(id=rate_id).update(
                    mason_full_rate=mason,
                    helper_full_rate=helper
                )

        # ================= DELETE TEAM =================
        elif action == "delete_team":
            rate_id = request.POST.get("rate_id")
            TeamRate.objects.filter(id=rate_id).delete()

        return redirect("masters_and_payments")

    context = {
        "dept_rates": DefaultRate.objects.select_related("department").order_by("department__name"),
        "team_rates": TeamRate.objects.select_related("team").order_by("team__name"),
    }

    return render(request, "masters_and_payments.html", context)

@login_required
def copy_previous_day(request, site_id):
    site = get_object_or_404(Site, id=site_id)

    date_str = request.GET.get("date")
    if not date_str:
        messages.error(request, "Date missing")
        return redirect(f"/site/{site_id}/")

    today = parse_date(date_str)
    prev_date = today - timedelta(days=1)

    # ✅ flags from modal
    copy_civil = request.GET.get("civil") == "1"
    copy_dept = request.GET.get("dept") == "1"
    copy_material = request.GET.get("material") == "1"
    copy_desc = request.GET.get("desc") == "1"
    replace = request.GET.get("replace") == "1"

    with transaction.atomic():

        # ================= CIVIL =================
        if copy_civil:
            prev_rows = CivilDailyWork.objects.filter(
                site=site,
                date=prev_date
            )

            for row in prev_rows:

                if replace:
                    CivilDailyWork.objects.filter(
                        site=site,
                        team=row.team,
                        date=today
                    ).delete()

                CivilDailyWork.objects.update_or_create(
                    site=site,
                    team=row.team,
                    date=today,
                    defaults={
                        "mason_full": row.mason_full,
                        "helper_full": row.helper_full,
                        "mason_half": row.mason_half,
                        "helper_half": row.helper_half,
                        "labour_amount": row.labour_amount,
                        "total_amount": row.total_amount,
                    }
                )

        # ================= DEPARTMENT =================

        if copy_dept:
            prev_rows = DepartmentWork.objects.filter(
                site=site,
                date=prev_date
            )

            for row in prev_rows:

                if replace:
                    DepartmentWork.objects.filter(
                        site=site,
                        department=row.department,
                        date=today
                    ).delete()

                DepartmentWork.objects.update_or_create(
                    site=site,
                    department=row.department,
                    date=today,
                    defaults={
                        "full_day_count": row.full_day_count,
                        "half_day_count": row.half_day_count,
                        "full_day_rate": row.full_day_rate,
                        "half_day_rate": row.half_day_rate,   # ✅ ⭐ CRITICAL FIX
                        "advance_amount": row.advance_amount,
                        "labour_amount": row.labour_amount,
                        "total_amount": row.total_amount,
                    }
                )

        # ================= MATERIAL =================
        if copy_material:
            if replace:
                MaterialEntry.objects.filter(
                    site=site,
                    date=today
                ).delete()

            prev_rows = MaterialEntry.objects.filter(
                site=site,
                date=prev_date
            )

            for m in prev_rows:
                MaterialEntry.objects.create(
                    site=site,
                    date=today,
                    agent_name=m.agent_name,
                    name=m.name,
                    quantity=m.quantity,
                    unit=m.unit,
                    rate=m.rate,
                    advance=m.advance,
                    total=m.total,
                )

        # ================= DESCRIPTION =================
        if copy_desc:
            prev_desc = DailyNote.objects.filter(
                site=site,
                date=prev_date
            ).first()

            if prev_desc:
                DailyNote.objects.update_or_create(
                    site=site,
                    date=today,
                    defaults={"description": prev_desc.description}
                )

    messages.success(request, "✅ Previous day copied successfully")
    return redirect(f"/site/{site_id}/?date={today}")

@login_required
def owner_cash_list(request):
    owners = Owner.objects.all()

    summary = []

    for owner in owners:
        total_in = OwnerCashEntry.objects.filter(owner=owner).aggregate(
            s=Sum("amount")
        )["s"] or 0

        total_out = OtherExpense.objects.filter(
            owner__owner=owner
        ).aggregate(
            s=Sum("amount")
        )["s"] or 0

        balance = total_in - total_out

        summary.append({
            "owner": owner,
            "total_in": total_in,
            "total_out": total_out,
            "balance": balance,
        })

    entries = OwnerCashEntry.objects.select_related("owner").order_by("-date")

    return render(request, "owner_cash_list.html", {
        "summary": summary,
        "entries": entries,
    })

@login_required
def owner_cash_add(request):
    owners = Owner.objects.all()

    if request.method == "POST":
        OwnerCashEntry.objects.create(
            owner_id=request.POST.get("owner"),
            date=request.POST.get("date"),
            amount=request.POST.get("amount"),
            notes=request.POST.get("notes", "")
        )
        return redirect("owner_cash_list")

    return render(request, "owner_cash_add.html", {
        "owners": owners,
        "today": date.today(),
    })


@login_required
def api_bill_expense(request, name):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

    qs = (
        OtherExpense.objects
        .filter(
            title=name,
            date__range=[from_date, to_date]
        )
        .select_related("site", "owner")
        .order_by("date")
    )

    data = []

    for r in qs:
        data.append({
            "date": r.date.strftime("%Y-%m-%d"),

            "site": r.site.name if r.site else "-",            
            "owner": r.owner.name if r.owner else "-",

            "advance": 0,
            "total": float(r.amount or 0),
        })

    return JsonResponse(data, safe=False)

