from autoapply.browser_apply import host_is_blocked


def test_login_boards_are_not_automated():
    assert host_is_blocked("https://www.linkedin.com/jobs/view/1")
    assert host_is_blocked("https://wellfound.com/jobs/1")
    assert not host_is_blocked("https://www.payoneer.com/careers/position/8211155/?gh_jid=8211155")
