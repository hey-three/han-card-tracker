"""分阶段区域覆盖、实测牌框尺度、矩形装箱及坐标恢复测试。"""

from math import ceil, floor
import unittest
from PIL import Image

from config.packing_profile import MEASURED_CARD_HEIGHTS, PACKED_INPUT_SIZE, REFERENCE_CARD_HEIGHT, REGION_SCALE_FACTORS, stage_regions
from core.square_packing import _source_rects, make_rect_plan, union_area
from core.frame_packing import Detection, PackingPlan, Tile
from core.packed_pipeline import PackedPipeline
from utils.preview_packing import EXAMPLE_LAYOUTS


class StagedPackingTests(unittest.TestCase):
    """不加载模型，验证实测比例确实用于单牌尺度而非区域框拉伸。"""

    def setUp(self) -> None:
        """使用与三张参考图相符的含控件归一化五区。"""
        keys = ("landlord_cards", "opponent_left", "opponent_right", "player_hand", "player_played")
        self.layout = {k: list(v) for k, v in zip(keys, EXAMPLE_LAYOUTS["controls"])}

    def test_measured_medians_are_equalised_with_bottom_twelve_percent_larger(self) -> None:
        """用单牌返回框测量值计算权重，普通牌角归一，底牌目标高度再加 12%。"""
        for name, height in MEASURED_CARD_HEIGHTS.items():
            expected = REFERENCE_CARD_HEIGHT*(1.12 if name == "landlord_cards" else 1.)
            self.assertAlmostEqual(height*REGION_SCALE_FACTORS[name], expected)
        self.assertGreater(REGION_SCALE_FACTORS["landlord_cards"], 1.)
        self.assertLess(REGION_SCALE_FACTORS["player_hand"], 1.)

    def test_two_then_four_roles_and_no_hand_after_initial_deduction(self) -> None:
        """开局只选底牌和手牌，已扣手牌后只选三家出牌与底牌。"""
        opening, _, _ = stage_regions(self.layout, False)
        playing, _, _ = stage_regions(self.layout, True)
        self.assertEqual(set(opening), {"landlord_cards", "player_hand"})
        self.assertEqual(set(playing), {"landlord_cards", "opponent_left", "opponent_right", "player_played"})

    def test_rectangular_and_square_plans_cover_all_selected_pixels(self) -> None:
        """不同画布都完整覆盖所选框；画布选择不能靠丢像素节省空间。"""
        for recording in (False, True):
            _, regions, scales = stage_regions(self.layout, recording)
            for canvas in (PACKED_INPUT_SIZE, (608, 608), (640, 640)):
                plan = make_rect_plan((1284, 804), regions, scales, canvas)
                self.assertEqual(plan.canvas_size, canvas)
                for a, b, c, d in regions:
                    roi = (ceil(a*1284-1e-7), ceil(b*804-1e-7),
                           floor(c*1284+1e-7), floor(d*804+1e-7))
                    parts = [(max(roi[0], t.source[0]), max(roi[1], t.source[1]),
                              min(roi[2], t.source[2]), min(roi[3], t.source[3])) for t in plan.tiles]
                    self.assertEqual(union_area([r for r in parts if r[0] < r[2] and r[1] < r[3]]),
                                     (roi[2]-roi[0])*(roi[3]-roi[1]))
                for i, tile in enumerate(plan.tiles):
                    w, h = tile.source[2]-tile.source[0], tile.source[3]-tile.source[1]
                    rw, rh = tile.render_size
                    self.assertLessEqual(abs(rw*h-rh*w), max(w, h))
                    for other in plan.tiles[i+1:]:
                        a, b = tile.bounds, other.bounds
                        self.assertTrue(a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
                    point = tuple(float(v) for v in tile.source)
                    for actual, expected in zip(tile.unproject(tile.project(point)), point):
                        self.assertAlmostEqual(actual, expected)

    def test_bottom_expected_card_height_exceeds_other_active_roles_after_packing(self) -> None:
        """验证最终渲染尺寸中的实测牌角中位高度，不能只比整块裁片尺寸。"""
        for recording in (False, True):
            names, regions, scales = stage_regions(self.layout, recording)
            plan = make_rect_plan((1284, 804), regions, scales)
            heights = {}
            for name, region in zip(names, regions):
                base = _source_rects((1284, 804), [region])[0]
                tiles = [t for t in plan.tiles if t.source[1] == base[1] and t.source[3] == base[3]
                         and base[0] <= t.source[0] < t.source[2] <= base[2]]
                heights[name] = MEASURED_CARD_HEIGHTS[name]*min(t.render_size[1]/(t.source[3]-t.source[1]) for t in tiles)
            self.assertGreater(heights["landlord_cards"], max(v for k, v in heights.items() if k != "landlord_cards"))

    def test_bad_scale_and_canvas_parameters_are_rejected(self) -> None:
        """非法权重不能产生部分规划或隐藏丢区。"""
        _, regions, scales = stage_regions(self.layout, False)
        for weights, canvas, error in (([], (768, 480), ValueError), ([0., 1.], (768, 480), ValueError),
                                        ([True, 1.], (768, 480), TypeError), (scales, (0, 480), ValueError)):
            with self.subTest(weights=weights, canvas=canvas), self.assertRaises(error):
                make_rect_plan((1284, 804), regions, weights, canvas)

    def test_source_crops_never_read_outside_user_rectangle(self) -> None:
        """框外蓝色哨兵像素不能进入拼图，任何重叠切片也必须在用户框内。"""
        region = (.205, .305, .795, .895)
        self.assertEqual(_source_rects((100, 100), [region]), [(21, 31, 79, 89)])
        source = Image.new('RGB', (100, 100), (0, 0, 255))
        source.paste((255, 0, 0), (21, 31, 79, 89))
        plan = make_rect_plan(source.size, [region], [1.])
        canvas = PackedPipeline()._render(source, plan)
        self.assertTrue(all(color in ((255, 0, 0), (114, 114, 114))
                            for _, color in canvas.getcolors(canvas.width*canvas.height)))
        for tile in plan.tiles:
            a, b, c, d = tile.source
            self.assertTrue(21 <= a < c <= 79 and 31 <= b < d <= 89)

    def test_user_border_is_not_mistaken_for_internal_cut(self) -> None:
        """人工外边界可接受模型识别结果，内部切口仍必须有完整副本。"""
        plan = make_rect_plan((1000, 800), [(.2, .2, .8, .3)], [1.])
        tile = next(t for t in plan.tiles if t.source[0] == 200)
        cards, reason = plan.restore([Detection('A', tile.project((200., 170., 220., 210.)))])
        self.assertEqual(reason, '')
        self.assertEqual(len(cards), 1)
        internal = next(t for t in plan.tiles if t.source[2] < 800)
        edge = float(internal.source[2])
        cards, reason = plan.restore([Detection('K', internal.project((edge-20., 170., edge, 210.)))])
        self.assertEqual(cards, [])
        self.assertIn('缺少完整副本', reason)

    def test_overlapping_user_border_fragment_uses_complete_copy(self) -> None:
        """用户两框重叠时，人工边缘残片也不能把同一张牌计算两次。"""
        whole = Tile((200, 200, 400, 300), 0, 0, source_region=(200, 200, 400, 300))
        partial = Tile((250, 200, 350, 300), 0, 110, source_region=(250, 200, 350, 300))
        plan = PackingPlan((1000, 800), [whole, partial])
        cards, reason = plan.restore([Detection('A', whole.project((240., 220., 280., 260.))),
                                      Detection('K', partial.project((250., 220., 280., 260.)))])
        self.assertEqual(reason, '')
        self.assertEqual([card.name for card in cards], ['A'])


if __name__ == "__main__":
    unittest.main()
