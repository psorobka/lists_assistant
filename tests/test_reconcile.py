"""Identity-preserving merge and edit/delete conflict regression cases."""

import pytest

from custom_components.lists_assistant.reconcile import plan_changes

BASE = {"name": "Milk", "checked": False}
RECORDS = {"h1": {"remote_id": "1", "base": BASE}}


def test_initial_merge_keeps_duplicate_records():
    plan = plan_changes({}, {"h1": BASE, "h2": BASE}, {"1": BASE, "2": BASE})
    assert len(plan["pending"]) == 4
    assert not plan["records"]
    assert not plan["conflicts"]


@pytest.mark.parametrize(
    "local,remote,actions",
    [
        (BASE, BASE, []),
        (None, None, []),
        (None, BASE, [("remote", "delete")]),
        (BASE, None, [("local", "delete")]),
        ({"name": "Bread", "checked": False}, BASE, [("remote", "update")]),
        (BASE, {"name": "Bread", "checked": False}, [("local", "update")]),
    ],
)
def test_changes_against_common_snapshot(local, remote, actions):
    plan = plan_changes(
        RECORDS, {"h1": local} if local else {}, {"1": remote} if remote else {}
    )
    assert [(o["direction"], o["action"]) for o in plan["pending"].values()] == actions
    assert not plan["conflicts"]


def test_independent_edits_merge_field_by_field():
    local = {"name": "Bread", "checked": False}
    remote = {"name": "Milk", "checked": True}
    plan = plan_changes(RECORDS, {"h1": local}, {"1": remote})
    assert len(plan["pending"]) == 2
    assert all(
        o["payload"] == {"name": "Bread", "checked": True}
        for o in plan["pending"].values()
    )


@pytest.mark.parametrize(
    "local,remote",
    [
        (None, {"name": "Bread", "checked": False}),
        ({"name": "Bread", "checked": False}, None),
        ({"name": "Bread", "checked": False}, {"name": "Eggs", "checked": False}),
    ],
)
def test_conflicting_changes_never_generate_a_write(local, remote):
    plan = plan_changes(
        RECORDS, {"h1": local} if local else {}, {"1": remote} if remote else {}
    )
    assert not plan["pending"]
    assert plan["conflicts"]["h1"]["local"] == local
    assert plan["conflicts"]["h1"]["remote"] == remote


def test_identical_simultaneous_changes_advance_common_snapshot():
    changed = {"name": "Bread", "checked": True}
    plan = plan_changes(RECORDS, {"h1": changed}, {"1": changed})
    assert plan["records"]["h1"]["base"] == changed
    assert RECORDS["h1"]["base"] == BASE
