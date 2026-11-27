"""Unit and integration tests for GUI components (Phase 8)."""

import os
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from src.database import init_db
from src.gui.main_window import MainWindow

# Ensure offscreen rendering for headless test environments
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_initialization(qapp, tmp_path):
    db_file = tmp_path / "test_gui.db"
    window = MainWindow(db_path=str(db_file))

    assert window.windowTitle() == "Music Library Integrity Manager"
    assert window.tabs.count() == 7

    # Verify tab names
    expected_tabs = ["Dashboard", "Albums", "Import", "Scanner", "Verification", "Export", "History"]
    actual_tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert actual_tabs == expected_tabs

    window.close()


def test_main_window_dashboard_refresh(qapp, tmp_path):
    db_file = tmp_path / "test_gui2.db"
    conn = init_db(db_file)
    with conn:
        cur = conn.execute("INSERT INTO libraries (name, root_path) VALUES (?, ?);", ("MyMusic", "D:\\Music"))
        lib_id = cur.lastrowid
        cur = conn.execute("INSERT INTO albums (library_id, album_rel_path, display_name) VALUES (?, ?, ?);",
                           (lib_id, "Artist\\Album1", "Album1"))
        alb_id = cur.lastrowid
        conn.execute("INSERT INTO file_records (album_id, rel_path, sha256) VALUES (?, ?, ?);",
                     (alb_id, "01.flac", "a" * 64))

    window = MainWindow(db_path=str(db_file))
    window.refresh_dashboard()

    assert "1 albums" in window.lbl_stats.text()
    assert "1 files/tracks" in window.lbl_stats.text()

    window.refresh_albums()
    assert window.tbl_albums.rowCount() == 1
    assert window.tbl_albums.item(0, 1).text() == "Album1"

    window.close()
