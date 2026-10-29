from typing import List

import win32gui

from config import settings
from config.settings import save_window_layout
from core.screen_capture import ScreenCapture


def list_visible_window_titles() -> List[str]:
    titles = []

    def _enum_callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                titles.append(title)

    win32gui.EnumWindows(_enum_callback, None)
    return sorted(set(titles))


def capture_window_by_title(window_title: str):
    cap = ScreenCapture(window_title)
    return cap.capture_window()


def get_layout_names() -> List[str]:
    return list(settings.WINDOW_LAYOUTS.keys())


def get_layout_config(layout_name: str):
    layout = settings.WINDOW_LAYOUTS.get(layout_name)
    if not layout:
        return None
    return layout.get("window_title"), layout.get("layout")


def save_layout(layout_name: str, window_title: str, normalized_layout: dict, set_current: bool) -> None:
    save_window_layout(layout_name, window_title, normalized_layout, set_current=set_current)

