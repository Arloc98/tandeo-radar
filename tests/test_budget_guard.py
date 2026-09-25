"""A public demo must not be able to drain the Tavily plan.

Anyone who can open the page can press "Actualizar en vivo", and one run costs real
credits. The guard reads Tavily's own meter before spending, so the cap holds even
on a stateless deployment where no counter survives between requests.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import collector, config, main


@pytest.fixture(autouse=True)
def _sin_llamadas_reales(monkeypatch):
    """Nothing here touches Tavily or the model."""
    monkeypatch.setattr(collector, "collect", lambda *a, **kw: ([], 0))
    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "k")
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "k")


def _refresh():
    return TestClient(main.app).post("/refresh?geocode=false").json()


def test_a_ceiling_of_zero_disables_the_guard(monkeypatch):
    """Local development must not need a meter call to run the pipeline."""
    monkeypatch.setattr(config, "TAVILY_USAGE_CEILING", 0)
    monkeypatch.setattr(collector.config, "TAVILY_USAGE_CEILING", 0)

    def _explota():
        raise AssertionError("the meter must not be read when the guard is off")

    monkeypatch.setattr(collector, "plan_usage", _explota)

    assert _refresh().get("budget_exhausted") is None


def test_usage_below_the_ceiling_lets_the_run_through(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_USAGE_CEILING", 300)
    monkeypatch.setattr(collector.config, "TAVILY_USAGE_CEILING", 300)
    monkeypatch.setattr(collector, "plan_usage", lambda: 246)

    assert _refresh().get("budget_exhausted") is None


def test_reaching_the_ceiling_refuses_before_spending(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_USAGE_CEILING", 300)
    monkeypatch.setattr(collector.config, "TAVILY_USAGE_CEILING", 300)
    monkeypatch.setattr(collector, "plan_usage", lambda: 300)

    def _no_gastar(*a, **kw):
        raise AssertionError("the collector must not run once the ceiling is reached")

    monkeypatch.setattr(collector, "collect", _no_gastar)

    body = _refresh()
    assert body["budget_exhausted"] is True
    assert body["budget_used"] == 300
    assert body["budget_ceiling"] == 300
    assert body["events"] == 0
    assert body["items"] == []


def test_an_unreadable_meter_fails_closed(monkeypatch):
    """Failing open is exactly the failure the ceiling exists to prevent."""
    monkeypatch.setattr(config, "TAVILY_USAGE_CEILING", 300)
    monkeypatch.setattr(collector.config, "TAVILY_USAGE_CEILING", 300)

    def _caido():
        raise RuntimeError("meter unreachable")

    monkeypatch.setattr(collector, "plan_usage", _caido)

    def _no_gastar(*a, **kw):
        raise AssertionError("an unreadable meter must stop the run, not wave it through")

    monkeypatch.setattr(collector, "collect", _no_gastar)

    assert _refresh()["budget_exhausted"] is True
