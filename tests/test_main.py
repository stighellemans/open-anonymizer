from __future__ import annotations

from open_anonymizer import main as main_module


def test_main_releases_backend_resources_on_shutdown(monkeypatch) -> None:
    events: list[str] = []
    scheduled: list[int] = []

    class FakeApplication:
        def __init__(self, argv):
            del argv

        def setApplicationName(self, value: str) -> None:
            events.append(f"app-name:{value}")

        def setOrganizationName(self, value: str) -> None:
            events.append(f"org-name:{value}")

        def setStyleSheet(self, value: str) -> None:
            assert value
            events.append("stylesheet")

        def setWindowIcon(self, value) -> None:
            assert value is not None
            events.append("icon")

        def exec(self) -> int:
            events.append("exec")
            return 17

    class FakeWindow:
        def __init__(self, *, show_startup_setup=False) -> None:
            events.append(f"setup:{show_startup_setup}")

        def show(self) -> None:
            events.append("show")

        def schedule_background_backend_warmup(self, delay: int) -> None:
            scheduled.append(delay)

    monkeypatch.setattr(main_module, "QApplication", FakeApplication)
    monkeypatch.setattr(main_module, "MainWindow", FakeWindow)
    monkeypatch.setattr(main_module, "application_icon", lambda: object())
    monkeypatch.setattr(
        "open_anonymizer.services.deduce_backend.release_backend_resources",
        lambda: events.append("cleanup"),
    )

    result = main_module.main()

    assert result == 17
    assert "setup:True" in events
    assert events.index("show") < events.index("exec")
    assert scheduled == [main_module.STARTUP_BACKEND_WARMUP_DELAY_MS]
    assert events[-1] == "cleanup"
