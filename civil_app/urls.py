from django.urls import path
from . import views

urlpatterns = [

    # ================= DASHBOARD =================
    path("", views.dashboard, name="dashboard"),

    # ================= SITE =================
    path("sites/", views.site_manage, name="site_manage"),
    path("delete-site/<int:id>/", views.delete_site, name="delete_site"),
    path("site/<int:site_id>/", views.site_detail, name="site_detail"),
    path("site/<int:site_id>/copy-previous/", views.copy_previous_day, name="copy_previous_day",),
    path("owners/cash/", views.owner_cash_list, name="owner_cash_list"),
    path("owners/cash/add/", views.owner_cash_add, name="owner_cash_add"),

    
    # ================= MASTERS =================
    # path("masters/", views.masters, name="masters"),
    path("masters/team/delete/<int:team_id>/", views.delete_team, name="delete_team"),
    path("masters/department/delete/<int:dept_id>/", views.delete_department, name="delete_department"),
    path("masters/", views.masters_and_payments, name="masters_and_payments"),


    # ================= REPORTS =================
    path("reports/", views.reports, name="reports"),
    path("reports/pdf/", views.report_pdf, name="report_pdf"),

    # ================= RESET ACTIONS =================
    path("site/<int:site_id>/reset/today/", views.reset_site_today, name="reset_site_today"),
    path("site/<int:site_id>/reset/month/", views.reset_site_month, name="reset_site_month"),
    path("site/<int:site_id>/reset/all/", views.reset_site_all, name="reset_site_all"),
    path("site/<int:site_id>/reset/date/", views.reset_site_date, name="reset_site_date"),

    # ================= BILLS =================
    path("bills/", views.all_bills, name="all_bills"),
    path("bills/all/pdf/", views.all_bills_pdf, name="all_bills_pdf"),

    # ================= BILL DETAIL API (MODAL) =================
    path("api/bill/civil/<int:team_id>/", views.bill_civil_detail, name="bill_civil_detail"),
    path("api/bill/department/<int:department_id>/", views.bill_department_detail, name="bill_department_detail"),
    path("api/bill/material/<str:agent_name>/", views.bill_material_detail, name="bill_material_detail"),
    path("api/bill/expense/<str:name>/",views.api_bill_expense,name="api_bill_expense"),
    path("api/day-full/", views.api_day_full_detail, name="api_day_full_detail"),

    # ================= BILL PDF (MODAL DOWNLOAD) =================
    path("bill/team/<int:team_id>/", views.bill_civil_pdf, name="bill_civil_pdf"),
    path("bill/department/<int:department_id>/", views.bill_department_pdf, name="bill_department_pdf"),
    path("bill/agent/<str:agent_name>/", views.bill_material_pdf, name="bill_material_pdf"),
    path("bill/expense/<str:name>/", views.bill_expense_pdf, name="bill_expense_pdf"),
]

