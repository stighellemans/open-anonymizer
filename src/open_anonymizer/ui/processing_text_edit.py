from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QHelpEvent,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip


PLACEHOLDER_PATTERN = re.compile(r"\[[A-Z][A-Z0-9_+]*(?:-\d+)?\]")
PLACEHOLDER_BACKGROUND = QColor("#fef3c7")
PLACEHOLDER_FOREGROUND = QColor("#92400e")
PSEUDONYM_BACKGROUND = QColor("#dbeafe")
PSEUDONYM_FOREGROUND = QColor("#1d4ed8")


class ScanningPlainTextEdit(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._processing_active = False
        self._processing_badge_text = "Scanning"
        self._placeholder_references: dict[str, tuple[str, ...]] = {}
        self._reference_ranges: list[tuple[int, int, str]] = []
        self._scan_phase = 0.0
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(32)
        self._scan_timer.timeout.connect(self._advance_scan)
        self.setMouseTracking(True)
        self.textChanged.connect(self._rebuild_reference_ranges)

    def set_processing_active(self, active: bool) -> None:
        if self._processing_active == active:
            return

        self._processing_active = active
        if active:
            self._scan_timer.start()
        else:
            self._scan_timer.stop()
            self._scan_phase = 0.0

        self.viewport().update()

    def is_processing_active(self) -> bool:
        return self._processing_active

    def set_processing_badge_text(self, text: str) -> None:
        badge_text = text or "Scanning"
        if self._processing_badge_text == badge_text:
            return

        self._processing_badge_text = badge_text
        if self._processing_active:
            self.viewport().update()

    def set_placeholder_references(
        self,
        placeholder_references: dict[str, tuple[str, ...]],
    ) -> None:
        normalized_references = dict(placeholder_references)
        if self._placeholder_references == normalized_references:
            return

        self._placeholder_references = normalized_references
        self._rebuild_reference_ranges()
        if not self._placeholder_references:
            QToolTip.hideText()

    def tooltip_text_for_position(self, position: int) -> str | None:
        replacement = self._replacement_at_position(position)
        if replacement is None:
            return None

        originals = self._placeholder_references.get(replacement)
        if not originals:
            return None

        replacement_kind = (
            "Placeholder"
            if PLACEHOLDER_PATTERN.fullmatch(replacement)
            else "Pseudonym"
        )
        if len(originals) == 1:
            return f"{replacement_kind}: {replacement}\nOriginal text: {originals[0]}"

        return (
            f"{replacement_kind}: {replacement}\nOriginal texts:\n"
            + "\n".join(originals)
        )

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            help_event = event if isinstance(event, QHelpEvent) else None
            if help_event is None:
                return super().viewportEvent(event)

            cursor = self.cursorForPosition(help_event.pos())
            tooltip_text = self.tooltip_text_for_position(cursor.position())
            if tooltip_text:
                QToolTip.showText(
                    help_event.globalPos(),
                    tooltip_text,
                    self.viewport(),
                )
                return True

            QToolTip.hideText()
            event.ignore()
            return True

        if event.type() == QEvent.Type.Leave:
            QToolTip.hideText()

        return super().viewportEvent(event)

    def _advance_scan(self) -> None:
        self._scan_phase = (self._scan_phase + 0.02) % 1.0
        self.viewport().update()

    def _rebuild_reference_ranges(self) -> None:
        self._reference_ranges = []
        if not self._placeholder_references:
            self.setExtraSelections([])
            return

        text = self.toPlainText()
        if not text:
            self.setExtraSelections([])
            return

        for replacement in sorted(
            self._placeholder_references,
            key=len,
            reverse=True,
        ):
            if not replacement:
                continue

            start = text.find(replacement)
            while start != -1:
                self._reference_ranges.append(
                    (start, start + len(replacement), replacement)
                )
                start = text.find(replacement, start + len(replacement))

        self._reference_ranges.sort(
            key=lambda item: (item[0], -(item[1] - item[0]), item[2])
        )
        self._apply_reference_highlights()

    def _apply_reference_highlights(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        for start, end, replacement in self._reference_ranges:
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)

            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = self._highlight_format(replacement)
            selections.append(selection)

        self.setExtraSelections(selections)

    def _highlight_format(self, replacement: str) -> QTextCharFormat:
        text_format = QTextCharFormat()
        if PLACEHOLDER_PATTERN.fullmatch(replacement):
            text_format.setBackground(PLACEHOLDER_BACKGROUND)
            text_format.setForeground(PLACEHOLDER_FOREGROUND)
            text_format.setFontWeight(700)
            return text_format

        text_format.setBackground(PSEUDONYM_BACKGROUND)
        text_format.setForeground(PSEUDONYM_FOREGROUND)
        text_format.setFontWeight(600)
        return text_format

    def _replacement_at_position(self, position: int) -> str | None:
        if not self._placeholder_references:
            return None

        text = self.toPlainText()
        if not text:
            return None

        candidate_positions: list[int] = []
        if 0 <= position < len(text):
            candidate_positions.append(position)
        if position > 0 and position - 1 < len(text):
            candidate_positions.append(position - 1)

        best_replacement: str | None = None
        best_length = -1
        for candidate_position in candidate_positions:
            for start, end, replacement in self._reference_ranges:
                if start > candidate_position:
                    break
                if start <= candidate_position < end:
                    length = end - start
                    if length > best_length:
                        best_replacement = replacement
                        best_length = length

        return best_replacement

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._processing_active:
            return

        viewport = self.viewport()
        rect = viewport.rect()
        if rect.isEmpty():
            return

        painter = QPainter(viewport)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.fillRect(rect, QColor(37, 99, 235, 10))

        band_width = max(140.0, rect.width() * 0.24)
        travel_width = rect.width() + (band_width * 2)
        center_x = -band_width + (travel_width * self._scan_phase)
        gradient = QLinearGradient(
            center_x - (band_width / 2),
            0.0,
            center_x + (band_width / 2),
            0.0,
        )
        gradient.setColorAt(0.0, QColor(59, 130, 246, 0))
        gradient.setColorAt(0.35, QColor(96, 165, 250, 30))
        gradient.setColorAt(0.5, QColor(59, 130, 246, 85))
        gradient.setColorAt(0.65, QColor(96, 165, 250, 30))
        gradient.setColorAt(1.0, QColor(59, 130, 246, 0))
        painter.fillRect(rect, gradient)

        badge_text = self._processing_badge_text
        metrics = painter.fontMetrics()
        badge_width = metrics.horizontalAdvance(badge_text) + 20
        badge_height = 26
        badge_left = rect.right() - badge_width - 14
        badge_top = rect.top() + 12
        badge_rect = rect.adjusted(
            badge_left - rect.left(),
            badge_top - rect.top(),
            -(rect.width() - badge_left - badge_width),
            -(rect.height() - badge_top - badge_height),
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(37, 99, 235, 220))
        painter.drawRoundedRect(badge_rect, 13.0, 13.0)
        painter.setPen(QColor("#f8fbff"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
        painter.end()
