import os
import sys
import shutil
import yaml
from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isfinite
from typing import TypedDict, cast

from config.store import read_config, write_config

# ==================== 路径配置 ====================
# BASE_DIR: 可写运行目录（用于持久化配置/调试输出）
# RESOURCE_DIR: 资源读取目录（兼容 PyInstaller 的 _internal 布局）
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _looks_like_resource_root(base_dir: str) -> bool:
    expected_files = (
        ('yolo', 'weights'),
        ('ui', 'ui.qss'),
        ('config', 'config.yaml'),
    )
    for parts in expected_files:
        if os.path.exists(os.path.join(base_dir, *parts)):
            return True
    return False


def _resolve_resource_dir() -> str:
    if not getattr(sys, 'frozen', False):
        return BASE_DIR

    candidates = []
    meipass_dir = getattr(sys, '_MEIPASS', None)
    if meipass_dir:
        candidates.append(meipass_dir)
    candidates.append(os.path.join(BASE_DIR, '_internal'))
    candidates.append(BASE_DIR)

    for candidate in candidates:
        if _looks_like_resource_root(candidate):
            return candidate
    return BASE_DIR


RESOURCE_DIR = _resolve_resource_dir()
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.yaml')
DEFAULT_CONFIG_PATH = os.path.join(RESOURCE_DIR, 'config', 'config.yaml')
YOLO_WEIGHTS_DIR = os.path.join(RESOURCE_DIR, 'yolo', 'weights')
QSS_PATH = os.path.join(RESOURCE_DIR, 'ui', 'ui.qss')
GAMES_DIR = os.path.join(RESOURCE_DIR, 'config', 'games')


def _scan_model_dirs() -> list[str]:
    """扫描 yolo/weights/ 下的子目录，返回包含 best.pt 的子目录名列表。"""
    dirs: list[str] = []
    if not os.path.isdir(YOLO_WEIGHTS_DIR):
        return dirs
    for name in sorted(os.listdir(YOLO_WEIGHTS_DIR)):
        sub = os.path.join(YOLO_WEIGHTS_DIR, name)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, 'best.pt')):
            dirs.append(name)
    return dirs


def _resolve_model_path(model_name: str | None) -> str:
    """根据模型名称解析 best.pt 的完整路径。

    - model_name 为子目录名 → yolo/weights/<model_name>/best.pt
    - model_name 为 None → 使用第一个可用子目录的 best.pt
    - 所有模型必须放在 yolo/weights/ 的子目录中。
    """
    if model_name:
        return os.path.join(YOLO_WEIGHTS_DIR, model_name, 'best.pt')
    dirs = _scan_model_dirs()
    if dirs:
        return os.path.join(YOLO_WEIGHTS_DIR, dirs[0], 'best.pt')
    return os.path.join(YOLO_WEIGHTS_DIR, 'default', 'best.pt')


def _scan_game_configs() -> list[str]:
    """扫描 config/games/ 目录下的 YAML 文件，返回游戏名称列表。"""
    if not os.path.isdir(GAMES_DIR):
        return ['doudizhu']
    names = []
    for f in sorted(os.listdir(GAMES_DIR)):
        if f.endswith('.yaml') or f.endswith('.yml'):
            names.append(os.path.splitext(f)[0])
    return names if names else ['doudizhu']


def _load_game_config(game_name: str) -> dict:
    """加载指定游戏的配置文件。

    Args:
        game_name: 游戏名称（对应 config/games/ 下的 YAML 文件名）。

    Returns:
        dict: 游戏配置字典。加载失败时返回斗地主默认配置。
    """
    path = os.path.join(GAMES_DIR, f'{game_name}.yaml')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"加载游戏配置失败: {e}")
    # Fallback: 内置斗地主默认配置
    return {
        'game_name': '斗地主',
        'total_cards': {
            '3': 4, '4': 4, '5': 4, '6': 4, '7': 4, '8': 4, '9': 4, '10': 4,
            'J': 4, 'Q': 4, 'K': 4, 'A': 4, '2': 4, '小': 1, '大': 1,
        },
        'played_zones': [
            {'key': 'left', 'label': '上家', 'region': 'opponent_left'},
            {'key': 'self', 'label': '本家', 'region': 'player_played'},
            {'key': 'right', 'label': '下家', 'region': 'opponent_right'},
        ],
        'layout_regions': [
            {'key': 'player_hand', 'label': '玩家手牌'},
            {'key': 'player_played', 'label': '本家出牌'},
            {'key': 'opponent_left', 'label': '上家出牌'},
            {'key': 'opponent_right', 'label': '下家出牌'},
            {'key': 'landlord_cards', 'label': '地主底牌'},
        ],
    }


def _ensure_runtime_config_exists():
    """冻结环境下：首次运行时将打包内配置复制到可写目录。"""
    if not getattr(sys, 'frozen', False):
        return

    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)

    if os.path.exists(CONFIG_PATH):
        return

    if os.path.exists(DEFAULT_CONFIG_PATH):
        try:
            shutil.copy2(DEFAULT_CONFIG_PATH, CONFIG_PATH)
            print(f"初始化配置文件: {CONFIG_PATH}")
        except Exception as e:
            print(f"初始化配置文件失败: {e}")


_ensure_runtime_config_exists()

# 加载配置文件
def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        if DEFAULT_CONFIG_PATH != CONFIG_PATH:
            try:
                with open(DEFAULT_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                print(f"已从打包资源读取默认配置: {DEFAULT_CONFIG_PATH}")
                return config
            except Exception as e2:
                print(f"加载打包内默认配置失败: {e2}")
        # 返回默认配置作为 fallback
        return {
            'reset_time': 3.5,
            'detect_interval_sec': 0.2,
            'little_joker_shown': "🃟",
            'big_joker_shown': "🃏",
            'yolo_confidence_threshold': 0.6,
            'yolo_iou_threshold': 0.45,
            'yolo_to_card_mapping': {
                'two': '2',
                'three': '3',
                'four': '4',
                'five': '5',
                'six': '6',
                'seven': '7',
                'eight': '8',
                'nine': '9',
                'ten': '10',
                'J': 'J',
                'Q': 'Q',
                'K': 'K',
                'A': 'A',
                'joker': '小',
                'JOKER': '大'
            },
            'window_layouts': {
                "JJ斗地主": {
                    "window_title": "JJ斗地主",
                    "layout": {
                        'player_hand': (0.04, 0.70, 0.96, 0.85),
                        "player_played": (0.04, 0.50, 0.96, 0.6),
                        'opponent_left': (0.20, 0.32, 0.455, 0.49),
                        'opponent_right': (0.46, 0.32, 0.80, 0.49),
                        'landlord_cards': (0.35, 0.08, 0.45, 0.15),
                    }
                }
            },
            'frame_length': 3,
            'device_choice': 'cuda',
            'debug_mode': True
        }

# 加载配置
config = load_config()

# ==================== 游戏选择 ====================
GAME_NAME = config.get('game_name', 'doudizhu')
GAME_CONFIG = _load_game_config(GAME_NAME)
GAME_DISPLAY_NAME = GAME_CONFIG.get('game_name', GAME_NAME)
PLAYED_ZONES = GAME_CONFIG.get('played_zones', [
    {'key': 'left', 'label': '上家', 'region': 'opponent_left'},
    {'key': 'self', 'label': '本家', 'region': 'player_played'},
    {'key': 'right', 'label': '下家', 'region': 'opponent_right'},
])
LAYOUT_REGIONS = GAME_CONFIG.get('layout_regions', [
    {'key': 'player_hand', 'label': '玩家手牌'},
    {'key': 'player_played', 'label': '本家出牌'},
    {'key': 'opponent_left', 'label': '上家出牌'},
    {'key': 'opponent_right', 'label': '下家出牌'},
    {'key': 'landlord_cards', 'label': '地主底牌'},
])

# ==================== YOLO 模型选择 ====================
YOLO_MODEL_NAME = config.get('yolo_model_name', '') or (_scan_model_dirs()[0] if _scan_model_dirs() else '')
YOLO_MODEL_PATH = _resolve_model_path(YOLO_MODEL_NAME)

# ==================== 基本配置 ====================
RESET_TIME = config.get('reset_time', 3.5)    # 底牌区连续无牌结束时长；显示保留至下一次底牌出现开局
DETECT_INTERVAL_SEC = config.get('detect_interval_sec', 0.2)  # 检测间隔秒数

# 大小王玩家出牌显示字符
LITTLE_JOKER_SHOWN = config.get('little_joker_shown', "🃟")
BIG_JOKER_SHOWN = config.get('big_joker_shown', "🃏")

# ==================== YOLO模型配置 ====================
YOLO_CONFIDENCE_THRESHOLD = config.get('yolo_confidence_threshold', 0.6)  # YOLO 置信度阈值
YOLO_IOU_THRESHOLD = config.get('yolo_iou_threshold', 0.45)

# ==================== YOLO类别映射配置 ====================
# 统一从 config.yaml 加载，不同游戏共用同一套 YOLO 映射
YOLO_TO_CARD_MAPPING = config.get('yolo_to_card_mapping', {
    'two': '2',
    'three': '3',
    'four': '4',
    'five': '5',
    'six': '6',
    'seven': '7',
    'eight': '8',
    'nine': '9',
    'ten': '10',
    'J': 'J',
    'Q': 'Q',
    'K': 'K',
    'A': 'A',
    'joker': '小',
    'JOKER': '大'
})

# ==================== 窗口和布局配置 ====================
# 预设的不同软件窗口和布局配置
# 结构: {配置名称: {"window_title": "窗口标题", "layout": {区域配置}}}
WINDOW_LAYOUTS = config.get('window_layouts', {
    "JJ斗地主": {
        "window_title": "JJ斗地主",
        "layout": {
            'player_hand': (0.04, 0.70, 0.96, 0.85),
            "player_played": (0.04, 0.50, 0.96, 0.6),
            'opponent_left': (0.20, 0.32, 0.455, 0.49),
            'opponent_right': (0.46, 0.32, 0.80, 0.49),
            'landlord_cards': (0.35, 0.08, 0.45, 0.15),
        }
    }
})

# 当前选择的布局名称，优先从配置文件读取，如不存在则取WINDOW_LAYOUTS的第一个key
try:
    _available = list(WINDOW_LAYOUTS.keys())
    if _available:
        CURRENT_LAYOUT = config.get('current_layout', _available[0])
    else:
        CURRENT_LAYOUT = None
except Exception:
    CURRENT_LAYOUT = None

# 连续多少帧检测相同内容算作正确截取
FRAME_LENGTH = config.get('frame_length', 3)


DEBUG_MODE = config.get('debug_mode', True)

# 是否将当前窗口截图实时传回主界面预览
SHOW_CURRENT_SCREENSHOT = config.get('show_current_screenshot', False)
FULL_DIAGNOSTICS = config.get('full_diagnostics', False)

# 仅控制正常识别时的布局提示；故障及恢复入口始终显示。
SHOW_LAYOUT_STATUS = config.get('show_layout_status', True)

# 是否在标题栏显示推理耗时和整轮耗时
SHOW_TIMING = config.get('show_timing', True)

# 保护拼图仍需真实牌局验证，旧配置默认沿用整图识别。
PACKED_INFERENCE = config.get('packed_inference', True)  # 手动布局检测方式；自动布局始终整图

# ==================== 设备选择配置 ====================
# 设备选择选项: "cpu" (使用CPU), "cuda" (使用GPU)
DEVICE_CHOICE = config.get('device_choice', 'cuda')

# ==================== 窗口显示配置 ====================
# 是否显示在最上层
ALWAYS_ON_TOP = config.get('always_on_top', False)

# 是否显示玩家所出的牌
SHOW_PLAYED_CARDS = config.get('show_played_cards', True)

class _LayoutOptions(TypedDict, total=False):
    """兼容旧配置的可选布局字段。

    Attributes:
        mode: manual 使用矩形；auto 只使用窗口标题。
    """

    mode: str


class LayoutConfig(_LayoutOptions):
    """可跨进程传递的布局。

    Attributes:
        window_title: 截图窗口标题。
        layout: 区域名到归一化矩形的映射。
        mode: 可选的 manual/auto 模式，缺省为 manual。
    """

    window_title: str
    layout: dict[str, list[float]]


_SETTING_FIELDS: dict[str, tuple[str, type]] = {
    "device_choice": ("DEVICE_CHOICE", str),
    "yolo_model_name": ("YOLO_MODEL_NAME", str),
    "yolo_confidence_threshold": ("YOLO_CONFIDENCE_THRESHOLD", float),
    "yolo_iou_threshold": ("YOLO_IOU_THRESHOLD", float),
    "reset_time": ("RESET_TIME", float),
    "frame_length": ("FRAME_LENGTH", int),
    "detect_interval_sec": ("DETECT_INTERVAL_SEC", float),
    "always_on_top": ("ALWAYS_ON_TOP", bool),
    "show_played_cards": ("SHOW_PLAYED_CARDS", bool),
    "debug_mode": ("DEBUG_MODE", bool),
    "show_current_screenshot": ("SHOW_CURRENT_SCREENSHOT", bool),
    "show_layout_status": ("SHOW_LAYOUT_STATUS", bool),
    "show_timing": ("SHOW_TIMING", bool),
    "packed_inference": ("PACKED_INFERENCE", bool),
    "full_diagnostics": ("FULL_DIAGNOSTICS", bool),
}


def _validate_setting(key: str, value: object) -> None:
    """校验可编辑标量设置。

    Args:
        key: YAML 配置键。
        value: 待保存值。

    Raises:
        TypeError: 值类型不符合定义。
        ValueError: 键未知、数值越界或名称为空。
    """
    if key not in _SETTING_FIELDS:
        raise ValueError(f"未知设置: {key}")
    expected = _SETTING_FIELDS[key][1]
    valid = type(value) is expected
    if expected is float:
        valid = type(value) in (float, int)
    if not valid:
        raise TypeError(f"{key} 必须是 {expected.__name__}")
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{key} 必须是有限正数")
        if key in ("yolo_confidence_threshold", "yolo_iou_threshold") and value > 1:
            raise ValueError(f"{key} 不能大于 1")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{key} 不能为空")
    if key == "device_choice" and value not in ("cpu", "cuda"):
        raise ValueError("设备必须是 cpu 或 cuda")


def validate_layout(value: object) -> LayoutConfig:
    """校验并复制手动或自动斗地主布局。

    Args:
        value: 包含窗口标题、区域字典和可选 mode 的字典；auto 允许空区域字典。

    Returns:
        独立的、坐标已转换为浮点数的布局。

    Raises:
        TypeError: 标题、区域或坐标类型错误。
        ValueError: 标题为空、缺少牌区或矩形越界。
    """
    if not isinstance(value, Mapping):
        raise TypeError("布局必须是字典")
    title = value.get("window_title")
    regions = value.get("layout")
    if not isinstance(title, str) or not isinstance(regions, Mapping):
        raise TypeError("布局必须包含字符串窗口标题和区域字典")
    if not title.strip():
        raise ValueError("窗口标题不能为空")
    mode = value.get("mode", "manual")
    if not isinstance(mode, str):
        raise TypeError("布局模式必须是字符串")
    if mode not in ("manual", "auto"):
        raise ValueError("布局模式必须是 manual 或 auto")
    if mode == "auto":
        return {"window_title": title, "layout": {}, "mode": "auto"}
    normalized: dict[str, list[float]] = {}
    for region in LAYOUT_REGIONS:
        key = region["key"]
        coords = regions.get(key)
        if not isinstance(coords, (tuple, list)) or len(coords) != 4:
            raise ValueError(f"区域 {key} 必须包含四个坐标")
        if any(type(x) not in (int, float) for x in coords):
            raise TypeError(f"区域 {key} 坐标必须是数字")
        box = [float(x) for x in coords]
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError(f"区域 {key} 坐标必须在 0~1 内且矩形非空")
        normalized[key] = box
    return {"window_title": title, "layout": normalized}


def _save_values(updates: Mapping[str, object]) -> None:
    """原子保存配置，成功后才发布主进程内存值。

    Args:
        updates: 已校验的 YAML 字段及值。

    Raises:
        OSError: 配置读写失败。
        ValueError: 已有配置损坏，拒绝覆盖。
        yaml.YAMLError: 更新内容无法序列化。
    """
    cfg = read_config(CONFIG_PATH)
    cfg.update(deepcopy(dict(updates)))
    write_config(CONFIG_PATH, cfg)
    config.update(deepcopy(dict(updates)))
    for key, value in updates.items():
        if key in _SETTING_FIELDS:
            globals()[_SETTING_FIELDS[key][0]] = deepcopy(value)
    if "yolo_model_name" in updates:
        globals()["YOLO_MODEL_PATH"] = _resolve_model_path(YOLO_MODEL_NAME)


def save_setting(key: str, value: object) -> None:
    """统一保存单个标量设置并更新主进程内存。

    Args:
        key: YAML 字段名。
        value: 新值。

    Raises:
        TypeError: 值类型不符合设置定义。
        ValueError: 值越界或原配置损坏。
        OSError: 文件读写失败。
        yaml.YAMLError: 序列化失败。
    """
    _validate_setting(key, value)
    _save_values({key: value})


def runtime_snapshot() -> dict[str, object]:
    """复制可传入新推理进程的运行配置。

    Returns:
        标量设置及全部布局的独立快照。
    """
    result = {attribute: deepcopy(globals()[attribute]) for attribute, _ in _SETTING_FIELDS.values()}
    result["WINDOW_LAYOUTS"] = deepcopy(WINDOW_LAYOUTS)
    return result


def apply_runtime_settings(updates: Mapping[str, object]) -> None:
    """仅在内存中应用子进程设置；全部验证后才发布。

    Args:
        updates: 大写运行时属性到值的映射。

    Raises:
        TypeError: 配置值类型错误。
        ValueError: 属性不允许更新或值无效。
    """
    reverse = {attribute: key for key, (attribute, _) in _SETTING_FIELDS.items()}
    validated: dict[str, object] = {}
    for attribute, value in updates.items():
        if attribute == "WINDOW_LAYOUTS":
            if not isinstance(value, Mapping) or not value:
                raise ValueError("布局列表不能为空")
            layouts: dict[str, LayoutConfig] = {}
            for name, layout in value.items():
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("布局名称必须是非空字符串")
                layouts[name] = validate_layout(layout)
            validated[attribute] = layouts
        elif attribute in reverse:
            _validate_setting(reverse[attribute], value)
            validated[attribute] = deepcopy(value)
        else:
            raise ValueError(f"不允许更新运行属性: {attribute}")
    globals().update(validated)
    if "YOLO_MODEL_NAME" in validated:
        globals()["YOLO_MODEL_PATH"] = _resolve_model_path(YOLO_MODEL_NAME)


def save_device_choice(device_choice: str) -> None:
    """保存 device_choice，成功后同步内存。

    Args:
        device_choice: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("device_choice", device_choice)


def save_model_choice(model_name: str) -> None:
    """保存 yolo_model_name，成功后同步内存。

    Args:
        model_name: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("yolo_model_name", model_name)


def save_confidence_choice(confidence: float) -> None:
    """保存 yolo_confidence_threshold，成功后同步内存。

    Args:
        confidence: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("yolo_confidence_threshold", confidence)


def save_reset_time(reset_time: float) -> None:
    """保存 reset_time，成功后同步内存。

    Args:
        reset_time: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("reset_time", reset_time)


def save_frame_length(frame_length: int) -> None:
    """保存 frame_length，成功后同步内存。

    Args:
        frame_length: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("frame_length", frame_length)


def save_detect_interval(detect_interval: float) -> None:
    """保存 detect_interval_sec，成功后同步内存。

    Args:
        detect_interval: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("detect_interval_sec", detect_interval)


def save_always_on_top(always_on_top: bool) -> None:
    """保存 always_on_top，成功后同步内存。

    Args:
        always_on_top: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("always_on_top", always_on_top)


def save_show_played_cards(show_played_cards: bool) -> None:
    """保存 show_played_cards，成功后同步内存。

    Args:
        show_played_cards: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("show_played_cards", show_played_cards)


def save_debug_mode(debug_mode: bool) -> None:
    """保存 debug_mode，成功后同步内存。

    Args:
        debug_mode: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("debug_mode", debug_mode)




def save_show_timing_choice(show_timing: bool) -> None:
    """保存 show_timing，成功后同步内存。

    Args:
        show_timing: 新设置值。

    Raises:
        TypeError: 值类型错误。
        ValueError: 值无效或原配置损坏。
        OSError: 配置读写失败。
        yaml.YAMLError: 序列化失败。
    """
    save_setting("show_timing", show_timing)


def apply_game_choice(game_name: str) -> None:
    """只更新本进程的游戏配置，不写文件。

    Args:
        game_name: 已注册的游戏名称。

    Raises:
        TypeError: 名称不是字符串。
        ValueError: 游戏未注册。
    """
    from core.games import TRACKER_REGISTRY

    if not isinstance(game_name, str):
        raise TypeError("游戏名称必须是字符串")
    if game_name not in TRACKER_REGISTRY:
        raise ValueError(f"游戏未注册: {game_name}")
    global GAME_NAME, GAME_CONFIG, GAME_DISPLAY_NAME, TOTAL_CARDS, PLAYED_ZONES, LAYOUT_REGIONS
    loaded = _load_game_config(game_name)
    GAME_NAME, GAME_CONFIG = game_name, loaded
    GAME_DISPLAY_NAME = loaded.get("game_name", game_name)
    TOTAL_CARDS = loaded.get("total_cards", TOTAL_CARDS)
    PLAYED_ZONES = loaded.get("played_zones", PLAYED_ZONES)
    LAYOUT_REGIONS = loaded.get("layout_regions", LAYOUT_REGIONS)


def save_game_choice(game_name: str) -> None:
    """保存游戏选择并发布内存配置。

    Args:
        game_name: 已注册的游戏名称。

    Raises:
        ValueError: 名称未注册或原配置损坏。
        OSError: 配置读写失败。
    """
    from core.games import TRACKER_REGISTRY

    if game_name not in TRACKER_REGISTRY:
        raise ValueError(f"游戏未注册: {game_name}")
    _save_values({"game_name": game_name})
    apply_game_choice(game_name)


def save_current_layout(layout_name: str) -> None:
    """保存已存在的当前布局名称。

    Args:
        layout_name: 布局名称。

    Raises:
        ValueError: 名称不存在或原配置损坏。
        OSError: 配置读写失败。
    """
    if layout_name not in WINDOW_LAYOUTS:
        raise ValueError(f"布局不存在: {layout_name}")
    _save_values({"current_layout": layout_name})
    globals()["CURRENT_LAYOUT"] = layout_name


def save_window_layout(
    layout_name: str, window_title: str,
    layout_dict: Mapping[str, Sequence[float]], set_current: bool = True, mode: str = "manual",
) -> None:
    """校验并保存布局，失败时保留原文件和内存。

    Args:
        layout_name: 非空布局名称。
        window_title: 目标窗口标题。
        layout_dict: 所有牌区的归一化矩形。
        set_current: 是否设为当前布局。
        mode: manual 保存矩形，auto 仅保存窗口标题。

    Raises:
        TypeError: 名称、开关或布局内容类型错误。
        ValueError: 名称为空、矩形无效或原配置损坏。
        OSError: 配置读写失败。
    """
    if not isinstance(layout_name, str) or type(set_current) is not bool:
        raise TypeError("布局名称必须为字符串，set_current 必须为布尔值")
    if not layout_name.strip():
        raise ValueError("布局名称不能为空")
    layout = validate_layout({"window_title": window_title, "layout": layout_dict, "mode": mode})
    cfg = read_config(CONFIG_PATH)
    layouts = deepcopy(cfg.get("window_layouts", WINDOW_LAYOUTS))
    if not isinstance(layouts, dict):
        raise ValueError("配置中的 window_layouts 必须是字典")
    layouts[layout_name] = layout
    updates: dict[str, object] = {"window_layouts": layouts}
    if set_current:
        updates["current_layout"] = layout_name
    _save_values(updates)
    globals()["WINDOW_LAYOUTS"] = layouts
    if set_current:
        globals()["CURRENT_LAYOUT"] = layout_name


def delete_window_layout(layout_name: str) -> str:
    """删除布局并返回仍然有效的当前布局，至少保留一个布局。

    Args:
        layout_name: 待删除名称。

    Returns:
        删除后的当前布局名称。

    Raises:
        ValueError: 布局不存在、仅剩一个布局或配置损坏。
        OSError: 配置读写失败。
    """
    cfg = read_config(CONFIG_PATH)
    layouts = deepcopy(cfg.get("window_layouts", WINDOW_LAYOUTS))
    if not isinstance(layouts, dict) or layout_name not in layouts:
        raise ValueError(f"布局不存在: {layout_name}")
    if len(layouts) <= 1:
        raise ValueError("至少需要保留一个布局")
    del layouts[layout_name]
    current = cfg.get("current_layout", CURRENT_LAYOUT)
    if current not in layouts:
        current = next(iter(layouts))
    _save_values({"window_layouts": layouts, "current_layout": current})
    globals()["WINDOW_LAYOUTS"] = layouts
    globals()["CURRENT_LAYOUT"] = current
    return cast(str, current)


# ==================== 路径配置 ====================
# 注意：BASE_DIR 和 YOLO_MODEL_PATH 已在文件开头定义


# 几个状态常数, 没必要动
WAIT_BEGIN = 0
HAS_STARTED = 1
STARTED_RECORD_CARD = 2


# ==================== 卡牌配置 ====================
# 优先从游戏配置加载
TOTAL_CARDS = GAME_CONFIG.get('total_cards', {
    '3' : 4,
    '4' : 4,
    '5' : 4,
    '6' : 4,
    '7' : 4,
    '8' : 4,
    '9' : 4,
    '10' : 4,
    'J' : 4,
    'Q' : 4,
    'K' : 4,
    'A' : 4,
    '2' : 4,
    '小' : 1,
    '大' : 1
})

