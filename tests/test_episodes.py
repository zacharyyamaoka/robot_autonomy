"""episodes.py: repeats merged into episodes (SPEC "episodes.py")."""

from __future__ import annotations

import random

import pytest

from helpers import HOUR_S, make_intervention, t_of
from robot_autonomy.episodes import EPISODE_GAP_S, Episode, episodes

START = t_of("2026-11-14", 0.0)


def test_gap_is_twelve_hours():
    assert EPISODE_GAP_S == 12 * 3600


def test_same_thread_within_the_gap_is_one_episode():
    mentions = [
        make_intervention("m1", START, thread="chat-1", what="replug the camera"),
        make_intervention("m2", START + 11 * HOUR_S, thread="chat-1", what="still needs you: replug"),
    ]
    [episode] = episodes(mentions)
    assert isinstance(episode, Episode)
    assert episode.mentions == 2
    assert episode.first_t == START
    assert episode.last_t == START + 11 * HOUR_S
    assert episode.ids == ("m1", "m2")
    assert episode.key == ("chat-1", "hands_on", "power_or_replug", "asked")


def test_the_gap_is_inclusive_and_measured_from_the_last_mention():
    # 0 h, 12 h (gap exactly 12 h: joins), 24 h (12 h after the last mention: joins, though 24 h after the first),
    # then 36 h + 1 s (12 h + 1 s after the last: a new episode).
    mentions = [
        make_intervention("m1", START, thread="chat-1"),
        make_intervention("m2", START + 12 * HOUR_S, thread="chat-1"),
        make_intervention("m3", START + 24 * HOUR_S, thread="chat-1"),
        make_intervention("m4", START + 36 * HOUR_S + 1.0, thread="chat-1"),
    ]
    first, second = episodes(mentions)
    assert first.ids == ("m1", "m2", "m3")
    assert first.mentions == 3
    assert first.last_t == START + 24 * HOUR_S
    assert second.ids == ("m4",)
    assert second.first_t == second.last_t == START + 36 * HOUR_S + 1.0


def test_a_new_episode_becomes_the_open_one_for_its_key():
    # m1 at 0 h; m2 at 13 h opens episode 2; m3 at 14 h joins episode 2 (1 h after its last mention).
    mentions = [
        make_intervention("m1", START, thread="chat-1"),
        make_intervention("m2", START + 13 * HOUR_S, thread="chat-1"),
        make_intervention("m3", START + 14 * HOUR_S, thread="chat-1"),
    ]
    first, second = episodes(mentions)
    assert first.ids == ("m1",)
    assert second.ids == ("m2", "m3")


def test_no_thread_means_every_intervention_is_its_own_episode():
    mentions = [make_intervention("m1", START), make_intervention("m2", START)]
    result = episodes(mentions)
    assert len(result) == 2
    assert {episode.key for episode in result} == {
        ("m1", "hands_on", "power_or_replug", "asked"),
        ("m2", "hands_on", "power_or_replug", "asked"),
    }
    assert all(episode.mentions == 1 for episode in result)


@pytest.mark.parametrize(
    "other_fields",
    [
        {"level": "remote", "cause": "run_robot_command"},  # different level (and its cause)
        {"cause": "belt_or_items"},  # different cause
        {"source": "operator"},  # different reading (observed)
    ],
)
def test_the_key_includes_level_cause_and_reading(other_fields):
    mentions = [
        make_intervention("m1", START, thread="chat-1"),
        make_intervention("m2", START + HOUR_S, thread="chat-1", **other_fields),
    ]
    assert len(episodes(mentions)) == 2


def test_first_mention_fields_fixes_and_human_seconds():
    mentions = [
        make_intervention(
            "m1", START, thread="t", avoidable="yes", what="replug the camera", fix="powered hub", human_s=60.0
        ),
        make_intervention("m2", START + HOUR_S, thread="t", avoidable="no", what="again", fix="", human_s=None),
        make_intervention("m3", START + 2 * HOUR_S, thread="t", avoidable="unclear", fix="strain relief", human_s=30.0),
        make_intervention("m4", START + 3 * HOUR_S, thread="t", fix="powered hub"),
    ]
    [episode] = episodes(mentions)
    assert episode.avoidable == "yes"  # the first mention's
    assert episode.what == "replug the camera"  # the first mention's
    assert episode.fixes == ("powered hub", "strain relief")  # distinct, non-empty, first-seen order
    assert episode.human_s == pytest.approx(90.0)  # 60 + 30; unknowns skipped
    assert episode.ids == ("m1", "m2", "m3", "m4")
    assert episode.robot == "arm-1"
    assert episode.level == "hands_on"
    assert episode.cause == "power_or_replug"
    assert episode.reading == "asked"
    assert episode.source == "chat"


def test_human_seconds_is_none_when_no_mention_knows_it():
    mentions = [make_intervention("m1", START, thread="t"), make_intervention("m2", START + 60.0, thread="t")]
    [episode] = episodes(mentions)
    assert episode.human_s is None
    assert episode.fixes == ()


def test_commissioning_is_the_first_mentions():
    mentions = [
        make_intervention("m1", START, thread="t", cause="mechanical"),  # commissioning resolves True
        make_intervention("m2", START + 60.0, thread="t", cause="mechanical", commissioning=False),
    ]
    [episode] = episodes(mentions)
    assert episode.commissioning is True


def test_input_order_does_not_matter_and_ties_sort_by_id():
    mentions = [
        make_intervention("m-b", START, thread="t", what="from b"),
        make_intervention("m-a", START, thread="t", what="from a"),
        make_intervention("m-c", START + HOUR_S, thread="t"),
        make_intervention("solo", START + 30 * HOUR_S),
    ]
    shuffled = list(mentions)
    random.Random(7).shuffle(shuffled)
    result = episodes(shuffled)
    assert result == episodes(mentions)
    first = result[0]
    assert first.ids == ("m-a", "m-b", "m-c")  # walked by (t, id)
    assert first.what == "from a"


def test_output_is_sorted_by_first_t_then_key():
    mentions = [
        make_intervention("z-late", START + 5 * HOUR_S),
        make_intervention("b-early", START),
        make_intervention("a-early", START),
        make_intervention("thread-mention", START + 2 * HOUR_S, thread="t-1"),
    ]
    result = episodes(mentions)
    assert [episode.key[0] for episode in result] == ["a-early", "b-early", "t-1", "z-late"]


def test_empty_input():
    assert episodes([]) == []


def test_custom_gap():
    mentions = [make_intervention("m1", START, thread="t"), make_intervention("m2", START + 120.0, thread="t")]
    assert len(episodes(mentions, gap_s=60.0)) == 2
    assert len(episodes(mentions, gap_s=120.0)) == 1
