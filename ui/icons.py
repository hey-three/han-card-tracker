"""适配明暗主题的统一细线操作图标，不使用带颜色或实心系统贴图。"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


_ICON_CACHE: dict[tuple[str, int], QIcon] = {}

def line_icon(name: str, color: QColor) -> QIcon:
    """绘制24逻辑像素、双倍分辨率的无填充图标。

    Args:
        name: settings、pause、play、reset、edit或delete。
        color: 当前主题的前景颜色。
    Returns:
        含正常与禁用状态的Qt图标。
    Raises:
        TypeError: 参数类型错误。
        ValueError: 图标名不支持。
    """
    if not isinstance(name, str) or not isinstance(color, QColor):
        raise TypeError('图标名称和颜色类型错误')
    if name not in ('settings', 'pause', 'play', 'reset', 'edit', 'delete'):
        raise ValueError(f'不支持的图标：{name}')
    cache_key = (name, color.rgba())
    if cache_key in _ICON_CACHE:
        return QIcon(_ICON_CACHE[cache_key])
    icon = QIcon()
    for mode, opacity in ((QIcon.Mode.Normal, 1.), (QIcon.Mode.Disabled, .35)):
        pixmap = QPixmap(48, 48)
        pixmap.setDevicePixelRatio(2.)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(opacity)
        pen = QPen(color, 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if name == 'settings':
            for tooth in range(8):
                for segment, (offset, radius) in enumerate(((-.38, 7.), (-.21, 7.), (-.15, 9.), (.15, 9.), (.21, 7.), (.38, 7.))):
                    angle = tooth*math.pi/4+offset*math.pi/4
                    point = QPointF(12+radius*math.cos(angle), 12+radius*math.sin(angle))
                    if tooth == 0 and segment == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
            path.closeSubpath()
            painter.drawEllipse(QRectF(9., 9., 6., 6.))
        elif name == 'pause':
            path.moveTo(9, 5)
            path.lineTo(9, 19)
            path.moveTo(15, 5)
            path.lineTo(15, 19)
        elif name == 'play':
            path.moveTo(8, 5)
            path.lineTo(19, 12)
            path.lineTo(8, 19)
            path.closeSubpath()
        elif name == 'reset':
            painter.drawArc(QRectF(4., 4., 16., 16.), 40*16, 300*16)
            path.moveTo(18, 3)
            path.lineTo(18, 7)
            path.lineTo(14, 7)
        elif name == 'edit':
            path.moveTo(5, 15)
            path.lineTo(15, 5)
            path.lineTo(19, 9)
            path.lineTo(9, 19)
            path.lineTo(4, 20)
            path.closeSubpath()
            path.moveTo(13, 7)
            path.lineTo(17, 11)
        else:
            path.moveTo(4, 7)
            path.lineTo(20, 7)
            path.moveTo(9, 4)
            path.lineTo(15, 4)
            path.moveTo(6, 7)
            path.lineTo(7, 20)
            path.lineTo(17, 20)
            path.lineTo(18, 7)
            path.moveTo(10, 10)
            path.lineTo(10, 17)
            path.moveTo(14, 10)
            path.lineTo(14, 17)
        painter.drawPath(path)
        painter.end()
        icon.addPixmap(pixmap, mode)
    if len(_ICON_CACHE) >= 64:
        _ICON_CACHE.clear()
    _ICON_CACHE[cache_key] = icon
    return icon
