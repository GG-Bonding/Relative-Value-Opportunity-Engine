"""Policy headlines are regime context. They do not set an EURGBP factor."""

from __future__ import annotations

from data.policy_news import policy_context, policy_pressure


def test_uk_ez_and_the_fed_do_not_set_a_signed_factor() -> None:
    assert policy_pressure("英国央行加息25个基点") is None
    assert policy_pressure("欧洲央行降息") is None
    assert policy_pressure("美联储意外加息") is None
    assert "does not set an EURGBP factor" in str(policy_context("英国央行加息25个基点"))
    assert "global liquidity" in str(policy_context("美联储意外加息"))
    assert "does not set an EURGBP factor" in str(policy_context("BoE unexpectedly dovish"))


def test_an_unrelated_flash_has_no_policy_context() -> None:
    assert policy_pressure("澳大利亚总理发表讲话") is None
    assert policy_context("市场波动加大") is None


def test_a_mixed_hike_and_cut_is_left_out() -> None:
    assert policy_context("英国央行加息还是降息仍不确定") is None
