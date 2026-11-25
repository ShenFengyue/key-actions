"""活动记录修改对话框：只允许改备注与日期，归属快照不可改。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import Activity
from ..services import LifeManagerService, ValidationError
from .common import AppDialog, Muted, SectionHeader, Subtle, str_to_qdate, qdate_to_str


class ActivityEditDialog(AppDialog):
    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None,
        activity: Activity,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.activity = activity
        self.saved = False
        self.setWindowTitle("修改活动记录")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(SectionHeader("修改活动记录"))
        layout.addWidget(Muted(f"归属快照：{activity.path_text}"))
        layout.addWidget(
            Subtle("归属快照记录的是活动发生时的事实，不能修改；要改变归属请记一条新活动。")
        )

        layout.addWidget(QLabel("日期"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(str_to_qdate(activity.activity_date))
        layout.addWidget(self.date_edit)

        layout.addWidget(QLabel("备注（可留空）"))
        self.note_edit = QLineEdit(activity.note)
        layout.addWidget(self.note_edit)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {theme.DANGER}; font-size: {theme.FONT_SIZE - 1}px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox()
        save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setObjectName("Primary")
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            self.service.update_activity(
                self.activity.id,
                note=self.note_edit.text(),
                activity_date=qdate_to_str(self.date_edit.date()),
            )
        except ValidationError as exc:
            self.error_label.setText(str(exc))
            self.error_label.setVisible(True)
            return
        self.saved = True
        self.accept()
