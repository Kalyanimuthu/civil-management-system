from django.db import models

# ---------- SITE ----------
class Site(models.Model):
    name = models.CharField(max_length=100)
    

    def __str__(self):
        return self.name


# ---------- DEPARTMENT (DEFAULT PER SITE) ----------
class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


# ---------- TEAM (ONLY FOR CIVIL) ----------
class Team(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name



# ---------- CIVIL TEAM RATE ----------
class TeamRate(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)

    mason_full_rate = models.IntegerField()
    helper_full_rate = models.IntegerField()
    effective_from = models.DateField(null=True, blank=True)
    from_date = models.DateField()
    is_locked = models.BooleanField(default=False)

    @property
    def mason_half_rate(self):
        return self.mason_full_rate // 2

    @property
    def helper_half_rate(self):
        return self.helper_full_rate // 2

    def __str__(self):
        return self.team.name


# ---------- CIVIL DAILY WORK ----------
class CivilDailyWork(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    date = models.DateField()

    mason_full = models.IntegerField(default=0)
    mason_half = models.IntegerField(default=0)
    helper_full = models.IntegerField(default=0)
    helper_half = models.IntegerField(default=0)

    labour_amount = models.IntegerField(default=0)
    material_amount = models.IntegerField(default=0)

    class Meta:
        unique_together = ("site", "team", "date")


# ---------- CIVIL MATERIAL ----------
class CivilMaterial(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.IntegerField()


# ---------- OTHER DEPARTMENT WORK ----------
class DepartmentWork(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="department_works")
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    date = models.DateField()

    full_day_count = models.IntegerField(default=0)
    half_day_count = models.IntegerField(default=0)

    full_day_rate = models.IntegerField()
    half_day_rate = models.IntegerField()

    labour_amount = models.IntegerField(default=0)
    material_amount = models.IntegerField(default=0)

    class Meta:
        unique_together = ("site", "department", "date")



# ---------- OTHER DEPARTMENT MATERIAL ----------
class DepartmentMaterial(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.IntegerField()

# ---------- DEFAULT RATE (PER SITE + DEPARTMENT) ----------
class DefaultRate(models.Model):
    department = models.OneToOneField(Department, on_delete=models.CASCADE)
    effective_from = models.DateField(null=True, blank=True)
    full_day_rate = models.IntegerField(default=0)
    is_locked = models.BooleanField(default=True)

    @property
    def half_day_rate(self):
        return self.full_day_rate // 2

