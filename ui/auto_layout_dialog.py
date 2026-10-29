"""自动布局的紧凑命名窗口，只编辑草稿，不直接写盘或切换推理。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QSizePolicy, QVBoxLayout, QWidget,
)

from config import settings


class AutoLayoutDialog(QDialog):
    """选择目标窗口并命名，长标题省略显示，完整内容保留在提示中。

    Attributes:
        layout_name: 确认后的布局名称，取消或未确认时为空。
        window_title: 确认后的原始完整窗口标题，不使用省略后的显示文本。
    """

    def __init__(
        self, titles: Sequence[str], parent: QWidget | None = None, *,
        initial_layout_name: str | None = None,
    ) -> None:
        """创建命名表单，已有布局允许保留当前未运行的目标窗口。

        Args:
            titles: 当前可选游戏窗口的完整标题。
            parent: 所属设置窗口。
            initial_layout_name: 已保存的自动布局名称；None表示新建。
        """
        super().__init__(parent)
        self.setWindowTitle('编辑自动布局' if initial_layout_name else '新建自动布局')
        self.setObjectName('AutoLayoutDialog')
        self.setMinimumWidth(320)
        self.setMaximumWidth(480)
        self.window_title = ''
        self.layout_name = ''
        self._original_name = initial_layout_name
        existing = settings.WINDOW_LAYOUTS.get(initial_layout_name or '', {})
        original_title = str(existing.get('window_title', ''))
        choices = list(dict.fromkeys(titles))
        if original_title and original_title not in choices:
            choices.insert(0, original_title)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        hint = QLabel('保存名称和游戏窗口，下次直接选择这个布局即可。自动布局始终识别完整画面。')
        hint.setWordWrap(True)
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(hint)
        form = QFormLayout()
        self.combo_window = QComboBox(self)
        self.combo_window.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.combo_window.setMinimumContentsLength(18)
        self.combo_window.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_window.setMaxVisibleItems(10)
        self.combo_window.addItems(choices)
        for index, title in enumerate(choices):
            self.combo_window.setItemData(index, title, Qt.ItemDataRole.ToolTipRole)
        self.combo_window.view().setMaximumWidth(440)
        self.combo_window.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.combo_window.view().setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if original_title:
            self.combo_window.setCurrentText(original_title)
        self.edit_name = QLineEdit(initial_layout_name or '', self)
        self.edit_name.setPlaceholderText('例如：MuMu欢乐斗地主')
        self.edit_name.setMinimumWidth(0)
        self.edit_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        form.addRow('游戏窗口：', self.combo_window)
        form.addRow('布局名称：', self.edit_name)
        root.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存并使用')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.combo_window.currentIndexChanged.connect(self._window_changed)
        self._window_changed()
        self.resize(440, self.sizeHint().height())

    def _window_changed(self) -> None:
        """更新完整标题提示，未手动命名的新建草稿跟随窗口生成默认名称。"""
        title = self.combo_window.currentText()
        self.combo_window.setToolTip(title)
        if not self._original_name and not self.edit_name.isModified():
            self.edit_name.setText(f'自动布局 · {title}' if title else '')

    def accept(self) -> None:
        """校验草稿，自动布局同名覆盖需确认，禁止覆盖手动框选布局。"""
        name = self.edit_name.text().strip()
        title = self.combo_window.currentText()
        if not name or not title:
            QMessageBox.warning(self, '无法保存', '请选择游戏窗口并填写布局名称。')
            return
        existing = settings.WINDOW_LAYOUTS.get(name)
        if existing is not None and existing.get('mode', 'manual') != 'auto':
            QMessageBox.warning(self, '名称已使用', '已有同名手动布局，请为自动布局换一个名称。')
            return
        if existing is not None and name != self._original_name:
            answer = QMessageBox.question(
                self, '覆盖自动布局', f'已存在“{name}”，是否覆盖？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.layout_name, self.window_title = name, title
        super().accept()
