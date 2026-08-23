from types import SimpleNamespace

from ps2_autopilot.profiles.madden2005_v19 import Madden2005V19Profile


class FakeController:
    def __init__(self):
        self.taps = []
        self.neutral_calls = 0

    def neutral_sticks(self):
        self.neutral_calls += 1

    def tap(self, button, duration):
        self.taps.append((button, duration))


def profile():
    return Madden2005V19Profile(
        {
            "ocr_enabled": False,
            "ocr_async_enabled": True,
            "ocr_bootstrap_sync": True,
        }
    )


def test_profile_save_prompt_detection_uses_continue_without_saving():
    text = (
        "Do you want to save your Profile to memory card (PS2)? "
        "Yes Continue Without Saving Enable Autosave"
    )
    assert Madden2005V19Profile._looks_like_profile_save_prompt(text)
    assert not Madden2005V19Profile._looks_like_profile_save_prompt(
        "SAVE MEMORY CARD slot 1 MEMORY CARD slot 2 Status Valid"
    )


def test_profile_save_prompt_moves_down_from_default_yes():
    p = profile()
    c = FakeController()
    p.profile_save_prompt_visible = True
    p.menu_highlight = SimpleNamespace(confidence=0.95, text="Yes")
    p.next_action_at = 0.0

    action = p._safe_save_backout(c, 10.0)

    assert [tap[0] for tap in c.taps] == ["down"]
    assert "CONTINUE WITHOUT SAVING" in action
    assert p.profile_save_prompt_submitted is False


def test_profile_save_prompt_confirms_verified_continue_once():
    p = profile()
    c = FakeController()
    p.profile_save_prompt_visible = True
    p.menu_highlight = SimpleNamespace(confidence=0.95, text="Continue Without Saving")
    p.next_action_at = 0.0

    first = p._safe_save_backout(c, 10.0)
    second = p._safe_save_backout(c, 10.5)

    assert [tap[0] for tap in c.taps] == ["cross"]
    assert "CROSS once" in first
    assert "submitted" in second
    assert p.profile_save_prompt_submitted is True
    assert p.profile_save_confirms == 1


def test_profile_save_prompt_moves_up_from_autosave():
    p = profile()
    c = FakeController()
    p.profile_save_prompt_visible = True
    p.menu_highlight = SimpleNamespace(confidence=0.95, text="Enable Autosave")
    p.next_action_at = 0.0

    p._safe_save_backout(c, 10.0)

    assert [tap[0] for tap in c.taps] == ["up"]
    assert p.profile_save_prompt_submitted is False


def test_generic_memory_card_screen_keeps_triangle_backout():
    p = profile()
    c = FakeController()
    p.profile_save_prompt_visible = False
    p.next_action_at = 0.0

    p._safe_save_backout(c, 10.0)

    assert [tap[0] for tap in c.taps] == ["triangle"]
