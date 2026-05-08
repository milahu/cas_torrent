#!/usr/bin/env python3

# remove file mappings from all torrents in qBittorrent
# so all files are stored at their original file paths

# this is useful to store content files in a CAS filesystem
# so all file paths can be derived from the torrent file

CONFIG_PATH = "~/.config/qbittorrentapi.json"
BT_BACKUP_DIR = "~/.local/share/qBittorrent/BT_backup"

dry_run = False
# dry_run = True # debug

verbose = False

test_infohash = None
# test_infohash = "fbc0685b029e732fb88d4f5788585f5668e427a1" # debug

test_stop_after_first = False
# test_stop_after_first = True # debug

if dry_run:
    print("dry_run is True -> not moving files")

if test_infohash:
    print(f"test_infohash is set -> only processing the torrent {test_infohash}")

import sys
import os
import json
import shutil
import time
from pathlib import Path

import bencodepy
import qbittorrentapi


script_time = int(time.time())

CONFIG_PATH = os.path.expanduser(CONFIG_PATH)
BT_BACKUP_DIR = os.path.expanduser(BT_BACKUP_DIR)

with open(CONFIG_PATH) as f:
    conn_info = json.load(f)


def wait_for_qbt_shutdown(timeout=300):
    """
    Ensure qBittorrent is NOT running before touching BT_backup.
    We do NOT rely on API functionality, only connectivity.
    """
    print("Checking if qBittorrent is running...")

    start = time.time()

    # while True:
    timeout = None
    if 1:
        try:
            client = qbittorrentapi.Client(**conn_info)
            client.auth_log_in()

            # If we get here, qBittorrent is running
            if timeout:
                time_left = int(timeout - (time.time() - start))
                print("error: qBittorrent is running. Please close qBittorrent. waiting {time_left} seconds")
            else:
                print("error: qBittorrent is running. Please close qBittorrent")
            sys.exit(1)

        except qbittorrentapi.exceptions.APIConnectionError:
            print("qBittorrent is not reachable -> safe to proceed.")
            return

        if time.time() - start > timeout:
            raise TimeoutError("qBittorrent still running after timeout")

        time.sleep(10)


def wait_for_qbt_start(timeout=3600):
    """
    Ensure qBittorrent is running
    """
    print("Checking if qBittorrent is running...")

    start = time.time()

    while True:
        try:
            client = qbittorrentapi.Client(**conn_info)
            client.auth_log_in()

            # If we get here, qBittorrent is running
            print("ok: qBittorrent is running")
            return

        except qbittorrentapi.exceptions.APIConnectionError:

            if timeout:
                time_left = int(timeout - (time.time() - start))
                print("error: qBittorrent is not running. Please start qBittorrent. waiting {time_left} seconds")
            else:
                print("error: qBittorrent is not running. Please start qBittorrent")

        if time.time() - start > timeout:
            raise TimeoutError("qBittorrent is not running")

        time.sleep(2)


wait_for_qbt_shutdown()



def load_bencode(path):
    with open(path, "rb") as f:
        return bencodepy.decode(f.read())


def save_bencode(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bencodepy.encode(obj))
    os.replace(tmp, path)


def is_flattened_mapping(mapped_files):
    if not mapped_files:
        return False
    for p in mapped_files:
        if b"/" not in p:
            return True
    return False


def torrent_expected_paths(torrent_meta):
    info = torrent_meta[b"info"]
    name = info[b"name"].decode()

    out = []
    for f in info[b"files"]:
        rel = "/".join(x.decode() for x in f[b"path"])
        out.append(f"{name}/{rel}")
    return out


def repair_fastresume(infohash):
    fastresume_path = f"{BT_BACKUP_DIR}/{infohash}.fastresume"
    torrent_path = f"{BT_BACKUP_DIR}/{infohash}.torrent"

    if not os.path.exists(fastresume_path) or not os.path.exists(torrent_path):
        return False

    fastresume = load_bencode(fastresume_path)
    torrent_meta = load_bencode(torrent_path)

    # torrent_info = torrent_meta[b"info"]
    # torrent_name = torrent_info[b"name"].decode()

    mapped = fastresume.get(b"mapped_files")
    if not is_flattened_mapping(mapped):
        return False

    save_path = fastresume[b"save_path"].decode()

    if not b"info" in torrent_meta:
        print(f"{infohash}: error: broken torrent file: missing info dict: {torrent_path}")
        return False

    if not b"files" in torrent_meta[b"info"]:
        # this would make torrent_expected_paths throw: KeyError: b'files'
        print(f"{infohash}: error: broken torrent file: missing info.files list: {torrent_path}")
        return False

    print(f"{infohash}: fixing fastresume file: {fastresume_path}")

    expected = torrent_expected_paths(torrent_meta)

    for old, new in zip(mapped, expected):
        old = old.decode()
        old_abs = os.path.join(save_path, old)
        new_abs = os.path.join(save_path, new)

        if os.path.exists(old_abs) and not os.path.exists(new_abs):
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            if verbose:
                print(f"{infohash}: moving file: mv {old_abs!r} {new_abs!r}")
            if not dry_run:
                shutil.move(old_abs, new_abs)

        if not os.path.exists(new_abs):
            print(f"{infohash}: error: missing file: {new_abs!r}")

    if dry_run: return True

    # backup
    fastresume_path_bak = fastresume_path + f".bak.{script_time}"
    # print(f"{infohash}: writing {fastresume_path_bak}")
    shutil.copy(fastresume_path, fastresume_path_bak)

    # remove mapping override
    del fastresume[b"mapped_files"]
    save_bencode(fastresume_path, fastresume)

    # print(f"{infohash}: done {fastresume_path}")

    return True


todo_recheck = []

# run offline repair over BT_backup ONLY
for f in os.listdir(BT_BACKUP_DIR):
    if not f.endswith(".fastresume"):
        continue

    infohash = f.replace(".fastresume", "")

    # skip torrents before the test torrent
    if test_infohash and infohash != test_infohash: continue

    try:
        if repair_fastresume(infohash):
            todo_recheck.append(infohash)
            if test_stop_after_first:
                break
    except Exception as e:
        raise
        print("ERROR", infohash, e)

    # skip torrents after the test torrent
    if test_infohash and infohash == test_infohash: break

if not todo_recheck:
    print("ok: all fastresume files are good")
    sys.exit()

todo_recheck.sort()

print("done fixing fastresume files")

todo_recheck_path = f"todo_recheck_moved_torrents.{script_time}.json"
print(f"writing {todo_recheck_path}")
with open(todo_recheck_path, "w") as f:
    json.dump(todo_recheck, f)



print("waiting for qBittorrent to start")
try:
    wait_for_qbt_start()
except TimeoutError:
    # TODO be more helpful...
    # for example, create a python script the user can run when qBittorrent is running
    print("error: qBittorrent is not running. you will have to make qBittorrent recheck these torrents: {todo_recheck}")
    sys.exit(1)



print(f"forcing recheck of fixed torrents")

qbt = qbittorrentapi.Client(**conn_info)
qbt.auth_log_in()

todo_recheck_set = set(todo_recheck)
for torrent in qbt.torrents_info():
    infohash = torrent.infohash_v1 or torrent.info.hash
    if not infohash in todo_recheck_set: continue
    torrent.recheck()
