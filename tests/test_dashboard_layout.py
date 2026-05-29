from __future__ import annotations

from app.web import DASHBOARD_HTML


def test_dashboard_html_has_overview_navigation_and_view_panels() -> None:
    for view in ("overview", "today", "watch", "sources"):
        assert f'data-view-nav="{view}"' in DASHBOARD_HTML
        assert f'data-views="{view}"' in DASHBOARD_HTML
    assert 'view: "overview"' in DASHBOARD_HTML
    assert "function setView" in DASHBOARD_HTML
