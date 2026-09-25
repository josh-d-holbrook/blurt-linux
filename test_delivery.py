import unittest
from types import SimpleNamespace
from unittest.mock import patch
import blurt
from gi.repository import GLib

class DeliveryTests(unittest.TestCase):
    def app(self, held=0, focus=True):
        clipboard = SimpleNamespace(wait_for_text=lambda: None, set_text=lambda *_: None, store=lambda:None)
        target=(1,'test-editor')
        mask=[held]
        app=SimpleNamespace(settings={'restore_clipboard':False,'auto_paste':True},
            clipboard=clipboard,target=target,paste_pending=False,
            hotkey=SimpleNamespace(focus=lambda: target if focus else None,
                root=SimpleNamespace(query_pointer=lambda:SimpleNamespace(mask=mask[0]))),
            status=SimpleNamespace(set_text=lambda *_:None),notify=lambda *_:None)
        return app,mask

    def test_initial_paste_has_no_timer_delay(self):
        app,_=self.app()
        with patch.object(blurt.GLib,'timeout_add') as timer, patch.object(blurt.GLib,'idle_add') as idle, patch.object(blurt.subprocess,'run') as run:
            blurt.Blurt.deliver(app,'text')
            timer.assert_not_called()
            callback=idle.call_args.args[0]
            self.assertFalse(callback())
            run.assert_called_once()
            self.assertFalse(app.paste_pending)

    def test_held_modifier_defers_then_pastes(self):
        app,mask=self.app(blurt.X.ControlMask)
        with patch.object(blurt.GLib,'timeout_add') as timer, patch.object(blurt.GLib,'idle_add') as idle, patch.object(blurt.subprocess,'run') as run:
            blurt.Blurt.deliver(app,'text')
            callback=idle.call_args.args[0]
            self.assertFalse(callback())
            run.assert_not_called()
            timer.assert_called_once_with(20,callback)
            mask[0]=0
            self.assertFalse(callback())
            run.assert_called_once()

    def test_changed_focus_never_pastes(self):
        app,_=self.app(focus=False)
        with patch.object(blurt.GLib,'idle_add') as idle, patch.object(blurt.subprocess,'run') as run:
            blurt.Blurt.deliver(app,'text')
            idle.assert_not_called()
            run.assert_not_called()

if __name__=='__main__': unittest.main()
