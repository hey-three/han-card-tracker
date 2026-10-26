"""切口完整性、坐标恢复、同点数多张及跨块冲突回归。"""

import unittest

from core.frame_packing import Detection, PackingPlan, Tile, make_plan


class PackingTests(unittest.TestCase):
    """使用确定像素框验证拼图几何，不加载任何模型。"""

    def test_long_strip_has_overlapping_center(self) -> None:
        """中间目标在两块内完整出现，画布保留原像素比例。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)])
        self.assertEqual([tile.source for tile in plan.tiles], [(0, 0, 540, 200), (460, 0, 1000, 200)])
        self.assertEqual(plan.canvas_size, (540, 404))
        for tile in plan.tiles:
            a, b, c, d = tile.source
            x, y, ex, ey = tile.bounds
            self.assertEqual((c-a, d-b), (ex-x, ey-y))

    def test_same_position_deduplicates_but_distinct_aces_remain(self) -> None:
        """重叠处同一 A 去重，另一位置的 A 不受影响。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)])
        detections = [Detection('A', (480., 30., 500., 80.), .9),
                      Detection('A', (20., 234., 40., 284.), .95),
                      Detection('A', (100., 30., 120., 80.), .9)]
        cards, reason = plan.restore(detections)
        self.assertEqual(reason, '')
        self.assertEqual(sorted(c.bbox for c in cards), [(100., 30., 120., 80.), (480., 30., 500., 80.)])

    def test_different_class_same_position_requires_full_frame(self) -> None:
        """原图同一目标被判成不同类别时不能按置信度悄悄选一个。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)])
        cards, reason = plan.restore([Detection('ten', (480., 30., 500., 80.), .99),
                                      Detection('A', (20., 234., 40., 284.), .8)])
        self.assertEqual(cards, [])
        self.assertIn('类别冲突', reason)

    def test_clipped_fragment_can_be_replaced_by_complete_copy(self) -> None:
        """切口残片即使类别错误，另一块的完整副本仍可消除该残片。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)])
        cards, reason = plan.restore([Detection('A', (530., 30., 540., 80.), .99),
                                      Detection('ten', (70., 234., 100., 284.), .9)])
        self.assertEqual(reason, '')
        self.assertEqual([(c.name, c.bbox) for c in cards], [('ten', (530., 30., 560., 80.))])

    def test_unprotected_cut_and_cross_gap_are_rejected(self) -> None:
        """没有完整副本的残片和跨画布间隙的框都要求复查。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)])
        for box in ((530., 30., 540., 80.), (10., 190., 30., 210.)):
            with self.subTest(box=box):
                cards, reason = plan.restore([Detection('A', box)])
                self.assertEqual(cards, [])
                self.assertTrue(reason)

    def test_overlapping_regions_are_unioned_before_splitting(self) -> None:
        """原始区域重叠不会先复制成两份不受控输入。"""
        plan = make_plan((100, 100), [(0., 0., .7, .7), (.3, .3, 1., 1.)])
        self.assertEqual(len(plan.tiles), 1)
        self.assertEqual(plan.tiles[0].source, (0, 0, 100, 100))

    def test_fractional_band_border_does_not_erase_hand_split(self) -> None:
        """相邻桌面带与手牌带取整重叠一像素时仍保留手牌中心保护切分。"""
        plan = make_plan((1000, 800), [(0., .2, 1., .724), (0., .724, 1., 1.)])
        self.assertEqual(len([t for t in plan.tiles if t.source[1] == 579]), 2)

    def test_same_tile_nearby_equal_ranks_are_not_merged(self) -> None:
        """同一图块里的相邻同点数不进入跨块去重。"""
        plan = PackingPlan((100, 100), [Tile((0, 0, 100, 100), 0, 0)])
        cards, reason = plan.restore([Detection('A', (10., 10., 30., 40.)), Detection('A', (12., 10., 32., 40.))])
        self.assertEqual(len(cards), 2)
        self.assertEqual(reason, '')

    def test_nonzero_crop_roundtrip_and_outward_rounding(self) -> None:
        """裁片偏移还原到原窗口，像素边界向外取整。"""
        plan = make_plan((100, 100), [(.101, .201, .499, .699)])
        self.assertEqual(plan.tiles[0].source, (10, 20, 50, 70))
        cards, reason = plan.restore([Detection('K', (5., 6., 15., 26.))])
        self.assertEqual(cards[0].bbox, (15., 26., 25., 46.))
        self.assertEqual(reason, '')

    def test_invalid_inputs_fail_explicitly(self) -> None:
        """拒绝 bool 尺寸、NaN、空区域和反向矩形。"""
        with self.assertRaises(TypeError):
            make_plan((True, 100), [(0., 0., 1., 1.)])
        for size, regions, guard in [((0, 1), [(0., 0., 1., 1.)], .04),
                                      ((100, 100), [], .04),
                                      ((100, 100), [(0., 0., 1., 1.)], float('nan')),
                                      ((100, 100), [(.5, .5, .1, .1)], .04)]:
            with self.subTest(size=size, regions=regions), self.assertRaises(ValueError):
                make_plan(size, regions, guard)

    def test_fixed_canvas_preserves_source_and_roundtrip_across_window_sizes(self) -> None:
        """窗口大小和比例不同，实际拼图始终固定方形，检测恢复仍与原图一致。"""
        for size in ((1000, 200), (2488, 1351), (720, 1600), (127, 65)):
            with self.subTest(size=size):
                native = make_plan(size, [(0., .2, 1., .7), (0., .75, 1., 1.)])
                plan = native.fit()
                self.assertEqual(plan.canvas_size, (640, 640))
                self.assertEqual([t.source for t in plan.tiles], [t.source for t in native.tiles])
                for tile in plan.tiles:
                    a, b, c, d = tile.source
                    source = (a+(c-a)*.2, b+(d-b)*.2, a+(c-a)*.4, b+(d-b)*.4)
                    restored, reason = plan.restore([Detection('A', tile.project(source))])
                    self.assertEqual(reason, '')
                    for actual, expected in zip(restored[0].bbox, source):
                        self.assertAlmostEqual(actual, expected)

    def test_scaled_overlap_still_deduplicates_and_rejects_conflict(self) -> None:
        """固定画布缩放后仍按原始位置去重，不能按缩放图中的位置比较。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)]).fit()
        boxes = [Detection('A', tile.project((480., 30., 500., 80.))) for tile in plan.tiles]
        cards, reason = plan.restore(boxes)
        self.assertEqual((len(cards), reason), (1, ''))
        boxes[1].name = 'K'
        self.assertIn('类别冲突', plan.restore(boxes)[1])

    def test_fixed_canvas_padding_is_not_a_card_region(self) -> None:
        """灰色留白中的伪检测必须触发原图回退。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)]).fit()
        cards, reason = plan.restore([Detection('A', (1., 1., 4., 4.))])
        self.assertEqual(cards, [])
        self.assertTrue(reason)

    def test_scaled_cut_fragment_uses_whole_copy(self) -> None:
        """统一缩放后切口残片仍由另一图块完整副本替代。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)]).fit()
        cards, reason = plan.restore([
            Detection('A', plan.tiles[0].project((530., 30., 540., 80.))),
            Detection('ten', plan.tiles[1].project((530., 30., 560., 80.))),
        ])
        self.assertEqual((len(cards), reason), (1, ''))
        self.assertEqual(cards[0].name, 'ten')

    def test_fixed_canvas_rejects_invalid_geometry(self) -> None:
        """非法画布、裁片绘制尺寸及越界目标在规划阶段报错。"""
        plan = make_plan((1000, 200), [(0., 0., 1., 1.)])
        with self.assertRaises(TypeError):
            plan.fit((True, 640))
        with self.assertRaises(ValueError):
            plan.fit((0, 640))
        with self.assertRaises(ValueError):
            plan.fit((1, 1))
        with self.assertRaises(ValueError):
            plan.fit((30, 30))
        with self.assertRaises(ValueError):
            PackingPlan((100, 100), [Tile((0, 0, 100, 100), 0, 0)], (50, 50))


if __name__ == '__main__':
    unittest.main()
