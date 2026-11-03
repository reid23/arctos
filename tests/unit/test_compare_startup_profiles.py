"""Keep startup comparisons distinct, reproducible, and free of private data."""

from copy import deepcopy
import json

import pytest

from scripts.check_public_html import PROFILES
from scripts.compare_startup_profiles import (
    DEFAULT_ORIGINS,
    Variant,
    arguments,
    healthy_network,
    journey_passes,
    report_metadata,
    save_report,
    startup_passes,
    summarize,
    variant_order,
)


def network(*, leagues=0, wasm=1, documents=1):
    return {
        "wasm_requests": wasm,
        "leagues_list_requests": leagues,
        "by_kind": {"document": {"requests": documents}},
        "unexpected_failed_requests": 0,
        "unexpected_http_errors": 0,
        "javascript_errors": 0,
        "non_read_requests": 0,
    }


def startup(variant):
    return {
        "ready_ms": 150,
        "runtime_warm_ms": 200,
        "visible_feedback_ms": 100,
        "fcp_ms": 90,
        "document_status": 200,
        "horizontal_overflow": False,
        "public_background_enabled": variant == "public-html",
        "loading_shell_seen": False,
        "public_content_seen": variant == "public-html",
        "background_start": {"passed": True},
        "network_after_startup": network(),
    }


def test_default_comparison_covers_each_variant_and_profile():
    args = arguments([])
    assert {variant.name: variant.origin for variant in args.variants} == DEFAULT_ORIGINS
    assert args.profiles == list(PROFILES)
    report = report_metadata(args)
    assert report["expected_samples"] == 48
    assert not args.skip_journey
    assert not args.include_direct_leagues


def test_cli_selects_profiles_variants_and_optional_direct_leagues():
    args = arguments(
        [
            "--variant",
            "public-html=http://localhost:9000",
            "--profile",
            "fast-mobile",
            "--runs",
            "2",
            "--include-direct-leagues",
            "--skip-journey",
        ]
    )
    assert args.variants == [Variant("public-html", "http://localhost:9000")]
    assert args.profiles == ["fast-mobile"]
    assert report_metadata(args)["expected_samples"] == 8
    assert args.skip_journey


def test_direct_leagues_only_has_12_mobile_samples_and_no_journeys():
    args = arguments(["--direct-leagues-only", "--profile", "mobile-4g"])
    report = report_metadata(args)
    assert args.targets == ["leagues"]
    assert report["expected_samples"] == 12
    with pytest.raises(SystemExit):
        arguments(["--direct-leagues-only", "--include-direct-leagues"])


@pytest.mark.parametrize(
    "options",
    [
        ["--variant", "unknown=http://localhost:5000"],
        ["--variant", "original=https://production.example"],
        ["--variant", "original=http://localhost:5000", "--variant", "original=http://localhost:5001"],
        ["--variant", "original=http://localhost:5000", "--variant", "public-html=http://localhost:5000"],
        ["--events-path", "//external.example"],
        ["--events-path", "/?q=changes-header-route"],
        ["--timeout-ms", "60000"],
        ["--timeout-ms", "0"],
        ["--settle-ms", "60000"],
        ["--width", "100"],
        ["--runs", "0"],
    ],
)
def test_rejects_ambiguous_variants_unsafe_origins_and_unbounded_waits(options):
    with pytest.raises(SystemExit):
        arguments(options)


def test_remote_requires_explicit_opt_in_but_origin_is_omitted_from_report():
    args = arguments(["--variant", "original=https://private-staging.example", "--allow-remote"])
    assert args.variants[0].origin == "https://private-staging.example"
    serialized = json.dumps(report_metadata(args))
    assert "private-staging" not in serialized
    assert "http" not in serialized


def test_variant_order_rotates_without_mutating_input():
    variants = [Variant(name, origin) for name, origin in DEFAULT_ORIGINS.items()]
    orders = [variant_order(variants, run) for run in (1, 2)]
    for position in range(2):
        assert {order[position].name for order in orders} == set(DEFAULT_ORIGINS)
    assert variants == orders[0]  # Rotation does not mutate the input.


@pytest.mark.parametrize("variant", DEFAULT_ORIGINS)
def test_correct_variant_with_useful_content_and_runtime_passes(variant):
    assert startup_passes(startup(variant), variant, "events")


@pytest.mark.parametrize("missing", ["ready_ms", "runtime_warm_ms", "visible_feedback_ms", "fcp_ms"])
def test_feedback_alone_cannot_count_as_useful_readiness(missing):
    row = startup("public-html")
    row[missing] = None
    assert not startup_passes(row, "public-html", "events")


def test_public_html_requires_content_and_after_paint_background_start():
    row = startup("public-html")
    row["background_start"]["passed"] = False
    assert not startup_passes(row, "public-html", "events")
    row["background_start"]["passed"] = True
    row["public_content_seen"] = False
    assert not startup_passes(row, "public-html", "events")


@pytest.mark.parametrize("variant", DEFAULT_ORIGINS)
def test_loading_shell_cannot_pass_as_completed_content(variant):
    row = startup(variant)
    row["loading_shell_seen"] = True
    assert not startup_passes(row, variant, "events")


@pytest.mark.parametrize("wasm,documents", [(0, 1), (2, 1), (1, 2)])
def test_startup_requires_exactly_one_document_and_runtime(wasm, documents):
    row = startup("original")
    row["network_after_startup"] = network(wasm=wasm, documents=documents)
    assert not startup_passes(row, "original", "events")


def test_direct_leagues_requires_its_list_request():
    row = startup("original")
    assert not startup_passes(row, "original", "leagues")
    row["network_after_startup"]["leagues_list_requests"] = 1
    assert startup_passes(row, "original", "leagues")


def test_public_leagues_reads_html_without_requiring_a_duplicate_api_request():
    row = startup("public-html")
    assert startup_passes(row, "public-html", "leagues")
    row["network_after_startup"]["leagues_list_requests"] = 1
    assert not startup_passes(row, "public-html", "leagues")
    row["network_after_startup"]["leagues_list_requests"] = 0
    row["public_content_seen"] = False
    assert not startup_passes(row, "public-html", "leagues")


@pytest.mark.parametrize(
    "field", ["unexpected_failed_requests", "unexpected_http_errors", "javascript_errors", "non_read_requests"]
)
def test_network_failures_are_never_hidden_by_readiness(field):
    row = startup("original")
    row["network_after_startup"][field] = 1
    assert not healthy_network(row["network_after_startup"])
    assert not startup_passes(row, "original", "events")


def test_journey_requires_retained_runtime_and_expected_cache_behavior():
    before, first, after = network(), network(leagues=1), network(leagues=1)
    assert journey_passes("public-html", True, before, first, after)
    assert not journey_passes("public-html", False, before, first, after)
    assert not journey_passes("original", True, before, first, after)
    after["leagues_list_requests"] = 2
    assert journey_passes("original", True, before, first, after)
    assert not journey_passes("public-html", True, before, first, after)
    after["leagues_list_requests"] = 3
    assert not journey_passes("original", True, before, first, after)


@pytest.mark.parametrize("extra", [{"wasm_requests": 2}, {"by_kind": {"document": {"requests": 2}}}])
def test_journey_cannot_pass_after_reloading(extra):
    before, first, after = network(), network(leagues=1), network(leagues=1)
    after.update(extra)
    assert not journey_passes("public-html", True, before, first, after)


def test_summary_keeps_failures_visible_and_excludes_them_from_medians():
    rows = [
        {
            "profile": "fast-mobile",
            "variant": "public-html",
            "target": "events",
            "cache": "cold",
            "passed": True,
            "ready_ms": 500,
            "visible_feedback_ms": 100,
            "runtime_warm_ms": 400,
            "journey": {"first_leagues_ready_ms": 200, "return_leagues_ready_ms": 50},
        },
    ]
    second = deepcopy(rows[0])
    second["ready_ms"] = 700
    rows.append(second)
    failed = deepcopy(rows[0])
    failed.update(passed=False, ready_ms=99999)
    rows.append(failed)
    summary = summarize(rows)[0]
    assert summary["samples"] == 3 and summary["passed"] == 2
    assert summary["ready_ms"] == {"median": 600, "min": 500, "max": 700, "samples": 2}
    assert summary["first_leagues_ready_ms"]["median"] == 200
    assert summary["visible_feedback_ms"]["median"] == 100
    assert summary["fcp_ms"] is None
    assert "p95" not in json.dumps(summary)


@pytest.mark.parametrize(
    "complete,count,failed,expected_pass",
    [(False, 2, False, False), (True, 1, False, False), (True, 2, True, False), (True, 2, False, True)],
)
def test_report_passes_only_with_every_expected_sample(complete, count, failed, expected_pass, tmp_path):
    args = arguments(["--variant", "public-html=http://localhost:5016", "--profile", "mobile-4g", "--runs", "1"])
    report = report_metadata(args)
    report["complete"] = complete
    report["samples"] = [
        {"profile": "mobile-4g", "variant": "public-html", "target": "events", "cache": "cold", "passed": not failed}
        for _ in range(count)
    ]
    save_report(report, tmp_path)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["passed"] is expected_pass
    assert saved["summary"][0]["passed"] == (0 if failed else count)
