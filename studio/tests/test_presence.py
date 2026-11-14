"""When the companion shows itself: it waits for a pause, never forever for
what the work is waiting on, never at all when nobody is there; one card at a
time, never swapped mid-reading; whispers only in passing."""
from __future__ import annotations

from secdogie_studio.presence import (
    AWAY,
    ENGAGED,
    FLOW,
    GAP,
    REST,
    SIGNING,
    SOON,
    SPEAKING,
    THINKING,
    UNKNOWN,
    WAITING,
    Attention,
    Moment,
    Stage,
    Whisper,
)


def typing(att, start, seconds, idle=0.5):
    for i in range(int(seconds)):
        att.sample(idle, start + i)
    return start + int(seconds)


def test_attention_modes():
    att = Attention()
    assert att.mode(0) == UNKNOWN
    att.sample(None, 0)
    assert att.mode(0) == UNKNOWN  # Wayland and friends: no idle counter
    t = typing(att, 1, 25)
    assert att.mode(t) == FLOW
    att.sample(0.2, t)
    assert att.mode(t + 1) == FLOW
    att.sample(5.0, t + 2)
    assert att.mode(t + 2) == GAP
    att.sample(400.0, t + 3)
    assert att.mode(t + 3) == AWAY


def test_short_pauses_are_engaged_not_flow_and_touching_the_light_is_a_gap():
    att = Attention()
    t = 0
    for i in range(30):
        att.sample(3.0 if i % 5 == 0 else 0.5, t + i)
    assert att.mode(t + 29) == ENGAGED
    att.touch(t + 29)
    assert att.mode(t + 30) == GAP
    att.sample(0.5, t + 29 + 13)  # back at work after the look
    assert att.mode(t + 29 + 13) == ENGAGED  # the look has passed


def ask(mid="probe:1", arrived=0.0, urgency="gap", kind="ask", expires=None):
    return Moment(mid, kind, "question?", ("a", "b"), urgency=urgency, arrived=arrived, expires=expires)


def test_a_card_waits_for_a_pause():
    st = Stage()
    m = ask()
    assert st.update([m], FLOW, 1).card is None
    v = st.update([m], ENGAGED, 2)
    assert v.card is None and v.held == 1 and v.mood == WAITING
    v = st.update([m], GAP, 3)
    assert v.card == m and v.mood == SPEAKING and v.held == 0


def test_what_the_work_waits_on_surfaces_after_max_hold_but_never_to_an_empty_room():
    st = Stage(max_hold=30)
    m = ask(urgency=SOON, arrived=0)
    assert st.update([m], FLOW, 29).card is None
    assert st.update([m], FLOW, 31).card == m
    st2 = Stage(max_hold=30)
    assert st2.update([m], AWAY, 500).card is None


def test_one_card_at_a_time_and_never_swapped_mid_reading():
    st = Stage()
    first = ask("probe:1", arrived=5)
    sign = ask("sign:1", arrived=6, urgency=SOON, kind="sign")
    assert st.update([first], GAP, 10).card == first
    v = st.update([first, sign], GAP, 11)
    assert v.card == first and v.held == 1  # the reader keeps their card
    v = st.update([sign], GAP, 12)
    assert v.card == sign and v.mood == SIGNING
    assert st.update([sign], GAP, 13).card == sign


def test_expired_moments_are_gone_and_welcome_always_shows():
    st = Stage()
    gone = ask(expires=10)
    assert st.update([gone], GAP, 11).card is None
    welcome = Moment("welcome", "welcome", "hello")
    assert Stage().update([welcome], AWAY, 0).card == welcome


def test_whispers_pass_and_are_held_in_flow_and_dropped_when_stale():
    st = Stage(whisper_for=5, whisper_stale=120)
    st.note(Whisper("w1", "started", arrived=0))
    st.note(Whisper("w1", "started", arrived=0))  # the same note once
    assert st.update([], FLOW, 1).whisper is None
    v = st.update([], GAP, 2)
    assert v.whisper.text == "started" and v.mood == REST
    assert st.update([], GAP, 6).whisper.text == "started"
    assert st.update([], GAP, 8).whisper is None
    st.note(Whisper("w2", "old news", arrived=0))
    assert st.update([], GAP, 500).whisper is None  # courtesy, not a record
    st.note(Whisper("w3", "now", arrived=600))
    assert st.update([ask()], GAP, 600).whisper is None  # a card takes the stage
    assert st.update([], GAP, 601, busy=True).mood == THINKING
