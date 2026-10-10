from autoapply.matching import company_is_skipped, is_eligible_location, is_target_role


def test_target_roles():
    assert is_target_role("Senior Backend Engineer")
    assert is_target_role("Staff Software Engineer")
    assert is_target_role("Full Stack Engineer")
    assert is_target_role("Intermediate Backend Engineer, India")
    assert is_target_role("Engineering Manager - Backend")
    assert not is_target_role("Frontend Engineer")
    assert not is_target_role("Software Engineer Intern")
    assert not is_target_role("Backend Engineer (C# and .Net)")
    assert not is_target_role("Fraud Operations Team Lead")
    assert not is_target_role("Director of Software Engineering")


def test_locations():
    assert is_eligible_location("Bengaluru, India")
    assert is_eligible_location("India - Remote")
    assert is_eligible_location("Remote - India")
    assert is_eligible_location("Gurugram")
    assert is_eligible_location("Remote")
    assert is_eligible_location("Anywhere in the World")
    assert not is_eligible_location("Remote - United States")
    assert not is_eligible_location("Hybrid")
    assert not is_eligible_location("San Francisco Bay Area")
    assert not is_eligible_location("Ukraine Anywhere")
    assert not is_eligible_location("")


def test_skip_current_employer():
    assert company_is_skipped("Deel", ["Deel"])
    assert company_is_skipped("Deel Inc", ["Deel"])
    assert not company_is_skipped("GitLab", ["Deel"])
