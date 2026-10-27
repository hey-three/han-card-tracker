"""五区装箱的几何覆盖、切口完整性、真实信息利用率与坐标恢复回归。"""

from math import ceil, floor
import unittest

from core.frame_packing import Detection, PackingPlan, Tile
from core.square_packing import make_square_plan, plan_metrics, union_area
from config import settings
from core.layout_analyzer import LayoutAnalyzer
from test_packed_pipeline import raw_result


FULL_REGIONS = [(.37, 0., .55, .08), (.2, .25, .47, .45), (.48, .25, .85, .45),
                (.04, .7, .96, .85), (.04, .46, .96, .57)]
CONTROLS_REGIONS = [(.35, .08, .45, .15), (.2, .32, .455, .49), (.46, .32, .8, .49),
                    (.04, .7, .96, .85), (.04, .5, .96, .6)]


class SquarePackingTests(unittest.TestCase):
    """验证全部输入像素被保留，而不是只检查输出尺寸正确。"""

    def test_all_five_regions_are_covered_without_holes(self) -> None:
        """两套人工布局、不同分辨率均完整覆盖五区，源矩形不越界。"""
        for size in ((1284, 804), (1000, 800), (1920, 1080)):
            for regions in (FULL_REGIONS, CONTROLS_REGIONS):
                with self.subTest(size=size, regions=regions):
                    plan = make_square_plan(size, regions)
                    self.assertEqual(plan.canvas_size, (640, 640))
                    for a, b, c, d in regions:
                        roi = (floor(a*size[0]), floor(b*size[1]), ceil(c*size[0]), ceil(d*size[1]))
                        intersections = [(max(roi[0], t.source[0]), max(roi[1], t.source[1]),
                                          min(roi[2], t.source[2]), min(roi[3], t.source[3]))
                                         for t in plan.tiles]
                        valid = [r for r in intersections if r[0] < r[2] and r[1] < r[3]]
                        self.assertEqual(union_area(valid), (roi[2]-roi[0])*(roi[3]-roi[1]))
                    for i, first in enumerate(plan.tiles):
                        self.assertGreaterEqual(min(first.source), 0)
                        self.assertLessEqual(first.source[2], size[0])
                        self.assertLessEqual(first.source[3], size[1])
                        for second in plan.tiles[i+1:]:
                            a, b = first.bounds, second.bounds
                            self.assertTrue(a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

    def test_current_layouts_reduce_coverage_and_padding_without_shrinking_cards(self) -> None:
        """常用布局保留五区，只覆盖不到半幅原图，灰边低于四分之一。"""
        for regions in (FULL_REGIONS, CONTROLS_REGIONS):
            metrics = plan_metrics(make_square_plan((1284, 804), regions))
            self.assertLess(metrics["source_coverage"], .5)
            self.assertLess(metrics["source_with_overlap"], .6)
            self.assertLess(metrics["padding_fraction"], .25)
            self.assertGreater(metrics["source_scale"], 640/1284)

    def test_cards_across_every_cut_have_whole_copies_and_restore_once(self) -> None:
        """让许多同点数目标扫过长手牌及第二行，切口碎片不得增减张数。"""
        plan = make_square_plan((1284, 804), FULL_REGIONS)
        originals = [(float(x), y, float(x+20), y+24) for y in (575., 630.) for x in range(65, 1200, 27)]
        projected: list[Detection] = []
        for original in originals:
            whole = False
            for tile in plan.tiles:
                a, b, c, d = tile.source
                x1, y1, x2, y2 = original
                whole |= a <= x1 and b <= y1 and x2 <= c and y2 <= d
                crop = (max(a, x1), max(b, y1), min(c, x2), min(d, y2))
                if crop[0] < crop[2] and crop[1] < crop[3]:
                    projected.append(Detection("A", tile.project(crop)))
            self.assertTrue(whole)
        cards, reason = plan.restore(projected)
        self.assertEqual(reason, "")
        self.assertEqual(len(cards), len(originals))
        restored = sorted(c.bbox for c in cards)
        for actual, expected in zip(restored, sorted(originals)):
            for value, original in zip(actual, expected):
                self.assertAlmostEqual(value, original)

    def test_overlapping_manual_regions_do_not_expand_into_a_large_envelope(self) -> None:
        """相交的 L 形区域不把中间大块无关背景也纳入推理。"""
        regions = [(0., 0., .8, .15), (.65, .1, .8, .9)]
        metrics = plan_metrics(make_square_plan((1000, 800), regions))
        self.assertLess(metrics["source_coverage"], .3)
        self.assertEqual(union_area([(0, 0, 10, 5), (5, 0, 15, 5)]), 75)

    def test_all_tiles_share_one_scale_with_only_integer_rounding_error(self) -> None:
        """不将长条单独拉宽或压扁；映射使用实际整数渲染尺寸。"""
        plan = make_square_plan((1284, 804), FULL_REGIONS)
        scale = plan_metrics(plan)["source_scale"]
        for tile in plan.tiles:
            width, height = tile.source[2]-tile.source[0], tile.source[3]-tile.source[1]
            self.assertLessEqual(abs(tile.render_size[0]-width*scale), 1.)
            self.assertLessEqual(abs(tile.render_size[1]-height*scale), 1.)
            box = tuple(float(v) for v in tile.source)
            for actual, expected in zip(tile.unproject(tile.project(box)), box):
                self.assertAlmostEqual(actual, expected)

    def test_large_cut_fragment_is_not_removed_by_a_small_contained_card(self) -> None:
        """大残片内仅有一张小完整牌，不能把残片整体当作已覆盖而丢弃。"""
        left, right = Tile((0, 0, 540, 200), 0, 0), Tile((460, 0, 1000, 200), 0, 205)
        plan = PackingPlan((1000, 200), [left, right])
        cards, reason = plan.restore([Detection("A", left.project((470., 30., 490., 80.))),
                                      Detection("K", right.project((460., 10., 550., 130.)))])
        self.assertEqual(cards, [])
        self.assertIn("缺少完整副本", reason)

    def test_fragment_crossing_tile_edge_can_use_its_whole_source_copy(self) -> None:
        """越界一像素的切口碎片有完整副本时可舍弃，否则必须整图复查。"""
        left, right = Tile((0, 0, 540, 200), 0, 0), Tile((460, 0, 1000, 200), 20, 205)
        plan = PackingPlan((1000, 200), [left, right])
        fragment = Detection("K", right.project((459., 40., 480., 60.)))
        whole = Detection("A", left.project((450., 30., 490., 80.)))
        cards, reason = plan.restore([whole, fragment])
        self.assertEqual(reason, "")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].name, "A")
        self.assertTrue(plan.restore([fragment])[1])

    def test_invalid_inputs_are_rejected(self) -> None:
        """非法尺寸、区域和保护参数不能产生部分拼图。"""
        for size, regions, guard, canvas, error in (
            ((1000, 800), [], .04, (640, 640), ValueError),
            ((0, 800), FULL_REGIONS, .04, (640, 640), ValueError),
            ((True, 800), FULL_REGIONS, .04, (640, 640), TypeError),
            ((1000, 800), [(0., 0., 1.1, 1.)], .04, (640, 640), ValueError),
            ((1000, 800), [(0., "0", 1., 1.)], .04, (640, 640), TypeError),
            ((1000, 800), FULL_REGIONS, True, (640, 640), TypeError),
            ((1000, 800), FULL_REGIONS, float("nan"), (640, 640), ValueError),
            ((1000, 800), FULL_REGIONS, .04, (640, 320), ValueError),
        ):
            with self.subTest(size=size, guard=guard, canvas=canvas):
                with self.assertRaises(error):
                    make_square_plan(size, regions, guard, canvas)

    def test_manual_partition_keeps_centers_inside_fractional_roi_edges(self) -> None:
        """原图边界不是整数时，不能因向内取整丢掉仍在用户框内的目标。"""
        keys = ("landlord_cards", "opponent_left", "opponent_right", "player_hand", "player_played")
        config = settings.validate_layout({"window_title": "Example", "mode": "manual",
                                           "layout": dict(zip(keys, FULL_REGIONS))})
        raw = raw_result([Detection("A", (1220.4, 620., 1244.4, 660.))])
        parsed = LayoutAnalyzer(config).parse_and_sort(raw, (1284, 804))
        self.assertEqual(len(parsed["player_hand"]), 1)


if __name__ == "__main__":
    unittest.main()
