import unittest

from behancer_bot.control import RunControl, SkipRequested, StopRequested


class RunControlTests(unittest.TestCase):
    def test_skip_is_consumed_by_checkpoint(self):
        control = RunControl()
        control.skip()
        with self.assertRaises(SkipRequested):
            control.checkpoint()
        control.checkpoint()

    def test_stop_always_interrupts(self):
        control = RunControl()
        control.stop()
        with self.assertRaises(StopRequested):
            control.checkpoint(allow_skip=False)


if __name__ == "__main__":
    unittest.main()
