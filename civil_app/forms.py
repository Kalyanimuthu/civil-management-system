from django import forms
from .models import CivilDailyWork

class CivilDailyWorkForm(forms.ModelForm):
    class Meta:
        model = CivilDailyWork
        fields = [
            "team",
            "mason_full",
            "mason_half",
            "helper_full",
            "helper_half",
            "material_amount",
        ]
