from anura.avss.models import HealthReport
from anura.marshalling import unmarshal


def test_unmarshal_HealthReport_missing_fields():
    # HealthReport can be unmarshalled with keys 7-9 missing.
    unmarshal(
        HealthReport,
        {
            0: 0,
            1: 0,
            2: 0,
            3: 0.0,
            4: 0,
            5: 0,
            6: 0,
        },
    )
