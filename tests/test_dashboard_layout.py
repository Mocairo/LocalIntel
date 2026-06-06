from __future__ import annotations

from app.web import DASHBOARD_HTML


def test_dashboard_html_has_overview_navigation_and_view_panels() -> None:
    for view in ("overview", "today"):
        assert f'data-view-nav="{view}"' in DASHBOARD_HTML
        assert f'data-views="{view}"' in DASHBOARD_HTML
    assert 'data-view-nav="watch"' not in DASHBOARD_HTML
    assert 'data-views="watch"' not in DASHBOARD_HTML
    assert 'data-view-nav="sources"' not in DASHBOARD_HTML
    assert 'class="sources-panel"' not in DASHBOARD_HTML
    assert 'view: "overview"' in DASHBOARD_HTML
    assert "function setView" in DASHBOARD_HTML


def test_dashboard_overview_has_visual_command_center() -> None:
    for marker in (
        'class="overview-shell"',
        'id="overviewHero"',
        'id="overviewCategoryPanel"',
        'id="overviewSourcesPanel"',
        'id="overviewTrendPanel"',
        'id="overviewAlertsPanel"',
    ):
        assert marker in DASHBOARD_HTML
    assert 'id="overviewWeeklyPanel"' not in DASHBOARD_HTML
    assert 'id="overviewWatchPanel"' not in DASHBOARD_HTML
    assert 'id="llmPanel"' not in DASHBOARD_HTML
    assert 'id="overviewKpis"' not in DASHBOARD_HTML
    assert 'id="stats"' not in DASHBOARD_HTML

    for function_name in (
        "function renderOverviewBrief",
        "function renderOverviewCategoryMix",
        "function renderOverviewSourceHealth",
    ):
        assert function_name in DASHBOARD_HTML

    assert "alerts.slice(0, 6)" in DASHBOARD_HTML


def test_today_page_has_workbench_layout() -> None:
    for marker in (
        'class="today-workbench"',
        'id="todayHero"',
        'id="todaySidebar"',
        'id="todayQueueSummary"',
        'id="todayQueue"',
        'id="todayMainlines"',
        'id="todayReader"',
        'class="reader-toolbar"',
    ):
        assert marker in DASHBOARD_HTML

    assert 'class="command" data-views="today"' not in DASHBOARD_HTML
    assert 'class="mainline-block" data-views="today"' not in DASHBOARD_HTML
    assert 'class="feed-panel" data-views="today"' not in DASHBOARD_HTML


def test_dashboard_cards_and_detail_drawer_are_reader_friendly() -> None:
    for marker in (
        "overview-command-center",
        "overview-reference-grid",
        "overview-dashboard-grid",
        'class="trend-curve-chart"',
        "function paddedTrendRows",
        "function smoothCurvePath",
        "function renderQueueSummary",
        'class="detail-shell"',
        'class="detail-hero"',
        'class="detail-actionbar"',
        'class="detail-grid"',
    ):
        assert marker in DASHBOARD_HTML

    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in DASHBOARD_HTML
    assert "height: 468px;" in DASHBOARD_HTML
    assert "grid-template-rows: 28px 56px 60px 88px 30px 44px 72px;" in DASHBOARD_HTML
    assert ".intel-card .card-actions {" in DASHBOARD_HTML
    assert "overflow: visible;" in DASHBOARD_HTML
    assert "-webkit-line-clamp: 2;" in DASHBOARD_HTML
    assert ".detail-actionbar span[data-icon]" in DASHBOARD_HTML
    assert ".detail-actionbar svg" in DASHBOARD_HTML


def test_overview_trend_uses_axis_area_chart() -> None:
    for marker in (
        "trend-hero-title",
        "每日情报趋势",
        "trend-curve-wrap",
        "trend-y-axis",
        "trend-grid",
        "trendCurveFill",
        "trendDateTicks",
    ):
        assert marker in DASHBOARD_HTML


def test_today_cards_do_not_render_risk_or_reading_tip_rows() -> None:
    judgement_start = DASHBOARD_HTML.index("function renderJudgement")
    judgement_end = DASHBOARD_HTML.index("async function loadAlerts")
    judgement_body = DASHBOARD_HTML[judgement_start:judgement_end]

    assert "推荐理由" in judgement_body
    assert "风险提示" not in judgement_body
    assert "阅读提示" not in judgement_body


def test_dashboard_uses_uupm_inspired_visual_skin() -> None:
    for marker in (
        'class="uupm-skin"',
        "uupm-dashboard-frame",
        "uupm-surface",
        "uupm-gradient-title",
        "uupm-command-card",
        "uupm-panel-grid",
    ):
        assert marker in DASHBOARD_HTML

    assert "body.uupm-skin > *" not in DASHBOARD_HTML
    assert "body.uupm-skin > .topbar" in DASHBOARD_HTML
    assert "body.uupm-skin > .app-shell" in DASHBOARD_HTML
    assert "function sourceShortName" in DASHBOARD_HTML


def test_overview_matches_reference_home_layout() -> None:
    for marker in (
        'class="app-sidebar-brand"',
        'id="sidebarCollapseBtn"',
        'class="topbar-clock"',
        "overview-reference-grid",
        "overview-status-strip",
        "overview-dashboard-grid",
        "overviewHero",
        "overviewTrendPanel",
        "overviewAlertsPanel",
        "overviewCategoryPanel",
        "overviewSourcesPanel",
        "renderOverviewRuntimeStrip",
    ):
        assert marker in DASHBOARD_HTML

    assert 'id="runtimeGrid"' not in DASHBOARD_HTML
    assert "padding-top: 20px;" in DASHBOARD_HTML
    assert "function setSidebarCollapsed" in DASHBOARD_HTML
    assert "sidebar-collapsed" in DASHBOARD_HTML
    assert 'localStorage.setItem("localIntelSidebarCollapsed"' in DASHBOARD_HTML


def test_overview_uses_unified_icon_system() -> None:
    for marker in (
        "function categoryIconName",
        "function sourceIconName",
        "function sourceIconTone",
        "overview-category-icon",
        "overview-source-icon",
    ):
        assert marker in DASHBOARD_HTML
