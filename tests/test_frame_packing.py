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


if __name__ == '__main__':
    unittest.main()
