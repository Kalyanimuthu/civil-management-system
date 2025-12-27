from django.urls import path
from . import views

urlpatterns = [

    # ================= DASHBOARD =================
    path("", views.dashboard, name="dashboard"),

    # ================= SITE =================
    path("sites/", views.site_manage, name="site_manage"),
    path("delete-site/<int:id>/", views.delete_site, name="delete_site"),
    path("site/<int:site_id>/", views.site_detail, name="site_detail"),
       

    # ================= PAYMENTS =================
    
    path("default-payment/", views.default_payment, name="default_payment"),

    # ================= MASTERS =================
    path("masters/", views.masters, name="masters"),

    # TEAM DELETE
    path("masters/team/delete/<int:team_id>/", views.delete_team, name="delete_team"),

    # DEPARTMENT DELETE
    path("masters/department/delete/<int:dept_id>/", views.delete_department, name="delete_department"),

    # ================= REPORTS =================
    path("reports/", views.reports, name="reports"),
    path("reports/pdf/", views.report_pdf, name="report_pdf"),

    # ================= RESET ACTIONS =================
    path("site/<int:site_id>/reset/today/", views.reset_site_today, name="reset_site_today"),
    path("site/<int:site_id>/reset/month/", views.reset_site_month, name="reset_site_month"),
    path("site/<int:site_id>/reset/all/", views.reset_site_all, name="reset_site_all"),
    path("site/<int:site_id>/reset/date/", views.reset_site_date, name="reset_site_date"),
   
]
