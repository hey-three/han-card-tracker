# -*- coding: utf-8 -*-
"""游戏模块。

提供游戏 Tracker 的工厂函数，根据游戏名称创建对应的子类实例。

新增游戏只需：
1. 在 ``config/games/`` 下添加 YAML 配置文件
2. 在 ``core/games/`` 下添加 Tracker 子类
3. 在 ``TRACKER_REGISTRY`` 中注册
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.base_tracker import BaseCardTracker

# 游戏名称 → Tracker 子类的注册表
# 延迟导入以避免循环依赖
TRACKER_REGISTRY: dict[str, str] = {
    "doudizhu": "core.games.doudizhu:DoudizhuTracker",
}


def create_tracker(game_name: str, layout_name: str | None = None) -> BaseCardTracker:
    """根据游戏名称创建对应的 Tracker 实例。

    Args:
        game_name: 游戏名称（如 ``"doudizhu"``）。
        layout_name: 布局名称。为 None 时自动使用第一个可用配置。

    Returns:
        BaseCardTracker: 游戏对应的 Tracker 子类实例。

    Raises:
        ValueError: 游戏名称未注册时抛出。
    """
    entry = TRACKER_REGISTRY.get(game_name)
    if entry is None:
        raise ValueError(f"未注册的游戏: {game_name}，可用游戏: {list(TRACKER_REGISTRY.keys())}")

    module_path, class_name = entry.rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(layout_name=layout_name)
