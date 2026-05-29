from __future__ import annotations

from app.web import DASHBOARD_HTML


def test_dashboard_html_has_overview_navigation_and_view_panels() -> None:
    for view in ("overview", "today", "watch", "sources"):
        assert f'data-view-nav="{view}"' in DASHBOARD_HTML
        assert f'data-views="{view}"' in DASHBOARD_HTML
    assert 'view: "overview"' in DASHBOARD_HTML
    assert "function setView" in DASHBOARD_HTML


def test_dashboard_overview_has_visual_command_center() -> None:
    for marker in (
        'class="overview-shell"',
        'id="overviewHero"',
        'id="overviewKpis"',
        'id="overviewCategoryMix"',
        'id="overviewSourceHealth"',
        'id="overviewWatchBrief"',
        'class="alerts-panel overview-signals"',
    ):
        assert marker in DASHBOARD_HTML

    for function_name in (
        "function renderOverviewBrief",
        "function renderOverviewCategoryMix",
        "function renderOverviewSourceHealth",
        "function renderOverviewWatchBrief",
    ):
        assert function_name in DASHBOARD_HTML

    assert "alerts.slice(0, 6)" in DASHBOARD_HTML
