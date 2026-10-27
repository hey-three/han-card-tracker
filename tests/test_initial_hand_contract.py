"""本家出牌只记录的契约与未解决首扣缺张的观察证据。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from config import settings
from core.diagnostic_state import tracker_snapshot
from core.diagnostics import DiagnosticFrame, DiagnosticRecorder
from core.game_controller import GameController
from core.games.doudizhu import DoudizhuTracker


class InitialHandContractTests(unittest.TestCase):
    """验证首扣手牌后，本家任何确认出牌都不再改变剩余计数。"""

    def setUp(self) -> None:
        """固定连续确认阈值，加载去隐私的历史牌面。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=1, DEBUG_MODE=False))
        self.fixture = json.loads(Path('tests/fixtures/initial_hand_missed_king.json').read_text(encoding='utf-8'))

    def frame(self, hand: list[str], zone: str = '', cards: list[str] | None = None) -> dict[str, list[str]]:
        """构造底牌可见、各区域明确已知的输入。

        Args:
            hand: 当前手牌。
            zone: 可选的出牌区域。
            cards: 当前区域的出牌。
        Returns:
            五区牌面，不含模型或截图对象。
        """
        frame = {region['key']: [] for region in settings.LAYOUT_REGIONS}
        frame.update(player_hand=list(hand), landlord_cards=['10'])
        if zone:
            frame[zone] = list(cards or [])
        return frame

    def sequence(self) -> list[dict[str, list[str]]]:
        """重放已记录的首扣和出牌顺序，不猜测漏掉的手牌。

        Returns:
            首扣16张、帧间有明确空区的历史观测序列。
        """
        frames = [self.frame(self.fixture['initial_hand'])]
        regions = {'left': 'opponent_left', 'self': 'player_played', 'right': 'opponent_right'}
        for event in self.fixture['plays']:
            frames.append(self.frame([]))
            frames.append(self.frame([], regions[event['zone']], event['cards']))
        return frames

    def test_incomplete_initial_hand_is_not_silently_rewritten_from_own_plays(self) -> None:
        """首扣缺张保留为待查证据，本家KK不能用来偷偷修正首扣。"""
        tracker = DoudizhuTracker()
        for frame in self.sequence():
            before = dict(tracker.remain_cards)
            tracker.get_cards_number(frame, 0.)
            if frame.get('player_played'):
                self.assertEqual(tracker.remain_cards, before)
        self.assertEqual(tracker.remain_cards, self.fixture['observed_remaining'])
        self.assertEqual(tracker.remain_cards['K'], 1)
        self.assertEqual(len(tracker._initial_hand), 16)
        self.assertEqual(tracker._initial_hand.count('K'), 1)

    def test_complete_initial_pair_and_repeated_own_display_do_not_deduct_twice(self) -> None:
        """首扣已含KK，本家反复显示不扣，对手两张K仍正常扣至零。"""
        tracker = DoudizhuTracker()
        tracker.get_cards_number(self.frame(['K', 'K']), 0.)
        for _ in range(5):
            tracker.get_cards_number(self.frame([], 'player_played', ['K', 'K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 2)
        tracker.get_cards_number(self.frame([], 'opponent_left', ['K']), 0.)
        tracker.get_cards_number(self.frame([], 'opponent_right', ['K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 0)

    def test_separate_own_single_kings_only_change_history_and_new_round_resets(self) -> None:
        """经过空区的两手本家K都只更新历史，换局才重新扣新手牌。"""
        tracker = DoudizhuTracker()
        tracker.get_cards_number(self.frame(['K']), 0.)
        tracker.get_cards_number(self.frame([], 'player_played', ['K']), 0.)
        tracker.get_cards_number(self.frame([]), 0.)
        for _ in range(6):
            tracker.get_cards_number(self.frame([], 'player_played', ['K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 3)
        self.assertEqual(tracker.show_cards['self'], [['K'], ['K']])
        self.assertEqual(tracker._initial_hand, ['K'])
        tracker.begin_waiting('test')
        self.assertEqual(tracker.remain_cards['K'], 3)
        tracker.get_cards_number(self.frame(['K', 'K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 2)
        self.assertEqual(tracker.show_cards['self'], [])
        tracker.reset()
        self.assertEqual(tracker.remain_cards['K'], 4)

    def test_own_play_disagreeing_with_initial_hand_still_cannot_change_counts(self) -> None:
        """即使本家牌面与首扣矛盾，也不能额外扣牌或改写原始手牌。"""
        tracker = DoudizhuTracker()
        tracker.get_cards_number(self.frame(['A']), 0.)
        before = dict(tracker.remain_cards)
        tracker.get_cards_number(self.frame([], 'player_played', ['K', 'K']), 0.)
        self.assertEqual(tracker.remain_cards, before)
        self.assertEqual(tracker._initial_hand, ['A'])
        self.assertEqual(tracker.show_cards['self'], [['K', 'K']])

    def test_controller_output_matches_with_diagnostics_off_recording_or_paused(self) -> None:
        """诊断是观察旁路，三种状态均保留同一首扣与出牌计数。"""
        outputs: list[list[dict[str, object]]] = []
        Path('tests/.tmp').mkdir(exist_ok=True)
        for mode in ('off', 'recording', 'paused'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir='tests/.tmp') as directory:
                recorder = DiagnosticRecorder(directory)
                controller = GameController.__new__(GameController)
                controller.tracker = DoudizhuTracker()
                controller.last_performance = {}
                controller.request_context = (1, 0)
                controller._diagnostic_round = 0
                controller._diagnostic_context = Mock(return_value={'test': 'private-log-fixture'})
                iterator = iter(self.sequence())

                def process(trace: DiagnosticFrame | None = None) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
                    """替换模型边界，保留真实控制器包装及Tracker。

                    Args:
                        trace: 可选诊断帧。
                    Returns:
                        Tracker实际牌账。
                    """
                    frame = next(iterator)
                    if trace is not None:
                        trace.payload.update(frame_data=deepcopy(frame), known_regions=sorted(frame), valid_frame=True)
                    return controller.tracker.get_cards_number(frame, 0.)

                controller._detect_frame = Mock(side_effect=process)
                try:
                    with patch.object(settings, 'FULL_DIAGNOSTICS', mode != 'off'):
                        controller.attach_diagnostics(recorder)
                    result: list[dict[str, object]] = []
                    for index in range(len(self.sequence())):
                        if mode == 'paused' and index == 4:
                            recorder.pause('test fault', recorder.frame_id+1)
                        controller.detect()
                        if mode == 'recording':
                            recorder._queue.join()
                        result.append(tracker_snapshot(controller.tracker))
                    self.assertTrue(recorder.stop(3))
                    self.assertEqual(controller.tracker.remain_cards['K'], 1)
                    outputs.append(result)
                    if mode == 'recording':
                        paths = list(Path(directory).glob('diagnostics/session_*/**/frames/*/frame.json'))
                        evidence = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
                        self.assertTrue(any(frame['after']['initial_hand']==self.fixture['initial_hand'] for frame in evidence))
                finally:
                    recorder.close()
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
