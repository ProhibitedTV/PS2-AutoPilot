from ps2_autopilot.madden_runtime_hygiene import semantic_context


def test_pick_a_play_is_known_playcall_context():
    assert semantic_context({"phase": "menu", "ocr_text": "DEFENSE PICK A PLAY!"}) == "playcall"


def test_game_stats_overlay_is_presentation_not_unknown_navigation():
    state = {
        "phase": "transition",
        "ocr_text": "GAME STATS | Current Drive | Time of Possession | Jets | Bills",
        "field_green": 0.08,
    }
    assert semantic_context(state) == "presentation"


def test_instant_replay_controls_are_presentation():
    state = {
        "phase": "menu",
        "ocr_text": "Instant Replay | X Hide Controls | Rewind | Forward",
        "field_green": 0.32,
    }
    assert semantic_context(state) == "presentation"


def test_pause_menu_single_instant_replay_option_is_not_misclassified_as_replay():
    state = {
        "phase": "menu",
        "ocr_text": "PAUSE MENU | RESUME GAME | INSTANT REPLAY | SETTINGS | QUIT/SAVE",
        "field_green": 0.05,
    }
    assert semantic_context(state) is None


def test_scorebug_plus_turf_suppresses_unknown_on_formation_field_shot():
    state = {
        "phase": "transition",
        "game_state": "transition",
        "ocr_text": "DOWN | TO GO | QTR | CLOCK | :16 | 3 | 38 | 3:10 | 0 MPH",
        "field_green": 0.46,
    }
    assert semantic_context(state) == "field"


def test_fresh_spatial_players_plus_turf_is_field_context():
    state = {
        "phase": "menu",
        "ocr_text": "",
        "field_green": 0.42,
        "spatial_players": 11,
        "spatial_fresh": True,
    }
    assert semantic_context(state) == "field"
