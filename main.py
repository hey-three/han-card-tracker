import sys
import multiprocessing
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtCore import qInstallMessageHandler
from ui.main_window import CardUI
from ui.styles import load_qss
from config.settings import QSS_PATH


def _qt_message_handler(msg_type, context, message):
    try:
        msg = str(message)
        if 'QFont::setPointSize' in msg:
            return
    except Exception:
        pass
    sys.__stderr__.write(str(message) + "\n")


def main() -> None:
    """创建Qt应用并启动记牌界面，图片证据由完整诊断独立管理。"""
    try:
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass

    app = QApplication(sys.argv)

    # 设置一个明确的应用程序字体，避免 Qt 在内部使用无效的 pointSize (-1)
    try:
        default_font = QFont("Microsoft YaHei", 9)
        default_font.setPixelSize(14)
        app.setFont(default_font)
    except Exception:
        try:
            app.setFont(QFont())
        except Exception:
            pass

    # 读取并应用 QSS（如果存在）
    load_qss(app, QSS_PATH)

    w = CardUI()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
