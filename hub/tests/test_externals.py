"""externals.env (read by shell scripts) must agree with externals.py (read by Python)."""
import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import externals  # noqa: E402

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "externals.env")


def read_env():
    out = {}
    for line in open(ENV):
        if line.strip() and not line.startswith("#"):
            k, _, v = line.rstrip("\n").partition("=")
            out[k] = shlex.split(v)[0] if v else ""
    return out


def test_env_matches_python():
    e = read_env()
    assert e["PLUGIN_NET_LIB"] == externals.PLUGIN_NET_LIB
    assert e["PLUGIN_FIND_PATTERNS"].split() == list(externals.PLUGIN_FIND_PATTERNS)
    assert e["BRIDGE_LIB_ENV"] == externals.BRIDGE_LIB_ENV
    assert e["PLUGIN_AGENT_DIR"] == externals.PLUGIN_AGENT_DIR
    assert e["CERT_URL"] == externals.CERT_URL
    assert e["FLATHUB_APP_ID"] == externals.FLATHUB_APP_ID
    assert e["STUDIO_BINARY_NAMES"].split() == list(externals.STUDIO_BINARY_NAMES)
    assert e["PROFILE_ROOT_FILE"] == externals.PROFILE_ROOT_FILE and e["PROFILE_DIR"] == externals.PROFILE_DIR
    assert e["PROFILE_SEARCH"] == externals.PROFILE_SEARCH
    assert e["DEFAULT_PRINTER"] == externals.DEFAULT_PRINTER == externals.PRINTERS["H2S"]["preset"]


def test_plugin_net_lib_is_one_of_the_libs():
    assert externals.PLUGIN_NET_LIB in externals.PLUGIN_LIBS
