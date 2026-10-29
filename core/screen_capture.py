"""Windows 平台的窗口截图工具。

该模块提供基于 Win32 API 的窗口截图功能，返回 PIL Image 对象。仅在
Windows 平台有效（依赖 win32gui/win32ui/win32con）。

优先请求目标窗口完整渲染，避免桌面截图混入覆盖目标的其他窗口。
GPU窗口不支持或返回黑画面时回退桌面拷贝，此时要求目标完整可见且无遮挡。
最小化不返回缩小的无效位图；截图期间统一物理像素坐标并恢复原线程DPI上下文。
"""

import ctypes
from ctypes import wintypes
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol
import win32con
import win32gui
import win32ui
from PIL import Image


class _GdiDC(Protocol):
    """仅声明截图清理所需的设备上下文接口。"""

    def DeleteDC(self) -> None:
        """释放原生设备上下文。"""
        ...


class _GdiBitmap(Protocol):
    """仅声明截图清理所需的位图句柄接口。"""

    def GetHandle(self) -> int:
        """返回供DeleteObject回收的原生位图句柄。"""
        ...


class ScreenCapture:
    """一个用于按窗口标题截图的简单封装类。

    Attributes:
        window_title: 可选的目标窗口标题（精确匹配）。如果为 None，则后续调用
            `capture_window` 时直接返回 None。
        last_capture_method: 本帧来源window/desktop，无法截图时为unavailable。
    """

    def __init__(self, window_title: str | None = None) -> None:
        """初始化 ScreenCapture 实例并设置 DPI 感知。

        Args:
            window_title: 目标窗口的精确标题；None或空字符串表示未选择窗口。
        Raises:
            TypeError: 标题不是字符串或None。
        """
        if window_title is not None and not isinstance(window_title, str):
            raise TypeError("window_title 必须是 str 或 None")

        # 在高 DPI 屏幕上确保坐标和截图尺寸正确
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            # 设置失败时不影响后续逻辑，保守继续执行
            pass

        self.window_title = window_title
        self.last_capture_method: str = 'unavailable'

    @staticmethod
    def _release_gdi(
        mem_dc: _GdiDC | None,
        img_dc: _GdiDC | None,
        hdesktop: int | None,
        desktop_dc: int | None,
        bmp: _GdiBitmap | None,
    ) -> None:
        """安全释放 GDI 资源，忽略二次释放等异常。

        Args:
            mem_dc: 内存设备上下文（win32ui.PyCDC）。
            img_dc: 图像设备上下文（win32ui.PyCDC）。
            hdesktop: 桌面窗口句柄。
            desktop_dc: 桌面设备上下文句柄。
            bmp: 位图对象（win32ui.PyCBitmap）。
        """
        for dc in (mem_dc, img_dc):
            if dc is not None:
                try:
                    dc.DeleteDC()
                except Exception:
                    pass
        if hdesktop is not None and desktop_dc is not None:
            try:
                win32gui.ReleaseDC(hdesktop, desktop_dc)
            except Exception:
                pass
        if bmp is not None:
            try:
                win32gui.DeleteObject(bmp.GetHandle())
            except Exception:
                pass

    @staticmethod
    @contextmanager
    def _physical_pixel_coordinates() -> Iterator[None]:
        """在截图期间统一使用物理像素，退出时恢复调用线程原来的DPI上下文。

        GetWindowRect与桌面BitBlt必须使用相同坐标单位；仅在初始化时调用
        SetProcessDPIAware可能因进程已初始化而失败，造成窗口矩形被DPI虚拟化。

        Yields:
            窗口矩形和GDI拷贝处于同一物理像素坐标系的执行区间。
        Raises:
            RuntimeError: 系统提供线程DPI接口，但无法建立非虚拟化坐标系。
        """
        setter = getattr(ctypes.windll.user32, 'SetThreadDpiAwarenessContext', None)
        if setter is None:
            # 较旧系统沿用构造时设置的进程DPI模式。
            yield
            return
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        previous = setter(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        if not previous:
            previous = setter(ctypes.c_void_p(-3))  # PER_MONITOR_AWARE
        if not previous:
            raise RuntimeError('无法建立物理像素截图坐标，停止本帧以避免截取不完整')
        try:
            yield
        finally:
            setter(ctypes.c_void_p(previous))

    def capture_window(self) -> Image.Image | None:
        """以完整窗口物理像素范围截图，不按模型尺寸或手动选区裁剪。

        Returns:
            完整窗口的RGB图；目标不存在时为None。模型的等比补边在后续推理中完成。
        Raises:
            RuntimeError: 无法统一DPI坐标、窗口尺寸无效或GDI截图失败。
        """
        self.last_capture_method = 'unavailable'
        if not self.window_title:
            return None
        with self._physical_pixel_coordinates():
            hwnd = win32gui.FindWindow(None, self.window_title)
            if not hwnd:
                print(f"没找到窗口: {self.window_title}")
                return None
            if win32gui.IsIconic(hwnd):
                return None
            return self._capture_physical_window(hwnd)

    def _capture_physical_window(self, hwnd: int) -> Image.Image:
        """在调用方建立的物理像素坐标系中读取整个窗口矩形。

        Args:
            hwnd: 目标窗口句柄，矩形与桌面GDI均不得切回逻辑坐标。
        Returns:
            与完整窗口矩形等宽高的RGB截图。
        Raises:
            RuntimeError: 窗口宽高非正数或GDI截图失败。
        """

        # 局部 GDI 对象先初始化为 None，以便在 finally 中安全清理
        hdesktop = None
        desktop_dc = None
        img_dc = None
        mem_dc = None
        bmp = None

        try:
            try:
                # 2) 获取窗口矩形并计算宽高
                left, top, right, bot = win32gui.GetWindowRect(hwnd)
                w = right - left
                h = bot - top
                if w <= 0 or h <= 0:
                    raise ValueError('目标窗口尺寸无效，无法截取完整画面')

                # 3) 准备设备上下文并执行位块传输
                hdesktop = 0
                desktop_dc = win32gui.GetDC(0)
                img_dc = win32ui.CreateDCFromHandle(desktop_dc)
                mem_dc = img_dc.CreateCompatibleDC()

                bmp = win32ui.CreateBitmap()
                bmp.CreateCompatibleBitmap(img_dc, w, h)
                mem_dc.SelectObject(bmp)

                def read_pixels() -> Image.Image:
                    """读取整个位图的RGB像素，不做模型尺寸或ROI裁切。

                    Returns:
                        与完整窗口矩形等宽高的RGB图。
                    """
                    return Image.frombuffer('RGB', (w, h), bmp.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)

                # 桌面拷贝会把覆盖目标的预览/设置窗口也截进去，造成局部画面或递归预览。
                # 优先由目标窗口绘制完整内容；部分GPU窗口不支持时才读可见桌面。
                mem_dc.PatBlt((0, 0), (w, h), win32con.BLACKNESS)
                print_window = ctypes.windll.user32.PrintWindow
                print_window.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
                print_window.restype = wintypes.BOOL
                if print_window(hwnd, mem_dc.GetSafeHdc(), 2):  # PW_RENDERFULLCONTENT
                    rendered = read_pixels()
                    center = rendered.crop((w//8, h//8, w-w//8, h-h//8))
                    if center.getbbox() is not None:
                        self.last_capture_method = 'window'
                        return rendered

                # 将桌面指定区域拷贝到内存位图
                mem_dc.BitBlt((0, 0), (w, h), img_dc, (left, top), win32con.SRCCOPY)
                self.last_capture_method = 'desktop'

                img = read_pixels()
            finally:
                # 5) 释放 GDI 资源（无论是否异常都会执行）
                self._release_gdi(mem_dc, img_dc, hdesktop, desktop_dc, bmp)
        except Exception as e:
            raise RuntimeError(f"窗口截图失败: {e}") from e

        return img
