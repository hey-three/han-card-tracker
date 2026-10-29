"""只读的牌局快照与诊断解释；不驱动或更改状态机。"""

from collections import Counter
from copy import deepcopy
from typing import cast

from core.base_tracker import BaseCardTracker
from core.games.doudizhu import BOTTOM_START_FRAMES


def tracker_snapshot(tracker: BaseCardTracker) -> dict[str, object]:
    """复制排查所需字段，不序列化模型、图片对象或整个__dict__。

    Args:
        tracker: 当前斗地主跟踪器。
    Returns:
        状态、牌数、历史、确认缓存及底牌结束累计。
    """
    return {'state': tracker.state, 'remain_cards': dict(tracker.remain_cards),
            'show_cards': deepcopy(tracker.show_cards), 'frame_caches': deepcopy(tracker.frame_caches),
            'has_found_empty': dict(tracker.has_found_empty),
            'initial_hand': list(getattr(tracker, '_initial_hand', [])),
            'bottom_visible': getattr(tracker, '_bottom_visible', False),
            'bottom_start_frames': getattr(tracker, '_bottom_start_frames', 0),
            'empty_frames': getattr(tracker, '_empty_frames', 0),
            'empty_since_monotonic': getattr(tracker, '_empty_since', None),
            'awaiting_next_round': getattr(tracker, 'awaiting_next_round', False),
            'round_end_reason': getattr(tracker, 'round_end_reason', '')}


def decision_reasons(before: dict[str, object], after: dict[str, object], data: dict[str, list[str]],
                     known: list[str], valid: bool, required: int) -> list[dict[str, object]]:
    """按牌点多重集解释实际确认进度，不因显示换行误报待确认，不决定扣牌。

    Args:
        before: 更新前快照。
        after: 更新后快照。
        data: 当帧区域牌点。
        known: 已知区域。
        valid: 最终模型结果是否有效。
        required: 当前确认阈值。
    Returns:
        明确区分未知、底牌门禁、待确认、重复显示及实际状态变化的记录。
    """
    result: list[dict[str, object]] = []
    if not valid:
        return [{'code': 'result_unknown'}]
    if 'landlord_cards' not in known:
        result.append({'code': 'bottom_region_unknown'})
    elif not data.get('landlord_cards'):
        result.append({'code': 'bottom_missing_pause', 'empty_frames': after['empty_frames']})
    if before['state'] == 0 and after['state'] != 0:
        result.append({'code': 'game_started'})
    elif after['state'] == 0 and data.get('landlord_cards') and 'landlord_cards' in known:
        result.append({'code': 'bottom_start_pending', 'observed': after.get('bottom_start_frames', 0),
                       'required': BOTTOM_START_FRAMES})
    if before['state'] != 2 and after['state'] == 2:
        result.append({'code': 'initial_hand_deducted', 'cards': after['initial_hand']})
    if not before['awaiting_next_round'] and after['awaiting_next_round']:
        result.append({'code': 'round_ended_waiting', 'reason': after['round_end_reason']})
    old = cast(dict[str, int], before['remain_cards'])
    new = cast(dict[str, int], after['remain_cards'])
    delta = {key: new[key]-old.get(key, 0) for key in new if new[key] != old.get(key, 0)}
    if delta:
        result.append({'code': 'remaining_changed', 'delta': delta})
    if before['show_cards'] != after['show_cards']:
        result.append({'code': 'play_history_changed'})
    if after['state'] == 0:
        return result or [{'code': 'no_state_change'}]
    caches = cast(dict[str, list[list[str]]], after['frame_caches'])
    for key in known:
        cards = data.get(key, [])
        history = caches.get(key, [])
        if not cards or key == 'landlord_cards':
            continue
        consecutive = 0
        signature = Counter(cards)
        for item in reversed(history):
            if Counter(item) != signature:
                break
            consecutive += 1
        if consecutive < required:
            result.append({'code': 'confirmation_pending', 'region': key, 'cards': cards,
                           'consecutive': consecutive, 'required': required})
        elif before['show_cards'] == after['show_cards'] and before['state'] == 2 and after['bottom_visible']:
            result.append({'code': 'stable_display_no_new_play', 'region': key, 'cards': cards})
    return result or [{'code': 'no_state_change'}]
