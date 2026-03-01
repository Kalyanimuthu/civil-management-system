from django.template.loader import render_to_string
from weasyprint import HTML
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
from django.db.models import Sum, Value, DecimalField, CharField, FloatField
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
def all_bills(request):
    from_date = parse_date(request.GET.get("from_date"))
    to_date = parse_date(request.GET.get("to_date"))

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

    # =================================================
    # ================= CIVIL (TEAM → SITE) ===========
    # =================================================

    teams = (
        CivilDailyWork.objects
        .filter(date__range=[from_date, to_date])
        .values("team_id", "team__name")
        .distinct()
    )

    civil_rows = []

    for t in teams:
        team_id = t["team_id"]

        # ---- SITE WORK ----
        site_qs = (
            CivilDailyWork.objects
            .filter(team_id=team_id, date__range=[from_date, to_date])
            .values("site_id", "site__name")
            .annotate(
                total=Coalesce(
                    Sum("total_amount"),
                    Value(0),
                    output_field=FloatField()
                )
            )
            .order_by("site__name")
        )

        # ---- SITE ADVANCE MAP (🔥 FIXED) ----
        adv_qs = (
            CivilAdvance.objects
            .filter(team_id=team_id, date__range=[from_date, to_date])
            .values("site_id")
            .annotate(
                advance=Coalesce(
                    Sum("amount"),
                    Value(0),
                    output_field=FloatField()
                )
            )
        )

        advance_map = {
            a["site_id"]: a["advance"]
            for a in adv_qs
        }

        sites = []
        team_total = 0
        team_adv_total = 0

        for s in site_qs:
            site_id = s["site_id"]

            adv = advance_map.get(site_id, 0)
            tot = s["total"] or 0

            team_total += tot
            team_adv_total += adv

            sites.append({
                "site": s["site__name"],
                "advance": adv,  # ✅ NOW WORKS
                "total": tot,
            })

        civil_rows.append({
            "name": t["team__name"],
            "advance": team_adv_total,
            "total": team_total,
            "sites": sites,
        })

    # =================================================
    # ================= DEPARTMENT ====================
    # =================================================
    departments = (
        DepartmentWork.objects
        .filter(date__range=[from_date, to_date])
        .values("department_id", "department__name")
        .distinct()
    )

    dept_rows = []

    for d in departments:
        dept_id = d["department_id"]

        site_qs = (
            DepartmentWork.objects
            .filter(department_id=dept_id, date__range=[from_date, to_date])
            .values("site_id", "site__name")
            .annotate(
                advance=Coalesce(Sum("advance_amount"), Value(0), output_field=FloatField()),
                total=Coalesce(Sum("total_amount"), Value(0), output_field=FloatField()),
            )
            .order_by("site__name")
        )

        sites = []
        adv_total = 0
        amt_total = 0

        for s in site_qs:
            adv_total += s["advance"]
            amt_total += s["total"]

            sites.append({
                "site": s["site__name"],
                "advance": s["advance"],
                "total": s["total"],
            })

        dept_rows.append({
            "name": d["department__name"],
            "advance": adv_total,
            "total": amt_total,
            "sites": sites,
        })

    # =================================================
    # ================= MATERIAL ======================
    # =================================================
    agents = (
        MaterialEntry.objects
        .filter(date__range=[from_date, to_date])
        .values("agent_name")
        .distinct()
    )

    material_rows = []

    for a in agents:
        name = a["agent_name"]

        site_qs = (
            MaterialEntry.objects
            .filter(agent_name=name, date__range=[from_date, to_date])
            .values("site_id", "site__name")
            .annotate(
                advance=Coalesce(Sum("advance"), Value(0), output_field=FloatField()),
                total_raw=Coalesce(Sum("total"), Value(0), output_field=FloatField()),
            )
            .order_by("site__name")
        )

        sites = []
        adv_total = 0
        amt_total = 0

        for s in site_qs:
            payable = (s["total_raw"] or 0) - (s["advance"] or 0)

            adv_total += s["advance"]
            amt_total += payable

            sites.append({
                "site": s["site__name"],
                "advance": s["advance"],
                "total": payable,
            })

        material_rows.append({
            "name": name,
            "advance": adv_total,
            "total": amt_total,
            "sites": sites,
        })

    # =================================================
    # ================= EXPENSE =======================
    # =================================================

    expenses = (
        OtherExpense.objects
        .filter(date__range=[from_date, to_date])
        .values("title")
        .distinct()
    )

    expense_rows = []

    for e in expenses:
        title = e["title"]

        site_qs = (
            OtherExpense.objects
            .filter(title=title, date__range=[from_date, to_date])
            .values("site_id", "site__name", "owner__name")
            .annotate(
                total=Coalesce(Sum("amount"), Value(0), output_field=FloatField())
            )
            .order_by("site__name")
        )

        sites = []
        amt_total = 0

        for s in site_qs:
            amt_total += s["total"]

            sites.append({
                "site": s["site__name"] or "-",
                "owner": s["owner__name"] or "-",
                "advance": 0,
                "total": s["total"],
            })

        expense_rows.append({
            "name": title,
            "advance": 0,
            "total": amt_total,
            "sites": sites,
        })

    # =================================================
    # ================= GRAND TOTAL ===================
    # =================================================

    civil_sum = sum(row["total"] for row in civil_rows)
    dept_sum = sum(d["total"] for d in dept_rows)
    material_sum = sum(m["total"] for m in material_rows)
    expense_sum = sum(e["total"] for e in expense_rows)

    grand_total = civil_sum + dept_sum + material_sum + expense_sum

    return render_to_pdf_weasy(
        "all_bills_pdf.html",
        {
            "from_date": from_date,
            "to_date": to_date,
            "civil_rows": civil_rows,
            "dept_rows": dept_rows,
            "material_rows": material_rows,
            "expense_rows": expense_rows,
            "grand_total": grand_total,
            "now": timezone.now(),
        },
    )


@login_required
def bill_civil_detail(request, team_id):
    from django.db.models import Sum, Value, FloatField
    from django.db.models.functions import Coalesce

    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    # ================= WORK =================
    work_qs = (
        CivilDailyWork.objects
        .filter(team_id=team_id, date__range=[from_date, to_date])
        .values("site_id", "site__name")
        .annotate(
            total=Coalesce(
                Sum("total_amount"),
                Value(0),
                output_field=FloatField()
            )
        )
    )

    # ================= ADVANCE (SITE WISE) =================
    adv_qs = (
        CivilAdvance.objects
        .filter(team_id=team_id, date__range=[from_date, to_date])
        .values("site_id")
        .annotate(
            advance=Coalesce(
                Sum("amount"),
                Value(0),
                output_field=FloatField()
            )
        )
    )

    adv_map = {a["site_id"]: a["advance"] for a in adv_qs}

    rows = []
    total_amt = 0
    total_adv = 0

    for w in work_qs:
        adv = adv_map.get(w["site_id"], 0)

        total_amt += w["total"]
        total_adv += adv

        rows.append({
            "site__name": w["site__name"],
            "advance": adv,
            "total": w["total"],
        })

    return JsonResponse({
        "rows": rows,
        "team_total": {
            "advance_total": total_adv,
            "grand_total": total_amt,
        }
    })

@login_required
def bill_department_detail(request, department_id):

    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    department = get_object_or_404(Department, id=department_id)

    # =============================
    # SITE-WISE GROUPING
    # =============================
    qs = (
        DepartmentWork.objects
        .filter(department=department, date__range=[from_date, to_date])
        .values("site_id", "site__name")
        .annotate(
            advance=Coalesce(
                Sum("advance_amount"),
                Value(0),
                output_field=FloatField()
            ),
            total=Coalesce(
                Sum("total_amount"),
                Value(0),
                output_field=FloatField()
            ),
        )
        .order_by("site__name")
    )

    rows = []
    total_adv = 0
    total_amt = 0

    for r in qs:
        adv = r["advance"] or 0
        tot = r["total"] or 0

        total_adv += adv
        total_amt += tot

        rows.append({
            "site__name": r["site__name"],
            "advance": r["advance"],
            "total": r["total"],
        })

    return JsonResponse({
        "rows": rows,
        "team_total": {  # keep same key for JS reuse
            "advance_total": total_adv,
            "grand_total": total_amt,
        }
    })

@login_required
def bill_material_detail(request, agent_name):

    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    # =============================
    # SITE-WISE GROUPING
    # =============================
    qs = (
        MaterialEntry.objects
        .filter(agent_name=agent_name, date__range=[from_date, to_date])
        .values("site_id", "site__name")
        .annotate(
            advance=Coalesce(
                Sum("advance"),
                Value(0),
                output_field=FloatField()
            ),
            total_raw=Coalesce(
                Sum("total"),
                Value(0),
                output_field=FloatField()
            ),
        )
        .order_by("site__name")
    )

    rows = []
    total_adv = 0
    total_amt = 0

    for r in qs:
        adv = r["advance"] or 0
        raw = r["total_raw"] or 0
        payable = raw - adv

        total_adv += r["advance"]
        total_amt += payable

        rows.append({
            "site__name": r["site__name"],
            "advance": r["advance"],
            "total": payable,  # 👈 payable shown in UI
        })

    return JsonResponse({
        "rows": rows,
        "team_total": {
            "advance_total": total_adv,
            "grand_total": total_amt,
        }
    })

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
            owner=owner
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
    from django.db.models import Sum, Value, FloatField
    from django.db.models.functions import Coalesce

    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    # =============================
    # SITE + OWNER GROUPING
    # =============================
    qs = (
        OtherExpense.objects
        .filter(title=name, date__range=[from_date, to_date])
        .values(
            "site_id",
            "site__name",
            "owner__name",   # ✅ owner optional
        )
        .annotate(
            total=Coalesce(
                Sum("amount"),
                Value(0),
                output_field=FloatField()
            )
        )
        .order_by("site__name")
    )

    rows = []
    total_amt = 0

    for r in qs:
        total_amt += r["total"]

        rows.append({
            "site__name": r["site__name"] or "-",
            "site__owner__name": r.get("owner__name") or "-",  # ✅ safe
            "advance": 0,
            "total": r["total"],
        })

    return JsonResponse({
        "rows": rows,
        "team_total": {
            "advance_total": 0,
            "grand_total": total_amt,
        }
    })


@login_required
def bill_civil_pdf(request, team_id):
    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    team = get_object_or_404(Team, id=team_id)

    # ================= SITE WISE =================
    work_qs = (
        CivilDailyWork.objects
        .filter(team_id=team_id, date__range=[from_date, to_date])
        .values("site_id", "site__name")
        .annotate(
            total=Coalesce(
                Sum("total_amount"),
                Value(0),
                output_field=FloatField()
            )
        )
        .order_by("site__name")
    )

    # ================= ADVANCE MAP (SAFE) =================
    adv_qs = (
        CivilAdvance.objects
        .filter(team_id=team_id, date__range=[from_date, to_date])
        .values("site_id")  # ⚠️ works only if site exists
        .annotate(
            advance=Coalesce(
                Sum("amount"),
                Value(0),
                output_field=FloatField()
            )
        )
    )

    adv_map = {a["site_id"]: a["advance"] for a in adv_qs}

    rows = []
    grand_total = 0
    advance_total = 0

    for w in work_qs:
        adv = adv_map.get(w["site_id"], 0)

        grand_total += w["total"]
        advance_total += adv

        rows.append({
            "site": w["site__name"],
            "advance": adv,
            "total": w["total"],
        })

    advance_total = (
        CivilAdvance.objects
        .filter(team_id=team_id, date__range=[from_date, to_date])
        .aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(0),
                output_field=FloatField()
            )
        )["total"]
    )

    html = render_to_string(
        "civil_team_pdf.html",
        {
            "team": team,
            "rows": rows,
            "advance_total": advance_total,
            "grand_total": grand_total,
            "from_date": from_date,
            "to_date": to_date,
        },
    )

    pdf = HTML(string=html).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="team_{team_id}_bill.pdf"'
    )
    return response


@login_required
def bill_department_pdf(request, department_id):
    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    department = get_object_or_404(Department, id=department_id)

    # ================= SITE-WISE =================
    qs = (
        DepartmentWork.objects
        .filter(department=department, date__range=[from_date, to_date])
        .values("site__name")
        .annotate(
            advance=Coalesce(
                Sum("advance_amount"),
                Value(0),
                output_field=FloatField()
            ),
            total=Coalesce(
                Sum("total_amount"),
                Value(0),
                output_field=FloatField()
            ),
        )
        .order_by("site__name")
    )

    rows = []
    total_adv = 0
    total_amt = 0

    for r in qs:
        adv = r["advance"] or 0
        tot = r["total"] or 0

        total_adv += adv
        total_amt += tot

        rows.append({
            "site": r["site__name"],
            "advance": adv,
            "total": tot,
        })

    html = render_to_string(
        "civil_team_pdf.html",  # ✅ reuse same premium template
        {
            "team": department,  # template expects .name
            "rows": rows,
            "advance_total": total_adv,
            "grand_total": total_amt,
            "from_date": from_date,
            "to_date": to_date,
            "now": timezone.now(),
        },
    )

    pdf = HTML(string=html).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="department_{department_id}_bill.pdf"'
    )
    return response

@login_required
def bill_material_pdf(request, agent_name):
    from django.template.loader import render_to_string
    from weasyprint import HTML
    from django.db.models import Sum, Value, FloatField
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    qs = (
        MaterialEntry.objects
        .filter(agent_name=agent_name, date__range=[from_date, to_date])
        .values("site__name")
        .annotate(
            advance=Coalesce(
                Sum("advance"),
                Value(0),
                output_field=FloatField()
            ),
            total_raw=Coalesce(
                Sum("total"),
                Value(0),
                output_field=FloatField()
            ),
        )
        .order_by("site__name")
    )

    rows = []
    total_adv = 0
    total_amt = 0

    for r in qs:
        adv = r["advance"] or 0
        raw = r["total_raw"] or 0
        payable = raw - adv

        total_adv += adv
        total_amt += payable

        rows.append({
            "site": r["site__name"],
            "advance": adv,
            "total": payable,
        })

    html = render_to_string(
        "civil_team_pdf.html",
        {
            "team": type("obj", (), {"name": agent_name})(),  # simple object
            "rows": rows,
            "advance_total": total_adv,
            "grand_total": total_amt,
            "from_date": from_date,
            "to_date": to_date,
            "now": timezone.now(),
        },
    )

    pdf = HTML(string=html).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="material_{agent_name}_bill.pdf"'
    )
    return response

@login_required
def bill_expense_pdf(request, name):
    from django.template.loader import render_to_string
    from weasyprint import HTML
    from django.db.models import Sum, Value, FloatField
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    from_date = parse_date(request.GET.get("from_date"))
    to_date   = parse_date(request.GET.get("to_date"))

    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = date.today()

    qs = (
        OtherExpense.objects
        .filter(title=name, date__range=[from_date, to_date])
        .values("site__name")
        .annotate(
            total=Coalesce(
                Sum("amount"),
                Value(0),
                output_field=FloatField()
            )
        )
        .order_by("site__name")
    )

    rows = []
    total_amt = 0

    for r in qs:
        tot = r["total"] or 0
        total_amt += tot

        rows.append({
            "site": r["site__name"] or "-",
            "advance": 0,
            "total": tot,
        })

    html = render_to_string(
        "civil_team_pdf.html",
        {
            "team": type("obj", (), {"name": name})(),
            "rows": rows,
            "advance_total": 0,
            "grand_total": total_amt,
            "from_date": from_date,
            "to_date": to_date,
            "now": timezone.now(),
        },
    )

    pdf = HTML(string=html).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="expense_{name}_bill.pdf"'
    )
    return response


@login_required
def api_day_full_detail(request):

    from_date = parse_date(request.GET.get("date"))

    if not from_date:
        return JsonResponse({"sites": []})

    sites = Site.objects.all().order_by("name")

    result = []

    for site in sites:

        # ================= CIVIL =================
        civil_qs = CivilDailyWork.objects.filter(
            site=site,
            date=from_date
        )

        civil_rows = [
            {
                "team": c.team.name,
                "mason_full": c.mason_full,
                "mason_half": c.mason_half,
                "helper_full": c.helper_full,
                "helper_half": c.helper_half,
            }
            for c in civil_qs
        ]

        # ================= MATERIAL =================
        material_qs = MaterialEntry.objects.filter(
            site=site,
            date=from_date
        )

        material_rows = [
            {
                "agent": m.agent_name,
                "description": getattr(m, "description", ""),
                "qty": getattr(m, "qty", ""),
            }
            for m in material_qs
        ]

        # ================= DEPARTMENT =================
        dept_qs = DepartmentWork.objects.filter(
            site=site,
            date=from_date
        )

        dept_rows = [
            {
                "department": d.department.name,
                "description": getattr(d, "description", ""),
            }
            for d in dept_qs
        ]

        # ================= EXPENSE =================
        expense_qs = OtherExpense.objects.filter(
            site=site,
            date=from_date
        )

        expense_rows = [
            {
                "title": e.title,
                "description": getattr(e, "description", ""),
                "owner": e.owner.name if e.owner else "-",
            }
            for e in expense_qs
        ]

        # skip empty sites
        if civil_rows or material_rows or dept_rows or expense_rows:
            result.append({
                "site": site.name,
                "civil": civil_rows,
                "material": material_rows,
                "department": dept_rows,
                "expense": expense_rows,
            })

    return JsonResponse({"sites": result})

    