from __future__ import annotations

from app.web import DASHBOARD_HTML


def test_dashboard_html_has_overview_navigation_and_view_panels() -> None:
    for view in ("overview", "today", "watch"):
        assert f'data-view-nav="{view}"' in DASHBOARD_HTML
        assert f'data-views="{view}"' in DASHBOARD_HTML
    assert 'data-view-nav="sources"' not in DASHBOARD_HTML
    assert 'class="sources-panel"' not in DASHBOARD_HTML
    assert 'view: "overview"' in DASHBOARD_HTML
    assert "function setView" in DASHBOARD_HTML


def test_dashboard_overview_has_visual_command_center() -> None:
    for marker in (
        'class="overview-shell"',
        'id="overviewHero"',
        'id="overviewCategoryMix"',
        'id="overviewSourceHealth"',
        'id="overviewSourceTrend"',
        'id="overviewWeeklyBrief"',
        'id="overviewWatchBrief"',
        'class="alerts-panel overview-signals"',
    ):
        assert marker in DASHBOARD_HTML
    assert 'id="overviewKpis"' not in DASHBOARD_HTML
    assert 'id="stats"' not in DASHBOARD_HTML

    for function_name in (
        "function renderOverviewBrief",
        "function renderOverviewCategoryMix",
        "function renderOverviewSourceHealth",
        "function renderOverviewWatchBrief",
    ):
        assert function_name in DASHBOARD_HTML

    assert "alerts.slice(0, 6)" in DASHBOARD_HTML


def test_today_page_has_workbench_layout() -> None:
    for marker in (
        'class="today-workbench"',
        'id="todayHero"',
        'id="todaySidebar"',
        'id="todayQueue"',
        'id="todayMainlines"',
        'id="todayReader"',
        'class="reader-toolbar"',
    ):
        assert marker in DASHBOARD_HTML

    assert 'class="command" data-views="today"' not in DASHBOARD_HTML
    assert 'class="mainline-block" data-views="today"' not in DASHBOARD_HTML
    assert 'class="feed-panel" data-views="today"' not in DASHBOARD_HTML
