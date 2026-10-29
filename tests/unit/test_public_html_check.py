"""Keep local measurement honest and report artifacts free of visited URLs."""

import json

import pytest

from scripts.check_public_html import (
    NetworkObservation,
    arguments,
    background_start_check,
    summarize,
    validate_base_url,
    validate_path,
)


@pytest.mark.parametrize("url", ["http://127.0.0.1:5006/", "http://localhost:5006", "http://[::1]:5006"])
def test_default_targets_are_loopback_only(url):
    assert validate_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://arctos.example",
        "http://localhost.example",
        "file:///tmp/demo.html",
        "http://user:secret@localhost:5006",  # pragma: allowlist secret -- synthetic URL credentials
    ],
)
def test_rejects_remote_or_credentialed_targets_by_default(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_remote_requires_explicit_opt_in_and_origin_has_no_query():
    assert validate_base_url("https://staging.example", allow_remote=True) == "https://staging.example"
    with pytest.raises(ValueError):
        validate_base_url("https://staging.example?token=private", allow_remote=True)


@pytest.mark.parametrize(
    "path", ["https://elsewhere.example", "//elsewhere.example", "/\\elsewhere", "/event\n", "/#event"]
)
def test_paths_cannot_change_origin_or_use_mockup_hash_routes(path):
    with pytest.raises(ValueError):
        validate_path(path)


def test_route_query_and_deployment_prefix_are_preserved():
    assert (
        validate_path("/arctos/login?next=/arctos/demo-open/register")
        == "/arctos/login?next=/arctos/demo-open/register"
    )


def test_cli_preserves_explicit_readiness_criteria():
    args = arguments(["--public-only", "--event-ready-selector", "main h1", "--event-ready-selector", "main time"])
    assert args.event_ready_selector == ["main h1", "main time"]
    assert args.public_only
    with pytest.raises(SystemExit):
        arguments(["--runs", "0"])


def test_browser_waits_are_bounded_below_a_minute():
    assert arguments([]).timeout_ms == 45_000
    for option, value in (("--timeout-ms", "0"), ("--timeout-ms", "60000"), ("--settle-ms", "60000")):
        with pytest.raises(SystemExit):
            arguments([option, value])


def test_report_network_data_does_not_keep_urls_cookies_headers_or_bodies():
    network = NetworkObservation()
    network.request(
        {
            "requestId": "1",
            "type": "Fetch",
            "request": {"url": "https://private.example/private-event/app.wasm?secret=abc", "method": "GET"},
        }
    )
    network.response(
        {
            "requestId": "1",
            "response": {
                "status": 200,
                "headers": {"Content-Encoding": "br", "Set-Cookie": "private-session"},
                "mimeType": "application/wasm",
                "timing": {"receiveHeadersEnd": 15},
            },
        }
    )
    network.finished({"requestId": "1", "encodedDataLength": 2048})
    report = network.summary()
    assert report["encoded_bytes"] == 2048
    assert report["wasm_requests"] == 1
    assert report["by_kind"]["wasm"]["encodings"] == {"br": 1}
    serialized = json.dumps(network.requests) + json.dumps(report)
    for private_text in ("private.example", "private-event", "secret", "private-session", "Set-Cookie"):
        assert private_text not in serialized


def test_failed_requests_and_unexpected_mutations_are_visible():
    network = NetworkObservation()
    network.request(
        {"requestId": "1", "type": "Fetch", "request": {"url": "http://localhost/_api/test", "method": "POST"}}
    )
    network.failed({"requestId": "1"})
    assert network.summary()["non_read_requests"] == 1
    assert network.summary()["by_kind"]["fetch"]["failed"] == 1


def test_verified_markdown_render_post_is_read_only_but_arbitrary_posts_are_not():
    network = NetworkObservation()
    for request_id, path in (("1", "/prefix/_api/render-markdown"), ("2", "/_api/settings")):
        network.request(
            {"requestId": request_id, "type": "Fetch", "request": {"url": "http://localhost" + path, "method": "POST"}}
        )
    assert network.summary()["read_only_markdown_requests"] == 1
    assert network.summary()["non_read_requests"] == 1


def test_only_canceled_markdown_is_exempt_without_hiding_other_read_failures_or_mutations():
    network = NetworkObservation()
    for request_id, method, path, canceled in (
        ("1", "GET", "/_api/events", True),
        ("2", "POST", "/_api/render-markdown", True),
        ("3", "POST", "/_api/settings", True),
        ("4", "GET", "/_api/teams", False),
    ):
        network.request(
            {"requestId": request_id, "type": "Fetch", "request": {"url": "http://localhost" + path, "method": method}}
        )
        network.failed({"requestId": request_id, "canceled": canceled})
    report = network.summary()
    assert report["failed_requests"] == 4
    assert report["canceled_read_requests"] == 2
    assert report["canceled_markdown_requests"] == 1
    assert report["unexpected_failed_requests"] == 3
    assert report["non_read_requests"] == 1


def test_small_lab_samples_report_median_range_and_failures_not_field_percentiles():
    rows = [
        {"page": "event", "cache": "cold", "passed": True, "ready_ms": 100},
        {"page": "event", "cache": "cold", "passed": True, "ready_ms": 200},
        {"page": "event", "cache": "cold", "passed": False, "ready_ms": 9000},
    ]
    report = summarize(rows)[0]
    assert report["samples"] == 3 and report["passed"] == 2
    assert report["ready_ms"] == {"median": 150, "min": 100, "max": 200, "samples": 2}
    assert report["fcp_ms"] is None
    assert "p95" not in json.dumps(report)


def test_only_expected_anonymous_account_probe_401_is_exempt_from_http_failure():
    network = NetworkObservation(allow_anonymous_account_probe=True)
    for request_id, path, status in [("1", "/_api/me", 401), ("2", "/_api/event", 401), ("3", "/_api/me", 500)]:
        network.request(
            {"requestId": request_id, "type": "Fetch", "request": {"url": "http://localhost" + path, "method": "GET"}}
        )
        network.response({"requestId": request_id, "response": {"status": status}})
        network.failed({"requestId": request_id})
    report = network.summary()
    assert report["http_errors"] == 3
    assert report["expected_anonymous_401"] == 1
    assert report["unexpected_http_errors"] == 2
    assert report["failed_requests"] == 3
    assert report["unexpected_failed_requests"] == 2


def test_background_marker_can_enable_expected_account_probe_after_document_arrives():
    network = NetworkObservation()
    network.request(
        {"requestId": "1", "type": "Fetch", "request": {"url": "http://localhost/_api/me", "method": "GET"}}
    )
    network.response({"requestId": "1", "response": {"status": 401}})
    assert network.summary()["unexpected_http_errors"] == 1
    network.allow_anonymous_account_probe = True
    assert network.summary()["expected_anonymous_401"] == 1
    assert network.summary()["unexpected_http_errors"] == 0


def test_cdp_request_timing_uses_navigation_origin_even_before_wasm_completes():
    network = NetworkObservation()
    network.navigation_start = 100.0
    network.request(
        {
            "requestId": "1",
            "type": "Fetch",
            "timestamp": 100.15,
            "request": {"url": "http://localhost/assets/private-app.wasm?secret=hidden", "method": "GET"},
        }
    )
    network.request(
        {
            "requestId": "2",
            "type": "Fetch",
            "timestamp": 100.2,
            "request": {"url": "http://localhost/deployment/_api/leagues?today=2026-10-27", "method": "GET"},
        }
    )
    report = network.summary()
    assert report["first_wasm_request_ms"] == pytest.approx(150)
    assert report["leagues_list_requests"] == 1
    assert report["encoded_bytes"] == 0
    assert "private-app" not in json.dumps(report)
    assert "2026-10-27" not in json.dumps(network.requests)


@pytest.mark.parametrize(
    "enabled,requests,started,fcp,expected,status",
    [
        (False, 0, None, 100, True, "not-enabled"),
        (False, 1, 150, 100, False, "not-enabled"),
        (True, 0, None, 100, True, "not-observed"),
        (True, 1, None, 100, False, "timing-unavailable"),
        (True, 1, 150, 100, True, "after-paint"),
        (True, 1, 50, 100, False, "before-paint"),
        (True, 1, 99, 100, True, "within-paint-precision"),
        (True, 1, 96, 100, True, "within-paint-precision"),
        (True, 1, 95.999, 100, False, "before-paint"),
        (True, 1, 150, None, False, "paint-unavailable"),
    ],
)
def test_background_wasm_is_allowed_after_paint_only_when_document_opts_in(
    enabled, requests, started, fcp, expected, status
):
    sample = {
        "public_background_enabled": enabled,
        "fcp_ms": fcp,
        # The readability probe intentionally occurs after FCP. It must not be
        # used as the Wasm scheduling boundary or valid startup would fail.
        "ready_ms": 300,
        "network": {"wasm_requests": requests, "first_wasm_request_ms": started},
    }
    result = background_start_check(sample)
    assert result["passed"] is expected
    assert result["status"] == status
