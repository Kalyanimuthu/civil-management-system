import json
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

@login_required
def dashboard(request):

    today = date.today()
    selected_range = request.GET.get("range", "week")

    labels = []
    values = []

    # ================= WEEK DATA =================
    if selected_range == "week":

        start = today - timedelta(days=6)

        for i in range(7):

            d = start + timedelta(days=i)

            civil = CivilDailyWork.objects.filter(date=d)\
                .aggregate(v=Sum("labour_amount"))["v"] or 0

            dept = DepartmentWork.objects.filter(date=d)\
                .aggregate(v=Sum("labour_amount"))["v"] or 0

            material = MaterialEntry.objects.filter(date=d)\
                .aggregate(v=Sum("total"))["v"] or 0

            expense = OtherExpense.objects.filter(date=d)\
                .aggregate(v=Sum("amount"))["v"] or 0

            total = civil + dept + material + expense

            labels.append(d.strftime("%d %b"))
            values.append(float(total))

    # ================= MONTH DATA =================
    else:

        start = today.replace(day=1)

        for i in range(31):

            d = start + timedelta(days=i)

            if d.month != today.month:
                break

            civil = CivilDailyWork.objects.filter(date=d)\
                .aggregate(v=Sum("labour_amount"))["v"] or 0

            dept = DepartmentWork.objects.filter(date=d)\
                .aggregate(v=Sum("labour_amount"))["v"] or 0

            material = MaterialEntry.objects.filter(date=d)\
                .aggregate(v=Sum("total"))["v"] or 0

            expense = OtherExpense.objects.filter(date=d)\
                .aggregate(v=Sum("amount"))["v"] or 0

            total = civil + dept + material + expense

            labels.append(d.strftime("%d"))
            values.append(float(total))

    # ================= TOP SITES =================
    top_sites = []

    sites = Site.objects.all()

    for site in sites:

        civil = CivilDailyWork.objects.filter(site=site)\
            .aggregate(v=Sum("labour_amount"))["v"] or 0

        dept = DepartmentWork.objects.filter(site=site)\
            .aggregate(v=Sum("labour_amount"))["v"] or 0

        material = MaterialEntry.objects.filter(site=site)\
            .aggregate(v=Sum("total"))["v"] or 0

        expense = OtherExpense.objects.filter(site=site)\
            .aggregate(v=Sum("amount"))["v"] or 0

        total = civil + dept + material + expense

        if total > 0:
            top_sites.append({
                "site": site,
                "total": total
            })

    top_sites = sorted(top_sites, key=lambda x: x["total"], reverse=True)[:5]

    # ================= TODAY STATS =================

    today_labour = (
        CivilDailyWork.objects.filter(date=today)
        .aggregate(v=Sum("labour_amount"))["v"] or 0
    ) + (
        DepartmentWork.objects.filter(date=today)
        .aggregate(v=Sum("labour_amount"))["v"] or 0
    )

    material_total = MaterialEntry.objects.filter(
        date=today
    ).aggregate(
        v=Sum("total")
    )["v"] or 0

    expense_total = OtherExpense.objects.filter(
        date=today
    ).aggregate(
        v=Sum("amount")
    )["v"] or 0


    # ================= SITE COMPARISON =================

    site_labels = []
    site_costs = []

    for site in Site.objects.all():

        civil = CivilDailyWork.objects.filter(site=site)\
            .aggregate(v=Sum("labour_amount"))["v"] or 0

        dept = DepartmentWork.objects.filter(site=site)\
            .aggregate(v=Sum("labour_amount"))["v"] or 0

        material = MaterialEntry.objects.filter(site=site)\
            .aggregate(v=Sum("total"))["v"] or 0

        expense = OtherExpense.objects.filter(site=site)\
            .aggregate(v=Sum("amount"))["v"] or 0

        total = civil + dept + material + expense

        if total > 0:
            site_labels.append(site.name)
            site_costs.append(float(total))
        
    context = {
        "chart_labels": json.dumps(labels),
        "chart_values": json.dumps(values),

        "site_labels": json.dumps(site_labels),
        "site_costs": json.dumps(site_costs),

        "selected_range": selected_range,
        "top_sites": top_sites,
        "today_labour": today_labour,
        "material_total": material_total,
        "expense_total": expense_total,
        "total_sites": Site.objects.count(),
    }

    return render(request, "dashboard.html", context)

# =========================================================
# Site_Entry
# =========================================================

@login_required
def site_entry(request):

    today = date.today()

    # Sunday → Saturday
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = today - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)

    sites = Site.objects.all()
    data = []

    for site in sites:

        # ===== CIVIL WORK =====
        civil = CivilDailyWork.objects.filter(
            site=site,
            date__range=(week_start, week_end)
        ).aggregate(
            labour=Sum("labour_amount"),
            
            advance=Sum("advance_amount"),
        )

        # ===== DEPARTMENT WORK =====
        dept = DepartmentWork.objects.filter(
            site=site,
            date__range=(week_start, week_end)
        ).aggregate(
            labour=Sum("labour_amount"),
            advance=Sum("advance_amount"),
        )

        # ===== MATERIAL =====
        material = MaterialEntry.objects.filter(
            site=site,
            date__range=(week_start, week_end)
        ).aggregate(
            total=Sum("total"),
            advance=Sum("advance"),
        )

        # ===== OTHER EXPENSE =====
        expense = OtherExpense.objects.filter(
            site=site,
            date__range=(week_start, week_end)
        ).aggregate(
            total=Sum("amount")
        )

        # ===== VALUES =====
        civil_labour = civil["labour"] or 0
        
        civil_adv_total = civil["advance"] or 0

        dept_labour = dept["labour"] or 0
        dept_adv_total = dept["advance"] or 0

        material_total = material["total"] or 0
        material_adv_total = material["advance"] or 0

        expense_total = expense["total"] or 0

        # ===== WEEKLY PAYMENT =====
        weekly_total = (
            civil_labour +
            dept_labour +
            material_total +
            expense_total
        )

        # ===== WEEKLY ADVANCE =====
        weekly_advance = (
            civil_adv_total +
            dept_adv_total +
            material_adv_total
        )

        data.append({
            "site": site,
            "weekly_total": weekly_total,
            "weekly_advance": weekly_advance,
        })

    return render(request, "site_entry.html", {"sites": data})

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

    sites = Site.objects.all().order_by("name")

    return render(request, "site_manage.html", {
        "sites": sites
    })



@login_required
def add_site(request):

    if request.method == "POST":

        data = json.loads(request.body)

        name = data.get("name")

        site = Site.objects.create(name=name)

        return JsonResponse({
            "status": "ok",
            "id": site.id
        })
    
@login_required
def edit_site(request, id):

    site = get_object_or_404(Site, id=id)

    if request.method == "POST":

        data = json.loads(request.body)

        name = data.get("name")

        if name:
            site.name = name
            site.save()

        return JsonResponse({"status": "ok"})


@login_required
@staff_required
def delete_site(request, id):
    Site.objects.filter(id=id).delete()
    return redirect("site_entry")



@login_required
@staff_required
def site_detail(request, site_id):

    site = get_object_or_404(Site, id=site_id)
    sites = Site.objects.all().order_by("name")

    raw_date = request.GET.get("date") or request.POST.get("date")
    work_date = parse_date(raw_date) if raw_date else date.today()
    work_date = work_date or date.today()

    teams = Team.objects.all()
    departments = Department.objects.exclude(name="Civil")

    # ✅ OWNER
    owners = Owner.objects.annotate(
        balance=Sum("ownercashentry__amount")
    )

    # ✅ DEFAULT RATE
    default_rates = {
        r.department_id: r.full_day_rate
        for r in DefaultRate.objects.all()
    }

    # =================================================
    # SAVE DATA
    # =================================================
    if request.method == "POST":

        # ================= CIVIL =================
        team_ids = set()

        for key in request.POST.keys():
            if key.startswith((
                "mason_full_", "helper_full_",
                "mason_half_", "helper_half_",
                "advance_", "extra_", "allowance_type_"
            )):
                try:
                    team_id = int(key.split("_")[-1])
                    team_ids.add(team_id)
                except:
                    pass

        for team_id in team_ids:
            try:
                team = Team.objects.get(id=team_id)
            except Team.DoesNotExist:
                continue

            mf = int(float(request.POST.get(f"mason_full_{team_id}") or 0))
            hf = int(float(request.POST.get(f"helper_full_{team_id}") or 0))
            mh = int(float(request.POST.get(f"mason_half_{team_id}") or 0))
            hh = int(float(request.POST.get(f"helper_half_{team_id}") or 0))
            
            adv = float(request.POST.get(f"advance_{team_id}") or 0)
            extra = float(request.POST.get(f"extra_{team_id}") or 0)
            allowance_type = request.POST.get(f"allowance_type_{team_id}") or ""
            
            # Labour = count × rate
            labour = calculate_civil_labour(
                team,
                mf,
                hf,
                mh,
                hh,
                work_date
            )
            
            total = max(labour + extra - adv, 0)

            if not any([mf, hf, mh, hh, adv, extra, allowance_type]):
                CivilDailyWork.objects.filter(
                    site=site, team=team, date=work_date
                ).delete()
                continue

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
                    "extra_allowance": extra,
                    "allowance_type": allowance_type,
                    "advance_amount": adv,
                    "total_amount": total,
                }
            )

        # ================= DEPARTMENT =================
        for dept in departments:

            full = int(request.POST.get(f"dept_full_{dept.id}") or 0)
            half = int(request.POST.get(f"dept_half_{dept.id}") or 0)
            advance = float(request.POST.get(f"dept_advance_{dept.id}") or 0)

            rate = float(request.POST.get(f"dept_rate_{dept.id}") or 0)
            half_rate = rate / 2

            labour = (full * rate) + (half * half_rate)
            total = labour - advance

            if not any([full, half, advance]):
                DepartmentWork.objects.filter(
                    site=site, department=dept, date=work_date
                ).delete()
                continue

            DepartmentWork.objects.update_or_create(
                site=site,
                department=dept,
                date=work_date,
                defaults={
                    "full_day_count": full,
                    "half_day_count": half,
                    "full_day_rate": rate,
                    "half_day_rate": half_rate,
                    "labour_amount": labour,
                    "advance_amount": advance,
                    "total_amount": total,
                }
            )

        # ================= MATERIAL =================
        MaterialEntry.objects.filter(site=site, date=work_date).delete()

        i = 0
        while True:
            name = request.POST.get(f"material_name_{i}")
            if name is None:
                break

            if not name and not request.POST.get(f"material_qty_{i}"):
                i += 1
                continue

            qty = float(request.POST.get(f"material_qty_{i}") or 0)
            rate = float(request.POST.get(f"material_rate_{i}") or 0)
            advance = float(request.POST.get(f"material_advance_{i}") or 0)

            total = max((qty * rate) - advance, 0)

            MaterialEntry.objects.create(
                site=site,
                date=work_date,
                agent_name=request.POST.get(f"agent_name_{i}") or "",
                name=name,
                quantity=qty,
                unit=request.POST.get(f"material_unit_{i}") or "",
                rate=rate,
                advance=advance,
                total=total,
            )

            i += 1

        # ================= OTHER EXPENSE =================
        OtherExpense.objects.filter(site=site, date=work_date).delete()

        i = 0
        while True:
            title = request.POST.get(f"expense_title_{i}")
            amount = request.POST.get(f"expense_amount_{i}")

            if title is None:
                break

            if not title and not amount:
                i += 1
                continue

            owner_id = request.POST.get(f"expense_owner_{i}")

            OtherExpense.objects.create(
                site=site,
                date=work_date,
                title=title,
                owner_id=owner_id if owner_id else None,
                amount=float(amount or 0),
                notes=request.POST.get(f"expense_notes_{i}") or "",
            )

            i += 1

    # =================================================
    # DISPLAY DATA
    # =================================================

    civil_entries = CivilDailyWork.objects.filter(site=site, date=work_date)
    civil_map = {c.team_id: c for c in civil_entries}

    civil_rows = []
    for team in teams:
        rate = get_team_rate(team, work_date)
        if not rate:
            continue

        work = civil_map.get(team.id)

        labour = work.labour_amount if work else 0
        extra = work.extra_allowance if work else 0
        total = work.total_amount if work else 0
        advance = work.advance_amount if work else 0
        allowance_type = work.allowance_type if work else ""

        if labour == 0 and extra == 0:
            continue

        civil_rows.append({
            "team": team,
            "rate": rate,
            "work": work,
            "labour": labour,
            "advance": advance,
            "extra": extra,
            "allowance_type": allowance_type,
            "total": total,
        })

    dept_entries = DepartmentWork.objects.filter(site=site, date=work_date)
    dept_map = {d.department_id: d for d in dept_entries}

    materials = MaterialEntry.objects.filter(site=site, date=work_date)
    other_expenses = OtherExpense.objects.filter(site=site, date=work_date)

    return render(request, "site_detail.html", {
        "site": site,
        "sites": sites,
        "teams": teams,
        "departments": departments,
        "other_depts": departments,

        "civil_rows": civil_rows,
        "dept_map": dept_map,
        "materials": materials,
        "other_expenses": other_expenses,

        "owners": owners,
        "default_rates": default_rates,

        "work_date": work_date,
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

    sites = Site.objects.all().order_by("name")
    teams = Team.objects.all().order_by("name")
    departments = Department.objects.all().order_by("name")

    rows = []

    total_labour = 0
    total_material = 0
    total_expense = 0
    total_advance = 0

    # =====================================================
    # ===================== CIVIL =========================
    # =====================================================
    if not material_only and not dept_id:

        civil_qs = CivilDailyWork.objects.filter(
            date__range=[from_date, to_date]
        ).select_related("site", "team")

        if site_id:
            civil_qs = civil_qs.filter(site_id=site_id)

        if team_id:
            civil_qs = civil_qs.filter(team_id=team_id)

        for r in civil_qs:

            labour = r.labour_amount or 0
            allowance = r.extra_allowance or 0
            advance = r.advance_amount or 0

            gross = labour + allowance

            # Stored total already follows labour + allowance - advance
            total = max(gross - advance, 0)

            rows.append({
                "type": "Civil",
                "date": r.date,
                "site": r.site,
                "department": "Civil",
                "team": r.team.name if r.team else "-",

                # Civil manpower
                "mason_full": r.mason_full or 0,
                "mason_half": r.mason_half or 0,
                "helper_full": r.helper_full or 0,
                "helper_half": r.helper_half or 0,

                # Labour
                "labour": labour,

                # Allowance
                "allowance_type": r.allowance_type or "-",
                "allowance": allowance,

                # Material
                "material_name": "-",
                "quantity": 0,
                "unit": "-",
                "rate": 0,

                # Advance / Total
                "advance": advance,

                # Expense
                "expense_title": "-",
                "owner": "-",
                "notes": "",

                "total": total,
            })

            total_labour += gross
            total_advance += advance

    # =====================================================
    # ================== DEPARTMENT =======================
    # =====================================================
    if not material_only and not team_id:

        dept_qs = DepartmentWork.objects.filter(
            date__range=[from_date, to_date]
        ).select_related("site", "department")

        if site_id:
            dept_qs = dept_qs.filter(site_id=site_id)

        if dept_id:
            dept_qs = dept_qs.filter(department_id=dept_id)

        for d in dept_qs:

            labour = d.labour_amount or 0
            advance = d.advance_amount or 0

            total = labour - advance

            rows.append({
                "type": "Department",
                "date": d.date,
                "site": d.site,
                "department": d.department.name if d.department else "-",
                "team": "-",

                # Civil manpower
                "mason_full": 0,
                "mason_half": 0,
                "helper_full": 0,
                "helper_half": 0,

                # Department manpower
                "full_day_count": d.full_day_count or 0,
                "half_day_count": d.half_day_count or 0,
                "full_day_rate": d.full_day_rate or 0,
                "half_day_rate": d.half_day_rate or 0,

                # Labour
                "labour": labour,

                # Allowance
                "allowance_type": "-",
                "allowance": 0,

                # Material
                "material_name": "-",
                "quantity": 0,
                "unit": "-",
                "rate": 0,

                # Advance
                "advance": advance,

                # Expense
                "expense_title": "-",
                "owner": "-",
                "notes": "",

                "total": total,
            })

            total_labour += labour
            total_advance += advance

    # =====================================================
    # ===================== MATERIAL ======================
    # =====================================================
    if material_only or (not team_id and not dept_id):

        material_qs = MaterialEntry.objects.filter(
            date__range=[from_date, to_date]
        ).select_related("site")

        if site_id:
            material_qs = material_qs.filter(site_id=site_id)

        for m in material_qs:

            quantity = m.quantity or 0
            rate = m.rate or 0
            advance = m.advance or 0

            # Gross material amount
            gross = quantity * rate

            # m.total is already net amount after advance
            total = m.total or max(gross - advance, 0)

            rows.append({
                "type": "Material",
                "date": m.date,
                "site": m.site,
                "department": "Material",
                "team": m.agent_name or "-",

                # Civil manpower
                "mason_full": 0,
                "mason_half": 0,
                "helper_full": 0,
                "helper_half": 0,

                # Department manpower
                "full_day_count": 0,
                "half_day_count": 0,
                "full_day_rate": 0,
                "half_day_rate": 0,

                # Labour
                "labour": 0,

                # Allowance
                "allowance_type": "-",
                "allowance": 0,

                # Material
                "material_name": m.name or "-",
                "quantity": quantity,
                "unit": m.unit or "-",
                "rate": rate,
                "material": gross,

                # Advance
                "advance": advance,

                # Expense
                "expense_title": "-",
                "owner": "-",
                "notes": "",

                # Net total
                "total": total,
            })

            total_material += gross
            total_advance += advance

    # =====================================================
    # ===================== EXPENSE =======================
    # =====================================================
    if not material_only and not team_id and not dept_id:

        expense_qs = OtherExpense.objects.filter(
            date__range=[from_date, to_date]
        ).select_related("site", "owner")

        if site_id:
            expense_qs = expense_qs.filter(site_id=site_id)

        for e in expense_qs:

            amount = e.amount or 0

            rows.append({
                "type": "Expense",
                "date": e.date,
                "site": e.site,
                "department": "Expense",
                "team": "-",

                # Civil manpower
                "mason_full": 0,
                "mason_half": 0,
                "helper_full": 0,
                "helper_half": 0,

                # Department manpower
                "full_day_count": 0,
                "half_day_count": 0,
                "full_day_rate": 0,
                "half_day_rate": 0,

                # Labour
                "labour": 0,

                # Allowance
                "allowance_type": "-",
                "allowance": 0,

                # Material
                "material_name": "-",
                "quantity": 0,
                "unit": "-",
                "rate": 0,
                "material": 0,

                # Advance
                "advance": 0,

                # Expense
                "expense_title": e.title or "-",
                "owner": e.owner.name if e.owner else "-",
                "notes": e.notes or "",

                "expense": amount,
                "total": amount,
            })

            total_expense += amount

    # =====================================================
    # ======================= SORT =========================
    # =====================================================

    rows = sorted(
        rows,
        key=lambda x: (
            x["date"],
            x["site"].name if x["site"] else "",
            x["department"],
            x["team"],
        )
    )

    # =====================================================
    # ===================== GRAND TOTAL ===================
    # =====================================================

    grand_total = (
        total_labour
        + total_material
        + total_expense
        - total_advance
    )

    # =====================================================
    # ===================== SUMMARY =======================
    # =====================================================

    team_site_totals = defaultdict(lambda: defaultdict(float))
    dept_site_totals = defaultdict(lambda: defaultdict(float))
    material_site_totals = defaultdict(lambda: defaultdict(float))

    for r in rows:

        site_name = r["site"].name if r["site"] else "-"

        if r["department"] == "Civil":
            team_site_totals[r["team"]][site_name] += r["total"]

        elif r["department"] == "Material":
            material_site_totals["Material"][site_name] += r["total"]

        elif r["department"] == "Expense":
            material_site_totals["Expense"][site_name] += r["total"]

        else:
            dept_site_totals[r["department"]][site_name] += r["total"]

    return render(request, "reports.html", {
        "sites": sites,
        "teams": teams,
        "departments": departments,

        "rows": rows,

        "total_labour": total_labour,
        "total_material": total_material,
        "total_expense": total_expense,
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

    total_labour = 0
    total_material = 0
    total_advance = 0

    # ---------------- CIVIL ----------------
    civil_qs = CivilDailyWork.objects.filter(
        date__range=[from_date, to_date]
    )

    if site_id:
        civil_qs = civil_qs.filter(site_id=site_id)

    if team_id:
        civil_qs = civil_qs.filter(team_id=team_id)

    for r in civil_qs:

        labour = r.labour_amount or 0
        allowance = r.extra_allowance or 0
        adv = r.advance_amount or 0

        labour_total = labour + allowance
        total = labour_total - adv

        rows.append({
            "date": r.date,
            "site": r.site.name,
            "department": "Civil",
            "team": r.team.name,
            "labour": labour,
            "allowance": allowance,
            "material": 0,
            "advance": adv,
            "total": total,
        })

        total_labour += labour_total
        total_advance += adv

    # ---------------- DEPARTMENT ----------------
    dept_qs = DepartmentWork.objects.filter(
        date__range=[from_date, to_date]
    )

    if site_id:
        dept_qs = dept_qs.filter(site_id=site_id)

    if dept_id:
        dept_qs = dept_qs.filter(department_id=dept_id)

    for d in dept_qs:

        labour = d.labour_amount or 0
        adv = d.advance_amount or 0
        total = labour - adv

        rows.append({
            "date": d.date,
            "site": d.site.name,
            "department": d.department.name,
            "team": "-",
            "labour": labour,
            "allowance": 0,
            "material": 0,
            "advance": adv,
            "total": total,
        })

        total_labour += labour
        total_advance += adv

    # ---------------- MATERIAL ----------------
    material_qs = MaterialEntry.objects.filter(
        date__range=[from_date, to_date]
    )

    if site_id:
        material_qs = material_qs.filter(site_id=site_id)

    for m in material_qs:

        material_total = m.total or 0
        adv = m.advance or 0
        net = material_total - adv

        rows.append({
            "date": m.date,
            "site": m.site.name,
            "department": "Material",
            "team": m.agent_name or "-",
            "labour": 0,
            "allowance": 0,
            "material": material_total,
            "advance": adv,
            "total": net,
        })

        total_material += material_total
        total_advance += adv

    # ---------------- EXPENSE ----------------
    expense_qs = OtherExpense.objects.filter(
        date__range=[from_date, to_date]
    )

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
            "allowance": 0,
            "material": amt,
            "advance": 0,
            "total": amt,
        })

        total_material += amt

    # ---------------- SORT ----------------
    rows = sorted(
        rows,
        key=lambda x: (
            x["date"],
            x["site"],
            x["department"],
            x["team"],
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
            total_allowance=Sum("extra_allowance"),
            total_advance=Sum("advance_amount"),
        )
    )

    civil_bills = []
    for c in civil_totals:
        civil_bills.append({
            "team__id": c["team_id"],
            "team__name": c["team__name"],
            "total_amount": c["total_amount"] or 0,
            "total_advance": c["total_advance"] or 0,
            "total_allowance": c["total_allowance"] or 0,
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

    # =================================================
    # ================= EXPENSE =======================
    # =================================================

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

    from_date = parse_date(request.GET.get("from_date")) or date.today()
    to_date = parse_date(request.GET.get("to_date")) or date.today()

    # =================================================
    # ================= CIVIL =========================
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

        site_qs = (
            CivilDailyWork.objects
            .filter(team_id=team_id, date__range=[from_date, to_date])
            .values("site_id", "site__name")
            .annotate(
                labour=Coalesce(Sum("labour_amount"), Value(0), output_field=FloatField()),
                advance=Coalesce(Sum("advance_amount"), Value(0), output_field=FloatField()),
                allowance=Coalesce(Sum("extra_allowance"), Value(0), output_field=FloatField()),
            )
            .order_by("site__name")
        )

        sites = []
        team_labour_total = 0
        team_adv_total = 0
        team_allow_total = 0
        team_total = 0

        for s in site_qs:

            labour = s["labour"] or 0
            advance = s["advance"] or 0
            allowance = s["allowance"] or 0

            # 🔥 NEW TOTAL LOGIC
            calc_total = labour + advance + allowance

            team_labour_total += labour
            team_adv_total += advance
            team_allow_total += allowance
            team_total += calc_total

            sites.append({
                "site": s["site__name"],
                "labour": labour,
                "advance": advance,
                "allowance": allowance,
                "total": calc_total,
            })

        civil_rows.append({
            "name": t["team__name"],
            "labour": team_labour_total,
            "advance": team_adv_total,
            "allowance": team_allow_total,
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
                labour=Coalesce(Sum("labour_amount"), Value(0), output_field=FloatField()),
                advance=Coalesce(Sum("advance_amount"), Value(0), output_field=FloatField()),
            )
            .order_by("site__name")
        )

        sites = []
        lab_total = 0
        adv_total = 0
        dept_total = 0

        for s in site_qs:

            labour = s["labour"] or 0
            advance = s["advance"] or 0

            # 🔥 TOTAL = labour + advance
            calc_total = labour + advance

            lab_total += labour
            adv_total += advance
            dept_total += calc_total

            sites.append({
                "site": s["site__name"],
                "labour": labour,
                "advance": advance,
                "total": calc_total,
            })

        dept_rows.append({
            "name": d["department__name"],
            "labour": lab_total,
            "advance": adv_total,
            "total": dept_total,
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
                total=Coalesce(Sum("total"), Value(0), output_field=FloatField()),
            )
            .order_by("site__name")
        )

        sites = []
        adv_total = 0
        amt_total = 0

        for s in site_qs:

            advance = s["advance"] or 0
            total = s["total"] or 0

            calc_total = total + advance  # 🔥 include advance

            adv_total += advance
            amt_total += calc_total

            sites.append({
                "site": s["site__name"],
                "advance": advance,
                "total": calc_total,
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

    site_qs = (
        OtherExpense.objects
        .filter(date__range=[from_date, to_date])
        .values("site_id", "site__name")
        .annotate(
            total=Coalesce(Sum("amount"), Value(0), output_field=FloatField())
        )
        .order_by("site__name")
    )

    expense_rows = []

    for s in site_qs:

        exp_qs = (
            OtherExpense.objects
            .filter(site_id=s["site_id"], date__range=[from_date, to_date])
            .values("title", "owner__name")
            .annotate(
                total=Coalesce(Sum("amount"), Value(0), output_field=FloatField())
            )
        )

        expenses = []

        for e in exp_qs:
            expenses.append({
                "name": e["title"],
                "owner": e["owner__name"] or "-",
                "total": e["total"],
            })

        expense_rows.append({
            "site": s["site__name"],
            "total": s["total"],
            "expenses": expenses,
        })

    # =================================================
    # ================= GRAND TOTAL ===================
    # =================================================

    civil_sum = sum(r["total"] for r in civil_rows)
    dept_sum = sum(r["total"] for r in dept_rows)
    material_sum = sum(r["total"] for r in material_rows)
    expense_sum = sum(r["total"] for r in expense_rows)

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
            ),

            allowance=Coalesce(   # 🔥 ADD THIS
                Sum("extra_allowance"),
                Value(0),
                output_field=FloatField()
            ),

            mason_full=Coalesce(Sum("mason_full"), Value(0)),
            mason_half=Coalesce(Sum("mason_half"), Value(0)),

            helper_full=Coalesce(Sum("helper_full"), Value(0)),
            helper_half=Coalesce(Sum("helper_half"), Value(0)),
        )
    )

    # ================= ADVANCE =================
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
    total_allow = 0

    for w in work_qs:

        adv = adv_map.get(w.get("site_id"), 0)

        total_amt += w["total"]
        total_adv += adv
        total_allow += w["allowance"]   # 🔥 ADD

        rows.append({

            "site__name": w["site__name"],

            "mason_full": w["mason_full"],
            "mason_half": w["mason_half"],

            "helper_full": w["helper_full"],
            "helper_half": w["helper_half"],

            "advance": adv,
            "allowance": w["allowance"],   # 🔥 முக்கியம்
            "allowance_type": "General",   # optional

            "total": w["total"],
        })

    return JsonResponse({
        "rows": rows,
        "team_total": {
            "advance_total": total_adv,
            "allowance_total": total_allow,   # 🔥 ADD
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

            # ⭐ labour counts
            full=Coalesce(Sum("full_day_count"), Value(0)),
            half=Coalesce(Sum("half_day_count"), Value(0)),
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

            "full": r["full"],
            "half": r["half"],

            "advance": adv,
            "total": tot,
        })

    return JsonResponse({
        "rows": rows,
        "team_total": {
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
            prev_desc = SiteDailyNote.objects.filter(
                site=site,
                date=prev_date
            ).first()

            if prev_desc:
                SiteDailyNote.objects.update_or_create(
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
            ),
            allowance=Coalesce(   # 🔥 ADD
                Sum("extra_allowance"),
                Value(0),
                output_field=FloatField()
            )
        )
        .order_by("site__name")
    )

    # ================= ADVANCE =================
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

    # ================= LOOP =================
    rows = []
    grand_total = 0
    advance_total = 0
    allowance_total = 0  # 🔥 ADD

    for w in work_qs:

        adv = adv_map.get(w["site_id"], 0)

        total = w["total"]
        allow = w["allowance"]

        grand_total += total + allow   # 🔥 FIX (include allowance)
        advance_total += adv
        allowance_total += allow

        rows.append({
            "site": w["site__name"],
            "advance": adv,
            "allowance": allow,   # 🔥 ADD
            "total": total,
        })

    # ================= HTML =================
    html = render_to_string(
        "civil_team_pdf.html",
        {
            "team": team,
            "rows": rows,
            "advance_total": advance_total,
            "allowance_total": allowance_total,  # 🔥 ADD
            "grand_total": grand_total,
            "from_date": from_date,
            "to_date": to_date,
        },
    )

    # ================= PDF =================
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

    selected_date = parse_date(request.GET.get("date"))

    if not selected_date:
        return JsonResponse({"sites": []})

    sites = Site.objects.all().order_by("name")

    result = []

    for site in sites:

        # =================================================
        # ===================== CIVIL =====================
        # =================================================

        civil_qs = CivilDailyWork.objects.filter(
            site=site,
            date=selected_date
        ).select_related("team")

        civil_rows = []

        for c in civil_qs:

            labour = c.labour_amount or 0
            allowance = c.extra_allowance or 0
            advance = c.advance_amount or 0

            total = max(
                labour + allowance - advance,
                0
            )

            civil_rows.append({
                "team": c.team.name if c.team else "-",

                "mason_full": c.mason_full or 0,
                "mason_half": c.mason_half or 0,
                "helper_full": c.helper_full or 0,
                "helper_half": c.helper_half or 0,

                "labour": labour,

                "allowance_type": c.allowance_type or "-",
                "allowance": allowance,

                "advance": advance,
                "total": total,
            })

        # =================================================
        # ================== DEPARTMENT ===================
        # =================================================

        dept_qs = DepartmentWork.objects.filter(
            site=site,
            date=selected_date
        ).select_related("department")

        dept_rows = []

        for d in dept_qs:

            labour = d.labour_amount or 0
            advance = d.advance_amount or 0

            dept_rows.append({
                "department": (
                    d.department.name
                    if d.department else "-"
                ),

                "full": d.full_day_count or 0,
                "half": d.half_day_count or 0,

                "full_rate": d.full_day_rate or 0,
                "half_rate": d.half_day_rate or 0,

                "labour": labour,
                "advance": advance,

                "total": d.total_amount or (
                    labour - advance
                ),
            })

        # =================================================
        # ==================== MATERIAL ====================
        # =================================================

        material_qs = MaterialEntry.objects.filter(
            site=site,
            date=selected_date
        )

        material_rows = []

        for m in material_qs:

            quantity = m.quantity or 0
            rate = m.rate or 0
            advance = m.advance or 0

            gross = quantity * rate

            material_rows.append({
                "agent": m.agent_name or "-",
                "name": m.name or "-",

                "qty": quantity,
                "unit": m.unit or "-",
                "rate": rate,

                "gross": gross,
                "advance": advance,

                "total": m.total or max(
                    gross - advance,
                    0
                ),
            })

        # =================================================
        # ===================== EXPENSE ====================
        # =================================================

        expense_qs = OtherExpense.objects.filter(
            site=site,
            date=selected_date
        ).select_related("owner")

        expense_rows = []

        for e in expense_qs:

            expense_rows.append({
                "title": e.title or "-",

                "owner": (
                    e.owner.name
                    if e.owner else "-"
                ),

                "amount": e.amount or 0,
                "notes": e.notes or "",
            })

        # =================================================
        # ===================== RESULT =====================
        # =================================================

        if (
            civil_rows
            or material_rows
            or dept_rows
            or expense_rows
        ):

            result.append({
                "site": site.name,

                "civil": civil_rows,
                "department": dept_rows,
                "material": material_rows,
                "expense": expense_rows,
            })

    return JsonResponse({
        "sites": result
    })