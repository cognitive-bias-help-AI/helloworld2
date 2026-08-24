import copy

import pytest

from app.domain.stock_scope import AssetType, InstrumentCandidate
from app.orchestration.nodes.s0 import make_nodes
from app.orchestration.runtime import ReviewRequestContext
from app.schemas.frozen import SourceTrace, StockCandidate
from app.ui_bridge import _project_structured_intake
from tests.s0.fakes import FixtureStockResolver
from tests.s0.runtime_fixtures import deps, initial_state


def instrument(**changes) -> InstrumentCandidate:
    values = {
        "code": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "asset_type": AssetType.COMMON_STOCK,
        "is_delisted": False,
        "is_managed": False,
    }
    return InstrumentCandidate(**(values | changes))


def target_body(
    *,
    selected_code="005930",
    name="삼성전자",
    market="KOSPI",
):
    return {
        "schema_version": "hybrid_intake/v1",
        "masked_intake": {
            "mode": "SURVEY_FIRST",
            "target": {
                "selected_code": selected_code,
                "name": name,
                "market": market,
                "source": SourceTrace.SURVEY.value,
            },
            "structured": [],
            "free_text": [],
        },
        "masked_input": "",
        "masked_security_input": name or "",
    }


async def run_n2(body, resolver):
    runtime_deps = deps(resolver=resolver)
    input_id = await runtime_deps.review_store.put_input("run-s0", body)
    before = copy.deepcopy(await runtime_deps.review_store.get_input(input_id))
    patch = await make_nodes(runtime_deps)["n2"](
        initial_state() | {"input_id": input_id}
    )
    after = await runtime_deps.review_store.get_input(input_id)
    return patch, runtime_deps, before, after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "expected_code"),
    [
        (instrument(), "005930"),
        (
            instrument(code="03473K", name="SK우", asset_type=AssetType.PREFERRED_STOCK),
            "03473K",
        ),
        (
            instrument(code="0126Z0", name="삼성에피스홀딩스"),
            "0126Z0",
        ),
        (instrument(is_managed=True), "005930"),
    ],
)
async def test_explicit_supported_target_is_canonicalized_without_fuzzy_or_llm(
    candidate, expected_code
):
    fuzzy = StockCandidate(
        code="000660", name="SK하이닉스", market="KOSPI", match_kind="exact_name", score=1.0
    )
    resolver = FixtureStockResolver({"": [fuzzy]}, {expected_code: [candidate]})
    body = target_body(selected_code=expected_code)

    patch, runtime_deps, before, after = await run_n2(body, resolver)

    assert patch["node_results"] == ["n2:ok"]
    assert patch["stock"] == {
        "code": candidate.code,
        "name": candidate.name,
        "market": candidate.market,
        "match_kind": "exact_code",
        "score": 1.0,
        "is_delisted": False,
        "is_managed": candidate.is_managed,
    }
    assert resolver.resolve_exact_calls == [expected_code]
    assert resolver.resolve_calls == []
    assert runtime_deps.model_gateway.calls == []
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (instrument(asset_type=AssetType.ETF), "out_of_scope"),
        (instrument(asset_type=AssetType.ETN), "out_of_scope"),
        (instrument(asset_type=AssetType.SPAC), "out_of_scope"),
        (instrument(asset_type=AssetType.OTHER), "out_of_scope"),
        (instrument(is_delisted=True), "out_of_scope"),
    ],
)
async def test_explicit_unsupported_target_is_rejected_without_fallback(candidate, reason):
    resolver = FixtureStockResolver({"": []}, {candidate.code: [candidate]})

    patch, _, _, _ = await run_n2(target_body(selected_code=candidate.code), resolver)

    assert patch == {"node_results": [f"n2:block:{reason}"]}
    assert resolver.resolve_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "exact_rows", "expected_exact_calls"),
    [
        (target_body(selected_code="123"), {}, []),
        ((lambda body: body | {"masked_intake": body["masked_intake"] | {"target": None}})(target_body(selected_code="005930")), {}, []),
        (target_body(selected_code="005930"), {}, ["005930"]),
    ],
)
async def test_explicit_invalid_or_nonexistent_target_never_falls_back(
    body, exact_rows, expected_exact_calls
):
    resolver = FixtureStockResolver({"": []}, exact_rows)

    patch, _, _, _ = await run_n2(body, resolver)

    assert patch == {"node_results": ["n2:block:stock_unresolved"]}
    assert resolver.resolve_exact_calls == expected_exact_calls
    expected_resolve_calls = [("", 5)] if body["masked_intake"]["target"] is None else []
    assert resolver.resolve_calls == expected_resolve_calls


@pytest.mark.asyncio
async def test_name_only_target_resolves_one_candidate_without_exact_lookup():
    candidate = StockCandidate(
        code="005930", name="삼성전자", market="KOSPI", match_kind="exact_name", score=1.0,
        is_delisted=False, is_managed=False,
    )
    resolver = FixtureStockResolver({"삼성전자": [candidate]}, {})

    patch, _, _, _ = await run_n2(target_body(selected_code=None), resolver)

    assert patch["node_results"] == ["n2:ok"]
    assert patch["stock"] == candidate.model_dump()
    assert resolver.resolve_calls == [("삼성전자", 5)]
    assert resolver.resolve_exact_calls == []


@pytest.mark.asyncio
async def test_name_only_target_uses_stock_choice_hitl_for_multiple_candidates(monkeypatch):
    candidates = [
        StockCandidate(code="005930", name="삼성전자", market="KOSPI", match_kind="exact_name", score=1.0),
        StockCandidate(code="005935", name="삼성전자우", market="KOSPI", match_kind="prefix", score=0.8),
    ]
    resolver = FixtureStockResolver({"삼성전자": candidates}, {})
    runtime_deps = deps(resolver=resolver)
    input_id = await runtime_deps.review_store.put_input("run-s0", target_body(selected_code=None))

    import app.orchestration.nodes.s0 as s0_module

    monkeypatch.setattr(s0_module, "interrupt", lambda payload: {"selected_code": "005935"})
    patch = await make_nodes(runtime_deps)["n2"](initial_state() | {"input_id": input_id})

    assert patch["node_results"] == ["n2:ok"]
    assert patch["stock"]["code"] == "005935"
    assert resolver.resolve_calls == [("삼성전자", 5)]


@pytest.mark.asyncio
async def test_frontend_name_intake_projects_through_n0_and_resolves_at_n2():
    candidate = StockCandidate(
        code="005930", name="삼성전자", market="KOSPI", match_kind="exact_name", score=1.0,
    )
    resolver = FixtureStockResolver({"삼성전자": [candidate]}, {})
    runtime_deps = deps(resolver=resolver)
    nodes = make_nodes(runtime_deps)
    intake = _project_structured_intake({"mode": "SURVEY_FIRST", "target": {"name": "삼성전자"}})
    runtime = type("Runtime", (), {"context": ReviewRequestContext(intake=intake)})()

    n0_patch = await nodes["n0"](initial_state(), runtime)
    n2_patch = await nodes["n2"](initial_state() | n0_patch)

    assert n2_patch["node_results"] == ["n2:ok"]
    assert n2_patch["stock"]["code"] == "005930"
    assert resolver.resolve_calls == [("삼성전자", 5)]


@pytest.mark.asyncio
async def test_graph_interrupt_is_logged_as_interrupt_not_failure(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_DEBUG_LOGS", "1")
    import app.orchestration.nodes.s0 as s0_module

    class GraphInterrupt(Exception):
        pass

    def raise_interrupt(_payload):
        raise GraphInterrupt()

    monkeypatch.setattr(s0_module, "interrupt", raise_interrupt)
    candidates = [
        StockCandidate(code="005930", name="삼성전자", market="KOSPI", match_kind="exact_name", score=1.0),
        StockCandidate(code="005935", name="삼성전자우", market="KOSPI", match_kind="prefix", score=0.8),
    ]
    resolver = FixtureStockResolver({"삼성전자": candidates}, {})
    runtime_deps = deps(resolver=resolver)
    input_id = await runtime_deps.review_store.put_input("run-s0", target_body(selected_code=None))

    with pytest.raises(BaseException) as raised:
        await make_nodes(runtime_deps)["n2"](initial_state() | {"input_id": input_id})

    assert type(raised.value).__name__ == "GraphInterrupt"
    diagnostic = capsys.readouterr().err
    assert '[graph] INTERRUPT' in diagnostic
    assert '[graph] FAIL' not in diagnostic


@pytest.mark.asyncio
async def test_duplicate_or_wrong_code_exact_results_are_contract_violations_without_hitl():
    resolver = FixtureStockResolver(
        {}, {"005930": [instrument(), instrument(name="삼성전자 canonical duplicate")]}
    )

    duplicate_patch, _, _, _ = await run_n2(target_body(), resolver)

    assert duplicate_patch == {"node_results": ["n2:block:contract_violation"]}
    assert resolver.resolve_calls == []

    wrong = instrument(code="000660", name="SK하이닉스")
    wrong_resolver = FixtureStockResolver({}, {"005930": [wrong]})
    wrong_patch, _, _, _ = await run_n2(target_body(), wrong_resolver)
    assert wrong_patch == {"node_results": ["n2:block:contract_violation"]}


@pytest.mark.asyncio
async def test_client_metadata_mismatch_uses_canonical_metadata_without_warning_channel():
    resolver = FixtureStockResolver({}, {"005930": [instrument()]})

    patch, _, before, after = await run_n2(
        target_body(name="틀린 이름", market="KOSDAQ"), resolver
    )

    assert patch["stock"]["name"] == "삼성전자"
    assert patch["stock"]["market"] == "KOSPI"
    assert patch["node_results"] == ["n2:ok"]
    assert before["masked_intake"]["target"]["name"] == "틀린 이름"
    assert after == before


@pytest.mark.asyncio
async def test_absent_target_with_empty_text_is_unresolved_on_legacy_path():
    resolver = FixtureStockResolver({})
    body = target_body()
    body["masked_intake"]["target"] = None

    patch, _, _, _ = await run_n2(body, resolver)

    assert patch == {"node_results": ["n2:block:stock_unresolved"]}
    assert resolver.resolve_calls == [("", 5)]
    assert resolver.resolve_exact_calls == []
