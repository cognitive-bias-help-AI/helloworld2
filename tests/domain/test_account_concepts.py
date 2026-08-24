from app.domain.account_concepts import (
    AccountConcept,
    accepted_dart_account_names,
    resolve_account_concepts,
)


def test_매출과_매출액은_같은_REVENUE_concept로_resolve된다():
    assert resolve_account_concepts("매출이 증가했다") == (AccountConcept.REVENUE,)
    assert resolve_account_concepts("매출액이 증가했다") == (AccountConcept.REVENUE,)
    assert accepted_dart_account_names((AccountConcept.REVENUE,)) == ("매출액",)


def test_영업이익과_순이익은_명시된_account_concept로_resolve된다():
    assert resolve_account_concepts("영업이익이 증가했다") == (AccountConcept.OPERATING_PROFIT,)
    assert resolve_account_concepts("순이익이 감소했다") == (AccountConcept.NET_INCOME,)


def test_알수없는_회계용어는_추측하지_않는다():
    assert resolve_account_concepts("조정 EBITDA가 증가했다") == ()


def test_명시적으로_허용된_DART_account만_채택한다():
    assert accepted_dart_account_names((AccountConcept.OPERATING_PROFIT,)) == ("영업이익",)
    assert accepted_dart_account_names((AccountConcept.NET_INCOME,)) == ("당기순이익",)
