import pytest

from yappy_library.application.database.sync import params as p


class ResourceNotFound(Exception):
    pass


_BINARY = object()
_BOOM = object()


class _FakeSsm:
    def __init__(self, conn):
        self.conn = conn

    def get_parameters(self, Names=None, WithDecryption=False):
        self.conn.ssm_calls.append(list(Names))
        if self.conn.ssm_raise is not None:
            raise self.conn.ssm_raise
        found = []
        for name in Names:
            if name in self.conn.ssm_store:
                stored = self.conn.ssm_store[name]
                found.append(
                    {"Name": name, "Value": stored[0], "Type": stored[1]}
                )
        return {"Parameters": found}


class _FakeSecrets:
    exceptions = type("Exc", (), {"ResourceNotFoundException": ResourceNotFound})

    def __init__(self, conn):
        self.conn = conn

    def get_secret_value(self, SecretId=None):
        self.conn.secret_calls.append(SecretId)
        value = self.conn.secret_store.get(SecretId, _BOOM)
        if value is _BOOM:
            raise RuntimeError("cualquier-error")
        if value is _BINARY:
            return {"SecretBinary": b"\x00"}
        if value is None:
            return {}
        return {"SecretString": value}


class _FakeConn:
    def __init__(self, ssm_store=None, secret_store=None):
        self.ssm_store = ssm_store or {}
        self.secret_store = secret_store or {}
        self.ssm_calls = []
        self.secret_calls = []
        self.ssm_raise = None

    def client(self, name):
        if name == "ssm":
            return _FakeSsm(self)
        return _FakeSecrets(self)


class _FakeSession:
    def __init__(self, conn):
        self.conn = conn

    def client(self, name):
        return self.conn.client(name)


def _monkey_session(monkeypatch, conn):
    monkeypatch.setattr(
        p, "_boto_session", lambda profile, region: _FakeSession(conn)
    )


class _CFG:
    profile = "prof-dev"
    region = "us-east-1"
    endpoint_url = None


def _cfg():
    return _CFG()


def test_read_many_mixed_ssm_and_secrets(monkeypatch):
    conn = _FakeConn(
        ssm_store={"/a": ("v1", "String"), "/b": ("20", "SecureString")},
        secret_store={"/s": "top"},
    )
    _monkey_session(monkeypatch, conn)

    results = p.read_many(
        _cfg(),
        [
            {"key": "/a"},
            {"key": "/b", "is_secret": False},
            {"key": "/s", "is_secret": True},
        ],
    )

    assert [r["key"] for r in results] == ["/a", "/b", "/s"]
    assert all(r["ok"] for r in results)
    assert [r["service"] for r in results] == ["ssm", "ssm", "secretsmanager"]
    assert results[0]["value"] == "v1" and results[0]["value_type"] == "String"
    assert results[1]["value"] == "20" and results[1]["value_type"] == "SecureString"
    assert results[2]["value"] == "top"
    assert conn.ssm_calls == [["/a", "/b"]]


def test_read_many_batches_ssm_in_chunks_of_10(monkeypatch):
    names = [f"/k{i}" for i in range(25)]
    conn = _FakeConn(ssm_store={n: (str(i), "String") for i, n in enumerate(names)})
    _monkey_session(monkeypatch, conn)

    results = p.read_many(_cfg(), [{"key": n} for n in names])

    assert all(r["ok"] for r in results)
    assert conn.ssm_calls == [names[:10], names[10:20], names[20:25]]


def test_read_many_secrets_preserve_order(monkeypatch):
    conn = _FakeConn(secret_store={f"/s{i}": f"v{i}" for i in range(5)})
    _monkey_session(monkeypatch, conn)

    results = p.read_many(
        _cfg(), [{"key": f"/s{i}", "is_secret": True} for i in range(5)]
    )

    assert [r["value"] for r in results] == ["v0", "v1", "v2", "v3", "v4"]
    assert all(r["ok"] for r in results)
    assert [r["key"] for r in results] == ["/s0", "/s1", "/s2", "/s3", "/s4"]


def test_read_many_missing_and_errors_do_not_stop_the_rest(monkeypatch):
    conn = _FakeConn(
        ssm_store={"/a": ("x", "String")},
        secret_store={"/s": "v", "/bin": _BINARY, "/none": None},
    )
    _monkey_session(monkeypatch, conn)

    entries = [
        {"key": "/a"},
        {"key": "/no"},
        {"key": "/s", "is_secret": True},
        {"key": "/bin", "is_secret": True},
        {"key": "/none", "is_secret": True},
        {"key": "/boom", "is_secret": True},
    ]
    results = p.read_many(_cfg(), entries)

    assert len(results) == 6
    assert results[0]["ok"] and results[0]["value"] == "x"
    assert not results[1]["ok"] and results[1]["error"] == "No existe"
    assert results[2]["ok"] and results[2]["value"] == "v"
    assert not results[3]["ok"] and "binario" in results[3]["error"]
    assert not results[4]["ok"] and "no tiene valor" in results[4]["error"]
    assert not results[5]["ok"] and results[5]["error"] == "cualquier-error"


def test_read_many_accepts_name_as_alias(monkeypatch):
    conn = _FakeConn(ssm_store={"/x": ("1", "String")})
    _monkey_session(monkeypatch, conn)

    results = p.read_many(_cfg(), [{"name": "/x"}])

    assert results[0]["key"] == "/x" and results[0]["ok"]
    assert results[0]["is_secret"] is False


def test_read_many_key_wins_over_name(monkeypatch):
    conn = _FakeConn(ssm_store={"/k": ("1", "String")})
    _monkey_session(monkeypatch, conn)

    results = p.read_many(_cfg(), [{"key": "/k", "name": "/other"}])

    assert results[0]["key"] == "/k" and results[0]["ok"]


def test_read_many_plain_strings_auto_detect_secrets(monkeypatch):
    conn = _FakeConn(
        ssm_store={
            "/prod/ecommerce/db/master_url": ("jdbc:postgresql://db", "String"),
            "/prod/auth/recaptcha/site_key": ("6Lc", "String"),
        },
        secret_store={
            "/prod/payment/stripe/secret_key": "sk_live_123",
            "/prod/notification/sendgrid/api_key": "SG.abc",
        },
    )
    _monkey_session(monkeypatch, conn)

    results = p.read_many(
        _cfg(),
        [
            "/prod/ecommerce/db/master_url",
            "/prod/payment/stripe/secret_key",
            "/prod/auth/recaptcha/site_key",
            "/prod/notification/sendgrid/api_key",
            "/prod/network/ecs/cluster_name",
        ],
    )

    assert [r["key"] for r in results] == [
        "/prod/ecommerce/db/master_url",
        "/prod/payment/stripe/secret_key",
        "/prod/auth/recaptcha/site_key",
        "/prod/notification/sendgrid/api_key",
        "/prod/network/ecs/cluster_name",
    ]
    assert [r["is_secret"] for r in results] == [False, True, False, True, False]
    assert [r["service"] for r in results] == [
        "ssm", "secretsmanager", "ssm", "secretsmanager", "ssm",
    ]
    assert results[1]["value"] == "sk_live_123"
    assert results[3]["value"] == "SG.abc"
    assert not results[4]["ok"] and results[4]["error"] == "No existe"
    assert results[1]["value_type"] is None


def test_read_many_plain_strings_trim_whitespace_and_missing(monkeypatch):
    conn = _FakeConn(ssm_store={"/a": ("v", "String")})
    _monkey_session(monkeypatch, conn)

    results = p.read_many(_cfg(), ["  /a  ", "/missing"])

    assert [r["key"] for r in results] == ["/a", "/missing"]
    assert results[0]["ok"] and results[0]["value"] == "v"
    assert not results[1]["ok"] and results[1]["error"] == "No existe"


def test_read_many_validation_errors():
    with pytest.raises(ValueError):
        p.read_many(_cfg(), [])
    with pytest.raises(ValueError):
        p.read_many(_cfg(), "not-a-list")
    with pytest.raises(ValueError):
        p.read_many(_cfg(), [{}])
    with pytest.raises(ValueError):
        p.read_many(_cfg(), [{"is_secret": True}])
    with pytest.raises(ValueError):
        p.read_many(_cfg(), [{"key": f"/k{i}"} for i in range(101)])


def test_read_many_ssm_batch_error_marks_all_ssm_entries(monkeypatch):
    conn = _FakeConn(ssm_store={}, secret_store={"/s": "v"})
    conn.ssm_raise = RuntimeError("credenciales")
    _monkey_session(monkeypatch, conn)

    results = p.read_many(
        _cfg(),
        [
            {"key": "/a"},
            {"key": "/b"},
            {"key": "/s", "is_secret": True},
        ],
    )

    assert not results[0]["ok"] and results[0]["error"] == "credenciales"
    assert not results[1]["ok"] and results[1]["error"] == "credenciales"
    assert results[2]["ok"] and results[2]["value"] == "v"
