from autoapply.inbox import extract_verification_code


def test_labeled_verification_code():
    text = "Your verification code is 48291375. It expires in 10 minutes."
    assert extract_verification_code(text) == "48291375"


def test_code_hidden_in_html():
    text = "<p>Your security code is <b>AB12CD34</b></p>"
    assert extract_verification_code(text) == "AB12CD34"


def test_unrelated_digits_are_ignored():
    assert extract_verification_code("Call 72750362 today about order 12345678") is None
