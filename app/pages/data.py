"""页面 E：本地数据 Settings。

查看数据库位置、手动备份、从备份恢复、JSON 导入导出、数据完整性检查。
所有导出、备份及配置文件都保存在项目文件夹内。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import clock, paths, theme
from ..appconfig import AppConfig
from ..models import NodeType
from ..services import ImportValidationError, LifeManagerService, ValidationError
from ..widgets.common import (
    Card,
    Muted,
    NodeComboBox,
    SectionHeader,
    Subtle,
    confirm,
    info,
    warn,
)


class DataPage(QWidget):
    changed = Signal()
    sloganEditRequested = Signal()

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None = None,
        *,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(14)

        layout.addWidget(self._build_location_card())
        layout.addWidget(self._build_slogan_card())
        layout.addWidget(self._build_backup_card())
        layout.addWidget(self._build_transfer_card())
        layout.addWidget(self._build_node_export_card())
        layout.addWidget(self._build_integrity_card())
        layout.addWidget(self._build_window_card())
        layout.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------ 标语历史
    def _build_slogan_card(self) -> QWidget:
        card = Card()
        header = QHBoxLayout()
        header.addWidget(SectionHeader("标语历史"))
        header.addStretch(1)
        edit = QPushButton("修改当前标语")
        edit.clicked.connect(self.sloganEditRequested.emit)
        header.addWidget(edit)
        card.body.addLayout(header)
        card.body.addWidget(
            Muted(
                "左栏顶部那句标语，每改一次就留一条记录；最新一条就是当前显示的"
                "（也可以直接在左栏双击标语来改）。"
            )
        )
        self.slogan_table = QTableWidget(0, 2)
        self.slogan_table.setHorizontalHeaderLabels(("修改时间", "标语"))
        self.slogan_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.slogan_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.slogan_table.verticalHeader().setVisible(False)
        self.slogan_table.setColumnWidth(0, 170)
        self.slogan_table.horizontalHeader().setStretchLastSection(True)
        self.slogan_table.setMinimumHeight(170)
        card.body.addWidget(self.slogan_table)
        return card

    def _reload_slogans(self) -> None:
        records = self.service.slogan_history()
        self.slogan_table.setRowCount(len(records))
        for row, slogan in enumerate(records):
            self.slogan_table.setItem(row, 0, QTableWidgetItem(slogan.created_at))
            text = slogan.display_text + ("　← 当前显示" if row == 0 else "")
            self.slogan_table.setItem(row, 1, QTableWidgetItem(text))

    # ---------------------------------------------------------------- 窗口
    def _build_window_card(self) -> QWidget:
        card = Card()
        card.body.addWidget(SectionHeader("窗口"))
        card.body.addWidget(
            Muted(
                "窗口的位置与大小会在关闭时记住，下次启动原样恢复；"
                "如果上次的位置超出了屏幕范围，启动时会自动校正到可见区域内。"
            )
        )
        self.maximized_checkbox = QCheckBox("启动时最大化（全屏）")
        if self.config is not None:
            self.maximized_checkbox.setChecked(bool(self.config.get("start_maximized")))
        else:
            self.maximized_checkbox.setEnabled(False)
        self.maximized_checkbox.stateChanged.connect(self._on_start_maximized_changed)
        card.body.addWidget(self.maximized_checkbox)
        card.body.addWidget(Subtle("关闭这个选项后，会按你上次调整的大小与位置打开。"))
        return card

    def _on_start_maximized_changed(self, state: int) -> None:
        if self.config is None:
            return
        self.config.set("start_maximized", bool(state))
        self.config.save()

    # ---------------------------------------------------------------- 位置
    def _build_location_card(self) -> QWidget:
        card = Card()
        card.body.addWidget(SectionHeader("数据位置"))
        card.body.addWidget(
            Muted("所有数据、日志、导出与备份都位于项目文件夹内，不写入 AppData、注册表或其他位置。")
        )
        rows = (
            ("项目根目录", paths.ROOT),
            ("SQLite 数据库（唯一正式数据源）", paths.DATABASE_PATH),
            ("日记文件（Markdown，全部日记写在这一个文件里）", paths.DIARY_PATH),
            ("备份目录", paths.BACKUP_DIR),
            ("导出目录", paths.EXPORT_DIR),
            ("日志文件", paths.LOG_PATH),
        )
        for label, path in rows:
            self._add_path_row(card, label, path)

        buttons = QHBoxLayout()
        copy_button = QPushButton("复制数据库路径")
        copy_button.clicked.connect(
            lambda: self._copy(str(paths.DATABASE_PATH))
        )
        open_button = QPushButton("打开数据文件夹")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.DATA_DIR)))
        )
        buttons.addWidget(copy_button)
        buttons.addWidget(open_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        return card

    def _add_path_row(self, card: Card, label: str, path: Path) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)
        name = QLabel(label)
        name.setMinimumWidth(210)
        name.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        row.addWidget(name)
        value = QLabel(str(path))
        value.setObjectName("Mono")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setWordWrap(True)
        row.addWidget(value, 1)
        card.body.addLayout(row)

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        info(self, "已复制", f"已复制到剪贴板：\n{text}")

    # ---------------------------------------------------------------- 备份
    def _build_backup_card(self) -> QWidget:
        card = Card()
        header = QHBoxLayout()
        header.addWidget(SectionHeader("本地备份与恢复"))
        header.addStretch(1)
        backup_button = QPushButton("立即创建备份")
        backup_button.setObjectName("Primary")
        backup_button.clicked.connect(self._make_backup)
        refresh_button = QPushButton("刷新列表")
        refresh_button.clicked.connect(self._reload_backups)
        header.addWidget(backup_button)
        header.addWidget(refresh_button)
        card.body.addLayout(header)

        self.backup_table = QTableWidget(0, 3)
        self.backup_table.setHorizontalHeaderLabels(("备份文件", "创建时间", "大小"))
        self.backup_table.verticalHeader().setVisible(False)
        self.backup_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.backup_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.backup_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.backup_table.setShowGrid(False)
        self.backup_table.setMinimumHeight(180)
        card.body.addWidget(self.backup_table)

        rows = QHBoxLayout()
        restore_button = QPushButton("从选中备份恢复…")
        restore_button.clicked.connect(self._restore_backup)
        verify_button = QPushButton("校验选中备份")
        verify_button.clicked.connect(self._verify_backup)
        rows.addWidget(restore_button)
        rows.addWidget(verify_button)
        rows.addStretch(1)
        card.body.addLayout(rows)
        card.body.addWidget(
            Subtle(
                "恢复会覆盖当前数据；执行前会自动把当前数据库另存为 pre-restore 备份。"
                "备份里如果带日记文件（同名 .diary.md），日记会一起回退到那个时间点。"
            )
        )
        card.body.addWidget(
            Subtle(
                "每天第一次启动会自动创建一份备份（文件名以 auto- 开头，日记一起存）。"
                "应用**不会自动删除任何备份**，需要清理时请你手动删。"
            )
        )
        return card

    def _reload_backups(self) -> None:
        backups = self.service.list_backups()
        self.backup_table.setRowCount(0)
        for path in backups:
            stat = path.stat()
            row = self.backup_table.rowCount()
            self.backup_table.insertRow(row)
            name_item = QTableWidgetItem(path.name)
            name_item.setData(1000, str(path))
            self.backup_table.setItem(row, 0, name_item)
            self.backup_table.setItem(
                row, 1, QTableWidgetItem(
                    datetime.fromtimestamp(stat.st_mtime, tz=clock.BEIJING_TZ).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
            )
            self.backup_table.setItem(
                row, 2, QTableWidgetItem(f"{stat.st_size / 1024:.0f} KB")
            )
        self.backup_table.resizeColumnsToContents()

    def _selected_backup(self) -> Path | None:
        rows = {index.row() for index in self.backup_table.selectedItems()}
        if not rows:
            warn(self, "未选择备份", "请先在列表里选择一个备份文件。")
            return None
        row = sorted(rows)[0]
        item = self.backup_table.item(row, 0)
        return Path(item.data(1000)) if item else None

    def _make_backup(self) -> None:
        target = self.service.backup("manual")
        self._reload_backups()
        info(self, "备份完成", f"已创建备份：\n{target}")

    def _verify_backup(self) -> None:
        path = self._selected_backup()
        if path is None:
            return
        ok, message = self.service.verify_backup(path)
        info(self, "校验结果", f"{path.name}\n\n{message}")

    def _restore_backup(self) -> None:
        path = self._selected_backup()
        if path is None:
            return
        ok, message = self.service.verify_backup(path)
        if not ok:
            warn(self, "无法恢复", message)
            return
        if not confirm(
            self,
            "确认恢复",
            f"用备份「{path.name}」覆盖当前数据？\n\n"
            "当前数据库会先被另存为 pre-restore 备份，因此可以再次恢复回来。",
            danger=True,
        ):
            return
        try:
            safety = self.service.restore_backup(path)
        except (ValidationError, ImportValidationError) as exc:
            warn(self, "恢复失败", str(exc))
            return
        self.refresh()
        self.changed.emit()
        info(
            self,
            "恢复完成",
            f"已从 {path.name} 恢复。\n\n恢复前的数据保存在：\n{safety}",
        )

    # ------------------------------------------------------------ 导入导出
    def _build_transfer_card(self) -> QWidget:
        card = Card()
        card.body.addWidget(SectionHeader("JSON 导入与导出"))
        card.body.addWidget(
            Muted(
                "导出会生成一份包含节点、关联、活动与历史事件的 JSON 文件；"
                "导入采用覆盖语义，导入前会自动备份，导入失败不会影响现有数据。"
            )
        )
        row = QHBoxLayout()
        export_button = QPushButton("导出为 JSON…")
        export_button.clicked.connect(self._export_json)
        import_button = QPushButton("从 JSON 导入（覆盖）…")
        import_button.clicked.connect(self._import_json)
        row.addWidget(export_button)
        row.addWidget(import_button)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _export_json(self) -> None:
        default_name = f"key-actions-{clock.now().strftime('%Y%m%d-%H%M%S')}.json"
        target, _ = QFileDialog.getSaveFileName(
            self, "导出为 JSON", str(paths.EXPORT_DIR / default_name), "JSON 文件 (*.json)"
        )
        if not target:
            return
        path = self.service.export_json(target)
        info(self, "导出完成", f"已导出：\n{path}")

    # ------------------------------------------------ 导出某个节点的活动记录
    def _build_node_export_card(self) -> QWidget:
        card = Card()
        card.body.addWidget(SectionHeader("导出活动记录"))
        card.body.addWidget(
            Muted(
                "选一个领域、进程或线程，把它全部的历史活动记录导出成 CSV"
                "（Excel 可以直接打开）。选领域会带上它下面所有进程与线程的记录；"
                "口径和界面上一致，按活动发生时的归属快照统计。"
            )
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.node_export_combo = NodeComboBox()
        self.node_export_combo.populate(
            self.service,
            kinds=(NodeType.AREA, NodeType.PROCESS, NodeType.THREAD),
        )
        row.addWidget(self.node_export_combo, 1)
        export_button = QPushButton("导出为 CSV…")
        export_button.clicked.connect(self._export_node_activities)
        row.addWidget(export_button)
        card.body.addLayout(row)
        self.node_export_hint = Subtle("")
        card.body.addWidget(self.node_export_hint)
        return card

    def _export_node_activities(self) -> None:
        node_id = self.node_export_combo.current_node_id()
        if not node_id:
            warn(self, "无法导出", "请先在上面选择一个领域、进程或线程。")
            return
        node = self.service.require_node(node_id)
        default_name = (
            f"{node.name}-活动记录-{clock.now().strftime('%Y%m%d-%H%M%S')}.csv"
        )
        target, _ = QFileDialog.getSaveFileName(
            self,
            "导出活动记录",
            str(paths.EXPORT_DIR / default_name),
            "CSV 文件 (*.csv)",
        )
        if not target:
            return
        try:
            path, count = self.service.export_activities_csv(node_id, target)
        except (OSError, ValidationError) as exc:
            warn(self, "导出失败", str(exc))
            return
        self.node_export_hint.setText(f"最近一次导出：{path}")
        if count:
            info(self, "导出完成", f"已导出 {count} 条活动记录：\n{path}")
        else:
            info(
                self,
                "导出完成",
                f"「{node.name}」目前还没有活动记录，导出的是一个只有表头的 CSV：\n{path}",
            )

    def _import_json(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self, "选择要导入的 JSON 文件", str(paths.EXPORT_DIR), "JSON 文件 (*.json)"
        )
        if not source:
            return
        try:
            payload = self.service.validate_import_file(source)
        except ImportValidationError as exc:
            warn(self, "文件无法导入", f"{exc}\n\n现有数据未被改动。")
            return
        if not confirm(
            self,
            "确认覆盖导入",
            f"将用该文件覆盖当前全部数据？\n\n"
            f"文件中包含 {len(payload['nodes'])} 个节点、{len(payload['activities'])} 条活动、"
            f"{len(payload['events'])} 条历史事件。\n\n"
            "导入前会自动把当前数据库另存为 pre-import 备份。",
            danger=True,
        ):
            return
        try:
            safety = self.service.import_json(source)
        except ImportValidationError as exc:
            warn(self, "导入失败", f"{exc}\n\n现有数据未被改动。")
            return
        self.refresh()
        self.changed.emit()
        info(self, "导入完成", f"已覆盖导入。\n\n导入前的数据保存在：\n{safety}")

    # ------------------------------------------------------------ 完整性
    def _build_integrity_card(self) -> QWidget:
        card = Card()
        header = QHBoxLayout()
        header.addWidget(SectionHeader("数据完整性检查"))
        header.addStretch(1)
        run_button = QPushButton("运行检查")
        run_button.setObjectName("Primary")
        run_button.clicked.connect(self._run_integrity)
        header.addWidget(run_button)
        card.body.addLayout(header)

        self.integrity_output = QPlainTextEdit()
        self.integrity_output.setReadOnly(True)
        self.integrity_output.setMinimumHeight(170)
        card.body.addWidget(self.integrity_output)
        card.body.addWidget(
            Subtle("检查内容：SQLite 结构、外键、归属类型、活动归属快照、关联合法性与重复 ID。")
        )
        return card

    def _run_integrity(self) -> None:
        report = self.service.db.integrity_report()
        stats = report["stats"]
        lines = [
            f"检查时间：{stats['checked_at']}",
            f"SQLite 结构检查：{report['sqlite']}",
            "",
            "统计：",
            f"  领域 {stats['areas']} · 进程 {stats['processes']} · 线程 {stats['threads']}",
            f"  活动 {stats['activities']} 条 · 历史事件 {stats['events']} 条",
            f"  有效关联 {stats['active_associations']} 条 · 已解除关联 {stats['ended_associations']} 条",
            f"  数据结构版本 v{stats['schema_version']}",
            "",
            "检查结果：",
        ]
        for finding in report["findings"]:
            prefix = {"ok": "✔", "warning": "!", "error": "✘"}.get(finding["level"], "·")
            lines.append(f"  {prefix} {finding['message']}")
        self.integrity_output.setPlainText("\n".join(lines))

    # ---------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        self._reload_backups()
        self._run_integrity()
        self._reload_slogans()
