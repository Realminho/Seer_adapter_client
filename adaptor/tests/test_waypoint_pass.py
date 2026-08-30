import unittest

from core.waypoint_pass import segment_passes_within, point_to_segment_distance


class SegmentPassesWithinTests(unittest.TestCase):
    def test_endpoint_inside_radius(self):
        self.assertTrue(segment_passes_within((0, 0), (500, 0), (30, 0), 40))

    def test_both_endpoints_outside_but_segment_crosses(self):
        # 폴링 주기 0.2s x 1002mm/s = 약 200mm 이동. 두 표본 모두 반경 밖이지만
        # 그 사이 선분은 노드를 스쳐 지나간다 — 점 판정이면 놓친다.
        self.assertTrue(segment_passes_within((0, 0), (-100, 10), (100, 10), 40))

    def test_segment_misses(self):
        self.assertFalse(segment_passes_within((0, 0), (-100, 500), (100, 500), 40))

    def test_zero_length_segment_is_point_check(self):
        self.assertTrue(segment_passes_within((0, 0), (10, 0), (10, 0), 40))
        self.assertFalse(segment_passes_within((0, 0), (500, 0), (500, 0), 40))

    def test_none_previous_falls_back_to_point_check(self):
        self.assertTrue(segment_passes_within((0, 0), None, (30, 0), 40))
        self.assertFalse(segment_passes_within((0, 0), None, (500, 0), 40))

    def test_clamp_prevents_infinite_line_false_positive(self):
        # 최근접점의 매개변수 t 를 [0,1] 로 자르지 않으면 무한 직선까지 거리를 재서
        # 선분을 통과하지 않는 노드도 양성 판정한다. 2026-08-26 의 test 계획 검토 시 발견.
        # (5,5)-(10,10) 직선 위에서 원점까지 최단거리는 0 이지만
        # 선분 말단 밖이므로 실제 거리는 7.071 이어야 한다.
        self.assertFalse(segment_passes_within((0, 0), (5, 5), (10, 10), 1))

    def test_point_to_segment_distance_interior_and_clamped(self):
        # 내부 최근접점: t=0.25 인 (1, 1) 이 최근접, 점 (2, 0) 에서 거리는 sqrt(2)
        dist_interior = point_to_segment_distance((2, 0), (0, 0), (4, 4))
        self.assertAlmostEqual(dist_interior, 1.4142135, places=6)

        # t<0 으로 자른 경우: 무한 직선 위의 최근접점은 선분 밖이므로
        # 클램프 후 선분 시작점 (5, 5) 이 최근접. 점 (0, 0) 에서 거리는 5*sqrt(2)
        dist_clamped = point_to_segment_distance((0, 0), (5, 5), (10, 10))
        self.assertAlmostEqual(dist_clamped, 7.0710678, places=6)


if __name__ == "__main__":
    unittest.main()
