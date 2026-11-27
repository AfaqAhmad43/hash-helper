"""Main window and view components for Music Library Integrity Manager GUI."""

import os
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import init_db
from src.exporter import generate_master_manifest
from src.importer import commit_import, preview_import
from src.registration import preview_album_registration, register_album
from src.scanner import scan_library
from src.verifier import verify_files


class VerifyWorker(QThread):
    progress = Signal(int, int, str)
    finished_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, db_path: str, library_id: int, scope: str = "library", album_ids: Optional[List[int]] = None):
        super().__init__()
        self.db_path = db_path
        self.library_id = library_id
        self.scope = scope
        self.album_ids = album_ids
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def run(self):
        worker_conn = None
        try:
            # Independent database connection for the worker thread
            worker_conn = init_db(self.db_path)

            def callback(current, total, file_path):
                self.progress.emit(current, total, file_path)
                return not self._is_cancelled

            summary = verify_files(
                conn=worker_conn,
                library_id=self.library_id,
                scope=self.scope,
                album_ids=self.album_ids,
                progress_callback=callback,
                cancel_check=self.is_cancelled,
            )
            self.finished_signal.emit(summary)
        except Exception as ex:
            self.error_signal.emit(str(ex))
        finally:
            if worker_conn:
                worker_conn.close()


class MainWindow(QMainWindow):
    def __init__(self, db_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Music Library Integrity Manager")
        self.resize(1000, 680)

        # Initialize or connect to database
        self.db_path = db_path or "music_integrity.db"
        self.conn = init_db(self.db_path)
        self.active_library_id = self._ensure_default_library()
        self.verify_worker: Optional[VerifyWorker] = None

        self._setup_ui()
        self.refresh_dashboard()

    def _ensure_default_library(self) -> int:
        cur = self.conn.execute("SELECT id FROM libraries ORDER BY id ASC LIMIT 1;")
        row = cur.fetchone()
        if row:
            return row["id"]
        # Default placeholder library
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO libraries (name, root_path) VALUES (?, ?);",
                ("Default Library", str(Path.cwd())),
            )
            return cur.lastrowid

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Create tab screens
        self.dashboard_tab = QWidget()
        self.albums_tab = QWidget()
        self.import_tab = QWidget()
        self.scanner_tab = QWidget()
        self.verify_tab = QWidget()
        self.export_tab = QWidget()
        self.history_tab = QWidget()

        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.albums_tab, "Albums")
        self.tabs.addTab(self.import_tab, "Import")
        self.tabs.addTab(self.scanner_tab, "Scanner")
        self.tabs.addTab(self.verify_tab, "Verification")
        self.tabs.addTab(self.export_tab, "Export")
        self.tabs.addTab(self.history_tab, "History")

        self._build_dashboard_tab()
        self._build_albums_tab()
        self._build_import_tab()
        self._build_scanner_tab()
        self._build_verify_tab()
        self._build_export_tab()
        self._build_history_tab()

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int):
        tab_name = self.tabs.tabText(index)
        if tab_name == "Dashboard":
            self.refresh_dashboard()
        elif tab_name == "Albums":
            self.refresh_albums()
        elif tab_name == "History":
            self.refresh_history()

    # 1. Dashboard Tab
    def _build_dashboard_tab(self):
        layout = QVBoxLayout(self.dashboard_tab)
        self.lbl_lib_info = QLabel("Library Root: Loading...")
        self.lbl_stats = QLabel("Registered: 0 albums, 0 files")

        btn_select_root = QPushButton("Select Library Root...")
        btn_select_root.clicked.connect(self._select_library_root)

        layout.addWidget(self.lbl_lib_info)
        layout.addWidget(self.lbl_stats)
        layout.addWidget(btn_select_root)
        layout.addStretch()

    def _select_library_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Music Library Root")
        if folder:
            with self.conn:
                self.conn.execute(
                    "UPDATE libraries SET root_path = ? WHERE id = ?",
                    (folder, self.active_library_id),
                )
            self.refresh_dashboard()

    def refresh_dashboard(self):
        cur = self.conn.execute("SELECT root_path FROM libraries WHERE id = ?", (self.active_library_id,))
        row = cur.fetchone()
        root = row["root_path"] if row else "None"
        self.lbl_lib_info.setText(f"Library Root: {root}")

        cur = self.conn.execute("SELECT COUNT(*) FROM albums WHERE library_id = ?", (self.active_library_id,))
        album_count = cur.fetchone()[0]

        cur = self.conn.execute(
            """SELECT COUNT(*) FROM file_records f
               JOIN albums a ON f.album_id = a.id
               WHERE a.library_id = ?""",
            (self.active_library_id,),
        )
        file_count = cur.fetchone()[0]

        self.lbl_stats.setText(f"Registered Baseline: {album_count} albums | {file_count} files/tracks")

    # 2. Albums Tab
    def _build_albums_tab(self):
        layout = QVBoxLayout(self.albums_tab)
        self.tbl_albums = QTableWidget()
        self.tbl_albums.setColumnCount(4)
        self.tbl_albums.setHorizontalHeaderLabels(["ID", "Album Name", "Relative Path", "Files"])
        self.tbl_albums.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        btn_refresh = QPushButton("Refresh Albums")
        btn_refresh.clicked.connect(self.refresh_albums)

        layout.addWidget(self.tbl_albums)
        layout.addWidget(btn_refresh)

    def refresh_albums(self):
        cur = self.conn.execute(
            """SELECT a.id, a.display_name, a.album_rel_path, COUNT(f.id) as file_count
               FROM albums a
               LEFT JOIN file_records f ON a.id = f.album_id
               WHERE a.library_id = ?
               GROUP BY a.id
               ORDER BY a.album_rel_path ASC""",
            (self.active_library_id,),
        )
        rows = cur.fetchall()
        self.tbl_albums.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.tbl_albums.setItem(row_idx, 0, QTableWidgetItem(str(r["id"])))
            self.tbl_albums.setItem(row_idx, 1, QTableWidgetItem(r["display_name"] or ""))
            self.tbl_albums.setItem(row_idx, 2, QTableWidgetItem(r["album_rel_path"]))
            self.tbl_albums.setItem(row_idx, 3, QTableWidgetItem(str(r["file_count"])))

    # 3. Import Tab
    def _build_import_tab(self):
        layout = QVBoxLayout(self.import_tab)
        h_layout = QHBoxLayout()

        self.txt_manifest_path = QLineEdit()
        self.txt_manifest_path.setPlaceholderText("Select RapidCRC .sha256 manifest file...")
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_manifest)

        h_layout.addWidget(self.txt_manifest_path)
        h_layout.addWidget(btn_browse)

        self.btn_preview_import = QPushButton("Preview Import")
        self.btn_preview_import.clicked.connect(self._preview_import_clicked)

        self.lbl_import_preview = QLabel("Select a manifest and click 'Preview Import'.")
        self.btn_commit_import = QPushButton("Register Approved Records")
        self.btn_commit_import.setEnabled(False)
        self.btn_commit_import.clicked.connect(self._commit_import_clicked)

        layout.addLayout(h_layout)
        layout.addWidget(self.btn_preview_import)
        layout.addWidget(self.lbl_import_preview)
        layout.addWidget(self.btn_commit_import)
        layout.addStretch()

        self.current_import_preview = None

    def _browse_manifest(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Checksum Manifest", "", "Checksum Files (*.sha256)")
        if file_path:
            self.txt_manifest_path.setText(file_path)

    def _preview_import_clicked(self):
        path = self.txt_manifest_path.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Error", "Please select a valid manifest file.")
            return

        try:
            self.current_import_preview = preview_import(self.conn, self.active_library_id, path)
            p = self.current_import_preview
            text = (
                f"Parsed {p.total_parsed} records:\n"
                f"  - New records: {len(p.new_records)}\n"
                f"  - Skipped duplicates: {len(p.duplicate_records)}\n"
                f"  - Conflicts detected: {len(p.conflict_records)}\n"
                f"  - Malformed lines: {len(p.malformed_lines)}"
            )
            self.lbl_import_preview.setText(text)
            self.btn_commit_import.setEnabled(len(p.new_records) > 0 and len(p.conflict_records) == 0)
        except Exception as ex:
            QMessageBox.critical(self, "Error", f"Failed to preview import: {ex}")

    def _commit_import_clicked(self):
        if not self.current_import_preview:
            return
        try:
            result = commit_import(self.conn, self.active_library_id, self.current_import_preview)
            QMessageBox.information(
                self,
                "Success",
                f"Import complete! {result['imported']} records registered, {result['skipped']} duplicates skipped.",
            )
            self.btn_commit_import.setEnabled(False)
            self.lbl_import_preview.setText("Records successfully registered to baseline.")
            self.refresh_dashboard()
        except Exception as ex:
            QMessageBox.critical(self, "Commit Error", f"Registration failed: {ex}")

    # 4. Scanner Tab
    def _build_scanner_tab(self):
        layout = QVBoxLayout(self.scanner_tab)
        btn_scan = QPushButton("Run Library Scan")
        btn_scan.clicked.connect(self._run_scan)

        self.lbl_scan_summary = QLabel("Click 'Run Library Scan' to detect discrepancies.")
        self.tbl_scanner = QTableWidget()
        self.tbl_scanner.setColumnCount(2)
        self.tbl_scanner.setHorizontalHeaderLabels(["Category", "Item Path"])
        self.tbl_scanner.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addWidget(btn_scan)
        layout.addWidget(self.lbl_scan_summary)
        layout.addWidget(self.tbl_scanner)

    def _run_scan(self):
        report = scan_library(self.conn, self.active_library_id)
        self.lbl_scan_summary.setText(
            f"Physical files: {report.total_physical_files} | "
            f"Missing: {len(report.missing_registered_files)} | "
            f"Unregistered: {len(report.unregistered_files)} | "
            f"New Albums: {len(report.potential_new_albums)}"
        )
        total_rows = len(report.missing_registered_files) + len(report.unregistered_files) + len(report.potential_new_albums)
        self.tbl_scanner.setRowCount(total_rows)

        row_idx = 0
        for m in report.missing_registered_files:
            self.tbl_scanner.setItem(row_idx, 0, QTableWidgetItem("Missing Registered File"))
            self.tbl_scanner.setItem(row_idx, 1, QTableWidgetItem(m))
            row_idx += 1

        for u in report.unregistered_files:
            self.tbl_scanner.setItem(row_idx, 0, QTableWidgetItem("Unregistered File"))
            self.tbl_scanner.setItem(row_idx, 1, QTableWidgetItem(u))
            row_idx += 1

        for a in report.potential_new_albums:
            self.tbl_scanner.setItem(row_idx, 0, QTableWidgetItem("Potential New Album"))
            self.tbl_scanner.setItem(row_idx, 1, QTableWidgetItem(a.album_rel_path))
            row_idx += 1

    # 5. Verification Tab
    def _build_verify_tab(self):
        layout = QVBoxLayout(self.verify_tab)
        h_layout = QHBoxLayout()

        self.btn_start_verify = QPushButton("Verify Entire Library")
        self.btn_start_verify.clicked.connect(self._start_verify)

        self.btn_cancel_verify = QPushButton("Cancel")
        self.btn_cancel_verify.setEnabled(False)
        self.btn_cancel_verify.clicked.connect(self._cancel_verify)

        h_layout.addWidget(self.btn_start_verify)
        h_layout.addWidget(self.btn_cancel_verify)

        self.prog_verify = QProgressBar()
        self.lbl_verify_status = QLabel("Ready.")
        self.tbl_verify = QTableWidget()
        self.tbl_verify.setColumnCount(3)
        self.tbl_verify.setHorizontalHeaderLabels(["Status", "Relative Path", "Details"])
        self.tbl_verify.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addLayout(h_layout)
        layout.addWidget(self.prog_verify)
        layout.addWidget(self.lbl_verify_status)
        layout.addWidget(self.tbl_verify)

    def _start_verify(self):
        self.tbl_verify.setRowCount(0)
        self.prog_verify.setValue(0)
        self.btn_start_verify.setEnabled(False)
        self.btn_cancel_verify.setEnabled(True)
        self.lbl_verify_status.setText("Verifying files...")

        self.verify_worker = VerifyWorker(self.db_path, self.active_library_id, scope="library")
        self.verify_worker.progress.connect(self._on_verify_progress)
        self.verify_worker.finished_signal.connect(self._on_verify_finished)
        self.verify_worker.error_signal.connect(self._on_verify_error)
        self.verify_worker.start()

    def _on_verify_error(self, err_msg: str):
        self.btn_start_verify.setEnabled(True)
        self.btn_cancel_verify.setEnabled(False)
        self.lbl_verify_status.setText(f"Error during verification: {err_msg}")
        QMessageBox.critical(self, "Verification Failure", f"Verification encountered an error:\n{err_msg}")

    def _on_verify_progress(self, current: int, total: int, file_path: str):
        if total > 0:
            self.prog_verify.setMaximum(total)
            self.prog_verify.setValue(current)
            self.lbl_verify_status.setText(f"Checking [{current}/{total}]: {file_path}")

    def _on_verify_finished(self, summary):
        self.btn_start_verify.setEnabled(True)
        self.btn_cancel_verify.setEnabled(False)
        self.lbl_verify_status.setText(
            f"Verification complete: {summary.files_matched} verified, "
            f"{summary.files_changed} changed, {summary.files_missing} missing, "
            f"{summary.files_unreadable} unreadable."
        )

        discrepancies = [r for r in summary.results if r.status != "verified"]
        self.tbl_verify.setRowCount(len(discrepancies))
        for row_idx, r in enumerate(discrepancies):
            self.tbl_verify.setItem(row_idx, 0, QTableWidgetItem(r.status.upper()))
            self.tbl_verify.setItem(row_idx, 1, QTableWidgetItem(r.rel_path))
            self.tbl_verify.setItem(row_idx, 2, QTableWidgetItem(r.error_message or ""))

    def _cancel_verify(self):
        if self.verify_worker:
            self.verify_worker.cancel()
            self.lbl_verify_status.setText("Cancelling verification...")

    # 6. Export Tab
    def _build_export_tab(self):
        layout = QVBoxLayout(self.export_tab)
        h_layout = QHBoxLayout()

        self.txt_export_path = QLineEdit()
        self.txt_export_path.setPlaceholderText("Select master manifest destination...")
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_export)

        h_layout.addWidget(self.txt_export_path)
        h_layout.addWidget(btn_browse)

        self.btn_export = QPushButton("Export Master Manifest")
        self.btn_export.clicked.connect(self._run_export)
        self.lbl_export_result = QLabel("Generates consolidated SHA-256 manifest directly from database baseline.")

        layout.addLayout(h_layout)
        layout.addWidget(self.btn_export)
        layout.addWidget(self.lbl_export_result)
        layout.addStretch()

    def _browse_export(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Master Manifest", "Master.sha256", "Checksum Files (*.sha256)")
        if file_path:
            self.txt_export_path.setText(file_path)

    def _run_export(self):
        path = self.txt_export_path.text().strip()
        if not path:
            QMessageBox.warning(self, "Error", "Please select an export destination.")
            return

        target_file = Path(path)
        overwrite_approved = False
        if target_file.exists():
            reply = QMessageBox.question(
                self,
                "Confirm Overwrite",
                f"Destination file already exists:\n{target_file}\n\nDo you want to overwrite it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.lbl_export_result.setText("Export cancelled: overwrite declined.")
                return
            overwrite_approved = True

        report = generate_master_manifest(self.conn, self.active_library_id, path, overwrite=overwrite_approved)
        if report.success:
            QMessageBox.information(self, "Export Complete", report.message)
            self.lbl_export_result.setText(report.message)
        else:
            QMessageBox.critical(self, "Export Error", report.message)

    # 7. History Tab
    def _build_history_tab(self):
        layout = QVBoxLayout(self.history_tab)
        self.tbl_history = QTableWidget()
        self.tbl_history.setColumnCount(3)
        self.tbl_history.setHorizontalHeaderLabels(["Timestamp", "Action", "Details"])
        self.tbl_history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        btn_refresh = QPushButton("Refresh History")
        btn_refresh.clicked.connect(self.refresh_history)

        layout.addWidget(self.tbl_history)
        layout.addWidget(btn_refresh)

    def refresh_history(self):
        cur = self.conn.execute("SELECT created_at, action, details FROM audit_log ORDER BY id DESC LIMIT 100")
        rows = cur.fetchall()
        self.tbl_history.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.tbl_history.setItem(row_idx, 0, QTableWidgetItem(r["created_at"]))
            self.tbl_history.setItem(row_idx, 1, QTableWidgetItem(r["action"]))
            self.tbl_history.setItem(row_idx, 2, QTableWidgetItem(r["details"] or ""))


def launch_gui(db_path: Optional[str] = None):
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(db_path)
    window.show()
    return app.exec()
