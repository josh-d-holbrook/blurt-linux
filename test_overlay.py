import unittest
import cairo
from appearance import PillRenderer, countdown_value, meter_level, PANEL_WIDTH, PANEL_HEIGHT

class OverlayTests(unittest.TestCase):
    def test_countdown_only_final_twenty_seconds(self):
        self.assertIsNone(countdown_value(0))
        self.assertIsNone(countdown_value(98.999))
        self.assertEqual(countdown_value(99),20)
        self.assertEqual(countdown_value(99.01),20)
        self.assertEqual(countdown_value(118.99),1)
        self.assertEqual(countdown_value(119),0)
        self.assertEqual(countdown_value(121),0)

    def test_original_meter_floor_and_mapping(self):
        self.assertEqual(meter_level(-80),0)
        self.assertEqual(meter_level(-50),0)
        self.assertEqual(meter_level(-25),.5)
        self.assertEqual(meter_level(0),1)
        self.assertEqual(meter_level(6),1)
        self.assertEqual(meter_level(float('nan')),0)

    def test_render_states_and_cache(self):
        renderer=PillRenderer()
        background=renderer.background
        images=[]
        for mode,secs in [('recording',30),('recording',99),('recording',119),('transcribing',30),('saving',30)]:
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,PANEL_WIDTH,PANEL_HEIGHT)
            renderer.draw(cairo.Context(surface),mode,secs,.55,.6)
            surface.flush()
            images.append(bytes(surface.get_data()))
            self.assertIs(renderer.background,background)
        self.assertEqual(len(set(images)),5)
        self.assertEqual(len(renderer.layouts),4)
        renderer.draw(cairo.Context(surface),'transcribing',30,.55,.7)
        self.assertEqual(len(renderer.layouts),4)

if __name__=='__main__': unittest.main()
