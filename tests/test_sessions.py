import pytest

from yappy_cli.web import sessions as S


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("YAPPY_SESSIONS_DB", str(tmp_path / "sessions.db"))
    S._connect().close()
    yield


def test_create_roundtrip_preserves_order(store):
    ses = S.create_session(
        env_a="dev", env_b="qa",
        keys=["/a", "/b", "/c"],
    )
    assert ses["env_a"] == "dev" and ses["env_b"] == "qa"
    assert ses["service"] == "ssm"
    assert ses["title"] == "qa → dev · 3 parámetros"
    assert [i["name"] for i in ses["items"]] == ["/a", "/b", "/c"]
    assert [i["status"] for i in ses["items"]] == ["pendiente"] * 3
    assert ses["status_counts"]["pendiente"] == 3

    got = S.get_session(ses["id"])
    assert got["id"] == ses["id"]
    assert [i["name"] for i in got["items"]] == ["/a", "/b", "/c"]


def test_create_dedupes_and_skips_blank(store):
    ses = S.create_session(
        env_a="prod", env_b="qa",
        keys=["/a", "/a", "  ", "/b"],
        service="secretsmanager",
    )
    assert [i["name"] for i in ses["items"]] == ["/a", "/b"]
    assert ses["service"] == "secretsmanager"
    assert ses["title"] == "qa → prod · 2 parámetros"


def test_session_title_updates_when_items_change(store):
    ses = S.create_session(env_a="dev", env_b="qa", keys=["/a", "/b"])
    assert ses["title"] == "qa → dev · 2 parámetros"

    S.add_item(ses["id"], "/c")
    got = S.get_session(ses["id"])

    assert got["title"] == "qa → dev · 3 parámetros"
    assert S.list_sessions()[0]["title"] == "qa → dev · 3 parámetros"


def test_create_validation(store):
    with pytest.raises(ValueError):
        S.create_session(env_a="dev", env_b="dev", keys=["/a"])
    with pytest.raises(ValueError):
        S.create_session(env_a="dev", env_b="qa", keys=["/a"], service="bad")
    with pytest.raises(ValueError):
        S.create_session(env_a="dev", env_b="qa", keys=[])
    with pytest.raises(ValueError):
        S.create_session(env_a="dev", env_b="qa", keys=[f"/k{i}" for i in range(201)])


def test_update_item_status_flow_with_timestamps(store):
    ses = S.create_session(env_a="dev", env_b="qa", keys=["/a", "/b"])

    updated = S.update_item(
        ses["id"], "/a", status="revisado", script="aws ssm put-parameter ...",
        preview='{"x": 2}',
        service="ssm", is_secret=True,
    )
    assert updated["status"] == "revisado"
    assert updated["script"] == "aws ssm put-parameter ..."
    assert updated["is_secret"] is True
    assert updated["visited_at"] is not None
    assert updated["applied_at"] is None

    applied = S.update_item(ses["id"], "/a", status="aplicado", notes="listo")
    assert applied["status"] == "aplicado"
    assert applied["applied_at"] is not None
    assert applied["notes"] == "listo"

    got = S.get_session(ses["id"])
    assert got["status_counts"] == {
        "pendiente": 1, "revisado": 0, "aplicado": 1, "saltado": 0,
    }
    assert got["items"][0]["applied_at"] is not None


def test_revisado_snapshot_never_downgrades_aplicado(store):
    ses = S.create_session(env_a="dev", env_b="qa", keys=["/x"])
    S.update_item(ses["id"], "/x", status="aplicado")
    S.update_item(ses["id"], "/x", status="revisado", diff_json="{}")

    item = S.get_session(ses["id"])["items"][0]
    assert item["status"] == "aplicado"
    assert item["applied_at"] is not None
    assert item["diff_json"] == "{}"


def test_update_item_validation_and_missing(store):
    ses = S.create_session(env_a="dev", env_b="qa", keys=["/a"])
    with pytest.raises(ValueError):
        S.update_item(ses["id"], "/a", status="hecho")
    with pytest.raises(ValueError):
        S.update_item(ses["id"], "/no-existe", status="revisado")
    with pytest.raises(ValueError):
        S.update_item("ses-inexistente", "/a", status="revisado")


def test_delete_session_cascades(store):
    ses = S.create_session(env_a="dev", env_b="qa", keys=["/a", "/b"])
    assert len(S.list_sessions()) == 1
    S.delete_session(ses["id"])
    assert S.list_sessions() == []
    with pytest.raises(ValueError):
        S.get_session(ses["id"])


def test_list_sessions_newest_first_with_counts(store):
    s1 = S.create_session(env_a="dev", env_b="qa", keys=["/a"])
    s2 = S.create_session(env_a="prod", env_b="qa", keys=["/b", "/c"])
    S.update_item(s2["id"], "/b", status="aplicado")

    rows = S.list_sessions()
    assert [r["id"] for r in rows] == [s2["id"], s1["id"]]
    assert rows[0]["item_count"] == 2
    assert rows[0]["status_counts"]["aplicado"] == 1


def test_reuse_returns_same_session_for_identical_list(store):
    keys = ["/a", "/b", "/c"]
    s1 = S.create_session(env_a="dev", env_b="qa", keys=keys, reuse=True)
    s2 = S.create_session(env_a="dev", env_b="qa", keys=keys, reuse=True)
    assert s2["id"] == s1["id"]
    assert len(S.list_sessions()) == 1

    s3 = S.create_session(env_a="dev", env_b="qa", keys=keys, reuse=True)
    assert s3["id"] == s1["id"]


def test_reuse_does_not_match_different_list_or_env(store):
    s1 = S.create_session(env_a="dev", env_b="qa", keys=["/a", "/b"], reuse=True)

    other_list = S.create_session(env_a="dev", env_b="qa", keys=["/a"], reuse=True)
    assert other_list["id"] != s1["id"]
    assert [i["name"] for i in other_list["items"]] == ["/a"]

    other_env = S.create_session(env_a="prod", env_b="qa", keys=["/a", "/b"], reuse=True)
    assert other_env["id"] != s1["id"]

    other_service = S.create_session(
        env_a="dev", env_b="qa", keys=["/a", "/b"], service="secretsmanager", reuse=True
    )
    assert other_service["id"] != s1["id"]


def test_reuse_preserves_progress_of_existing_session(store):
    s1 = S.create_session(env_a="dev", env_b="qa", keys=["/a", "/b"], reuse=True)
    S.update_item(s1["id"], "/a", status="aplicado", notes="listo")

    again = S.create_session(env_a="dev", env_b="qa", keys=["/a", "/b"], reuse=True)
    assert again["id"] == s1["id"]
    assert again["status_counts"]["aplicado"] == 1
    assert again["items"][0]["notes"] == "listo"