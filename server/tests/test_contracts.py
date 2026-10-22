from landline.contracts import Fee, as_e164, monthly_cost


def test_as_e164():
    assert as_e164("+14165550101") == "+14165550101"
    assert as_e164("4165550101") == "+14165550101"
    assert as_e164("(416) 555-0101") == "+14165550101"
    assert as_e164("+4165550101") == "+14165550101"     # NANP missing the 1
    assert as_e164("call me maybe") == ""
    assert as_e164(None) == ""


def test_monthly_cost_counts_only_mandatory_monthly_fees():
    fees = [
        Fee(name="parking", amount=180, period="monthly", mandatory=False),
        Fee(name="utilities", amount=120, period="monthly", mandatory=True),
        Fee(name="deposit", amount=1200, period="one_time", mandatory=True),
    ]
    assert monthly_cost(2400, fees) == 2520
