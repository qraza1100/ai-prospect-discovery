# Test plan

The structure loosely follows ISO/IEC/IEEE 29119-3. A local Ollama model (qwen3.5:4b) wrote the first draft of the scope, levels and criteria sections, which were then checked and corrected. The traceability matrix was written by hand from the code.

## Scope

The tests cover the discovery pipeline from request validation to the JSON response: query understanding, per-platform URL handling, search and LLM clients, website enrichment, consolidation, relevance scoring and the HTTP layer. They also cover the security controls around outbound fetching and inbound requests.

No test contacts a real network service. Search providers, LLMs, the YouTube API and websites are replaced with fakes or mocked HTTP transports (`httpx.MockTransport`, and `httpx2.MockTransport` for the Anthropic SDK). All names, handles, domains and phone numbers in the fixtures are synthetic.

## Test levels

| Level | Where | What it exercises |
|---|---|---|
| Unit | `tests/test_validators.py`, `test_platforms.py`, `test_query_service.py`, `test_safe_fetcher.py`, `test_extraction_and_dedup.py`, `test_relevance.py`, `test_cache_and_rate_limit.py`, `test_clients.py` | One module at a time with fakes for its collaborators |
| API / integration | `tests/test_prospect_discovery.py` | The whole pipeline through FastAPI `TestClient`, wired from real services with fake search, fake LLM and a mocked web transport |
| Live (manual) | `scripts/run_test_queries.py`, `scripts/benchmark_llm.py` | The five assessment queries and LLM latency against real APIs; needs keys and is not part of CI |

## Requirement traceability

| Requirement (assessment section) | Implementation | Tests |
|---|---|---|
| One endpoint `POST /prospect-discovery` on port 4000 (2) | `app/main.py`, `app/config.py` | `test_end_to_end_discovery_returns_consolidated_evidenced_prospects` |
| Validate input, handle invalid queries (2) | `ProspectDiscoveryRequest`, `QueryService` | `test_invalid_payloads_return_structured_422`, `test_non_prospect_query_returns_400`, `test_invalid_query_is_rejected` |
| Extract interest, audience, city, state, country (4.1) | `app/services/query_service.py` | `test_llm_intent_is_used_and_cached`, `test_rule_parser_handles_assessment_queries` (all five queries), `test_rule_parser_country_only` |
| No hardcoded search queries (4.1) | adapters build queries from the intent | `test_queries_are_site_restricted_and_use_intent`, `test_business_queries_include_company_pages` |
| Nine platforms, independent modules, extensible (3) | `app/platforms/*`, `PlatformRegistry` | `test_profile_urls_are_canonicalised`, `test_non_profile_urls_are_rejected`, `test_names_come_from_titles` |
| Official APIs where available (4.2) | `YouTubeDataClient` | `test_youtube_uses_official_api_when_configured` |
| Profiles, not search-result URLs (3) | `GoogleAdapter`, listicle filter | `test_non_profile_urls_are_rejected`, `test_listicle_titles_are_detected`, end-to-end assertions on links |
| Consolidate profiles, avoid wrong merges (4.3) | `app/services/deduplication_service.py` | `test_same_handle_and_name_merge_across_platforms`, `test_same_name_alone_never_merges`, `test_same_handle_with_different_names_does_not_merge`, `test_instagram_and_threads_share_handles`, `test_cluster_uses_website_links_and_shared_contacts`, `test_cluster_refuses_two_accounts_on_one_platform`, `test_matching_handle_and_name_consolidates_x_profile` |
| Public contacts only, validated, never guessed (4.4) | `app/utils/validators.py`, `parse_page` | `test_normalize_email`, `test_extract_emails_*`, `test_normalize_phone_requires_valid_number`, `test_extract_phones_needs_context_or_plus_prefix`, `test_whatsapp_number_from_url`, `test_parse_page_collects_owner_published_facts`, `test_parse_page_drops_directory_style_link_lists`, `test_parse_page_drops_mass_email_lists` |
| Relevance on interest and location, no match on a name alone (4.5) | `app/services/relevance_service.py` | `test_rules_mode_requires_interest_and_location`, `test_hallucinated_location_quote_is_discarded`, `test_grounded_llm_quote_can_supply_location`, `test_articles_and_low_confidence_are_rejected`, `test_signals_need_city_level_location` |
| Output format rules (5) | `app/schemas.py`, `social_links()` | end-to-end test validates the body against `ProspectDiscoveryResponse` and checks non-empty `social_link`, no duplicate links, `mailto:` / `tel:` / `wa.me` forms |
| Platform failures handled independently (2, 7) | `ProspectService._run_adapter` | `test_platform_failure_is_isolated`, `test_no_sources_configured_returns_503` |
| Timeouts, retries, rate-limit handling (7) | `send_with_retry`, `asyncio.timeout` | `test_send_with_retry_retries_then_fails_cleanly`, `test_send_with_retry_does_not_retry_client_errors`, `test_send_with_retry_recovers_after_rate_limit` |
| Caching, no repeated calls (7) | `AsyncTTLCache`, response cache | `test_cache_single_flight_and_expiry`, `test_cache_does_not_store_failures`, `test_responses_are_cached`, `test_llm_intent_is_used_and_cached` |
| Do not bypass access restrictions (7, 11) | `RobotsPolicy`, adapters never fetch social pages | `test_robots_policy` |
| Protect credentials, secure handling (11) | `SecretStr`, header-based keys, error handlers | `test_youtube_uses_official_api_when_configured` (key in header, not URL), `test_unexpected_errors_do_not_leak_details`, `test_send_with_retry_does_not_retry_client_errors` |
| Outbound request safety | `SafeFetcher` | `test_is_public_ip`, `test_unsafe_urls_are_refused_before_any_request`, `test_hostname_resolving_to_private_address_is_refused`, `test_connection_is_pinned_to_checked_address`, `test_redirect_to_internal_address_is_refused`, `test_redirects_are_followed_with_limit`, `test_non_html_and_oversized_bodies` |
| Inbound abuse protection | rate limiter, API key, headers | `test_rate_limit_returns_429_with_retry_after`, `test_api_key_is_enforced_when_configured`, `test_security_headers_health_and_docs`, `test_rate_limiter_window` |
| LLM integration with structured output and fallback (7, 8) | `app/services/llm_service.py` | `test_anthropic_client_uses_structured_output`, `test_anthropic_incomplete_answers_are_errors`, `test_gemini_client_sends_schema_and_parses_text`, `test_openai_client_uses_strict_json_schema`, `test_fallback_chain_uses_second_client`, `test_llm_failure_falls_back_to_rules`, `test_llm_failure_degrades_to_rules`, `test_works_without_llm_using_rules` |
| Prompt-injection hardening | query wrapped as data, term sanitising | `test_llm_prompt_wraps_query_as_data`, `test_sanitize_term_strips_search_operators`, `test_llm_output_is_sanitised` |

## Environment

Python 3.11 or newer with the pinned packages in `requirements-dev.txt`. `pytest-asyncio` runs in auto mode. Commands:

```powershell
python -m pytest --cov=app
ruff check .
mypy app
```

## Entry and exit criteria

Entry: dependencies install cleanly and `ruff check .` passes.

Exit: every test passes, `mypy app` reports no issues in strict mode, and `ruff check .` reports none. At the time of writing that holds with 174 tests and about 90% line coverage.

## Out of scope

- Live calls to search, LLM and YouTube APIs. Run `scripts/run_test_queries.py` for that.
- Precision and recall of real discovery results. This needs a labelled set of profiles per query, which does not exist yet.
- Load and soak testing.
