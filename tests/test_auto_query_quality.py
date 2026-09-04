"""Auto-query signal-quality gate (fleet check-in, 2026-09-03).

Seven of eight long-running sessions had never deliberately queried the
palace. Their reasons, and the behaviour each test pins:

* peer-agent messages were mined for entities  -> strip_foreign_blocks
* generic English fired the query               -> generic caps score 0
* domain identifiers never fired                 -> identifier signals, union search
* the same manifest / diary drawers every turn  -> exhaust filter + per-session dedupe
* five uniformly-0.44 hits                       -> confidence floor
* "I could not tell what is IN it"               -> wing inventory header
* silent misses                                  -> receipt even on zero hits
"""

import json

from mempalace.auto_query import SessionState
from mempalace.auto_query.router import DEPTH_FETCH, pick_tool
from mempalace.auto_query.runner import (
    _filter_search_results,
    _is_exhaust,
    _wing_inventory_lines,
    run_auto_query,
)
from mempalace.auto_query.signals import (
    _extract_identifier_signals,
    extract_signals,
    strip_foreign_blocks,
)


def _session(turn=2):
    return SessionState(turn_index=turn, queried_entities=set(), session_id="t")


class _Cfg:
    auto_query_enabled = True
    auto_query_mode = "balanced"
    auto_query_depth_cache_ttl = 0
    auto_query_min_similarity = 0.50
    auto_query_min_bm25 = 1.5
    daemon_url = "http://127.0.0.1:1"


# --- peer-message stripping -------------------------------------------------


def test_strip_foreign_blocks_removes_peer_and_harness_text():
    text = (
        "fix the mine please\n"
        '<cross-session-message from="x" from-name="peer">Decline Write Reply Context '
        "Goal ONE Hold</cross-session-message>\n"
        "<system-reminder>Lint Commit Fault</system-reminder>"
    )
    out = strip_foreign_blocks(text)
    assert "Decline" not in out and "Lint" not in out
    assert "fix the mine please" in out


def test_peer_message_entities_do_not_fire():
    text = (
        'ok\n<teammate-message teammate_id="a">Please Decline the Write and Reply to '
        "Context about Candela</teammate-message>"
    )
    sig = extract_signals(text, _session(), "", {"candela"}, None)
    assert sig.entity == []
    assert sig.total_score == 0


# --- generic English ---------------------------------------------------------


def test_generic_capitalized_words_score_nothing():
    text = "Goal set: You should Hold this. Decline nothing, Write it, Reply soon. Context."
    sig = extract_signals(text, _session(), "", set(), None)
    assert sig.entity == []
    assert sig.identifier == []


def test_known_wing_still_fires():
    sig = extract_signals("what happened with Candela", _session(), "", {"candela"}, None)
    assert [s.wing for s in sig.entity] == ["candela"]


# --- identifiers -------------------------------------------------------------


def test_identifier_shapes_are_signals():
    text = "reject is EF_FPLMN and NSAPI already used; rejectCause 0x807 on CPE710 via com.apple.commcenter"
    names = {s.name for s in _extract_identifier_signals(text, _session())}
    # capped at 3, but every one must be identifier-shaped
    assert names and names <= {
        "EF_FPLMN",
        "NSAPI",
        "rejectCause",
        "0x807",
        "CPE710",
        "com.apple.commcenter",
    }


def test_harness_allcaps_are_not_identifiers():
    names = {
        s.name for s in _extract_identifier_signals("NOTE the JSON API is OK TODO", _session())
    }
    assert names == set()


def test_identifier_routes_to_union_search():
    sig = extract_signals("NSAPI already used on IuUP", _session(), "2g", {"2g"}, None)
    assert sig.identifier
    call = pick_tool(sig, "balanced", _session())
    assert call is not None
    assert call.tool == "mempalace_search"
    assert call.args["candidate_strategy"] == "union"
    assert call.args["wing"] == "2g"
    assert "NSAPI" in call.args["query"]


# --- exhaust filter / floor / dedupe ------------------------------------------


def _item(**kw):
    base = {
        "drawer_id": "drawer_x_" + kw.get("room", "r"),
        "wing": "x",
        "room": "references",
        "text": "a real finding",
        "similarity": 0.61,
    }
    base.update(kw)
    return base


def test_is_exhaust_flags_manifests_diary_and_autosave():
    assert _is_exhaust(_item(room="sessions"))
    assert _is_exhaust(_item(drawer_id="diary_2g_2026"))
    assert _is_exhaust(_item(text="AUTO-SAVE:abc|54.msgs|2026-09-01|hook.time"))
    assert _is_exhaust(_item(text="Session manifest ───── session_id: x"))
    assert not _is_exhaust(_item())


def test_filter_drops_exhaust_and_below_floor_keeps_real(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = {
        "results": [
            _item(drawer_id="a", room="sessions", similarity=0.9),
            _item(drawer_id="b", similarity=0.44),
            _item(drawer_id="c", similarity=0.58),
            _item(drawer_id="d", similarity=None, bm25_score=0.3),
            _item(drawer_id="e", similarity=None, bm25_score=2.1),
        ]
    }
    out = _filter_search_results(result, "sess", _Cfg())
    assert [r["drawer_id"] for r in out["results"]] == ["c", "e"]
    assert out["best_score"] == 0.9
    assert out["floor"] == 0.50


def test_same_drawer_is_never_injected_twice_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query import runner as r

    first = {"results": [_item(drawer_id="seen1"), _item(drawer_id="seen2")]}
    r._remember_injected("sess-1", first)
    again = _filter_search_results(
        {"results": [_item(drawer_id="seen1"), _item(drawer_id="fresh")]}, "sess-1", _Cfg()
    )
    assert [x["drawer_id"] for x in again["results"]] == ["fresh"]
    # a different session is unaffected
    other = _filter_search_results({"results": [_item(drawer_id="seen1")]}, "sess-2", _Cfg())
    assert [x["drawer_id"] for x in other["results"]] == ["seen1"]


# --- inventory header ---------------------------------------------------------


def test_wing_inventory_line_names_size_rank_and_related():
    lines = _wing_inventory_lines("2g", {"2g": 84091, "candela": 114501, "2g_lab": 12, "ha": 3})
    assert len(lines) == 1
    assert "84,091 drawers" in lines[0]
    assert "rank 2 of 4" in lines[0]
    assert "2g_lab" in lines[0]


def test_wing_inventory_line_for_empty_wing():
    assert "no drawers yet" in _wing_inventory_lines("new", {"other": 5})[0]


# --- receipt even on zero hits -------------------------------------------------


def test_receipt_emitted_when_everything_is_filtered(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query import runner as r

    monkeypatch.setattr(
        r,
        "_call_mcp",
        lambda call, cfg: {"results": [_item(drawer_id="m", room="sessions", similarity=0.7)]},
    )
    res = run_auto_query(
        prompt="why does the bridge radio drop on channel 157",
        session_id="s",
        turn=1,
        project_wing="openwrt",
        known_wings={"openwrt"},
        config=_Cfg(),
        log_dir=str(tmp_path),
        wing_counts={"openwrt": 20445},
    )
    assert res.injection is None
    assert res.receipt is not None
    assert res.receipt["hits"] == 0
    assert res.receipt["raw_hits"] == 1
    assert res.receipt["best"] == 0.7
    assert res.decision.reason.startswith("all results filtered")


def test_depth_injection_carries_inventory_header(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query import runner as r

    monkeypatch.setattr(r, "_call_mcp", lambda call, cfg: {"results": [_item(drawer_id="ok")]})
    res = run_auto_query(
        prompt="why does the bridge radio drop on channel 157",
        session_id="s2",
        turn=1,
        project_wing="openwrt",
        known_wings={"openwrt"},
        config=_Cfg(),
        log_dir=str(tmp_path),
        wing_counts={"openwrt": 20445, "candela": 114501},
    )
    assert res.injection is not None
    assert "palace: wing 'openwrt' holds 20,445 drawers" in res.injection
    assert "depth-refresh" in res.injection
    assert res.tool_call.args["limit"] == DEPTH_FETCH
    assert json.dumps(res.receipt)  # serializable for the hook


# --- fast-first for identifiers -------------------------------------------------


def test_identifier_query_uses_fast_route_when_it_hits(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query import runner as r

    calls = {"fast": 0, "mcp": 0}

    def fake_fast(query, wing, limit, cfg):
        # One fast call per identifier term (BM25-fast ANDs a query's terms).
        calls["fast"] += 1
        assert query in {"RAB", "NSAPI"}
        if query != "NSAPI":
            return {"results": []}
        return {
            "results": [
                {
                    "drawer_id": "d1",
                    "room": "problems",
                    "text": "NSAPI already used",
                    "similarity": None,
                    "rank": 0.5,
                },
                {"drawer_id": "diary_x", "room": "diary", "text": "AUTO-SAVE", "similarity": None},
            ]
        }

    def fake_mcp(call, cfg):
        calls["mcp"] += 1
        return {"results": []}

    monkeypatch.setattr(r, "_call_fast_search", fake_fast)
    monkeypatch.setattr(r, "_call_mcp", fake_mcp)
    res = run_auto_query(
        prompt="the RAB fails with NSAPI already used",
        session_id="fast-1",
        turn=2,
        project_wing="2g",
        known_wings={"2g"},
        config=_Cfg(),
        log_dir=str(tmp_path),
    )
    assert calls["mcp"] == 0 and calls["fast"] >= 1
    assert res.receipt["route"] == "bm25-fast"
    assert res.receipt["hits"] == 1  # diary chunk filtered
    assert "d1" in res.injection and "diary_x" not in res.injection


def test_identifier_query_falls_back_to_hybrid_on_fast_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query import runner as r

    monkeypatch.setattr(r, "_call_fast_search", lambda q, w, lim, c: {"results": []})
    monkeypatch.setattr(r, "_call_mcp", lambda call, cfg: {"results": [_item(drawer_id="h1")]})
    res = run_auto_query(
        prompt="the RAB fails with NSAPI already used",
        session_id="fast-2",
        turn=2,
        project_wing="2g",
        known_wings={"2g"},
        config=_Cfg(),
        log_dir=str(tmp_path),
    )
    assert res.receipt["route"] == "hybrid"
    assert "h1" in res.injection


# --- curated memory files rank first -------------------------------------------


def test_curated_memory_file_drawers_rank_first(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query.runner import _apply_filtered, _is_curated

    transcript = _item(drawer_id="t1", similarity=0.9, source_file="/x/abc.jsonl")
    curated = _item(
        drawer_id="m1",
        similarity=0.6,
        source_file="/home/u/.claude/projects/-home-u-Projects-openwrt/memory/crystal-ssid.md",
    )
    assert _is_curated(curated) and not _is_curated(transcript)
    out = _apply_filtered({"results": []}, {"results": [transcript, curated]}, 5)
    assert [r["drawer_id"] for r in out["results"]] == ["m1", "t1"]


def test_curated_hit_clears_a_lower_floor_than_transcript(tmp_path, monkeypatch):
    """openwrt-a0 fleet finding: a correct 0.486 memory-file hit sat under the
    flat 0.50 floor. A curated .md hit clears floor - _CURATED_FLOOR_MARGIN;
    a transcript hit at the same score does not."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from mempalace.auto_query.runner import _CURATED_FLOOR_MARGIN, _filter_search_results

    curated = _item(
        drawer_id="m",
        similarity=0.486,
        source_file="/h/.claude/projects/-h-Projects-openwrt/memory/cpe710-ptp.md",
    )
    transcript = _item(drawer_id="t", similarity=0.486, source_file="/x/abc.jsonl")
    out = _filter_search_results({"results": [curated, transcript]}, "s", _Cfg())
    kept = [r["drawer_id"] for r in out["results"]]
    assert "m" in kept, "curated 0.486 hit must clear the lowered floor (0.50 - margin)"
    assert "t" not in kept, "transcript 0.486 hit stays below the flat floor"
    assert _CURATED_FLOOR_MARGIN >= 0.05


def test_prompt_echo_drawers_are_exhaust():
    """gnome-speaks fleet finding: harness prompt-echo ('Investigate per the
    method…', 'Get started. Read…') is not knowledge — filter it like manifests."""
    from mempalace.auto_query.runner import _is_exhaust

    assert _is_exhaust(_item(text="Investigate per the method in your instructions [Read x]"))
    assert _is_exhaust(_item(text="Get started. Read the RFC 002 spec first."))
    assert _is_exhaust(_item(text="You are working in the memorypalace project at /home/jp/..."))
    assert not _is_exhaust(_item(text="A11 accepts u:r:su:s0; A7 refuses the transition"))
