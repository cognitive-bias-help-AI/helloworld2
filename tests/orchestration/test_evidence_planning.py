from datetime import UTC, datetime

import httpx
import pytest

from app.gateway.adapters.dart import DartAdapter
from app.gateway.adapters.kiwoom import KiwoomAdapter
from app.gateway.adapters.naver import NaverAdapter
from app.gateway.admission import ProviderAdmissionController
from app.gateway.evidence_gateway import collect_evidence
from app.orchestration.evidence_planning import plan_claim_queries
from app.schemas.frozen import Claim, SourceTrace
from app.store.memory_evidence_store import MemoryEvidenceStore
from providers.dart.corp_code import DartCorpCodeResolver
from providers.kiwoom.core import AdapterResult, Environment, ResultStatus

NOW = datetime(2026, 8, 22, tzinfo=UTC)


class Ids:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return f"01ARZ3NDEKTSV4RRFFQ69G{9200 + self.value:04d}"


class NoCallKiwoomCore:
    async def request(self, request):
        raise AssertionError("build_request must not call the provider core")


class FakeKiwoomCore:
    async def request(self, request):
        return AdapterResult(
            status=ResultStatus.SUCCESS,
            provider="kiwoom",
            tr=request.tr,
            request_params=request.params,
            data=[{
                "stock_code": "005930",
                "date": "20260822",
                "close": "70000",
                "adjusted_price": True,
            }],
        )


def claim(text: str, *, slot_id: int = 4, verifiable: bool = True) -> Claim:
    return Claim(
        claim_id=f"01ARZ3NDEKTSV4RRFFQ69G{9300 + slot_id:04d}",
        slot_id=slot_id,
        user_text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=text,
        verifiable=verifiable,
        origin=SourceTrace.LLM_EXTRACTION,
        created_at=NOW,
    )


def plan(item: Claim):
    from app.orchestration.evidence_planning import plan_claim_queries

    return plan_claim_queries(
        item,
        stock_code="005930",
        stock_name="삼성전자",
        as_of=NOW,
        id_factory=Ids(),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("text", "provider", "endpoint"),
    [
        ("2025 사업보고서 연결 영업이익이 증가했다", "dart", "financial_statement"),
        ("2025 사업보고서 수익성 PER이 낮아졌다", "dart", "financial_indicator"),
        ("유상증자 공시가 발표됐다", "dart", "disclosure_list"),
        ("최근 HBM 공급 확대 뉴스가 있다", "naver", "news_search"),
        ("수정주가 기준 최근 주가가 20% 상승했다", "kiwoom", "daily_price_history"),
        ("외국인 순매수 수량 주 단위가 증가했다", "kiwoom", "investor_flow"),
    ],
)
def test_evidence_need는_deterministic_provider_endpoint로_매핑된다(
    text, provider, endpoint
):
    queries = plan(claim(text))
    assert {(item.provider, item.endpoint) for item in queries} == {(provider, endpoint)}


def test_unknown과_non_verifiable은_Query를_만들지_않는다():
    assert plan(claim("HBM 전망이 좋다")) == ()
    assert plan(claim("최근 뉴스", verifiable=False)) == ()
    assert plan(claim("2025 사업보고서 연결 별도 영업이익 증가")) == ()


def test_system_opposing_search는_need와_독립적으로_counter_intent를_보존한다():
    queries = plan(claim("최근 HBM 공급 확대 뉴스가 있다", slot_id=7))
    assert queries and {item.intent for item in queries} == {"counter"}


def test_naver_params_expansion은_각각_독립_Query다():
    queries = plan(claim("최근 HBM 공급 확대 뉴스가 있다"))
    assert len(queries) == 1
    assert len({item.query_id for item in queries}) == len(queries)


def test_uncurated_stock은_NAVER_params_수만큼_Query를_생성한다():
    from app.orchestration.evidence_planning import plan_claim_queries

    queries = plan_claim_queries(
        claim("최근 산업 뉴스가 있다"),
        stock_code="123456",
        stock_name="테스트기업",
        as_of=NOW,
        id_factory=Ids(),
        clock=lambda: NOW,
    )
    assert len(queries) == 2
    assert [item.params["query"] for item in queries] == ["테스트기업", "123456"]


def test_생성_Query는_각_실제_Adapter_build_request_계약을_통과한다():
    adapters = {
        "dart": DartAdapter(
            "test-placeholder", DartCorpCodeResolver({"005930": "00126380"})
        ),
        "naver": NaverAdapter("test-placeholder", "test-placeholder"),
        "kiwoom": KiwoomAdapter(NoCallKiwoomCore(), environment=Environment.MOCK),
    }
    texts = (
        "2025 사업보고서 연결 영업이익이 증가했다",
        "2025 사업보고서 수익성 PER이 낮아졌다",
        "유상증자 공시가 발표됐다",
        "최근 HBM 공급 확대 뉴스가 있다",
        "수정주가 기준 최근 주가가 20% 상승했다",
        "외국인 순매수 수량 주 단위가 증가했다",
    )
    queries = [query for text in texts for query in plan(claim(text))]

    requests = [adapters[item.provider].build_request(item, NOW) for item in queries]

    assert len(requests) == len(queries) == 6
    assert all(
        request.provider == query.provider
        for request, query in zip(requests, queries, strict=True)
    )


@pytest.mark.asyncio
async def test_three_provider_network_free_vertical_slice는_lineage를_보존한다():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "opendart" in request.url.host:
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "000",
                    "list": [{
                        "rcept_no": "20260822000001",
                        "corp_code": "00126380",
                        "stock_code": "005930",
                        "corp_name": "삼성전자",
                        "report_nm": "유상증자 결정",
                        "rcept_dt": "20260822",
                        "flr_nm": "삼성전자",
                        "corp_cls": "Y",
                        "rm": "",
                    }],
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "items": [{
                    "title": "삼성전자, HBM 공급 확대",
                    "description": "삼성전자(005930)가 공급 확대를 발표했다.",
                    "link": "https://n.news.naver.com/mnews/article/001/777",
                    "originallink": "https://example.com/news/777",
                    "pubDate": "Sat, 22 Aug 2026 09:00:00 +0900",
                }]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        adapters = {
            "dart": DartAdapter(
                "test-placeholder",
                DartCorpCodeResolver({"005930": "00126380"}),
                client=client,
            ),
            "naver": NaverAdapter(
                "test-placeholder", "test-placeholder", client=client
            ),
            "kiwoom": KiwoomAdapter(FakeKiwoomCore(), environment=Environment.MOCK),
        }
        claims = (
            claim("유상증자 공시가 발표됐다"),
            claim("최근 HBM 공급 확대 뉴스가 있다", slot_id=5),
            claim("수정주가 기준 최근 주가가 20% 상승했다", slot_id=8),
        )
        ids = Ids()
        queries = [
            query
            for item in claims
            for query in plan_claim_queries(
                item,
                stock_code="005930",
                stock_name="삼성전자",
                as_of=NOW,
                id_factory=ids,
                clock=lambda: NOW,
            )
        ]
        store = MemoryEvidenceStore()
        await store.put_queries("run-planner", queries)

        result = await collect_evidence(
            run_id="run-planner",
            as_of=NOW,
            queries=queries,
            adapters=adapters,
            evidence_store=store,
            provider_admission=ProviderAdmissionController(
                {name: adapter.max_concurrency for name, adapter in adapters.items()}
            ),
            clock=lambda: NOW,
            id_factory=ids,
            current_external_calls=0,
            external_call_limit=25,
        )

        assert result.external_calls == 3
        assert {call.query_id for call in result.provider_calls} == {
            query.query_id for query in queries
        }
        evidence_ids = await store.evidence_ids_for_queries(
            [query.query_id for query in queries]
        )
        evidence = await store.get_many(evidence_ids)
        assert {item.source_type for item in evidence} == {"dart", "news", "quote"}
        linked = [
            await store.evidence_ids_for_queries([query.query_id]) for query in queries
        ]
        assert all(linked)
    finally:
        await client.aclose()
