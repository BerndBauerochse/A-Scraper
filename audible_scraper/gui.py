import sys
import webbrowser
import csv
import os
from datetime import datetime
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QTableView, QHeaderView, QLabel, 
    QStatusBar, QMessageBox, QApplication, QStyledItemDelegate, 
    QStyleOptionButton, QStyle, QLineEdit, QFileDialog
)
from PySide6.QtCore import Qt, QAbstractTableModel, QThread, Signal, QUrl, QEvent, QModelIndex, QTimer, QRectF
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QPixmap, QPainter, QPen, QIcon

from .models import Entry
from .scraper import AudibleScraper
from .storage import load_entries, save_entries
from .update_manager import UpdateManager

# --- Loading Spinner ---
class LoadingSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.is_spinning = False

    def rotate(self):
        self.angle = (self.angle + 10) % 360
        self.update()

    def start(self):
        self.is_spinning = True
        self.setVisible(True)
        self.timer.start(30)

    def stop(self):
        self.is_spinning = False
        self.setVisible(False)
        self.timer.stop()

    def paintEvent(self, event):
        if not self.is_spinning:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pen = QPen(QColor("#0078d7"))
        pen.setWidth(3)
        painter.setPen(pen)

        rect = QRectF(2, 2, 20, 20)
        span_angle = 270 * 16 # 270 degrees
        start_angle = -self.angle * 16 

        painter.drawArc(rect, start_angle, span_angle)

# --- Link Delegate ---
class LinkDelegate(QStyledItemDelegate):
    clicked = Signal(QModelIndex)

    def paint(self, painter, option, index):
        # Draw text as link (blue, underlined)
        text = index.data(Qt.DisplayRole)
        if not text:
            return

        painter.save()
        
        # Use standard link color or custom
        link_color = QColor("#0078d7")
        if option.state & QStyle.State_MouseOver:
             link_color = QColor("#005a9e")
        
        painter.setPen(link_color)
        
        # Draw text
        # We can use drawText but underlining is manual or use font
        font = option.font
        font.setUnderline(True)
        painter.setFont(font)
        
        # Align left vertically centered
        rect = option.rect
        # Elide text if too long
        elided_text = option.fontMetrics.elidedText(text, Qt.ElideRight, rect.width())
        
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, elided_text)
        
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease:
            self.clicked.emit(index)
            return True
        return False

# --- Button Delegate ---
class ButtonDelegate(QStyledItemDelegate):
    copyClicked = Signal(QModelIndex)
    openClicked = Signal(QModelIndex)

    def paint(self, painter, option, index):
        if index.column() == 12: # Action column
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Calculate button rects
            rect = option.rect
            # Padding: 2px top/bottom, 4px between buttons
            # Total width available
            w = rect.width()
            h = rect.height()
            
            btn_width = (w - 12) / 2 # 4px padding left, 4px middle, 4px right
            btn_height = h - 6
            
            copy_rect = QRectF(rect.x() + 4, rect.y() + 3, btn_width, btn_height)
            open_rect = QRectF(rect.x() + 8 + btn_width, rect.y() + 3, btn_width, btn_height)
            
            # Draw Copy Button (Blue)
            self._draw_button(painter, copy_rect, "Copy", "#0078d7", option, "copy")
            
            # Draw Open Button (Green)
            self._draw_button(painter, open_rect, "Open", "#28a745", option, "open")
            
            painter.restore()
        else:
            super().paint(painter, option, index)

    def _draw_button(self, painter, rect, text, color_hex, option, btn_type):
        path = QPainter.drawRoundedRect
        
        # Check mouse state (simplification: we don't track hover per button easily without more logic, 
        # but we can just draw flat or use the row state)
        # For now, simple flat colored buttons
        
        bg_color = QColor(color_hex)
        
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 4, 4)
        
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, text)

    def editorEvent(self, event, model, option, index):
        if index.column() == 12:
            if event.type() == QEvent.MouseButtonRelease:
                # Determine which button was clicked
                click_x = event.position().x()
                rect = option.rect
                w = rect.width()
                btn_width = (w - 12) / 2
                
                # Copy button range
                if rect.x() + 4 <= click_x <= rect.x() + 4 + btn_width:
                    self.copyClicked.emit(index)
                    return True
                
                # Open button range
                if rect.x() + 8 + btn_width <= click_x <= rect.x() + 8 + 2*btn_width:
                    self.openClicked.emit(index)
                    return True
        return False

# --- Worker Thread ---
class ScraperWorker(QThread):
    finished = Signal(list, str) # entries, error_message
    progress = Signal(str) # status message

    def __init__(self, start_urls):
        super().__init__()
        self.start_urls = start_urls
        self.scraper = AudibleScraper()

    def run(self):
        all_entries = []
        try:
            total_urls = len(self.start_urls)
            for i, url in enumerate(self.start_urls):
                # We pass a callback to get progress updates
                def progress_callback(page_num):
                    self.progress.emit(f"URL {i+1}/{total_urls} - Seite {page_num}...")

                entries = self.scraper.fetch_all_pages(url, progress_callback=progress_callback)
                all_entries.extend(entries)
            
            self.finished.emit(all_entries, "")
        except Exception as e:
            self.finished.emit([], str(e))

# --- Update Worker ---
class UpdateWorker(QThread):
    finished = Signal(bool, str) # success, message
    progress = Signal(str) # status message

    def __init__(self, entries_map):
        super().__init__()
        self.entries_map = entries_map
        self.manager = UpdateManager(self.entries_map, save_entries)

    def run(self):
        # Redirect manager log to progress signal
        self.manager.set_log_callback(self.progress.emit)
        success, msg = self.manager.run_update()
        self.finished.emit(success, msg)

# --- Table Model ---
class EntryTableModel(QAbstractTableModel):
    def __init__(self, entries=None):
        super().__init__()
        self.entries = entries or []
    def __init__(self, entries=None):
        super().__init__()
        self.entries = entries or []
        # Added "Anz." for rating count
        self.headers = ["Status", "Titel", "Untertitel", "Autor", "Note", "Anz.", "VÖ", "Zeit", "LZ", "DB €", "€", "EAN", "Aktion"]

    def rowCount(self, parent=None):
        return len(self.entries)

    def columnCount(self, parent=None):
        return len(self.headers)

    def sort(self, column, order):
        self.layoutAboutToBeChanged.emit()
        
        reverse = (order == Qt.DescendingOrder)
        
        if column == 0: # Status
            self.entries.sort(key=lambda x: (bool(x.ean), not x.is_new, not x.is_changed), reverse=reverse)
        elif column == 1: # Title
            self.entries.sort(key=lambda x: x.title, reverse=reverse)
        elif column == 2: # Subtitle
            self.entries.sort(key=lambda x: x.subtitle, reverse=reverse)
        elif column == 3: # Author
            self.entries.sort(key=lambda x: x.author, reverse=reverse)
        elif column == 4: # Rating
            self.entries.sort(key=lambda x: x.rating, reverse=reverse)
        elif column == 5: # Rating Count
            self.entries.sort(key=lambda x: x.rating_count, reverse=reverse)
        elif column == 6: # Release Date
            def date_key(entry):
                try:
                    return datetime.strptime(entry.release_date, "%d.%m.%Y")
                except ValueError:
                    return datetime.min
            self.entries.sort(key=date_key, reverse=reverse)
        elif column == 7: # Runtime
            self.entries.sort(key=lambda x: x.runtime, reverse=reverse)
        elif column == 8: # LZ (Runtime Price)
            self.entries.sort(key=lambda x: x.runtime_price, reverse=reverse)
        elif column == 9: # Calculated Price (DB €)
            self.entries.sort(key=lambda x: x.calculated_price, reverse=reverse)
        elif column == 10: # Price
            self.entries.sort(key=lambda x: x.price_without_sub, reverse=reverse)
        elif column == 11: # EAN
            self.entries.sort(key=lambda x: x.ean, reverse=reverse)
        # Column 12 (Action) is not sortable
        
        self.layoutChanged.emit()

    def data(self, index, role):
        if not index.isValid():
            return None
        
        entry = self.entries[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                if not entry.ean: return "ToDo"
                if entry.is_new: return "Neu"
                if entry.is_changed: return "Änd."
                return ""
            elif col == 1:
                return entry.title
            elif col == 2:
                return entry.subtitle
            elif col == 3:
                return entry.author
            elif col == 4:
                return entry.rating
            elif col == 5:
                return str(entry.rating_count)
            elif col == 6:
                return entry.release_date
            elif col == 7:
                return str(entry.runtime)
            elif col == 8:
                return entry.runtime_price
            elif col == 9:
                return entry.calculated_price
            elif col == 10:
                return entry.price_without_sub
            elif col == 11:
                return entry.ean
            elif col == 12:
                return "" # Action column handled by delegate

        if role == Qt.ForegroundRole:
            if col == 0:
                if not entry.ean: return QBrush(QColor("#d9534f"))
                if entry.is_new: return QBrush(QColor("red"))
                if entry.is_changed: return QBrush(QColor("blue"))
            return None 

        if role == Qt.BackgroundRole:
            if not entry.ean:
                return QBrush(QColor(255, 245, 230)) # Light Orange for ToDo
            if entry.is_new:
                return QBrush(QColor(255, 230, 230)) # Light red
            
            # Check for specific cell changes
            if entry.is_changed:
                # Default changed row background
                bg_color = QColor(230, 230, 255) # Light blue
                
                # Highlight specific cells
                if col == 6 and "release_date" in entry.changed_fields:
                    return QBrush(QColor(255, 255, 150)) # Yellow for changed cell
                if col == 7 and "runtime" in entry.changed_fields:
                    return QBrush(QColor(255, 255, 150))
                if col == 8 and "runtime" in entry.changed_fields: # LZ depends on runtime
                    return QBrush(QColor(255, 255, 150))
                if col == 9 and "runtime" in entry.changed_fields: # DB € depends on runtime (if not imported)
                    return QBrush(QColor(255, 255, 150))
                if col == 10 and "price_without_sub" in entry.changed_fields:
                    return QBrush(QColor(255, 255, 150))
                if (col == 4 or col == 5) and "rating" in entry.changed_fields:
                    return QBrush(QColor(255, 255, 150))
                if col == 3 and "author" in entry.changed_fields:
                    return QBrush(QColor(255, 255, 150))
                
                return QBrush(bg_color)

        return None

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None

    def update_entries(self, new_entries):
        self.beginResetModel()
        self.entries = new_entries
        self.endResetModel()

# --- Helper Functions ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# --- Main Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("Audible DAV – Titelübersicht")
        self.resize(1000, 600)
        
        # Set App Icon
        icon_path = resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Apply Modern Stylesheet
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121212;
            }
            QWidget {
                color: #ffffff;
            }
            QPushButton {
                background-color: #0078d7;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1084e3;
            }
            QPushButton:pressed {
                background-color: #006cc1;
            }
            QPushButton:checked {
                background-color: #005a9e;
                border: 2px solid #004578;
            }
            QPushButton:disabled {
                background-color: #333333;
                color: #888888;
            }
            QLineEdit {
                padding: 8px;
                border: 1px solid #333;
                border-radius: 4px;
                background-color: #252525;
                color: white;
                selection-background-color: #0078d7;
            }
            QLineEdit:focus {
                border: 2px solid #0078d7;
            }
            QProgressBar {
                border: none;
                background-color: #333;
                border-radius: 4px;
                height: 8px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #0078d7;
                border-radius: 4px;
            }
            QStatusBar {
                background-color: #1e1e1e;
                color: #ccc;
            }
            QTableView {
                background-color: white;
                color: black;
                gridline-color: #d0d0d0;
                selection-background-color: #0078d7;
                selection-color: white;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                color: black;
                padding: 4px;
                border: 1px solid #d0d0d0;
            }
        """)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # Main layout (Zero margins for banner)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Banner
        banner_path = resource_path(os.path.join("assets", "banner.png"))
        
        if os.path.exists(banner_path):
            self.banner_label = QLabel()
            pixmap = QPixmap(banner_path)
            self.banner_label.setFixedHeight(200)
            self.banner_label.setPixmap(pixmap)
            self.banner_label.setScaledContents(True)
            self.main_layout.addWidget(self.banner_label)

        # Content Layout (Standard margins for controls and table)
        self.content_widget = QWidget()
        self.layout = QVBoxLayout(self.content_widget)
        self.main_layout.addWidget(self.content_widget)

        # Top Controls
        self.top_layout = QHBoxLayout()
        
        # Left Group
        self.refresh_btn = QPushButton("Scrape")
        self.refresh_btn.clicked.connect(self.start_refresh)
        self.top_layout.addWidget(self.refresh_btn)
        
        # Spinner for Scrape Button (Overlay)
        self.scrape_spinner = LoadingSpinner(self.refresh_btn)
        self.scrape_spinner.setVisible(False)
        
        self.show_all_btn = QPushButton("Alle anzeigen")
        self.show_all_btn.setCheckable(True)
        self.show_all_btn.clicked.connect(self.toggle_filter)
        self.top_layout.addWidget(self.show_all_btn)
        
        # Search Bar (with Clear Button)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Suche...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(250)
        self.search_input.textChanged.connect(self.apply_filter)
        self.top_layout.addWidget(self.search_input)
        
        # Spacer (pushes everything else to the right)
        self.top_layout.addStretch()
        
        # Right Group
        self.update_db_btn = QPushButton("DB Update")
        self.update_db_btn.clicked.connect(self.start_update)
        self.top_layout.addWidget(self.update_db_btn)
        
        self.export_btn = QPushButton()
        self.export_btn.setIcon(self.create_excel_icon())
        self.export_btn.setToolTip("Export Excel/CSV")
        self.export_btn.setFixedWidth(40) # Square-ish button
        self.export_btn.clicked.connect(self.export_data)
        self.top_layout.addWidget(self.export_btn)
        
        # Loading Spinner (Far Right)
        self.spinner = LoadingSpinner()
        self.spinner.setVisible(False)
        self.top_layout.addWidget(self.spinner)
        
        self.layout.addLayout(self.top_layout)

        # Table
        self.table_view = QTableView()
        self.model = EntryTableModel()
        self.table_view.setModel(self.model)
        self.table_view.setSortingEnabled(True)
        
        # Column Width Optimization
        header = self.table_view.horizontalHeader()
        
        # 1. Compact columns (Metadata) - Optimized for performance
        # We use Interactive mode with fixed initial widths instead of ResizeToContents
        # because ResizeToContents is very slow with many rows.
        
        # Status
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.table_view.setColumnWidth(0, 60)
        
        # Note
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        self.table_view.setColumnWidth(4, 40)
        
        # Anz.
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        self.table_view.setColumnWidth(5, 50)
        
        # VÖ
        header.setSectionResizeMode(6, QHeaderView.Interactive)
        self.table_view.setColumnWidth(6, 80)
        
        # Zeit
        header.setSectionResizeMode(7, QHeaderView.Interactive)
        self.table_view.setColumnWidth(7, 50)
        
        # LZ
        header.setSectionResizeMode(8, QHeaderView.Interactive)
        self.table_view.setColumnWidth(8, 60)
        
        # DB €
        header.setSectionResizeMode(9, QHeaderView.Interactive)
        self.table_view.setColumnWidth(9, 60)
        
        # €
        header.setSectionResizeMode(10, QHeaderView.Interactive)
        self.table_view.setColumnWidth(10, 60)
        
        # EAN
        header.setSectionResizeMode(11, QHeaderView.Interactive)
        self.table_view.setColumnWidth(11, 100)

        # 2. Main Content
        # Title takes remaining space (Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        
        # Subtitle and Author get fixed, smaller widths (Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        
        self.table_view.setColumnWidth(2, 140) # Subtitle reduced further (-30%)
        self.table_view.setColumnWidth(3, 105) # Author reduced further (-30%)
        
        # 3. Action Column (Index 12)
        header.setSectionResizeMode(12, QHeaderView.Fixed)
        self.table_view.setColumnWidth(12, 120) # Enough for 2 buttons

        self.table_view.setSelectionBehavior(QTableView.SelectRows)
        
        # Set Button Delegate for Action column (12)
        self.button_delegate = ButtonDelegate(self.table_view)
        self.button_delegate.copyClicked.connect(self.handle_copy_click)
        self.button_delegate.openClicked.connect(self.handle_open_click)
        self.table_view.setItemDelegateForColumn(12, self.button_delegate)
        
        self.layout.addWidget(self.table_view)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Bereit")
        self.status_bar.addWidget(self.status_label)

        # Data
        self.entries_map = {} # id -> Entry
        self.start_urls = [
            "https://www.audible.de/search?ref=&searchProvider=Der+Audio+Verlag&sort=pubdate-desc-rank",
            "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290273031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
            "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290274031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
            "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290275031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
            "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290276031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
            "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290277031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
            "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290278031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title"
        ]
        
        # Load initial data
        self.load_local_data()
        
        # Initial filter state: Show All by default
        self.show_all_btn.setChecked(True)
        self.apply_filter()

    def create_excel_icon(self):
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw Green Box
        painter.setBrush(QBrush(QColor("#217346"))) # Excel Green
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(2, 2, 20, 20, 4, 4)
        
        # Draw White X
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawLine(8, 8, 16, 16)
        painter.drawLine(16, 8, 8, 16)
        
        painter.end()
        return QIcon(pixmap)

    def load_local_data(self):
        self.entries_map = load_entries()
        # Initially no new/changed flags are set from disk (they are runtime flags)
        # So we just show all.
        self.update_table_data()
        self.update_status(f"Geladen: {len(self.entries_map)} Einträge aus lokalem Speicher.")

    def toggle_filter(self):
        self.apply_filter()

    def apply_filter(self):
        show_all = self.show_all_btn.isChecked()
        search_text = self.search_input.text().lower()
        all_entries = list(self.entries_map.values())
        
        display_entries = []
        
        # First filter by status (New/Changed) unless "Show All" is checked
        if show_all:
            self.show_all_btn.setText("Nur Neue/Änderungen anzeigen")
            candidates = all_entries
        else:
            self.show_all_btn.setText("Alle anzeigen")
            candidates = [e for e in all_entries if e.is_new or e.is_changed]
            
        # Then filter by search text
        # Then filter by search text
        if search_text:
            display_entries = [
                e for e in candidates 
                if search_text in e.title.lower() or 
                   search_text in e.subtitle.lower() or 
                   search_text in e.author.lower()
            ]
        else:
            display_entries = candidates
            
        self.model.update_entries(display_entries)

    def update_table_data(self):
        # Helper to refresh table from entries_map based on current filter
        self.apply_filter()

    def start_refresh(self):
        # Lock width to prevent collapse when removing text
        self.refresh_btn.setFixedWidth(self.refresh_btn.width())
        self.refresh_btn.setText("")
        self.refresh_btn.setEnabled(False)
        
        # Center spinner on button
        btn_rect = self.refresh_btn.rect()
        spinner_rect = self.scrape_spinner.rect()
        x = (btn_rect.width() - spinner_rect.width()) // 2
        y = (btn_rect.height() - spinner_rect.height()) // 2
        self.scrape_spinner.move(x, y)
        self.scrape_spinner.start()
        
        self.update_status("Lade Daten von Audible...")
        
        self.worker = ScraperWorker(self.start_urls)
        self.worker.finished.connect(self.on_refresh_finished)
        self.worker.progress.connect(self.on_progress)
        self.worker.start()

    def on_progress(self, message):
        self.update_status(message)

    def on_refresh_finished(self, fetched_entries, error_msg):
        self.scrape_spinner.stop()
        self.refresh_btn.setText("Scrape")
        self.refresh_btn.setEnabled(True)
        # Unlock width (reset to default max/min)
        self.refresh_btn.setMinimumWidth(0)
        self.refresh_btn.setMaximumWidth(16777215)
        
        if error_msg:
            QMessageBox.critical(self, "Fehler", f"Fehler beim Abruf: {error_msg}")
            self.update_status("Fehler beim Abruf.")
            return

        change_count = 0
        new_count = 0
        current_time = datetime.now().isoformat()

        for entry in fetched_entries:
            if entry.id not in self.entries_map:
                # New entry
                entry.is_new = True
                entry.first_seen = current_time
                entry.last_seen = current_time
                self.entries_map[entry.id] = entry
                new_count += 1
            else:
                # Existing entry, update details
                existing = self.entries_map[entry.id]
                
                # Check for changes
                if existing.price_without_sub != entry.price_without_sub:
                    existing.is_changed = True
                    existing.changed_fields.append("price_without_sub")
                    change_count += 1
                if existing.release_date != entry.release_date:
                    existing.is_changed = True
                    existing.changed_fields.append("release_date")
                    change_count += 1
                if existing.runtime != entry.runtime:
                    existing.is_changed = True
                    existing.changed_fields.append("runtime")
                    change_count += 1
                if existing.rating != entry.rating:
                    existing.is_changed = True
                    existing.changed_fields.append("rating")
                    change_count += 1
                if existing.rating_count != entry.rating_count:
                    existing.is_changed = True
                    existing.changed_fields.append("rating") # We group it under rating
                    change_count += 1
                if existing.author != entry.author:
                    existing.is_changed = True
                    existing.changed_fields.append("author")
                    change_count += 1
                    
                existing.last_seen = current_time
                existing.price_without_sub = entry.price_without_sub
                existing.release_date = entry.release_date
                existing.runtime = entry.runtime
                existing.rating = entry.rating
                existing.rating_count = entry.rating_count
                existing.subtitle = entry.subtitle # Update subtitle but don't flag as change (usually static)
                existing.author = entry.author
                # Keep first_seen
                entry.first_seen = existing.first_seen
                entry.last_seen = current_time
                # Update map with new object but keep old meta (actually we updated existing object in place mostly)
                # But let's ensure we keep the object that has the flags
                pass

        # Save to disk
        save_entries(self.entries_map)
        
        # Update UI
        self.update_table_data()
        self.update_status(f"Aktualisiert: {len(fetched_entries)} geladen. {new_count} Neu, {change_count} Geändert.")

    def update_status(self, message):
        self.status_label.setText(message)

    def handle_copy_click(self, index):
        entry = self.model.entries[index.row()]
        clipboard = QApplication.clipboard()
        clipboard.setText(entry.url)
        self.update_status(f"Link kopiert: {entry.title}")

    def handle_open_click(self, index):
        entry = self.model.entries[index.row()]
        QDesktopServices.openUrl(QUrl(entry.url))

    def export_data(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Exportieren", "audible_liste.csv", "CSV Dateien (*.csv)")
        if not file_path:
            return

        try:
            # Export all entries currently in the map (entire list)
            all_entries = list(self.entries_map.values())
            
            # Sort them for nicer output (e.g. by date desc)
            def date_key(entry):
                try:
                    return datetime.strptime(entry.release_date, "%d.%m.%Y")
                except ValueError:
                    return datetime.min
            all_entries.sort(key=date_key, reverse=True)

            with open(file_path, mode='w', newline='', encoding='utf-8-sig') as file:
                writer = csv.writer(file, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
                
                # Header
                writer.writerow(["ID", "Titel", "Untertitel", "Autor", "Bewertung", "VÖ-Datum", "Laufzeit (Min)", "Preis (LZ)", "Preis", "EAN", "URL", "Neu?", "Geändert?", "Erstmals gesehen", "Zuletzt gesehen"])
                
                # Rows
                for entry in all_entries:
                    writer.writerow([
                        entry.id,
                        entry.title,
                        entry.subtitle,
                        entry.author,
                        entry.rating,
                        entry.release_date,
                        entry.runtime,
                        entry.calculated_price,
                        entry.price_without_sub,
                        entry.ean,
                        entry.url,
                        "JA" if entry.is_new else "NEIN",
                        "JA" if entry.is_changed else "NEIN",
                        entry.first_seen,
                        entry.last_seen
                    ])
            
            self.update_status(f"Export erfolgreich: {len(all_entries)} Zeilen gespeichert.")
            QMessageBox.information(self, "Export", f"Daten erfolgreich nach {file_path} exportiert.")
            
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Fehler beim Export: {e}")

    def start_update(self):
        self.update_db_btn.setEnabled(False)
        self.spinner.start()
        self.update_status("Starte erweitertes Update...")
        
        self.update_worker = UpdateWorker(self.entries_map)
        self.update_worker.finished.connect(self.on_update_finished)
        self.update_worker.progress.connect(self.update_status)
        self.update_worker.start()

    def on_update_finished(self, success, message):
        self.update_db_btn.setEnabled(True)
        self.spinner.stop()
        
        if success:
            QMessageBox.information(self, "Update Erfolgreich", message)
            # Refresh table to show new prices/EANs
            self.update_table_data()
        else:
            QMessageBox.warning(self, "Update Fehlgeschlagen", message)
            
        self.update_status(message)
