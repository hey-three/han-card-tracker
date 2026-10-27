"""底牌门禁、V3 非空稳定手牌、保留历史和实际 YOLO 输入预览集成测试。"""

from collections.abc import Sequence
from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from config import settings
from core.auto_layout import AutoLayoutAnalyzer, CardBox
from core.game_controller import GameController
from core.games.doudizhu import DoudizhuTracker
from core.frame_packing import Detection
from core.layout_analyzer import LayoutAnalyzer
from core.square_packing import make_rect_plan
from config.packing_profile import PACKED_INPUT_SIZE, stage_regions
from test_auto_layout import opening, row
from test_packed_pipeline import raw_result


class RoundLifecycleTests(unittest.TestCase):
    """使用真实控制器、分区与跟踪器，仅替换窗口和模型边界。"""

    def setUp(self) -> None:
        """建立一张底牌与十七张手牌开局，使用可控时钟而非真实等待。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=3, SHOW_CURRENT_SCREENSHOT=False, DEBUG_MODE=False,
                                        PACKED_INFERENCE=True, RESET_TIME=3.))
        config = settings.validate_layout({"window_title": "Game", "mode": "auto", "layout": {}})
        self.enterContext(patch.object(settings, "WINDOW_LAYOUTS", {"Auto": config}))
        with patch.object(GameController, "_create_inferencer", return_value=Mock()), patch("core.game_controller.ScreenCapture"):
            self.controller = GameController("doudizhu", "Auto")
        self.hand = opening()[:-3]
        self.bottom = row(["A"], .43, .1, .035, .035)
        self.image = Image.new("RGB", (1000, 800), (20, 60, 80))
        self.manual_plan = None
        self.now = 0.
        self.enterContext(patch("core.games.doudizhu.monotonic", side_effect=lambda: self.now))
        self.feed(self.hand)

    @property
    def tracker(self) -> DoudizhuTracker:
        """返回真实斗地主跟踪器。"""
        assert isinstance(self.controller.tracker, DoudizhuTracker)
        return self.controller.tracker

    def feed(self, cards: Sequence[CardBox], count: int = 3, captured: bool = True,
             result: bool = True, step: float = 1.5,
             bottom: Sequence[CardBox] | None = None, packed_bottom: bool = True) -> None:
        """把原图坐标的模拟 YOLO 输出交给完整流水线。

        Args:
            cards: 本轮检测框，空序列代表成功识别后无牌。
            count: 连续帧数。
            captured: False 模拟截图失败。
            result: False 模拟模型没有返回结果，不等同有效空检测。
            step: 相邻截图的单调时钟间隔，无需实际等待。
        """
        cards = list(cards) + list(self.bottom if bottom is None else bottom)
        width, height = self.image.size
        detections = [Detection(c.name, (c.bbox[0]*width, c.bbox[1]*height,
                                        c.bbox[2]*width, c.bbox[3]*height)) for c in cards]
        raw = raw_result(detections)
        self.controller.yolo_inferencer.detect.return_value = (raw if result else None, 1.)
        self.controller.screen_capture.capture_window.return_value = self.image if captured else None
        for _ in range(count):
            if self.manual_plan is not None:
                recording = self.tracker.state == settings.STARTED_RECORD_CARD
                names, regions, _ = stage_regions(self.controller.layout_config['layout'], recording)
                scales = self.controller.packed_pipeline.scale_weights(self.image.size, self.controller.layout_config['layout'], names)
                key = (recording, tuple(scales))
                if key not in self.manual_plans:
                    self.manual_plans[key] = make_rect_plan(self.image.size, regions, scales, PACKED_INPUT_SIZE)
                plan = self.manual_plans[key]
                self.manual_plans[recording] = plan
                projected = []
                for tile in plan.tiles:
                    a, b, c, d = tile.source
                    for card in detections:
                        if not packed_bottom and card.bbox[1] < self.image.height*.25:
                            continue
                        x1, y1, x2, y2 = card.bbox
                        crop = (max(a, x1), max(b, y1), min(c, x2), min(d, y2))
                        if crop[0] < crop[2] and crop[1] < crop[3]:
                            projected.append(Detection(card.name, tile.project(crop)))
                self.controller.yolo_inferencer.detect_packed.return_value = (raw_result(projected) if result else None, 1.)
                self.controller.yolo_inferencer.restore_result.side_effect = lambda raw, image, cards: raw_result(list(cards))
            self.now += step
            self.controller.detect()

    def use_manual_layout(self) -> None:
        """切换为真实五区手动分析，模拟模型同时支持拼图与整图复查。"""
        config = settings.validate_layout({"window_title": "Game", "mode": "manual", "layout": {
            "player_hand": [.04, .7, .96, .86], "player_played": [.04, .46, .96, .66],
            "opponent_left": [.08, .25, .48, .45], "opponent_right": [.5, .25, .94, .45],
            "landlord_cards": [.35, 0., .6, .2],
        }})
        self.controller.layout_config = config
        self.controller.layout_analyzer = LayoutAnalyzer(config)
        self.controller.reset()
        self.manual_plan = True
        self.manual_plans = {}
        for recording in (False, True):
            _, regions, scales = stage_regions(config["layout"], recording)
            self.manual_plans[recording] = make_rect_plan(self.image.size, regions, scales, PACKED_INPUT_SIZE)
        self.feed(self.hand)

    def test_complete_diagnostics_records_real_controller_state_and_request(self) -> None:
        """真实流水线输出可序列化的前后状态、模型调用和请求标识。"""
        import json
        import tempfile
        from pathlib import Path
        from core.diagnostics import DiagnosticRecorder
        directory = tempfile.TemporaryDirectory(dir='tests/.tmp')
        self.addCleanup(directory.cleanup)
        recorder = DiagnosticRecorder(directory.name)
        self.addCleanup(recorder.close)
        self.enterContext(patch.object(settings, 'FULL_DIAGNOSTICS', True))
        self.enterContext(patch.object(settings, 'YOLO_MODEL_PATH', 'missing-model.pt'))
        self.controller.reset()
        self.controller.attach_diagnostics(recorder)
        self.controller.request_context = (42, 4)
        self.feed(self.hand, count=3)
        self.assertTrue(recorder.stop(3))
        self.assertEqual(recorder.status()['saved_frames'], 3)
        frames = sorted(Path(directory.name).glob('diagnostics/session_*/frames/*/frame.json'))
        data = [json.loads(p.read_text(encoding='utf-8')) for p in frames]
        self.assertEqual(data[0]['context']['request_id'], 42)
        self.assertEqual(data[0]['context']['generation'], 4)
        self.assertTrue(all(p['before'] and p['after'] and p['calls'] and p['region_boxes'] for p in data))
        self.assertTrue(any(r['code']=='initial_hand_deducted' for p in data for r in p['reasons']))
        self.assertEqual(data[-1]['after']['remain_cards'], self.controller.tracker.remain_cards)

    def test_one_bottom_card_starts_immediately_before_hand_confirmation(self) -> None:
        """第一帧底牌进入已开局，手牌另等稳定，空手牌不能漏扣初始牌。"""
        for manual in (False, True):
            with self.subTest(manual=manual):
                if manual:
                    self.use_manual_layout()
                self.controller.reset()
                self.feed([], 1)
                self.assertEqual(self.tracker.state, settings.HAS_STARTED)
                self.feed([], 4)
                self.assertEqual(self.tracker.state, settings.HAS_STARTED)
                self.assertEqual(sum(self.tracker.remain_cards.values()), 54)
                self.feed(self.hand[:1], 2)
                self.assertEqual(sum(self.tracker.remain_cards.values()), 54)
                self.feed(self.hand[:1], 1)
                self.assertEqual(self.tracker.state, settings.STARTED_RECORD_CARD)
                self.assertEqual(sum(self.tracker.remain_cards.values()), 53)

    def test_any_nonempty_stable_hand_count_is_accepted_once(self) -> None:
        """五、十六、十七、二十张均可首次扣牌，之后数量变化不重扣或补扣。"""
        for manual in (False, True):
            if manual:
                self.use_manual_layout()
            for count in (5, 16, 17, 20):
                with self.subTest(manual=manual, count=count):
                    self.controller.reset()
                    cards = row(["three"]*4+["four"]*4+["five"]*4+["six"]*4+["seven"]*4, .1, .76)[:count]
                    self.feed(cards)
                    self.assertEqual(sum(self.tracker.remain_cards.values()), 54-count)
                    self.feed(self.hand, 6)
                    self.assertEqual(sum(self.tracker.remain_cards.values()), 54-count)

    def test_middle_flip_without_top_bottom_never_starts_or_records(self) -> None:
        """手牌齐全且中部三张翻牌稳定，也不能在上方底牌到位前扣牌或记出牌。"""
        for manual in (False, True):
            if manual:
                self.use_manual_layout()
            self.controller.reset()
            self.feed(self.hand + row(["A", "K", "Q"], .22, .37), 8, bottom=[])
            self.assertEqual(self.tracker.state, settings.WAIT_BEGIN)
            self.assertEqual(sum(self.tracker.remain_cards.values()), 54)
            self.assertTrue(all(not cards for cards in self.tracker.show_cards.values()))
            self.feed(self.hand, 1)
            self.assertEqual(self.tracker.state, settings.HAS_STARTED)
            self.feed(self.hand, 2)
            self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
            self.assertEqual(self.tracker.show_cards["left"], [])

    def test_bottom_count_or_class_changes_do_not_require_three_identical_cards(self) -> None:
        """底牌的一、二、三张及类别变化都只按存在性判断。"""
        for manual in (False, True):
            if manual:
                self.use_manual_layout()
            self.controller.reset()
            for names in (["A"], ["K", "Q"], ["J", "ten", "nine"]):
                self.feed(self.hand, 1, bottom=row(names, .43, .1, .035, .035))
            self.assertEqual(sum(self.tracker.remain_cards.values()), 37)

    def test_bottom_absence_ends_even_if_hand_and_other_cards_remain(self) -> None:
        """只看底牌消失，其他区域还有牌也结束；结束保留旧显示。"""
        for manual in (False, True):
            if manual:
                self.use_manual_layout()
            self.feed(self.hand + row(["K"], .22, .37))
            old = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
            self.feed(self.hand + row(["Q"], .22, .37), 2, bottom=[])
            self.assertFalse(self.tracker.awaiting_next_round)
            self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), old)
            self.feed(self.hand, 1, bottom=[])
            self.assertTrue(self.tracker.awaiting_next_round)
            self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), old)
            self.feed(self.hand[:5], 5, bottom=[])
            self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), old)
            self.feed(self.hand[:5], 1)
            self.assertEqual(self.tracker.state, settings.HAS_STARTED)
            self.assertEqual(sum(self.tracker.remain_cards.values()), 54)
            self.assertTrue(all(not cards for cards in self.tracker.show_cards.values()))
            self.feed(self.hand[:5], 2)
            self.assertEqual(sum(self.tracker.remain_cards.values()), 49)

    def test_short_missing_bottom_pauses_then_recovers_without_reinitialization(self) -> None:
        """单次漏检暂停记录，恢复后不重扣，也不继承漏检前未确认的出牌帧。"""
        old = deepcopy(self.tracker.remain_cards)
        self.feed(self.hand + row(["K"], .22, .37), 2)
        self.feed(self.hand + row(["Q"], .22, .37), 2, bottom=[])
        self.feed(self.hand + row(["K"], .22, .37), 1)
        self.assertEqual(self.tracker.show_cards["left"], [])
        self.assertEqual(self.tracker.remain_cards, old)
        self.feed(self.hand + row(["K"], .22, .37), 2)
        self.assertEqual(self.tracker.show_cards["left"], [["K"]])
        self.assertFalse(self.tracker.awaiting_next_round)

    def test_unknown_frames_and_pause_interrupt_end_timer(self) -> None:
        """失败帧和暂停不当底牌消失，也不能累计到结束时长。"""
        for kwargs in ({"captured": False}, {"result": False}):
            self.feed(self.hand, 2, bottom=[])
            self.feed([], 10, bottom=[], **kwargs)
            self.feed(self.hand, 2, bottom=[])
            self.assertFalse(self.tracker.awaiting_next_round)
            self.controller.touch_time()
            self.now += 100
            self.feed(self.hand, 1, bottom=[])
            self.assertFalse(self.tracker.awaiting_next_round)
            self.feed(self.hand, 1)

    def test_bottom_end_requires_duration_and_configured_frame_count(self) -> None:
        """高速空底牌序列仍需持续时长，配置六帧不能只用三帧结束。"""
        self.feed(self.hand, 20, bottom=[], step=.1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed(self.hand, 1, bottom=[], step=1.2)
        self.assertTrue(self.tracker.awaiting_next_round)
        self.feed(self.hand)
        settings.FRAME_LENGTH = 6
        self.feed(self.hand, 5, bottom=[])
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed(self.hand, 1, bottom=[])
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_end_applies_even_before_hand_has_been_confirmed(self) -> None:
        """已经看到底牌开局但还没有手牌时，底牌消失也能进入等待。"""
        self.controller.reset()
        self.feed([], 1)
        self.assertEqual(self.tracker.state, settings.HAS_STARTED)
        self.feed([], 3, bottom=[])
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_manual_missing_packed_bottom_rechecks_original_once_per_frame(self) -> None:
        """拼图漏底牌但原图有牌时不能误结束，补查不算第二帧。"""
        self.use_manual_layout()
        self.feed(self.hand + row(["K"], .22, .37), 4, packed_bottom=False)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(self.controller.packed_pipeline.timings["model_calls"], 2.)
        self.assertIn("底牌区", self.controller.packed_pipeline.route)
        self.feed(self.hand, 2, bottom=[])
        self.assertEqual(self.tracker._empty_frames, 2)
        self.assertFalse(self.tracker.awaiting_next_round)

    def test_explicit_reset_clears_immediately(self) -> None:
        """显式重置与自动等待不同，立即清除旧历史。"""
        self.feed(self.hand + row(["K"], .22, .37))
        self.controller.reset()
        self.assertEqual(sum(self.tracker.remain_cards.values()), 54)
        self.assertTrue(all(not cards for cards in self.tracker.show_cards.values()))

    def test_preview_shows_last_actual_input_for_full_packed_and_fallback(self) -> None:
        """JPEG 对应实际模型输入，回退后为原图，拼图不画原图坐标框。"""
        settings.SHOW_CURRENT_SCREENSHOT = True
        self.feed(self.hand, 1)
        self.assertEqual(Image.open(BytesIO(self.controller.last_screenshot_jpeg)).size, self.image.size)
        self.use_manual_layout()
        self.assertEqual(Image.open(BytesIO(self.controller.last_screenshot_jpeg)).size, PACKED_INPUT_SIZE)
        self.assertEqual(self.controller.last_screenshot_regions, {})
        self.feed(self.hand, 1, packed_bottom=False)
        self.assertEqual(Image.open(BytesIO(self.controller.last_screenshot_jpeg)).size, self.image.size)
        self.assertEqual(self.controller.packed_pipeline.timings["model_calls"], 2.)
        settings.SHOW_CURRENT_SCREENSHOT = False
        self.feed(self.hand, 1)
        self.assertIsNone(self.controller.last_screenshot_jpeg)

    def test_invalid_capture_does_not_publish_previous_input_as_new_preview(self) -> None:
        """截图失败时不能重发上一帧的输入图。"""
        settings.SHOW_CURRENT_SCREENSHOT = True
        self.feed(self.hand, 1)
        self.assertIsNotNone(self.controller.last_screenshot_jpeg)
        self.feed([], 1, captured=False)
        self.assertIsNone(self.controller.last_screenshot_jpeg)

    def test_auto_remains_full_regardless_of_legacy_preference(self) -> None:
        """本次业务变更不改变自动布局始终整图的路线。"""
        settings.PACKED_INFERENCE = True
        self.feed(self.hand, 4)
        self.controller.yolo_inferencer.detect_packed.assert_not_called()

    def test_before_bottom_appears_only_opening_tiles_are_inferred(self) -> None:
        """有手牌但无底牌时连续多帧仍只推理两区，不能偷偷复查原图。"""
        self.use_manual_layout()
        self.controller.reset()
        self.controller.yolo_inferencer.reset_mock()
        settings.SHOW_CURRENT_SCREENSHOT = True
        self.feed(self.hand + row(['K'], .22, .37), 6, bottom=[])
        self.assertEqual(self.tracker.state, settings.WAIT_BEGIN)
        self.controller.yolo_inferencer.detect.assert_not_called()
        self.assertEqual(self.controller.yolo_inferencer.detect_packed.call_count, 6)
        self.assertEqual(self.controller.last_performance['region_count'], 2.)
        self.assertEqual(Image.open(BytesIO(self.controller.last_screenshot_jpeg)).size, PACKED_INPUT_SIZE)

    def test_empty_and_unknown_opening_results_never_infer_full_frame(self) -> None:
        """两区全空可继续等待，结果未知也不能以整图推理代替开局输入。"""
        self.use_manual_layout()
        self.controller.reset()
        self.controller.yolo_inferencer.reset_mock()
        self.feed([], 4, bottom=[])
        self.feed([], 1, bottom=[], result=False)
        self.controller.yolo_inferencer.detect.assert_not_called()
        self.assertEqual(self.tracker.state, settings.WAIT_BEGIN)

    def test_manual_original_mode_and_hot_switch_preserve_accounting(self) -> None:
        """改为原图后只调用原图模型，不重置牌局或再次扣手牌。"""
        self.use_manual_layout()
        before = deepcopy(self.tracker.remain_cards)
        self.controller.update_settings({'PACKED_INFERENCE': False})
        self.controller.yolo_inferencer.reset_mock()
        self.feed(self.hand, 2)
        self.controller.yolo_inferencer.detect_packed.assert_not_called()
        self.assertEqual(self.controller.yolo_inferencer.detect.call_count, 2)
        self.assertEqual(self.tracker.remain_cards, before)
        self.assertEqual(self.controller.packed_pipeline.route, '手动布局原图')

    def test_manual_switches_two_to_four_regions_only_after_initial_deduction(self) -> None:
        """已开局但手牌未稳定仍用二区；扣牌后用四区且裁片不再包含手牌。"""
        self.use_manual_layout()
        self.controller.reset()
        self.feed(self.hand, 1)
        self.assertEqual(self.tracker.state, settings.HAS_STARTED)
        self.assertIn("2 区", self.controller.packed_pipeline.route)
        self.feed(self.hand, 2)
        self.assertEqual(self.tracker.state, settings.STARTED_RECORD_CARD)
        self.feed(self.hand, 1)
        self.assertIn("4 区", self.controller.packed_pipeline.route)
        plan = self.controller.packed_pipeline._plan
        self.assertTrue(all(t.source[3] < self.image.height*.7 for t in plan.tiles))
        self.assertEqual(self.tracker.frame_caches["player_hand"], [])
        self.feed(self.hand, 3, bottom=[])
        self.assertTrue(self.tracker.awaiting_next_round)
        self.feed(self.hand, 1, bottom=[])
        self.assertEqual(self.controller.packed_pipeline.timings["tile_count"],
                         float(len(self.manual_plans[False].tiles)))


if __name__ == "__main__":
    unittest.main()
