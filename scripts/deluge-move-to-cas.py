#!/usr/bin/env python3

# move all finished torrents to ~/cas/btih/{btih}

import json
import os
import time
from pathlib import Path

from deluge_client import DelugeRPCClient

# example config:
"""
{
  "dirs": [
    "/run/media/user/WSC14YZM_8TB/cas"
  ]
}
"""
cas_config_path = os.path.expanduser("~/.config/cas.json")

# example config:
r'''
{
  "host": "127.0.0.1",
  "port": 58846,
  "username": "localclient",
  "password": "secret"
}
'''
CONFIG_JSON = os.path.expanduser("~/.config/delugeclient.json")

DELUGE_DIR = os.path.expanduser("~/.config/deluge")
AUTH_FILE = os.path.join(DELUGE_DIR, "auth")
CORE_CONF = os.path.join(DELUGE_DIR, "core.conf")

with open(cas_config_path) as f:
    cas_config = json.load(f)
cas_path_list = cas_config["dirs"]

# TODO better
# simple: use the first CAS dir
CAS_ROOT = cas_path_list[0]

def load_json_config():
    with open(CONFIG_JSON, "r") as f:
        cfg = json.load(f)
    # NOTE config dict keys must match kwargs of DelugeRPCClient.__init__
    # class DelugeRPCClient(object):
    #     def __init__(self, host, port, username, password, decode_utf8=False, automatic_reconnect=True, timeout=20):
    required = ["host", "port", "username", "password"]
    for key in required:
        if key not in cfg:
            print(f"Missing key '{key}' in {CONFIG_JSON}")
            sys.exit(1)
    return cfg



def extract_second_json(content: str) -> dict:
    """Return the second JSON block in core.conf (everything from 2nd '{' to EOF)."""
    first_end = content.find('}')
    if first_end == -1:
        raise ValueError("No closing brace for first JSON block")
    second_start = content.find('{', first_end + 1)
    if second_start == -1:
        raise ValueError("No second JSON block found")
    second_json_text = content[second_start:].strip()
    try:
        return json.loads(second_json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse second JSON: {e}")


def load_deluge_native_config():
    user = os.environ.get("USER")
    if not user:
        print("Environment variable USER not set.")
        sys.exit(1)

    if not os.path.exists(AUTH_FILE):
        print(f"Missing Deluge auth file: {AUTH_FILE}")
        sys.exit(1)
    if not os.path.exists(CORE_CONF):
        print(f"Missing Deluge core.conf file: {CORE_CONF}")
        sys.exit(1)

    # read password from auth
    password = None
    with open(AUTH_FILE) as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            name, *rest = line.strip().split(":")
            if name == user and len(rest) >= 1:
                password = rest[0]
                break
    if not password:
        print(f"User '{user}' not found in {AUTH_FILE}")
        sys.exit(1)

    with open(CORE_CONF) as f:
        content = f.read()
    core_data = extract_second_json(content)

    port = core_data.get("daemon_port")
    if not port:
        print(f"No daemon_port found in {CORE_CONF}")
        sys.exit(1)

    # NOTE config dict keys must match kwargs of DelugeRPCClient.__init__
    # class DelugeRPCClient(object):
    #     def __init__(self, host, port, username, password, decode_utf8=False, automatic_reconnect=True, timeout=20):
    return {
        "host": "10.0.0.1",
        "port": int(port),
        "username": user,
        "password": password
    }


def load_config():
    if os.path.exists(CONFIG_JSON):
        return load_json_config()
    return load_deluge_native_config()


def connect():

    cfg = load_config()
    try:
        client = DelugeRPCClient(**cfg)
        client.connect()
    except Exception as e:
        print(f"Error connecting to Deluge: {e}")
        sys.exit(1)

    if not client.connected:
        raise RuntimeError("failed to connect to deluge")

    return client


def torrent_status(client, torrent_id):
    keys = [
        "name",
        "hash",
        "save_path",
        "state",
        "progress",
        "is_finished",
    ]

    return client.call(
        "core.get_torrent_status",
        torrent_id,
        keys,
    )


def wait_for_move(client, torrent_id):
    while True:
        st = torrent_status(client, torrent_id)

        if st[b"state"] != "Moving":
            return

        print(f"waiting for move: {st['name']}")
        time.sleep(2)


def main():
    client = connect()
    # print("connected")

    torrent_ids = client.call("core.get_torrents_status", {}, ["name"]).keys()
    print(f"processing {len(torrent_ids)} torrents")
    for torrent_id in torrent_ids:
        st = torrent_status(client, torrent_id)

        if not st.get(b"is_finished"):
            # print(f"unfinished torrent: {st}")
            continue

        # print(f"finished torrent: {st}")

        btih = st[b"hash"].lower().decode("ascii")
        torrent_name = st[b"name"].decode("utf8")
        src_save_path = st[b"save_path"].decode("utf8")
        dst_save_path = os.path.join(CAS_ROOT, "btih", btih)
        dst_content_path = os.path.join(dst_save_path, torrent_name)

        #
        # Skip torrents already stored in CAS.
        #
        if os.path.normpath(src_save_path) == os.path.normpath(dst_save_path):
            # print(f"already in CAS: {torrent_name}")
            continue

        print()
        print(f"torrent: {torrent_name}")
        print(f"btih:    {btih}")
        print(f"src:     {src_save_path}")
        print(f"dst:     {dst_content_path}")

        #
        # Create destination root.
        #
        os.makedirs(dst_save_path, exist_ok=True)

        #
        # If destination exists Deluge will re-check.
        #
        if os.path.exists(dst_content_path):
            print("destination exists")

        print("moving storage...")

        client.call(
            "core.move_storage",
            [torrent_id],
            dst_save_path,
        )

        wait_for_move(client, torrent_id)

        print("done")

        # break # debug: stop after first move


if __name__ == "__main__":
    main()
