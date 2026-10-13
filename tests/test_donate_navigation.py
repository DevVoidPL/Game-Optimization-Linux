from pathlib import Path

from game_optimization_linux.controllers.app_controller import AppController


ROOT = Path(__file__).resolve().parents[1]
QML_ROOT = ROOT / "src" / "game_optimization_linux" / "qml"


def test_donate_is_a_valid_page() -> None:
    assert AppController._normalize_page("donate") == "donate"
    assert AppController._normalize_page("DONATE") == "donate"


def test_donate_is_above_settings_in_sidebar() -> None:
    sidebar = (QML_ROOT / "components" / "Sidebar.qml").read_text(encoding="utf-8")
    assert sidebar.index('"page": "donate"') < sidebar.index('"page": "settings"')


def test_donate_page_uses_exact_external_links() -> None:
    page = (QML_ROOT / "pages" / "DonatePage.qml").read_text(encoding="utf-8")
    assert "https://buymeacoffee.com/voiddeveloperpl" in page
    assert "https://www.paypal.com/paypalme/GameOptimation" in page
    assert "Qt.openUrlExternally" in page


def test_donate_is_wired_into_desktop_stack() -> None:
    main = (QML_ROOT / "Main.qml").read_text(encoding="utf-8")
    assert 'Qt.resolvedUrl("pages/DonatePage.qml")' in main
    assert "donatePageLoader" in main
