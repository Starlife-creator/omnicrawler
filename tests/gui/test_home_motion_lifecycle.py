"""Hidden and closed home pages must release their animation work."""
import pytest
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.home import AmbientHero, HomePage
from omnicrawler.gui.motion_signal import MotionSignal


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    previous = app.property("omnicrawlerReducedMotion")
    previous_signal = MotionSignal.instance().is_reduced
    app.setProperty("omnicrawlerReducedMotion", False)
    MotionSignal.instance().notify(False)
    yield app
    app.setProperty("omnicrawlerReducedMotion", previous)
    MotionSignal.instance().notify(previous_signal)


def test_hero_timer_runs_only_while_visible(app):
    hero = AmbientHero()
    try:
        assert not hero._timer.isActive()
        hero.show()
        app.processEvents()
        assert hero._timer.isActive()
        hero.hide()
        assert not hero._timer.isActive()
        hero.show()
        assert hero._timer.isActive()
        hero.close()
        assert not hero._timer.isActive()
    finally:
        hero.close()
        hero.deleteLater()


def test_reduced_motion_stops_timer_and_can_resume(app):
    hero = AmbientHero()
    try:
        hero.show()
        assert hero._timer.isActive()
        MotionSignal.instance().notify(True)
        assert not hero._timer.isActive()
        MotionSignal.instance().notify(False)
        assert hero._timer.isActive()
    finally:
        hero.close()
        hero.deleteLater()


def test_home_shutdown_does_not_restart_animation_on_late_motion_signal(app):
    home = HomePage()
    try:
        home.show()
        hero = home.findChild(AmbientHero)
        assert hero._timer.isActive()
        home.shutdown()
        home.shutdown()
        assert not hero._timer.isActive()
        MotionSignal.instance().notify(True)
        MotionSignal.instance().notify(False)
        assert not hero._timer.isActive()
    finally:
        home.shutdown()
        home.close()
        home.deleteLater()


def test_accepted_main_window_close_shuts_down_home(app, monkeypatch):
    from omnicrawler.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    window = MainWindow()
    try:
        assert window.close()
        assert window._home._enrich_shutting_down
        assert not window._home.findChild(AmbientHero)._timer.isActive()
    finally:
        window._home.shutdown()
        window.close()
        window.deleteLater()
