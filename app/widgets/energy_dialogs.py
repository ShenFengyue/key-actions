"""能量池的两个对话框：编辑 / 新建（PoolDialog）与 Markdown 档案（PoolLogDialog）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..energy import DEFAULT_COLORS, EnergyManager, PoolLog, percent_to_units, units_to_percent
from .common import AppDialog, Muted, confirm, warn


class PoolDialog(AppDialog):
    """编辑模式传 pool dict；新建模式 pool=None（初始 0%）。"""

    def __init__(self, manager: EnergyManager, pool: dict | None,
                 pool_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.pool = pool
        self.deleted = False
        self.is_free = bool(pool and pool.get("is_free"))
        self.new_color = (pool["color"] if pool
                          else DEFAULT_COLORS[pool_count % len(DEFAULT_COLORS)])
        self.setWindowTitle(
            "空余能量" if self.is_free else ("编辑能量池" if pool else "新增能量池"))
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(pool["name"] if pool else "")
        self.name_edit.setPlaceholderText("例如：英语、运动、副业")
        self.name_edit.setMaxLength(20)
        if self.is_free:
            self.name_edit.setEnabled(False)
        form.addRow("名称", self.name_edit)

        color_row = QHBoxLayout()
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(64, 28)
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_btn.clicked.connect(self._pick_color)
        if self.is_free:
            self.color_btn.setEnabled(False)
        self._refresh_color_btn()
        color_row.addWidget(self.color_btn)
        color_row.addStretch(1)
        form.addRow("颜色", color_row)

        self.percent_spin: QDoubleSpinBox | None = None
        if pool:
            if self.is_free:
                value = QLabel(f"{units_to_percent(pool['percentage_units'])} %")
                value.setStyleSheet("font-weight: 600;")
                form.addRow("占比", value)
                hint = Muted("空余能量 = 100% − 其他池占比之和，不能直接调。\n"
                             "给其他池加能量时从这里扣；删池或调低占比时回到这里。")
            else:
                self.percent_spin = QDoubleSpinBox()
                self.percent_spin.setRange(0.0, 100.0)
                self.percent_spin.setDecimals(2)
                self.percent_spin.setSingleStep(1.0)
                self.percent_spin.setSuffix(" %")
                self.percent_spin.setValue(float(units_to_percent(pool["percentage_units"])))
                form.addRow("占比", self.percent_spin)
                hint = Muted("增加占比从「空余能量」扣除，不够会提示能量不足；\n"
                             "调低占比，多出的能量回到空余。其他池不受影响。")
            hint.setWordWrap(True)
            form.addRow("", hint)
        else:
            hint = Muted("新池初始为 0%，创建后点击水缸即可调整占比。")
            hint.setWordWrap(True)
            form.addRow("", hint)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("保存")
        ok_button.setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        if pool and not self.is_free:
            del_btn = QPushButton("删除此池")
            del_btn.setStyleSheet(f"color: {theme.DANGER};")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(self._on_delete)
            layout.addWidget(del_btn)
        elif self.is_free:
            layout.addWidget(Muted("空余能量池常驻，不能删除。"))
        if pool:
            log_btn = QPushButton("变更记录（Markdown 档案）")
            log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            log_btn.clicked.connect(self._open_log)
            layout.addWidget(log_btn)
        layout.addWidget(buttons)

    def _open_log(self) -> None:
        PoolLogDialog(self.manager.log, self.pool, self).exec()

    def _refresh_color_btn(self) -> None:
        self.color_btn.setStyleSheet(
            f"background-color: {self.new_color}; border-radius: 6px;")

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.new_color), self, "选择液体颜色")
        if color.isValid():
            self.new_color = color.name()
            self._refresh_color_btn()

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            warn(self, "提示", "请填写能量池名称。")
            return
        if self.pool:
            # 先调占比（可能因能量不足失败）：失败时什么都不写，弹窗留着改
            if self.percent_spin is not None:
                new_units = percent_to_units(self.percent_spin.value())
                if new_units != self.pool["percentage_units"]:
                    try:
                        self.manager.set_percentage(self.pool["id"], new_units)
                    except ValueError as exc:
                        warn(self, "提示", str(exc))
                        return
            self.manager.update_info(self.pool["id"], name=name, color=self.new_color)
        else:
            self.manager.add_pool(name, self.new_color)
        self.accept()

    def _on_delete(self) -> None:
        if not confirm(
            self, "删除确认",
            f"确定删除“{self.pool['name']}”吗？\n它的能量会全部转入“空余能量”，等待你重新分配。",
            danger=True,
        ):
            return
        try:
            self.manager.delete_pool(self.pool["id"])
        except ValueError as exc:
            warn(self, "无法删除", str(exc))
            return
        self.deleted = True
        self.accept()


class PoolLogDialog(AppDialog):
    """能量池 Markdown 档案查看 / 编辑对话框。"""

    def __init__(self, pool_log: PoolLog, pool: dict,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pool_log = pool_log
        self.pool = pool
        self.setWindowTitle(f"变更记录 · {pool['name']}")
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        hint = Muted("Markdown 档案：池的每次变化都会自动追加到这里，可直接编辑。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(pool_log.read(pool["id"], pool["name"]))
        self.editor.setStyleSheet(
            "font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px;")
        layout.addWidget(self.editor, 1)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("清空记录")
        clear_btn.setStyleSheet(f"color: {theme.DANGER};")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear)
        save_btn = QPushButton("保存修改")
        save_btn.setObjectName("Primary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _save(self) -> None:
        self.pool_log.write(self.pool["id"], self.editor.toPlainText())
        self.accept()

    def _clear(self) -> None:
        if confirm(self, "清空确认", "确定清空这个池的全部变更记录吗？此操作不可撤销。",
                   danger=True):
            self.pool_log.clear(self.pool["id"], self.pool["name"])
            self.editor.setPlainText(
                self.pool_log.read(self.pool["id"], self.pool["name"]))