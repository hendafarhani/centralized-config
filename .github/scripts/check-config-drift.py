#!/usr/bin/env python3
"""Fail when a service's test-side copy of a config key disagrees with the real value.

Services read their runtime configuration from this repository over Spring Cloud
Config. Tests cannot reach a config server, so each service carries its own
src/test/resources/application.properties holding a local copy of the keys it
needs. Nothing compares the copy to the original, so a value can change here and
the service's suite stays green while production breaks.

This script compares them. For each service:

    effective = resolve( centralized-configuration/application.properties
                         <- centralized-configuration/<config-name>.properties )

The shared application.properties is served to every client, merged UNDERNEATH
the service's own file, so the service file wins on any key defined in both.
Most values are ${microgo.topics.*} references after the topic consolidation,
so placeholders are resolved before anything is compared - otherwise the report
would print '${microgo.topics.ride-request-events}' as a production value, and
would flag matching keys as drifted.

Both sources are needed. Topic names live only in the shared file; consumer
group ids and listener ids live only in the service file, because each service
must have its own (Kafka gives each message to exactly one member of a consumer
group, so two services sharing an id would split the stream and each would
silently receive half its records). Reading either file alone drops real
coverage.

THE RULE: only keys present in BOTH the effective config and the test file, and
disagreeing, are considered at all. Keys on one side only are never reported.

WHAT FAILS THE BUILD, AND WHY IT IS NOT EVERYTHING
A test may override *where things are*; it may not restate *what they are
called*. So a disagreeing key is reported one of two ways:

  contract key  -> ::error::  fails the build.  Topic names, consumer group ids,
                   listener ids, app tuning: what the service and its tests must
                   agree on for the tests to mean anything.
  environment   -> ::notice:: does not fail.    Addresses, credentials, driver
                   classes, on/off switches: h2 instead of MySQL,
                   localhost:9092 instead of kafka:9092. These are *supposed*
                   to differ - that is what makes the suite runnable without
                   the fleet.

This split is not an allowlist and it hides nothing: every disagreement is
printed either way, with its classification, and the totals are reported. It
exists because a check that fails on `spring.datasource.url` being h2 in tests
would be red on every service on day one and would be switched off within a
week. Run with --strict to fail on environment mismatches too.

Usage:
    check-config-drift.py <workspace> [--strict]

<workspace> holds centralized-config/ and the service repositories side by side,
which is both the monorepo layout and what the CI job checks out.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SHARED = "application.properties"
TEST_PROPS = Path("src/test/resources/application.properties")
BOOTSTRAPS = [Path("src/main/resources/bootstrap.properties"),
              Path("src/main/resources/application.properties")]
NAME_KEYS = ("spring.cloud.config.name", "spring.application.name")

PLACEHOLDER = re.compile(r"\$\{([^{}:]+)(?::([^{}]*))?\}")

# Namespaces owned by the framework or by the environment, not by the service's
# messaging contract. Test-side overrides here are the documented, intended
# pattern - see the module docstring.
ENV_PREFIXES = ("spring.", "eureka.", "management.", "logging.")
# Last key segment. An address, a credential, a driver, or an on/off switch is
# about where things are or whether they are wired up, never about naming.
ENV_SUFFIXES = ("bootstrap-servers", "enabled", "host", "port", "url",
                "username", "password", "driver-class-name")


def parse_properties(path: Path) -> dict[str, str]:
    """Parse the java.util.Properties subset these files actually use.

    Deliberately not grep: values contain dots, colons and ${...} placeholders
    that a naive cut would mangle. Later definitions win, as Properties does.
    """
    props: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line[0] in "#!" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key.strip()] = value.strip()
    return props


def resolve(env: dict[str, str], key: str, seen: frozenset[str] = frozenset()) -> str:
    """Resolve ${ref} and ${ref:default} against env, the way a Spring client does.

    Transitive by design: an alias points at a canonical key which holds the
    literal. A reference to a key we cannot see is left as-is rather than
    blanked - ${PORT:0} and ${random.value} resolve at runtime from sources this
    script has no access to, and leaving them intact keeps them comparable.
    `seen` stops a self-referential key from recursing forever.
    """
    if key in seen:
        return env.get(key, "")
    seen = seen | {key}
    value = env.get(key, "")

    def substitute(match: re.Match[str]) -> str:
        ref, default = match.group(1), match.group(2)
        if ref in env:
            return resolve(env, ref, seen)
        return default if default is not None else match.group(0)

    previous = None
    while previous != value:
        previous = value
        value = PLACEHOLDER.sub(substitute, value)
    return value


def is_contract_key(key: str) -> bool:
    """True when the two sides must agree for the tests to mean anything."""
    if key.startswith(ENV_PREFIXES):
        return False
    return key.rsplit(".", 1)[-1] not in ENV_SUFFIXES


def config_name(service_dir: Path) -> str | None:
    """The config file is named after spring.cloud.config.name / .application.name,
    which is not always the repository name."""
    for candidate in BOOTSTRAPS:
        path = service_dir / candidate
        if not path.exists():
            continue
        props = parse_properties(path)
        for key in NAME_KEYS:
            if props.get(key):
                return props[key]
    return None


def services_from_gitmodules(workspace: Path) -> tuple[list[str], bool]:
    """Every service in the fleet, and whether that list is authoritative.

    .gitmodules is the fleet roster, so a service listed there but missing from
    the workspace means the job is not checking it - worth a warning, not a
    silent skip. Without .gitmodules all we can say is what is checked out.
    """
    gitmodules = workspace / ".gitmodules"
    if gitmodules.exists():
        paths = re.findall(r"^\s*path\s*=\s*(.+)$", gitmodules.read_text(), re.MULTILINE)
        return sorted(p.strip() for p in paths), True
    return sorted(p.name for p in workspace.iterdir() if p.is_dir()), False


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    strict = "--strict" in argv[1:]
    if len(args) != 1:
        print(f"usage: {argv[0]} <workspace> [--strict]", file=sys.stderr)
        return 2
    workspace = Path(args[0]).resolve()

    config_dir = workspace / "centralized-config" / "centralized-configuration"
    if not config_dir.is_dir():
        print(f"::error::centralized-configuration not found at {config_dir}")
        return 2

    shared_path = config_dir / SHARED
    shared = parse_properties(shared_path) if shared_path.exists() else {}
    if shared:
        print(f"Loaded shared {SHARED}: {len(shared)} keys")
    else:
        # Not fatal: the file is expected, but its absence is a config-repo
        # state, not a drift. Say so loudly instead of silently comparing less.
        print(f"::warning::no shared {SHARED} in centralized-configuration - "
              f"comparing against service files alone")

    contract_drifts: list[tuple[str, str, str, str]] = []
    env_diffs: list[tuple[str, str, str, str]] = []
    compared = 0

    listed, from_gitmodules = services_from_gitmodules(workspace)
    not_checked_out: list[str] = []

    for service in listed:
        service_dir = workspace / service
        if service == "centralized-config":
            continue
        if not service_dir.is_dir():
            # In the fleet but not in the workspace, so not checked. Collected
            # and reported once at the end: silence here would read as coverage,
            # but one annotation per infra repo every run is noise that trains
            # people to ignore the annotations that matter.
            if from_gitmodules:
                not_checked_out.append(service)
            continue

        test_file = service_dir / TEST_PROPS
        if not test_file.exists():
            print(f"{service}: no {TEST_PROPS} - nothing to compare, skipping")
            continue

        name = config_name(service_dir)
        if name is None:
            print(f"::warning file={service}::could not read spring.application.name / "
                  f"spring.cloud.config.name - skipping")
            continue

        service_props = config_dir / f"{name}.properties"
        if not service_props.exists():
            print(f"::warning file={service}::no centralized-configuration/"
                  f"{name}.properties - skipping")
            continue

        # Shared first, service file on top: the service file wins, exactly as
        # Spring Cloud Config merges them.
        merged = {**shared, **parse_properties(service_props)}
        effective = {key: resolve(merged, key) for key in merged}
        test_props = parse_properties(test_file)

        both = sorted(set(effective) & set(test_props))
        compared += len(both)
        disagreeing = [(key, effective[key], test_props[key])
                       for key in both if effective[key] != test_props[key]]
        contract = [d for d in disagreeing if is_contract_key(d[0])]
        environment = [d for d in disagreeing if not is_contract_key(d[0])]

        print(f"{service}: {name}.properties + {SHARED} -> {len(both)} key(s) in both, "
              f"{len(contract)} contract drift(s), "
              f"{len(environment)} expected environment override(s)")

        for key, production, test in contract:
            contract_drifts.append((service, key, production, test))
            print(f"::error file={service}/{TEST_PROPS}::"
                  f"{service} - {key} - production '{production}' - test '{test}'")
        for key, production, test in environment:
            env_diffs.append((service, key, production, test))
            level = "error" if strict else "notice"
            print(f"::{level} file={service}/{TEST_PROPS}::[environment] "
                  f"{service} - {key} - production '{production}' - test '{test}'")

    if not_checked_out:
        print(f"\n::warning::not checked out, so NOT checked for drift: "
              f"{', '.join(not_checked_out)}. Expected for repositories with no Spring "
              f"test config; add a checkout step in the config-drift job for any that "
              f"gains a {TEST_PROPS}.")

    print(f"\n{compared} key(s) compared; {len(contract_drifts)} contract drift(s), "
          f"{len(env_diffs)} environment override(s)")

    if env_diffs:
        print(f"\nEnvironment overrides ({'FAILING - --strict' if strict else 'not failing'}) "
              f"- tests point at local infrastructure, which is intended:")
        for service, key, production, test in env_diffs:
            print(f"  {service} - {key} - production '{production}' - test '{test}'")

    if contract_drifts:
        print("\nCONTRACT DRIFT - the tests exercise one value while production uses "
              "another. Fix whichever side is wrong; do not silence the check.")
        for service, key, production, test in contract_drifts:
            print(f"  {service} - {key} - production '{production}' - test '{test}'")
        return 1

    if strict and env_diffs:
        return 1

    print("\nNo contract drift: every name defined on both sides agrees.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
