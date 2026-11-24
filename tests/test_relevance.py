from app.errors import LLMError
from app.schemas import Platform, ProfileCandidate
from app.services.relevance_service import Draft, RelevanceService, compute_signals, grounded
from tests.conftest import FakeLLM


def draft(did, text, platform=Platform.INSTAGRAM):
    member = ProfileCandidate(
        id=did,
        platform=platform,
        url=f"https://x.test/{did}",
        handle=did,
        name="N",
        kind="unknown",
        title=text,
    )
    return Draft(id=did, members=[member])


def test_grounded_requires_verbatim_quote():
    text = "Die-hard football fan living in Washington, D.C."
    assert grounded("football fan", text)
    assert grounded("WASHINGTON, D.C.", text)
    assert not grounded("season ticket holder", text)
    assert not grounded("ab", text)


def test_signals_need_city_level_location(intent):
    assert compute_signals("Football fan in the DMV", intent).location_level == "city"
    assert compute_signals("Football fan, District of Columbia", intent).location_level == "state"
    assert compute_signals("Football fan in the United States", intent).location_level == "country"
    assert compute_signals("dc comics fan", intent).location_level is None


async def test_rules_mode_requires_interest_and_location(intent):
    service = RelevanceService(None, batch_size=10)
    drafts = [
        draft("a", "Football fan from Washington DC"),
        draft("b", "Football fan from Chicago"),
        draft("c", "Washington DC photographer"),
        draft("d", "Gardening tips"),
    ]
    results = await service.assess(drafts, intent)
    assert results["a"].accepted and results["a"].method == "rules"
    assert not results["b"].accepted
    assert not results["c"].accepted
    assert "d" not in results


async def test_hallucinated_location_quote_is_discarded(intent):
    def handler(system, payload):
        return {
            "results": [
                {
                    "id": "a",
                    "entity_type": "individual",
                    "relevant": True,
                    "interest_quote": "football",
                    "location_quote": "lives in Washington DC",
                    "confidence": 0.95,
                }
            ]
        }

    results = await RelevanceService(FakeLLM(handler), 10).assess(
        [draft("a", "Football fan from Chicago")], intent
    )
    assert not results["a"].accepted
    assert not results["a"].location_match


async def test_grounded_llm_quote_can_supply_location(intent):
    def handler(system, payload):
        return {
            "results": [
                {
                    "id": "a",
                    "entity_type": "individual",
                    "relevant": True,
                    "interest_quote": "football",
                    "location_quote": "Capitol Hill",
                    "confidence": 0.8,
                }
            ]
        }

    results = await RelevanceService(FakeLLM(handler), 10).assess(
        [draft("a", "Sunday football at Capitol Hill with friends")], intent
    )
    assert results["a"].accepted
    assert results["a"].location_level == "city"
    assert 'location: "Capitol Hill"' in results["a"].evidence


async def test_articles_and_low_confidence_are_rejected(intent):
    def handler(system, payload):
        return {
            "results": [
                {
                    "id": "a",
                    "entity_type": "article",
                    "relevant": True,
                    "interest_quote": "football",
                    "location_quote": "Washington DC",
                    "confidence": 0.9,
                },
                {
                    "id": "b",
                    "entity_type": "individual",
                    "relevant": True,
                    "interest_quote": "football",
                    "location_quote": "Washington DC",
                    "confidence": 0.3,
                },
            ]
        }

    drafts = [draft("a", "football news Washington DC"), draft("b", "football fan Washington DC")]
    results = await RelevanceService(FakeLLM(handler), 10).assess(drafts, intent)
    assert not results["a"].accepted
    assert not results["b"].accepted


async def test_llm_failure_degrades_to_rules(intent):
    def broken(system, payload):
        raise LLMError("timeout")

    results = await RelevanceService(FakeLLM(broken), 10).assess(
        [draft("a", "Football fan from Washington DC")], intent
    )
    assert results["a"].accepted and results["a"].method == "rules"


async def test_items_are_batched(intent):
    llm = FakeLLM(lambda s, p: {"results": []})
    drafts = [draft(f"d{i}", "Football fan Washington DC") for i in range(25)]
    await RelevanceService(llm, batch_size=10).assess(drafts, intent)
    assert [len(c["items"]) for c in llm.calls] == [10, 10, 5]
