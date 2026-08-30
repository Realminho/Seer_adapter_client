import unittest

from core.coalescing import drivable_run


class DrivableRunTests(unittest.TestCase):
    def test_single_step_when_next_is_a_breaker(self):
        steps = ["a", "b", "c"]
        self.assertEqual(drivable_run(steps, 0, lambda s: s == "b"), 0)

    def test_runs_to_the_end_when_nothing_breaks(self):
        steps = ["a", "b", "c"]
        self.assertEqual(drivable_run(steps, 0, lambda s: False), 2)

    def test_stops_before_the_breaker(self):
        steps = ["a", "b", "c", "d"]
        self.assertEqual(drivable_run(steps, 0, lambda s: s == "c"), 1)

    def test_breaker_itself_is_its_own_run(self):
        # 끊는 노드는 합치지 않고 그 자체로 한 구간이다 — dock/move 룰이나
        # SOFT/HARD 액션 노드는 반드시 자기 goto 로 가야 한다.
        steps = ["a", "b", "c"]
        self.assertEqual(drivable_run(steps, 1, lambda s: s == "b"), 1)

    def test_last_index(self):
        self.assertEqual(drivable_run(["a"], 0, lambda s: False), 0)


if __name__ == "__main__":
    unittest.main()
