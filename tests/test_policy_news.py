"""Policy headlines move the EURGBP factor. An unrelated flash does not."""

from __future__ import annotations

from data.policy_news import policy_narrative, policy_pressure


def test_uk_hike_and_ez_cut_and_the_fed_have_signs() -> None:
    assert policy_pressure("英国央行加息25个基点") == -0.5
    assert policy_pressure("欧洲央行降息") == -0.5
    assert policy_pressure("美联储意外加息") == 0.7
    assert policy_pressure("BoE unexpectedly dovish") == 0.7


def test_an_unrelated_flash_has_no_policy_pressure() -> None:
    assert policy_pressure("澳大利亚总理发表讲话") is None
    assert policy_narrative("市场波动加大") is None


def test_a_mixed_hike_and_cut_is_left_out() -> None:
    assert policy_pressure("英国央行加息还是降息仍不确定") is None
