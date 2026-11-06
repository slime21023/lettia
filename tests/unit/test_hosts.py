import pytest

from lettia.core.hosts import HostPolicy, hostname_of, parse_host, server_authority


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Example.COM", "example.com"),
        ("api.example.com:8443", "api.example.com:8443"),
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.1:8000", "127.0.0.1:8000"),
        ("[::1]:8000", "[::1]:8000"),
        ("[FE80::1]", "[fe80::1]"),
        ("my_service", "my_service"),
        ("localhost", "localhost"),
        ("a.b-c.example", "a.b-c.example"),
    ],
)
def test_parse_host_accepts_names_and_addresses_in_lower_case(
    value: str, expected: str
) -> None:
    assert parse_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        "a b",
        "a/b",
        "a\\b",
        "user@example.com",
        "example.com:",
        ":80",
        "example.com:70000",
        "example.com:http",
        "-a.com",
        "a-.com",
        "a..com",
        "[::1",
        "::1",
        "exa\r\nmple.com",
    ],
)
def test_parse_host_rejects_what_is_not_a_host(value: str | None) -> None:
    assert parse_host(value) is None


@pytest.mark.parametrize(
    ("host", "name"),
    [
        ("example.com", "example.com"),
        ("example.com:8080", "example.com"),
        ("[::1]", "[::1]"),
        ("[::1]:8080", "[::1]"),
    ],
)
def test_hostname_of_drops_the_port_and_keeps_ipv6_brackets(
    host: str, name: str
) -> None:
    assert hostname_of(host) == name


@pytest.mark.parametrize("server", [[], (), ("a",), ("a", 1, 2), 5, ("", 80)])
def test_server_authority_is_localhost_for_a_scope_server_it_cannot_use(
    server: object,
) -> None:
    assert server_authority(server, "http") == "localhost"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("server", "scheme", "expected"),
    [
        (("Example.com", 80), "http", "example.com"),
        (("example.com", 8000), "http", "example.com:8000"),
        (("example.com", 443), "https", "example.com"),
        (("example.com", 80), "https", "example.com:80"),
        (("example.com", 80), "ws", "example.com"),
        (("::1", 8000), "http", "[::1]:8000"),
        (None, "http", "localhost"),
        (("/run/app.sock", None), "http", "localhost"),
    ],
)
def test_server_authority_writes_the_listening_address_as_a_host(
    server: tuple[str, int | None] | None, scheme: str, expected: str
) -> None:
    assert server_authority(server, scheme) == expected


def test_host_policy_matches_exact_names_without_regard_to_case_or_port() -> None:
    policy = HostPolicy(["API.example.com", "[::1]"])

    assert policy.allows("api.example.com")
    assert policy.allows("api.example.com:8443")
    assert policy.allows("[::1]:8000")
    assert not policy.allows("example.com")
    assert not policy.allows("evil.api.example.com")


def test_host_policy_wildcard_matches_subdomains_but_not_the_bare_domain() -> None:
    policy = HostPolicy(["*.example.com"])

    assert policy.allows("a.example.com")
    assert policy.allows("a.b.example.com:81")
    assert not policy.allows("example.com")
    assert not policy.allows("badexample.com")
    assert not policy.allows("example.com.evil.net")


def test_host_policy_star_allows_every_host() -> None:
    assert HostPolicy(["*"]).allows("anything.test:1")


@pytest.mark.parametrize(
    "patterns",
    [[], [""], ["example.com:80"], ["*."], ["*.a b"], ["http://example.com"], ["a b"]],
)
def test_host_policy_rejects_an_invalid_or_empty_list(patterns: list[str]) -> None:
    with pytest.raises(ValueError):
        HostPolicy(patterns)


def test_host_policy_rejects_a_bare_string() -> None:
    with pytest.raises(TypeError, match="sequence"):
        HostPolicy("example.com")
