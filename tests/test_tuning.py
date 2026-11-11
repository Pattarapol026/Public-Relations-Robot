"""Admin tuning: stored over .env, validated, applied to the running parts, guarded in the UI."""
from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.tuning import AREA_WIDTH_FACTOR, TuningController, TuningError, validate
from database.repositories import SettingsRepository
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.log_bridge import QtLogHandler


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    assert apply_app_font(qt_app) == BUNDLED_FAMILY
    return qt_app


@pytest.fixture
def application(qt_app, settings):
    application = Application(settings, QtLogHandler())
    yield application
    application.shutdown()
    application.window.deleteLater()


def make_controller(settings, database, audit=None):
    return TuningController(
        settings, SettingsRepository(database), audit or (lambda *_args: None)
    )


def test_defaults_come_from_env_and_saved_values_survive_a_restart(settings, database):
    audit = []
    tuning = make_controller(settings, database, lambda *args: audit.append(args))
    assert tuning.values == tuning.defaults
    assert tuning.values.tts_speed == settings.tts_speed
    assert tuning.values.vision_greet_distance_m == settings.vision_greet_distance_m

    seen = []
    tuning.changed.connect(seen.append)
    tuned = replace(tuning.values, tts_speed=0.8, vision_greet_distance_m=2.5, voice_follow_up=False)
    tuning.save(tuned)
    assert seen == [tuned]
    assert audit[-1][1] == "tuning.update" and "tts_speed=0.8" in audit[-1][2]

    reopened = make_controller(settings, database)
    assert reopened.values == tuned

    reopened.restore_defaults()
    assert reopened.values == reopened.defaults
    assert make_controller(settings, database).values == reopened.defaults


def test_saving_the_same_values_changes_nothing(settings, database):
    tuning = make_controller(settings, database)
    seen = []
    tuning.changed.connect(seen.append)
    tuning.save(tuning.values)
    assert seen == []


def test_values_that_cannot_work_together_are_refused(settings, database):
    tuning = make_controller(settings, database)
    base = tuning.values
    with pytest.raises(TuningError, match="ระยะสนทนา"):
        tuning.save(replace(base, vision_engage_distance_m=2.0, vision_greet_distance_m=1.8))
    with pytest.raises(TuningError, match="เดินออกไป"):
        tuning.save(replace(base, vision_greet_distance_m=3.0, vision_release_distance_m=3.0))
    with pytest.raises(TuningError, match="ความเร็ว"):
        validate(replace(base, tts_speed=5.0))
    assert tuning.values == base


def test_broken_stored_values_fall_back_to_env(settings, database):
    repository = SettingsRepository(database)
    repository.set("tuning.tts_speed", "fast")  # not a number: ignored
    assert make_controller(settings, database).values.tts_speed == settings.tts_speed
    # Each stored number is fine alone but together they break the distance order.
    repository.set("tuning.vision_engage_distance_m", "2.9")
    repository.set("tuning.vision_greet_distance_m", "1.0")
    assert make_controller(settings, database).values == make_controller(settings, database).defaults


def test_presence_configs_follow_the_tuning(settings, database):
    tuned = replace(
        make_controller(settings, database).values,
        vision_lateral_limit_m=1.2, vision_engage_distance_m=1.1, vision_greet_distance_m=2.2,
    )
    assert tuned.talk_config().lateral_limit_m == 1.2
    assert tuned.area_config().lateral_limit_m == pytest.approx(1.2 * AREA_WIDTH_FACTOR)
    assert tuned.talk_config().engage_distance_m == 1.1
    assert tuned.area_config().engage_distance_m == 2.2


def test_saved_tuning_reaches_the_running_parts_without_a_restart(application):
    tuning = application.tuning
    tuned = replace(
        tuning.values,
        tts_speed=1.4, mic_silence_s=2.0, mic_level_threshold=0.05,
        vision_greet_distance_m=2.4, vision_engage_distance_m=1.0, vision_release_distance_m=3.0,
        vision_auto_listen=False,
    )
    tuning.save(tuned)

    assert application.speech._tts.speed == 1.4
    endpoint = application.voice.endpoint_config
    assert (endpoint.silence_s, endpoint.level_threshold) == (2.0, 0.05)
    assert application.vision._area.config.engage_distance_m == 2.4
    assert application.vision._area.config.release_distance_m == 3.0
    assert application.vision._talk.config.engage_distance_m == 1.0
    assert application.window._tuning.values.vision_auto_listen is False


def test_admin_sound_page_tracks_unsaved_edits_and_saves_them(application):
    page = application.window.admin_screen._audio_tab
    assert not page.has_unsaved_changes()
    page._editors.widget("tts_speed").set_value(1.25)
    assert page.has_unsaved_changes()
    page.discard_changes()
    assert not page.has_unsaved_changes()
    assert application.tuning.values.tts_speed == application.settings.tts_speed

    page._editors.widget("tts_speed").set_value(1.25)
    page._editors.widget("voice_follow_up").setChecked(False)
    page._save()
    assert application.tuning.values.tts_speed == 1.25
    assert application.tuning.values.voice_follow_up is False
    assert not page.has_unsaved_changes()


def test_admin_camera_page_refuses_distances_out_of_order(application):
    page = application.window.admin_screen._vision_tab
    page._editors.widget("vision_engage_distance_m").set_value(2.8)
    page._editors.widget("vision_greet_distance_m").set_value(2.0)
    page._save()
    assert "ระยะสนทนา" in page._result.text()
    assert application.tuning.values == application.tuning.defaults
    assert page.has_unsaved_changes()

    application.window.admin_screen.reset()  # leaving Admin drops the edits
    assert not page.has_unsaved_changes()
