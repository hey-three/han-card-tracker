"""开局手牌漏K的日志回归，以及诊断状态与补扣账本的隔离验证。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from config import settings
from core.diagnostic_state import decision_reasons, tracker_snapshot
from core.diagnostics import DiagnosticFrame, DiagnosticRecorder
from core.game_controller import GameController
from core.games.doudizhu import DoudizhuTracker


class InitialHandReconciliationTests(unittest.TestCase):
    """只使用已确认牌面，不加载真实模型或假设诊断一定完整。"""

    def setUp(self) -> None:
        """固定确认阈值，隔离临时诊断文件和用户设置。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=1, DEBUG_MODE=False))
        self.fixture = json.loads(Path('tests/fixtures/initial_hand_missed_king.json').read_text(encoding='utf-8'))

    def frame(self, hand: list[str], zone: str = '', cards: list[str] | None = None) -> dict[str, list[str]]:
        """建立明确已知的五区牌面帧。

        Args:
            hand: 当前手牌。
            zone: 可选出牌区域键。
            cards: 当前出牌。
        Returns:
            所有区域明确已知，底牌保持可见的帧。
        """
        frame = {region['key']: [] for region in settings.LAYOUT_REGIONS}
        frame.update(player_hand=list(hand), landlord_cards=['10'])
        if zone:
            frame[zone] = list(cards or [])
        return frame

    def sequence(self) -> list[dict[str, list[str]]]:
        """把去隐私后的真实出牌顺序变为稳定帧，帧间显式观察空区。

        Returns:
            首扣16张、随后包含本家KK与对手K的回归序列。
        """
        frames = [self.frame(self.fixture['initial_hand'])]
        regions = {'left': 'opponent_left', 'self': 'player_played', 'right': 'opponent_right'}
        for event in self.fixture['plays']:
            frames.append(self.frame([]))
            frames.append(self.frame([], regions[event['zone']], event['cards']))
        return frames

    def test_actual_missed_king_sequence_finishes_at_zero_and_preserves_raw_hand(self) -> None:
        """复现截图牌序：原首扣16张含一K，本家KK后只补一K，最终为零。"""
        tracker = DoudizhuTracker()
        for frame in self.sequence():
            tracker.get_cards_number(frame, 0.)
        self.assertEqual(tracker.remain_cards, self.fixture['expected_remaining'])
        self.assertEqual(tracker.initial_hand_corrections, {'K': 1})
        self.assertEqual(len(tracker._initial_hand), 16)
        self.assertEqual(tracker._initial_hand.count('K'), 1)
        self.assertEqual(tracker.counting_conflicts, {})

    def test_normally_detected_pair_and_repeated_display_do_not_deduct_twice(self) -> None:
        """开局已扣一对K，本家反复显示KK不额外扣；对手两张K仍归零。"""
        tracker = DoudizhuTracker()
        tracker.get_cards_number(self.frame(['K', 'K']), 0.)
        for _ in range(5):
            tracker.get_cards_number(self.frame([], 'player_played', ['K', 'K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 2)
        self.assertEqual(tracker.initial_hand_corrections, {})
        tracker.get_cards_number(self.frame([], 'opponent_left', ['K']), 0.)
        tracker.get_cards_number(self.frame([], 'opponent_right', ['K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 0)

    def test_repeated_single_king_only_repairs_cumulative_gap_once(self) -> None:
        """本家连续两次单K，经空区确认后补一张，持续显示不重复补。"""
        tracker = DoudizhuTracker()
        tracker.get_cards_number(self.frame(['K']), 0.)
        tracker.get_cards_number(self.frame([], 'player_played', ['K']), 0.)
        tracker.get_cards_number(self.frame([]), 0.)
        before = tracker_snapshot(tracker)
        for _ in range(6):
            tracker.get_cards_number(self.frame([], 'player_played', ['K']), 0.)
        after = tracker_snapshot(tracker)
        self.assertEqual(tracker.remain_cards['K'], 2)
        self.assertEqual(tracker.initial_hand_corrections, {'K': 1})
        reasons = decision_reasons(before, after, {'landlord_cards': ['10'], 'player_played': ['K']},
                                   ['landlord_cards', 'player_played'], True, 1)
        self.assertIn('initial_hand_corrected', [r['code'] for r in reasons])
        tracker.begin_waiting('test')
        self.assertEqual(tracker.initial_hand_corrections, {'K': 1})
        tracker.get_cards_number(self.frame(['K', 'K']), 0.)
        self.assertEqual(tracker.initial_hand_corrections, {})
        self.assertEqual(tracker.remain_cards['K'], 2)
        tracker.reset()
        self.assertEqual(tracker.initial_hand_corrections, {})

    def test_contradictory_self_play_does_not_repair_into_negative(self) -> None:
        """已知四张K都计入后，矛盾的本家三K不被盲目补扣。"""
        tracker = DoudizhuTracker()
        tracker.get_cards_number(self.frame(['K', 'K']), 0.)
        tracker.get_cards_number(self.frame([], 'opponent_left', ['K', 'K']), 0.)
        tracker.get_cards_number(self.frame([], 'player_played', ['K', 'K', 'K']), 0.)
        self.assertEqual(tracker.remain_cards['K'], 0)
        self.assertEqual(tracker.initial_hand_corrections, {})
        self.assertIn('K', tracker.counting_conflicts)

    def test_controller_output_is_identical_with_diagnostics_off_recording_or_paused(self) -> None:
        """真实控制器诊断包装和Tracker，在诊断中途暂停后也产生同样的牌账。"""
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
                    """仅替换模型边界，让真实诊断包装记录实际Tracker结果。

                    Args:
                        trace: 控制器预留的诊断帧。
                    Returns:
                        真实Tracker结果。
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
                            recorder.pause('memory_limit: simulated', recorder.frame_id+1)
                        controller.detect()
                        if mode == 'recording':
                            recorder._queue.join()  # 模拟真实采样间隔，避免无模型的测试瞬间灌满队列。
                        result.append(tracker_snapshot(controller.tracker))
                    self.assertTrue(recorder.stop(3))
                    self.assertEqual(controller.tracker.remain_cards['K'], 0)
                    self.assertEqual(controller.tracker.initial_hand_corrections, {'K': 1})
                    outputs.append(result)
                    if mode == 'recording':
                        saved = list(Path(directory).glob('diagnostics/session_*/frames/*/frame.json'))
                        evidence = [json.loads(path.read_text(encoding='utf-8')) for path in saved]
                        self.assertTrue(any(r['code']=='initial_hand_corrected' for frame in evidence for r in frame['reasons']))
                finally:
                    recorder.close()
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
