from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QPointF, QRect, QRectF, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from open_anonymizer.branding import (
    application_header_icon,
    application_icon,
    bug_report_icon,
)
from open_anonymizer.models import (
    AnonymizationSettings,
    ExportMode,
    ImportedDocument,
    ProcessBatchRequest,
    ProcessedDocument,
)
from open_anonymizer.services.deduce_backend import backend_is_ready
from open_anonymizer.services.deidentifier import document_smart_key
from open_anonymizer.services.exporter import export_processed_documents
from open_anonymizer.services.workers import (
    BatchProcessingFailure,
    BatchProcessingResult,
    BatchProcessorRunnable,
    BackendWarmupFailure,
    BackendWarmupRequest,
    BackendWarmupResult,
    BackendWarmupRunnable,
    ImportDocumentRequest,
    ImportDocumentResult,
    ImportDocumentRunnable,
)
from open_anonymizer.ui.anonymization_dialog import (
    AnonymizationDialog,
    load_saved_anonymization_settings,
    save_anonymization_settings,
)
from open_anonymizer.ui.drop_area import DropArea
from open_anonymizer.ui.processing_text_edit import ScanningPlainTextEdit


STATUS_LABELS = {
    "pending": "Pending",
    "processing": "Processing",
    "ready": "Ready",
    "error": "Error",
}
STATUS_COLORS = {
    "pending": QColor("#9ca3af"),
    "processing": QColor("#f59e0b"),
    "ready": QColor("#16a34a"),
    "error": QColor("#dc2626"),
}
DOCUMENT_ID_ROLE = Qt.ItemDataRole.UserRole
DOCUMENT_STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
DOCUMENT_SOURCE_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 2
DOCUMENT_ERROR_ROLE = int(Qt.ItemDataRole.UserRole) + 3
SETTINGS_ORGANIZATION = "Open Anonymizer"
SETTINGS_APPLICATION = "Open Anonymizer"
EXPORT_DIRECTORY_SETTINGS_KEY = "export/last_directory"
BUG_REPORT_FORM_URL = "https://forms.gle/Ww8d6JajzAsbpxH38"
DEFAULT_WINDOW_SIZE = QSize(1160, 664)
MINIMUM_WINDOW_SIZE = QSize(840, 520)
WINDOW_SCREEN_MARGIN = 48
PREPARING_BACKEND_TEXT = "Preparing anonymizer engine…"
PREPARING_BACKEND_BADGE_TEXT = "Preparing"
SCANNING_BADGE_TEXT = "Scanning"
DEFAULT_MAX_CONCURRENT_IMPORTS = 1


@dataclass(frozen=True)
class WindowLayoutProfile:
    root_margins: tuple[int, int, int, int]
    root_spacing: int
    header_spacing: int
    title_spacing: int
    left_panel_spacing: int
    document_action_spacing: int
    right_panel_spacing: int
    header_icon_size: int
    header_icon_pixmap_size: int
    drop_area_height: int
    paste_input_height: int
    export_button_min_width: int


def layout_profile_for_window_height(window_height: int) -> WindowLayoutProfile:
    if window_height <= 560:
        return WindowLayoutProfile(
            root_margins=(12, 12, 12, 6),
            root_spacing=8,
            header_spacing=12,
            title_spacing=1,
            left_panel_spacing=10,
            document_action_spacing=8,
            right_panel_spacing=10,
            header_icon_size=56,
            header_icon_pixmap_size=36,
            drop_area_height=80,
            paste_input_height=92,
            export_button_min_width=152,
        )

    if window_height <= 680:
        return WindowLayoutProfile(
            root_margins=(14, 14, 14, 6),
            root_spacing=8,
            header_spacing=14,
            title_spacing=1,
            left_panel_spacing=10,
            document_action_spacing=10,
            right_panel_spacing=12,
            header_icon_size=60,
            header_icon_pixmap_size=40,
            drop_area_height=88,
            paste_input_height=100,
            export_button_min_width=160,
        )

    return WindowLayoutProfile(
        root_margins=(16, 16, 16, 8),
        root_spacing=10,
        header_spacing=16,
        title_spacing=2,
        left_panel_spacing=12,
        document_action_spacing=12,
        right_panel_spacing=14,
        header_icon_size=68,
        header_icon_pixmap_size=48,
        drop_area_height=96,
        paste_input_height=112,
        export_button_min_width=168,
    )


def recommended_window_size(available_size: QSize) -> QSize:
    def fit_dimension(preferred: int, minimum: int, available: int) -> int:
        usable = max(minimum, available - WINDOW_SCREEN_MARGIN)
        return max(1, min(preferred, usable, available))

    return QSize(
        fit_dimension(DEFAULT_WINDOW_SIZE.width(), MINIMUM_WINDOW_SIZE.width(), available_size.width()),
        fit_dimension(DEFAULT_WINDOW_SIZE.height(), MINIMUM_WINDOW_SIZE.height(), available_size.height()),
    )


def main_window_qsettings() -> QSettings:
    return QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORGANIZATION,
        SETTINGS_APPLICATION,
    )


def default_export_directory() -> Path:
    downloads_path = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )
    if downloads_path:
        downloads_dir = Path(downloads_path).expanduser()
        if downloads_dir.exists():
            return downloads_dir

    home_downloads = Path.home() / "Downloads"
    if home_downloads.exists():
        return home_downloads

    return Path.home()


def _source_kind_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    return "text_file"


def configured_max_concurrent_imports() -> int:
    raw_value = os.getenv(
        "OPEN_ANONYMIZER_MAX_CONCURRENT_IMPORTS",
        str(DEFAULT_MAX_CONCURRENT_IMPORTS),
    ).strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_IMPORTS


class DocumentListItemDelegate(QStyledItemDelegate):
    def __init__(self, parent: QListWidget):
        super().__init__(parent)
        self._spinner_angle = 0

    def advance_spinner(self) -> None:
        self._spinner_angle = (self._spinner_angle + 24) % 360
        list_widget = self.parent()
        if isinstance(list_widget, QListWidget):
            list_widget.viewport().update()

    def sizeHint(self, option, index) -> QSize:
        del option, index
        return QSize(0, 44)

    def _card_rect(self, item_rect: QRect) -> QRectF:
        return QRectF(item_rect.adjusted(5, 9, -10, -3))

    def remove_button_rect(self, item_rect: QRect) -> QRectF:
        card_rect = self._card_rect(item_rect)
        button_size = 16.0
        overlap = 4.0
        return QRectF(
            card_rect.right() - button_size + overlap,
            card_rect.top() - overlap,
            button_size,
            button_size,
        )

    def _status_indicator_rect(self, item_rect: QRect) -> QRectF:
        card_rect = self._card_rect(item_rect)
        indicator_size = 16.0
        inset = 8.0
        return QRectF(
            card_rect.x() + card_rect.width() - indicator_size - inset,
            card_rect.y() + card_rect.height() - indicator_size - inset,
            indicator_size,
            indicator_size,
        )

    def paint(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        status = index.data(DOCUMENT_STATUS_ROLE) or "pending"
        error_message = index.data(DOCUMENT_ERROR_ROLE) or ""
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        card_rect = self._card_rect(option.rect)
        background = QColor("#fffdf9")
        border = QColor("#e7ddd2")
        title_color = QColor("#1f2933")

        if selected:
            background = QColor("#eaf4ef")
            border = QColor("#1f4d45")
            title_color = QColor("#14352f")
        elif hovered:
            background = QColor("#fdf7ef")
            border = QColor("#eadfce")
        elif status == "error":
            title_color = QColor("#991b1b")

        card_pen = QPen(border)
        card_pen.setWidthF(1.0)
        painter.setPen(card_pen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(card_rect), 14, 14)

        accent_color = QColor(STATUS_COLORS.get(status, STATUS_COLORS["pending"]))
        accent_color.setAlpha(220 if selected else 180)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent_color)
        painter.drawRoundedRect(
            QRectF(card_rect.left() + 10, card_rect.top() + 5, 4, card_rect.height() - 10),
            2,
            2,
        )

        button_enabled = True
        list_widget = self.parent()
        if isinstance(list_widget, DocumentListWidget):
            button_enabled = list_widget.remove_enabled()

        button_rect = self.remove_button_rect(option.rect)
        indicator_rect = self._status_indicator_rect(option.rect)
        content_rect = card_rect.adjusted(22, 0, 0, 0)
        right_limit = min(button_rect.left(), indicator_rect.left()) - 10
        text_width = max(0, int(right_limit - content_rect.left()))

        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_metrics = QFontMetrics(title_font)
        title_text = title_metrics.elidedText(
            title,
            Qt.TextElideMode.ElideRight,
            text_width,
        )

        title_rect = QRect(content_rect.left(), content_rect.top(), text_width, content_rect.height())

        painter.setFont(title_font)
        painter.setPen(title_color)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title_text,
        )

        self._paint_remove_button(
            painter,
            button_rect,
            selected=selected,
            enabled=button_enabled,
        )
        self._paint_status_indicator(painter, indicator_rect, status)
        painter.restore()

    def _paint_remove_button(
        self,
        painter: QPainter,
        button_rect: QRectF,
        *,
        selected: bool,
        enabled: bool,
    ) -> None:
        fill = QColor("#f5f5f5" if not selected else "#eceff2")
        border = QColor("#d1d5db" if not selected else "#c4cbd4")
        icon = QColor("#6b7280")
        if not enabled:
            fill.setAlpha(110)
            border.setAlpha(90)
            icon.setAlpha(90)

        button_pen = QPen(border)
        button_pen.setWidthF(1.0)
        painter.setPen(button_pen)
        painter.setBrush(fill)
        painter.drawEllipse(button_rect)

        icon_pen = QPen(icon)
        icon_pen.setWidthF(1.6)
        icon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(icon_pen)
        inset = 4.5
        painter.drawLine(
            QPointF(button_rect.left() + inset, button_rect.top() + inset),
            QPointF(button_rect.right() - inset, button_rect.bottom() - inset),
        )
        painter.drawLine(
            QPointF(button_rect.right() - inset, button_rect.top() + inset),
            QPointF(button_rect.left() + inset, button_rect.bottom() - inset),
        )

    def _paint_status_indicator(
        self,
        painter: QPainter,
        indicator_rect: QRectF,
        status: str,
    ) -> None:
        circle_rect = indicator_rect.adjusted(2, 2, -2, -2)
        status_color = QColor(STATUS_COLORS.get(status, STATUS_COLORS["pending"]))

        if status == "processing":
            faded_color = QColor(status_color)
            faded_color.setAlpha(55)
            background_pen = QPen(faded_color)
            background_pen.setWidthF(2.4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(background_pen)
            painter.drawEllipse(circle_rect)

            spinner_pen = QPen(status_color)
            spinner_pen.setWidthF(2.8)
            spinner_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(spinner_pen)
            painter.drawArc(
                circle_rect,
                (90 - self._spinner_angle) * 16,
                220 * 16,
            )
            return

        if status == "ready":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(status_color)
            painter.drawEllipse(circle_rect)

            check_pen = QPen(QColor("#ffffff"))
            check_pen.setWidthF(2.2)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.drawLine(
                QPointF(circle_rect.left() + 4, circle_rect.center().y()),
                QPointF(circle_rect.left() + 7, circle_rect.bottom() - 4),
            )
            painter.drawLine(
                QPointF(circle_rect.left() + 7, circle_rect.bottom() - 4),
                QPointF(circle_rect.right() - 4, circle_rect.top() + 4),
            )
            return

        if status == "error":
            painter.setBrush(QColor("#fee2e2"))
            error_pen = QPen(status_color)
            error_pen.setWidthF(2.0)
            error_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(error_pen)
            painter.drawEllipse(circle_rect)
            painter.drawLine(
                QPointF(circle_rect.left() + 4, circle_rect.top() + 4),
                QPointF(circle_rect.right() - 4, circle_rect.bottom() - 4),
            )
            painter.drawLine(
                QPointF(circle_rect.right() - 4, circle_rect.top() + 4),
                QPointF(circle_rect.left() + 4, circle_rect.bottom() - 4),
            )
            return

        pending_pen = QPen(status_color)
        pending_pen.setWidthF(2.0)
        painter.setPen(pending_pen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(circle_rect)


class DocumentListWidget(QListWidget):
    remove_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._remove_enabled = True

    def set_remove_enabled(self, enabled: bool) -> None:
        if self._remove_enabled == enabled:
            return
        self._remove_enabled = enabled
        if not enabled:
            self.viewport().unsetCursor()
        self.viewport().update()

    def remove_enabled(self) -> bool:
        return self._remove_enabled

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._is_remove_button_hit(point)
        ):
            item = self.itemAt(point)
            if item is not None:
                document_id = item.data(DOCUMENT_ID_ROLE)
                if document_id:
                    self.remove_requested.emit(document_id)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if self._is_remove_button_hit(event.position().toPoint()):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        self.viewport().unsetCursor()

    def leaveEvent(self, event) -> None:
        self.viewport().unsetCursor()
        super().leaveEvent(event)

    def _is_remove_button_hit(self, point) -> bool:
        if not self._remove_enabled:
            return False
        item = self.itemAt(point)
        if item is None:
            return False
        delegate = self.itemDelegate()
        if not isinstance(delegate, DocumentListItemDelegate):
            return False
        return delegate.remove_button_rect(self.visualItemRect(item)).contains(QPointF(point))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.documents: list[ImportedDocument] = []
        self.processed_documents = {}
        self.processing_active = False
        self.pending_document_ids: set[str] = set()
        self.active_batch_document_ids: set[str] = set()
        self.active_batch_worker: BatchProcessorRunnable | None = None
        self.active_import_workers: dict[str, ImportDocumentRunnable] = {}
        self.queued_import_requests: deque[ImportDocumentRequest] = deque()
        self.anonymization_settings_generation = 0
        self.anonymization_settings = load_saved_anonymization_settings()
        self.paste_processed_document: ProcessedDocument | None = None
        self.paste_error_message: str | None = None
        self.paste_processing_active = False
        self.paste_processing_worker: BatchProcessorRunnable | None = None
        self._active_paste_processing_flags_key: tuple[bool, ...] | None = None
        self.paste_processing_generation = 0
        self._paste_processing_restart_requested = False
        self._paste_processing_restart_debounce = False
        self._background_backend_warmup_enabled = False
        self._background_backend_warmup_delay_ms = 0
        self.backend_warmup_worker: BackendWarmupRunnable | None = None
        self._active_backend_warmup_flags_key: tuple[bool, ...] | None = None
        self._queued_backend_warmup_flags_key: tuple[bool, ...] | None = None
        self._expected_backend_preparation_flags_key: tuple[bool, ...] | None = None
        self._backend_preparation_started_at: float | None = None
        self._active_batch_flags_key: tuple[bool, ...] | None = None
        self._closing = False
        self.paste_processing_timer = QTimer(self)
        self.paste_processing_timer.setSingleShot(True)
        self.paste_processing_timer.setInterval(250)
        self.paste_processing_timer.timeout.connect(self.process_pasted_text)
        self.backend_status_timer = QTimer(self)
        self.backend_status_timer.setInterval(75)
        self.backend_status_timer.timeout.connect(
            self._refresh_backend_preparation_state
        )
        self.backend_warmup_start_timer = QTimer(self)
        self.backend_warmup_start_timer.setSingleShot(True)
        self.backend_warmup_start_timer.timeout.connect(
            self.start_background_backend_warmup
        )

        self.setWindowTitle("Open Anonymizer")
        self.setWindowIcon(application_icon())
        initial_size = self._recommended_window_size()
        self._layout_profile = layout_profile_for_window_height(initial_size.height())
        self.resize(initial_size)
        self.setMinimumSize(
            min(MINIMUM_WINDOW_SIZE.width(), initial_size.width()),
            min(MINIMUM_WINDOW_SIZE.height(), initial_size.height()),
        )
        self._build_ui()
        self.refresh_document_list()
        self.refresh_actions()

    def _build_ui(self) -> None:
        layout_profile = self._layout_profile
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(*layout_profile.root_margins)
        root_layout.setSpacing(layout_profile.root_spacing)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(layout_profile.header_spacing)

        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(layout_profile.title_spacing)

        title = QLabel("Open Anonymizer")
        title.setObjectName("windowTitle")
        subtitle = QLabel(
            "Paste text or import files, configure anonymization, and copy or export clean output."
        )
        subtitle.setObjectName("windowSubtitle")

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        self.header_icon_label = QLabel()
        self.header_icon_label.setObjectName("headerIcon")
        self.header_icon_label.setFixedSize(
            layout_profile.header_icon_size,
            layout_profile.header_icon_size,
        )
        self.header_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_icon_label.setPixmap(
            application_header_icon(
                QSize(
                    layout_profile.header_icon_pixmap_size,
                    layout_profile.header_icon_pixmap_size,
                )
            )
        )

        header_layout.addWidget(
            self.header_icon_label,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        header_layout.addLayout(title_layout, stretch=1)

        root_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(layout_profile.left_panel_spacing)

        self.customize_anonymization_button = QPushButton("Customize anonymization")
        self.customize_anonymization_button.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.customize_anonymization_button.clicked.connect(
            self.open_anonymization_dialog
        )
        left_layout.addWidget(
            self.customize_anonymization_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        self.drop_area = DropArea()
        self.drop_area.setFixedHeight(layout_profile.drop_area_height)
        self.drop_area.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.drop_area.import_requested.connect(self.import_files)
        self.drop_area.files_dropped.connect(self.handle_dropped_paths)
        self.drop_area.text_dropped.connect(self.handle_dropped_text)

        self.paste_input = QPlainTextEdit()
        self.paste_input.setPlaceholderText(
            "Paste medical text here. It is processed automatically and can be copied."
        )
        self.paste_input.setFixedHeight(layout_profile.paste_input_height)
        self.paste_input.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.paste_input.textChanged.connect(self.handle_paste_input_changed)

        self.document_list = DocumentListWidget()
        self.document_list.setObjectName("documentList")
        self.document_list.currentItemChanged.connect(
            self.handle_document_selection_changed
        )
        self.document_list.remove_requested.connect(self.remove_document)
        self.document_list.setAlternatingRowColors(False)
        self.document_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.document_list.setMouseTracking(True)
        self.document_list.setSpacing(0)
        self.document_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.document_list.setMinimumHeight(0)
        self.document_list.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        self.document_list_delegate = DocumentListItemDelegate(self.document_list)
        self.document_list.setItemDelegate(self.document_list_delegate)
        self.document_list_spinner_timer = QTimer(self)
        self.document_list_spinner_timer.setInterval(80)
        self.document_list_spinner_timer.timeout.connect(
            self.document_list_delegate.advance_spinner
        )

        document_actions = QHBoxLayout()
        document_actions.setContentsMargins(0, 0, 0, 0)
        document_actions.setSpacing(layout_profile.document_action_spacing)
        self.clear_button = QPushButton("Clear All")
        self.clear_button.clicked.connect(self.clear_all_documents)
        self.copy_button = QPushButton("Copy Output")
        self.copy_button.clicked.connect(self.copy_output)
        self.export_button = QToolButton()
        self.export_button.setObjectName("exportButton")
        self.export_button.setText("Export")
        self.export_button.setMinimumWidth(layout_profile.export_button_min_width)
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.export_button.clicked.connect(
            lambda checked=False: self.export_original_formats()
        )
        self.export_menu = QMenu(self.export_button)
        self.export_button.setMenu(self.export_menu)
        self.export_original_action = self.export_menu.addAction(
            "Original formats (.pdf/.html/.txt)"
        )
        self.export_original_action.triggered.connect(
            lambda checked=False: self.export_original_formats()
        )
        self.export_text_action = self.export_menu.addAction("Text files (.txt)")
        self.export_text_action.triggered.connect(
            lambda checked=False: self.export_text_files()
        )
        document_actions.addWidget(self.clear_button)
        document_actions.addWidget(self.copy_button)
        document_actions.addWidget(self.export_button)
        document_actions.addStretch(1)

        left_layout.addWidget(self.drop_area)
        left_layout.addWidget(QLabel("Paste text"))
        left_layout.addWidget(self.paste_input)
        left_layout.addWidget(QLabel("Imported files"))
        left_layout.addWidget(self.document_list, stretch=1)
        left_layout.addLayout(document_actions)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(layout_profile.right_panel_spacing)

        self.document_status_label = QLabel("Paste text or select an imported file.")
        self.document_status_label.setWordWrap(True)
        self.document_status_label.setObjectName("documentStatus")

        self.output_view = ScanningPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.output_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.output_view.setMinimumHeight(0)

        right_layout.addWidget(QLabel("Output"))
        right_layout.addWidget(self.document_status_label)
        right_layout.addWidget(self.output_view, stretch=1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        initial_size = self.size()
        splitter.setSizes([min(400, int(initial_size.width() * 0.38)), max(480, int(initial_size.width() * 0.62))])

        root_layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(root)
        self._build_status_bar()

    def _build_status_bar(self) -> None:
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)
        status_bar.setContentsMargins(0, 0, 6, 0)

        self.bug_report_link_button = QToolButton()
        self.bug_report_link_button.setObjectName("bugReportLinkButton")
        self.bug_report_link_button.setAutoRaise(True)
        self.bug_report_link_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bug_report_link_button.setText("report a bug or incomplete anonimization")
        self.bug_report_link_button.setIcon(bug_report_icon())
        self.bug_report_link_button.setIconSize(QSize(12, 12))
        self.bug_report_link_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.bug_report_link_button.clicked.connect(self.open_bug_report_form)
        status_bar.addPermanentWidget(self.bug_report_link_button)
        status_bar.setFixedHeight(max(20, self.bug_report_link_button.sizeHint().height() + 2))

    def _recommended_window_size(self) -> QSize:
        screen = QApplication.primaryScreen()
        if screen is None:
            return DEFAULT_WINDOW_SIZE

        return recommended_window_size(screen.availableGeometry().size())

    def open_bug_report_form(self) -> None:
        QDesktopServices.openUrl(QUrl(BUG_REPORT_FORM_URL))

    def open_anonymization_dialog(self) -> None:
        dialog = AnonymizationDialog(
            self.anonymization_settings,
            preview_document_key=self._preview_document_key(),
            parent=self,
        )
        if not dialog.exec():
            return

        self.apply_anonymization_settings(dialog.settings())

    def _preview_document_key(self) -> str | None:
        pasted_text = self.current_pasted_text()
        if pasted_text:
            return document_smart_key(
                ImportedDocument(
                    id="preview-paste",
                    source_kind="paste",
                    display_name="Pasted text",
                    raw_text=pasted_text,
                )
            )

        document = self.current_document()
        if document is None:
            return None

        return document_smart_key(document)

    def current_backend_flags_key(self) -> tuple[bool, ...]:
        return self.anonymization_settings.recognition_flags.as_key()

    def schedule_background_backend_warmup(self, delay_ms: int | None = None) -> None:
        if self._closing:
            return

        self._background_backend_warmup_enabled = True
        if delay_ms is not None:
            self._background_backend_warmup_delay_ms = max(0, delay_ms)

        flags_key = self.current_backend_flags_key()
        if backend_is_ready(flags_key):
            self.backend_warmup_start_timer.stop()
            self._clear_expected_backend_preparation(flags_key)
            self.update_output_panel()
            self.refresh_actions()
            return

        if self.backend_warmup_worker is not None:
            if self._active_backend_warmup_flags_key != flags_key:
                self._queued_backend_warmup_flags_key = flags_key
            return

        self.backend_warmup_start_timer.start(self._background_backend_warmup_delay_ms)

    def enable_background_backend_warmup(self) -> None:
        self.schedule_background_backend_warmup()

    def defer_background_backend_warmup(self) -> None:
        if not self._background_backend_warmup_enabled or self._closing:
            return

        self.backend_warmup_start_timer.stop()
        self.schedule_background_backend_warmup()

    def _background_warmup_can_start(self) -> bool:
        return (
            not self.processing_active
            and not self.paste_processing_active
            and self.paste_processing_worker is None
            and not self.paste_processing_timer.isActive()
        )

    def start_background_backend_warmup(self) -> None:
        if not self._background_backend_warmup_enabled or self._closing:
            return

        self.backend_warmup_start_timer.stop()
        if not self._background_warmup_can_start():
            self.schedule_background_backend_warmup()
            return

        flags_key = self.current_backend_flags_key()
        if backend_is_ready(flags_key):
            self._clear_expected_backend_preparation(flags_key)
            self.update_output_panel()
            self.refresh_actions()
            return

        if self.backend_warmup_worker is not None:
            if self._active_backend_warmup_flags_key == flags_key:
                self._set_expected_backend_preparation(flags_key)
                return

            self._queued_backend_warmup_flags_key = flags_key
            return

        worker = BackendWarmupRunnable(
            BackendWarmupRequest(
                settings=self.anonymization_settings,
                flags_key=flags_key,
            )
        )
        worker.signals.completed.connect(self.on_backend_warmup_completed)
        worker.signals.failed.connect(self.on_backend_warmup_failed)
        self.backend_warmup_worker = worker
        self._active_backend_warmup_flags_key = flags_key
        self._set_expected_backend_preparation(flags_key)
        worker.start()

    def on_backend_warmup_completed(self, result: BackendWarmupResult) -> None:
        self.backend_warmup_worker = None
        self._active_backend_warmup_flags_key = None
        self._clear_expected_backend_preparation(result.flags_key)

        queued_flags_key = self._queued_backend_warmup_flags_key
        self._queued_backend_warmup_flags_key = None
        if (
            queued_flags_key is not None
            and queued_flags_key == self.current_backend_flags_key()
            and not backend_is_ready(queued_flags_key)
        ):
            self.schedule_background_backend_warmup()
            return

        self.update_output_panel()
        self.refresh_actions()

    def on_backend_warmup_failed(self, failure: BackendWarmupFailure) -> None:
        self.backend_warmup_worker = None
        self._active_backend_warmup_flags_key = None
        self._clear_expected_backend_preparation(failure.flags_key)

        queued_flags_key = self._queued_backend_warmup_flags_key
        self._queued_backend_warmup_flags_key = None
        if (
            queued_flags_key is not None
            and queued_flags_key == self.current_backend_flags_key()
            and not backend_is_ready(queued_flags_key)
        ):
            self.schedule_background_backend_warmup()
            return

        self.statusBar().showMessage(
            "Preparing anonymizer engine failed. It will retry on the next run.",
            5000,
        )
        self.update_output_panel()
        self.refresh_actions()

    def _set_expected_backend_preparation(self, flags_key: tuple[bool, ...]) -> None:
        if self._expected_backend_preparation_flags_key == flags_key:
            if self._backend_preparation_started_at is None:
                self._backend_preparation_started_at = monotonic()
            if not self.backend_status_timer.isActive():
                self.backend_status_timer.start()
            return

        self._expected_backend_preparation_flags_key = flags_key
        self._backend_preparation_started_at = monotonic()
        if not self.backend_status_timer.isActive():
            self.backend_status_timer.start()
        self.update_output_panel()
        self.refresh_actions()

    def _clear_expected_backend_preparation(
        self,
        flags_key: tuple[bool, ...] | None = None,
    ) -> None:
        if (
            flags_key is not None
            and self._expected_backend_preparation_flags_key != flags_key
        ):
            return

        self._expected_backend_preparation_flags_key = None
        self._backend_preparation_started_at = None
        self.backend_status_timer.stop()

    def _backend_preparation_in_flight(self, flags_key: tuple[bool, ...]) -> bool:
        if self._active_backend_warmup_flags_key == flags_key:
            return True
        if (
            self.processing_active
            and self.active_batch_worker is not None
            and self._active_batch_flags_key == flags_key
        ):
            return True
        if (
            self.paste_processing_worker is not None
            and self._active_paste_processing_flags_key == flags_key
        ):
            return True
        return False

    def _is_backend_preparation_pending(self) -> bool:
        flags_key = self.current_backend_flags_key()
        if backend_is_ready(flags_key):
            return False
        if self._expected_backend_preparation_flags_key != flags_key:
            return False
        return self._backend_preparation_in_flight(flags_key)

    def _refresh_backend_preparation_state(self) -> None:
        flags_key = self._expected_backend_preparation_flags_key
        if flags_key is None:
            self.backend_status_timer.stop()
            return

        current_flags_key = self.current_backend_flags_key()
        if flags_key != current_flags_key:
            self._clear_expected_backend_preparation(flags_key)
            self.update_output_panel()
            self.refresh_actions()
            return

        if backend_is_ready(current_flags_key):
            self._clear_expected_backend_preparation(current_flags_key)
            self.update_output_panel()
            self.refresh_actions()
            return

        if not self._backend_preparation_in_flight(current_flags_key):
            self._clear_expected_backend_preparation(current_flags_key)
            self.update_output_panel()
            self.refresh_actions()
            return

        self.update_output_panel()

    def _set_output_processing_state(self, active: bool, *, badge_text: str) -> None:
        self.output_view.set_processing_badge_text(badge_text)
        self.output_view.set_processing_active(active)

    def _backend_preparation_elapsed_text(self) -> str:
        started_at = self._backend_preparation_started_at
        if started_at is None:
            return "0.0s"
        return f"{max(0.0, monotonic() - started_at):.1f}s"

    def _show_backend_preparing_output(self) -> None:
        self.output_view.setPlaceholderText("")
        self.output_view.set_placeholder_references({})
        elapsed_text = self._backend_preparation_elapsed_text()
        self.output_view.setPlainText(PREPARING_BACKEND_TEXT)
        self._set_output_processing_state(
            True,
            badge_text=PREPARING_BACKEND_BADGE_TEXT,
        )
        self._set_document_status(
            f"{PREPARING_BACKEND_TEXT} ({elapsed_text})"
        )

    def apply_anonymization_settings(
        self,
        anonymization_settings: AnonymizationSettings,
        *,
        persist: bool = True,
        reprocess: bool = True,
    ) -> None:
        if anonymization_settings == self.anonymization_settings and persist:
            return

        self.anonymization_settings = anonymization_settings
        self._clear_expected_backend_preparation()

        if persist:
            save_anonymization_settings(anonymization_settings)

        if self._background_backend_warmup_enabled:
            self.schedule_background_backend_warmup()

        if reprocess:
            self.schedule_anonymization_settings_reprocess()
        else:
            self.update_output_panel()
            self.refresh_actions()

    def import_files(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Documents",
            "",
            "Documents (*.txt *.html *.htm *.pdf)",
        )
        if filenames:
            self.handle_dropped_paths([Path(name) for name in filenames])

    def set_pasted_text(self, text: str) -> None:
        self.paste_input.setPlainText(text.strip())

    def handle_dropped_text(self, text: str) -> None:
        cleaned_text = text.strip()
        if not cleaned_text:
            return

        self.set_pasted_text(cleaned_text)
        self.statusBar().showMessage("Loaded pasted text.", 3000)

    def handle_dropped_paths(self, paths: list[Path]) -> None:
        placeholder_documents = [
            ImportedDocument(
                id=self._new_document_id(),
                source_kind=_source_kind_for_path(path),
                display_name=path.name,
                path=path,
                status="processing",
            )
            for path in paths
        ]
        self._append_documents(
            placeholder_documents,
            start_processing=False,
            status_message=(
                f"Importing {placeholder_documents[0].display_name}..."
                if len(placeholder_documents) == 1
                else f"Importing {len(placeholder_documents)} document(s)..."
            ),
        )

        for document in placeholder_documents:
            if document.path is None:
                continue

            self.queued_import_requests.append(
                ImportDocumentRequest(path=document.path, document_id=document.id)
            )
        self._start_queued_imports()
        self.refresh_actions()

    def _append_documents(
        self,
        documents: list[ImportedDocument],
        *,
        start_processing: bool = True,
        status_message: str | None = None,
    ) -> None:
        if not documents:
            return

        self.documents.extend(documents)
        self.pending_document_ids.update(
            document.id for document in documents if document.raw_text is not None
        )
        self.refresh_document_list(select_document_id=documents[-1].id)
        if status_message is None:
            if len(documents) == 1:
                status_message = f"Loaded {documents[0].display_name}"
            else:
                status_message = f"Loaded {len(documents)} document(s)."
        self.statusBar().showMessage(status_message, 3000 if len(documents) == 1 else 5000)
        if start_processing:
            self.start_processing_if_possible()

    def _new_document_id(self) -> str:
        return uuid4().hex

    def handle_document_selection_changed(self) -> None:
        self.update_output_panel()
        self.refresh_actions()

    def current_document(self) -> ImportedDocument | None:
        item = self.document_list.currentItem()
        if not item:
            return None
        document_id = item.data(DOCUMENT_ID_ROLE)
        return self.document_by_id(document_id)

    def document_by_id(self, document_id: str) -> ImportedDocument | None:
        return next(
            (document for document in self.documents if document.id == document_id),
            None,
        )

    def on_import_completed(self, result: ImportDocumentResult) -> None:
        document = result.document
        self.active_import_workers.pop(document.id, None)
        self._start_queued_imports()

        if self._closing:
            return

        existing_document = self.document_by_id(document.id)
        if existing_document is None:
            return

        existing_document.source_kind = document.source_kind
        existing_document.display_name = document.display_name
        existing_document.path = document.path
        existing_document.raw_text = document.raw_text
        existing_document.pdf_pages = document.pdf_pages
        existing_document.error_message = document.error_message

        if document.raw_text is None:
            existing_document.status = "error"
            self.statusBar().showMessage(
                f"Could not import {document.display_name}.",
                5000,
            )
        else:
            existing_document.status = "pending"
            self.pending_document_ids.add(document.id)
            self.statusBar().showMessage(
                f"Imported {document.display_name}.",
                3000,
            )

        self.refresh_document_list(preserve_selection=True)
        self.start_processing_if_possible()
        if (
            self._background_backend_warmup_enabled
            and not self._has_pending_import_work()
        ):
            self.schedule_background_backend_warmup()

    def schedule_anonymization_settings_reprocess(self) -> None:
        self.anonymization_settings_generation += 1
        for document in self.documents:
            if document.raw_text is None:
                continue
            document.status = "pending"
            document.error_message = None
            self.pending_document_ids.add(document.id)
            self.processed_documents.pop(document.id, None)

        if self.documents:
            self.refresh_document_list(preserve_selection=True)
            self.statusBar().showMessage(
                "Anonymization settings updated. Reprocessing queued.",
                5000,
            )
        else:
            self.statusBar().showMessage("Anonymization settings saved.", 3000)

        self.schedule_pasted_text_processing(debounce=False)
        self.start_processing_if_possible()

    def start_processing_if_possible(self) -> None:
        if self._has_pending_import_work():
            self.refresh_actions()
            return

        if self.processing_active:
            self.refresh_actions()
            return

        documents_to_process = [
            document
            for document in self.documents
            if document.id in self.pending_document_ids and document.raw_text is not None
        ]
        if not documents_to_process:
            self.refresh_actions()
            return

        flags_key = self.current_backend_flags_key()
        for document in documents_to_process:
            document.status = "processing"
            document.error_message = None
            self.processed_documents.pop(document.id, None)

        request = ProcessBatchRequest(
            anonymization_settings=self.anonymization_settings,
            documents=documents_to_process,
            context_generation=self.anonymization_settings_generation,
        )
        worker = BatchProcessorRunnable(request)
        worker.signals.completed.connect(self.on_batch_completed)
        worker.signals.failed.connect(self.on_batch_failed)

        self.processing_active = True
        self.backend_warmup_start_timer.stop()
        self.active_batch_worker = worker
        self._active_batch_flags_key = flags_key
        self.active_batch_document_ids = {document.id for document in documents_to_process}
        self.pending_document_ids.difference_update(self.active_batch_document_ids)
        if not backend_is_ready(flags_key):
            self._set_expected_backend_preparation(flags_key)
        self.refresh_document_list(preserve_selection=True)
        self.refresh_actions()
        self.statusBar().showMessage(f"Processing {len(documents_to_process)} document(s)...")
        worker.start()

    def on_batch_completed(self, result: BatchProcessingResult) -> None:
        self.processing_active = False
        self.active_batch_worker = None
        self._active_batch_flags_key = None
        self.active_batch_document_ids.clear()

        documents_by_id = {document.id: document for document in self.documents}
        batch_is_current = result.context_generation == self.anonymization_settings_generation

        if batch_is_current:
            for document_id in result.document_ids:
                document = documents_by_id.get(document_id)
                if not document or document.raw_text is None:
                    continue
                if document_id in result.processed_documents:
                    document.status = "ready"
                    document.error_message = None
                    self.processed_documents[document_id] = result.processed_documents[
                        document_id
                    ]
                elif document_id in result.errors:
                    document.status = "error"
                    document.error_message = result.errors[document_id]
                    self.processed_documents.pop(document_id, None)

            self.statusBar().showMessage(
                f"Finished processing {len(result.processed_documents)} document(s).",
                5000,
            )
        else:
            for document_id in result.document_ids:
                document = documents_by_id.get(document_id)
                if not document or document.raw_text is None:
                    continue
                document.status = "pending"
                document.error_message = None
                self.pending_document_ids.add(document_id)
                self.processed_documents.pop(document_id, None)

            if self.pending_document_ids:
                self.statusBar().showMessage(
                    "Anonymization settings changed. Reprocessing queued.",
                    5000,
                )

        self.refresh_document_list(preserve_selection=True)
        self.refresh_actions()
        self.start_processing_if_possible()
        if self._background_backend_warmup_enabled:
            self.schedule_background_backend_warmup()

    def on_batch_failed(self, failure: BatchProcessingFailure) -> None:
        self.processing_active = False
        self.active_batch_worker = None
        self._active_batch_flags_key = None
        batch_is_current = (
            failure.context_generation == self.anonymization_settings_generation
        )

        for document in self.documents:
            if document.id not in self.active_batch_document_ids:
                continue
            if batch_is_current:
                document.status = "error"
                document.error_message = failure.message
            else:
                document.status = "pending"
                document.error_message = None
                self.pending_document_ids.add(document.id)
            self.processed_documents.pop(document.id, None)
        self.active_batch_document_ids.clear()

        if batch_is_current:
            self.statusBar().showMessage("Processing failed.", 5000)
            QMessageBox.critical(self, "Processing failed", failure.message)
        elif self.pending_document_ids:
            self.statusBar().showMessage(
                "Anonymization settings changed. Reprocessing queued.",
                5000,
            )

        self.refresh_document_list(preserve_selection=True)
        self.refresh_actions()
        self.start_processing_if_possible()
        if self._background_backend_warmup_enabled:
            self.schedule_background_backend_warmup()

    def refresh_document_list(
        self,
        select_document_id: str | None = None,
        preserve_selection: bool = False,
    ) -> None:
        selected_id = None
        if preserve_selection and self.document_list.currentItem():
            selected_id = self.document_list.currentItem().data(DOCUMENT_ID_ROLE)
        if select_document_id:
            selected_id = select_document_id

        self.document_list.clear()
        for document in self.documents:
            item = QListWidgetItem(document.display_name)
            item.setData(DOCUMENT_ID_ROLE, document.id)
            item.setData(DOCUMENT_STATUS_ROLE, document.status)
            item.setData(DOCUMENT_SOURCE_KIND_ROLE, document.source_kind)
            item.setData(DOCUMENT_ERROR_ROLE, document.error_message or "")
            item.setToolTip(self._document_tooltip(document))
            self.document_list.addItem(item)
        self._update_document_list_animation()

        selection_applied = False
        if selected_id:
            for row in range(self.document_list.count()):
                item = self.document_list.item(row)
                if item.data(DOCUMENT_ID_ROLE) == selected_id:
                    self.document_list.setCurrentRow(row)
                    selection_applied = True
                    break

        if not selection_applied and self.document_list.count() > 0:
            self.document_list.setCurrentRow(0)

        self.update_output_panel()
        self.refresh_actions()

    def _document_tooltip(self, document: ImportedDocument) -> str:
        if document.error_message:
            return document.error_message
        if document.status == "processing" and document.raw_text is None:
            return "Importing"
        return STATUS_LABELS[document.status]

    def _update_document_list_animation(self) -> None:
        has_processing_documents = any(
            document.status == "processing" for document in self.documents
        )
        if has_processing_documents:
            if not self.document_list_spinner_timer.isActive():
                self.document_list_spinner_timer.start()
            return

        if self.document_list_spinner_timer.isActive():
            self.document_list_spinner_timer.stop()

    def _set_document_status(self, text: str, *, tooltip: str = "") -> None:
        self.document_status_label.setText(text)
        self.document_status_label.setToolTip(tooltip)

    def _placeholder_help_tooltip(
        self, placeholder_references: dict[str, tuple[str, ...]]
    ) -> str:
        if not placeholder_references:
            return ""
        return (
            "Blue highlights mark pseudonyms, amber highlights mark placeholders. "
            "Hover a highlight to see the original text."
        )

    def _warnings_text(self, warnings: list[str]) -> str:
        if not warnings:
            return ""
        label = "Warning" if len(warnings) == 1 else "Warnings"
        return f"{label}: {'; '.join(warnings)}"

    def update_output_panel(self) -> None:
        pasted_text = self.current_pasted_text()
        if pasted_text:
            self._update_pasted_text_output(pasted_text)
            return

        document = self.current_document()
        if not document:
            if self._is_backend_preparation_pending():
                self._show_backend_preparing_output()
                return

            self._set_document_status("Paste text or select an imported file.")
            self._set_output_processing_state(
                False,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self.output_view.set_placeholder_references({})
            self.output_view.clear()
            return

        if document.status == "processing" and document.raw_text is None:
            self.output_view.setPlaceholderText("")
            self.output_view.clear()
            self.output_view.set_placeholder_references({})
            self._set_output_processing_state(
                True,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self._set_document_status(f"{document.display_name} is importing.")
            return

        processed = self.processed_documents.get(document.id)
        if processed:
            self._set_output_processing_state(
                False,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self.output_view.set_placeholder_references(
                processed.placeholder_references
            )
            self.output_view.setPlainText(processed.output_text)
            warnings_text = self._warnings_text(processed.warnings)
            status_text = (
                f"{document.display_name}. {warnings_text}"
                if warnings_text
                else document.display_name
            )
            self._set_document_status(
                status_text,
                tooltip=self._placeholder_help_tooltip(
                    processed.placeholder_references
                ),
            )
            return

        if document.status == "error":
            self._set_output_processing_state(
                False,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self.output_view.set_placeholder_references({})
            self.output_view.setPlainText(document.raw_text or "")
            error_message = document.error_message or "Could not be processed."
            self._set_document_status(f"{document.display_name}: {error_message}")
            return

        if self._is_backend_preparation_pending():
            self._show_backend_preparing_output()
            return

        self.output_view.setPlainText(document.raw_text or "")
        self.output_view.set_placeholder_references({})
        if document.status == "processing":
            self._set_output_processing_state(
                True,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self._set_document_status(f"{document.display_name} is processing.")
        else:
            self._set_output_processing_state(
                False,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self._set_document_status(document.display_name)

    def _update_pasted_text_output(self, pasted_text: str) -> None:
        processed = self.paste_processed_document
        if processed:
            self._set_output_processing_state(
                False,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self.output_view.set_placeholder_references(
                processed.placeholder_references
            )
            self.output_view.setPlainText(processed.output_text)
            warnings_text = self._warnings_text(processed.warnings)
            status_text = (
                f"Pasted text. {warnings_text}" if warnings_text else "Pasted text"
            )
            self._set_document_status(
                status_text,
                tooltip=self._placeholder_help_tooltip(
                    processed.placeholder_references
                ),
            )
            return

        self.output_view.setPlainText(pasted_text)
        self.output_view.set_placeholder_references({})

        if self.paste_error_message:
            self._set_output_processing_state(
                False,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self._set_document_status(
                f"Pasted text: {self.paste_error_message}".strip()
            )
            return

        if self._is_backend_preparation_pending():
            self._show_backend_preparing_output()
            return

        if self.paste_processing_active:
            self._set_output_processing_state(
                True,
                badge_text=SCANNING_BADGE_TEXT,
            )
            self._set_document_status("Pasted text is processing.")
            return

        self._set_output_processing_state(
            False,
            badge_text=SCANNING_BADGE_TEXT,
        )
        self._set_document_status("Pasted text")

    def copy_output(self) -> None:
        pasted_text = self.current_pasted_text()
        document = self.current_document()
        processed = (
            self.paste_processed_document
            if pasted_text
            else (
                self.processed_documents.get(document.id)
                if document is not None
                else None
            )
        )
        if not processed:
            return

        QApplication.clipboard().setText(processed.output_text)
        source_label = "pasted text" if pasted_text else document.display_name
        self.statusBar().showMessage(f"Copied output for {source_label}.", 3000)

    def export_original_formats(self) -> None:
        self.export_zip("original_formats")

    def export_text_files(self) -> None:
        self.export_zip("text_files")

    def export_zip(self, export_mode: ExportMode = "original_formats") -> None:
        if not self.processed_documents:
            QMessageBox.warning(
                self,
                "Nothing to export",
                "Process at least one document before exporting.",
            )
            return

        mode_label = (
            "Original formats"
            if export_mode == "original_formats"
            else "Text files"
        )
        default_filename = (
            "open-anonymizer-original-export.zip"
            if export_mode == "original_formats"
            else "open-anonymizer-text-export.zip"
        )
        suggested_path = self.export_directory() / default_filename
        filename, _ = QFileDialog.getSaveFileName(
            self,
            f"Export ZIP ({mode_label})",
            str(suggested_path),
            "ZIP archive (*.zip)",
        )
        if not filename:
            return

        export_path = Path(filename).expanduser()
        result = export_processed_documents(
            self.documents,
            self.processed_documents,
            export_path,
            export_mode=export_mode,
            anonymization_settings=self.anonymization_settings,
        )
        self.save_export_directory(export_path.parent)
        self.statusBar().showMessage(
            f"Exported {result.exported_count} document(s) as {mode_label.lower()} to {result.zip_path.name}.",
            5000,
        )
        QMessageBox.information(
            self,
            "Export complete",
            (
                f"ZIP saved to:\n{result.zip_path}\n\n"
                f"Mode: {mode_label}\n"
                f"Exported: {result.exported_count}\n"
                f"Skipped: {result.skipped_count}"
            ),
        )

    def export_directory(self) -> Path:
        settings = main_window_qsettings()
        raw_value = settings.value(EXPORT_DIRECTORY_SETTINGS_KEY, "", str) or ""
        saved_path = Path(raw_value).expanduser() if raw_value.strip() else None
        if saved_path is not None and saved_path.exists() and saved_path.is_dir():
            return saved_path
        return default_export_directory()

    def save_export_directory(self, directory: Path) -> None:
        settings = main_window_qsettings()
        settings.setValue(EXPORT_DIRECTORY_SETTINGS_KEY, str(directory))
        settings.sync()

    def remove_selected_document(self) -> None:
        document = self.current_document()
        if not document:
            return
        self.remove_document(document.id)

    def remove_document(self, document_id: str) -> None:
        if self.processing_active:
            return

        document = self.document_by_id(document_id)
        if not document:
            return

        worker = self.active_import_workers.pop(document.id, None)
        if worker is not None:
            worker.cancel()
        self.queued_import_requests = deque(
            request
            for request in self.queued_import_requests
            if request.document_id != document.id
        )
        self.documents = [item for item in self.documents if item.id != document.id]
        self.pending_document_ids.discard(document.id)
        self.active_batch_document_ids.discard(document.id)
        self.processed_documents.pop(document.id, None)
        self.refresh_document_list(preserve_selection=True)

    def clear_all_documents(self) -> None:
        if self.processing_active:
            return
        for worker in self.active_import_workers.values():
            worker.cancel()
        self.active_import_workers.clear()
        self.queued_import_requests.clear()
        self.documents.clear()
        self.pending_document_ids.clear()
        self.active_batch_document_ids.clear()
        self.processed_documents.clear()
        self.refresh_document_list()
        self.statusBar().clearMessage()

    def current_pasted_text(self) -> str:
        return self.paste_input.toPlainText().strip()

    def handle_paste_input_changed(self) -> None:
        self.schedule_pasted_text_processing(debounce=True)

    def schedule_pasted_text_processing(self, *, debounce: bool) -> None:
        self.paste_processing_timer.stop()
        self.paste_processing_generation += 1
        self.paste_processed_document = None
        self.paste_error_message = None

        if not self.current_pasted_text():
            self._paste_processing_restart_requested = False
            self._paste_processing_restart_debounce = False
            if self.paste_processing_worker is not None:
                self.paste_processing_worker.cancel()
            self.update_output_panel()
            self.refresh_actions()
            return

        if self.paste_processing_worker is not None:
            self.paste_processing_worker.cancel()
            self._queue_paste_processing_restart(debounce)
        else:
            self._schedule_paste_processing_run(debounce)

        self.update_output_panel()
        self.refresh_actions()

    def _queue_paste_processing_restart(self, debounce: bool) -> None:
        if self._paste_processing_restart_requested:
            self._paste_processing_restart_debounce = (
                self._paste_processing_restart_debounce and debounce
            )
        else:
            self._paste_processing_restart_requested = True
            self._paste_processing_restart_debounce = debounce
        self.paste_processing_active = True

    def _schedule_paste_processing_run(self, debounce: bool) -> None:
        if debounce:
            self.paste_processing_active = False
            self.paste_processing_timer.start()
            return

        self.process_pasted_text()

    def process_pasted_text(self) -> None:
        pasted_text = self.current_pasted_text()
        if not pasted_text or self.paste_processing_worker is not None:
            return

        generation = self.paste_processing_generation
        flags_key = self.current_backend_flags_key()
        request = ProcessBatchRequest(
            anonymization_settings=self.anonymization_settings,
            documents=[
                ImportedDocument(
                    id=f"paste-preview-{generation}",
                    source_kind="paste",
                    display_name="Pasted text",
                    raw_text=pasted_text,
                )
            ],
            context_generation=generation,
        )
        worker = BatchProcessorRunnable(request)
        worker.signals.completed.connect(self.on_paste_processing_completed)
        worker.signals.failed.connect(self.on_paste_processing_failed)

        self.paste_processing_active = True
        self.backend_warmup_start_timer.stop()
        self.paste_processing_worker = worker
        self._active_paste_processing_flags_key = flags_key
        if not backend_is_ready(flags_key):
            self._set_expected_backend_preparation(flags_key)
        self.update_output_panel()
        self.refresh_actions()
        worker.start()

    def on_paste_processing_completed(self, result: BatchProcessingResult) -> None:
        self.paste_processing_worker = None
        self._active_paste_processing_flags_key = None
        restart_requested = self._paste_processing_restart_requested
        restart_debounce = self._paste_processing_restart_debounce
        self._paste_processing_restart_requested = False
        self._paste_processing_restart_debounce = False
        self.paste_processing_active = False

        if result.context_generation == self.paste_processing_generation:
            self.paste_error_message = next(iter(result.errors.values()), None)
            self.paste_processed_document = next(
                iter(result.processed_documents.values()),
                None,
            )

        if restart_requested and self.current_pasted_text():
            self._schedule_paste_processing_run(restart_debounce)
        self.update_output_panel()
        self.refresh_actions()
        if self._background_backend_warmup_enabled:
            self.schedule_background_backend_warmup()

    def on_paste_processing_failed(self, failure: BatchProcessingFailure) -> None:
        self.paste_processing_worker = None
        self._active_paste_processing_flags_key = None
        restart_requested = self._paste_processing_restart_requested
        restart_debounce = self._paste_processing_restart_debounce
        self._paste_processing_restart_requested = False
        self._paste_processing_restart_debounce = False
        self.paste_processing_active = False

        if failure.context_generation == self.paste_processing_generation:
            self.paste_processed_document = None
            self.paste_error_message = failure.message

        if restart_requested and self.current_pasted_text():
            self._schedule_paste_processing_run(restart_debounce)
        self.update_output_panel()
        self.refresh_actions()
        if self._background_backend_warmup_enabled:
            self.schedule_background_backend_warmup()

    def refresh_actions(self) -> None:
        has_documents = bool(self.documents)
        has_pasted_text = bool(self.current_pasted_text())
        has_active_imports = self._has_pending_import_work()
        current_document = self.current_document()
        active_processed_document = (
            self.paste_processed_document
            if has_pasted_text
            else (
                self.processed_documents.get(current_document.id)
                if current_document is not None
                else None
            )
        )

        self.document_list.set_remove_enabled(not self.processing_active)
        self.clear_button.setEnabled(has_documents and not self.processing_active)
        self.copy_button.setEnabled(active_processed_document is not None)
        export_enabled = (
            bool(self.processed_documents)
            and not self.processing_active
            and not has_active_imports
            and not has_pasted_text
        )
        self.export_button.setEnabled(export_enabled)
        self.export_original_action.setEnabled(export_enabled)
        self.export_text_action.setEnabled(export_enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.paste_processing_timer.stop()
        self.backend_status_timer.stop()
        self.backend_warmup_start_timer.stop()
        if self.active_batch_worker is not None:
            self.active_batch_worker.cancel()
            self.active_batch_worker = None
        if self.paste_processing_worker is not None:
            self.paste_processing_worker.cancel()
            self.paste_processing_worker = None
        if self.backend_warmup_worker is not None:
            self.backend_warmup_worker.cancel()
            self.backend_warmup_worker = None
        for worker in self.active_import_workers.values():
            worker.cancel()
        self.active_import_workers.clear()
        self.queued_import_requests.clear()
        super().closeEvent(event)

    def _has_pending_import_work(self) -> bool:
        return bool(self.active_import_workers or self.queued_import_requests)

    def _start_queued_imports(self) -> None:
        max_concurrent_imports = configured_max_concurrent_imports()
        while (
            len(self.active_import_workers) < max_concurrent_imports
            and self.queued_import_requests
        ):
            request = self.queued_import_requests.popleft()
            if self.document_by_id(request.document_id) is None:
                continue

            worker = ImportDocumentRunnable(request)
            worker.signals.completed.connect(self.on_import_completed)
            self.active_import_workers[request.document_id] = worker
            worker.start()
