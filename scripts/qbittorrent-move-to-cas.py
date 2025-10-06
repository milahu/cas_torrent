#!/usr/bin/env python3

# move all finished torrents to ~/cas/btih/{btih}

# if the destination exists, qbittorrent checks the existing files
# uses the new location, but keeps the old files
#
# todo: delete old files if they are part of the torrent
# and if the file is complete in the new location
# dont delete extra files added by the user



# https://github.com/rmartin16/qbittorrent-api
# https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)
# https://qbittorrent-api.readthedocs.io/en/latest/
# https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)#set-torrent-location
# https://qbittorrent-api.readthedocs.io/en/latest/apidoc/torrents.html#qbittorrentapi.torrents.TorrentDictionary.set_location
# https://github.com/rmartin16/qbittorrent-api/raw/main/src/qbittorrentapi/torrents.py

import os
import sys
import time
import json
import shutil

import qbittorrentapi

# no v2 torrents
# https://github.com/rndusr/torf/issues/55
# import torf

# fails to parse invalid torrents
# https://github.com/p2p-ld/torrent-models/issues/13
# import torrent_models

# config
# home_dir = os.environ["HOME"]
# src_dir = home_dir + "/qbittorrent/data"
# dst_dir = home_dir + "/cas"



cas_config_path = os.path.expanduser("~/.config/cas.json")
# example config:
"""
{
  "dirs": [
    "/run/media/user/WSC14YZM_8TB/cas"
  ]
}
"""

ignore_mountpoint_list = (
    '/nix/store',
    '/boot',
)

# host is container ip address from ipaddr.sh
# example config:
"""
{
  "host": "10.0.18.33",
  "port": 9001,
  "username": "admin",
  "password": "xxx"
}
"""
qbt_config_path = os.path.expanduser("~/.config/qbittorrentapi.json")

debug_torrent_name = None
if 0:
    debug_torrent_name = "xxx"

debug_torrent_hashes = None
if 0:
    debug_torrent_hashes = """
        xxx
    """
if debug_torrent_hashes and type(debug_torrent_hashes) == str:
    debug_torrent_hashes = list(
        map(
            lambda s: s.strip(),
            debug_torrent_hashes.strip().split("\n")
        )
    )

with open(cas_config_path) as f:
    cas_config = json.load(f)
cas_path_list = cas_config["dirs"]

with open(qbt_config_path) as f:
    conn_info = json.load(f)

def format_dev(dev):
    minor = dev & 0xff
    major = dev >> 8 & 0xff
    return f"{major},{minor}"

def hardlink_copy(src: str, dst: str):
    """
    Recursively copy src to dst using hardlinks.
    - Creates directories as needed in dst.
    - Preserves existing files in dst.
    - Ignores existing directories in dst.
    """
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    if not os.path.isdir(src):
        # copy a single file
        if os.path.exists(dst):
            return
        try:
            os.link(src, dst)  # hard link
        except OSError:
            # If hardlink fails, fallback to copy
            shutil.copy2(src, dst)
        return
    for root, dirs, files in os.walk(src):
        dirs.sort()
        rel_path = os.path.relpath(root, src)
        dst_dir = os.path.join(dst, rel_path)
        os.makedirs(dst_dir, exist_ok=True)
        for fname in sorted(files):
            src_file = os.path.join(root, fname)
            dst_file = os.path.join(dst_dir, fname)
            if os.path.exists(dst_file):
                continue
            try:
                os.link(src_file, dst_file)  # hard link
            except OSError:
                # If hardlink fails, fallback to copy
                shutil.copy2(src_file, dst_file)

with qbittorrentapi.Client(**conn_info) as qbt_client:

    #if qbt_client.torrents_add(urls="...") != "Ok.":
    #    raise Exception("Failed to add torrent.")

    # display qBittorrent info
    if False:
        print(f"qBittorrent: {qbt_client.app.version}")
        print(f"qBittorrent Web API: {qbt_client.app.web_api_version}")
        for k, v in qbt_client.app.build_info.items():
            print(f"{k}: {v}")

    # state
    finished_states = (
        "uploading", # Torrent is being seeded and data is being transferred
        "pausedUP",  # Torrent is paused and has finished downloading
        "queuedUP",  # Queuing is enabled and torrent is queued for upload
        "stalledUP", # Torrent is being seeded, but no connection were made
        "forcedUP",  # Torrent is forced to uploading and ignore queue limit
    )

    torrents_by_src_content_path = dict()
    for torrent in qbt_client.torrents_info():
        if not torrent.state in finished_states:
            continue
        src_content_path = torrent.content_path
        src_save_path = torrent.save_path
        if os.path.dirname(src_content_path) != src_save_path:
            # single-file in single-directory torrent
            src_content_path = os.path.dirname(src_content_path)
        assert os.path.dirname(src_content_path) == src_save_path
        if not src_content_path in torrents_by_src_content_path:
            torrents_by_src_content_path[src_content_path] = list()
        torrents_by_src_content_path[src_content_path].append(torrent)

    for torrent in qbt_client.torrents_info():

        #print(f"torrent {torrent.hash} {torrent.name} {torrent.state} {src_content_path}")

        # TODO remove? move all torrents
        if not torrent.state in finished_states:
            continue

        if debug_torrent_name and torrent.name != debug_torrent_name:
            continue

        if debug_torrent_hashes:
            if torrent.info.hash not in debug_torrent_hashes:
                continue
            # else:
            #     print(f"debugging torrent {torrent.info.hash} {torrent.name}")

        src_content_path = torrent.content_path
        src_save_path = torrent.save_path

        # print(f"src_save_path    {src_save_path}")
        # print(f"src_content_path {src_content_path}")

        if os.path.dirname(src_content_path) != src_save_path:
            # single-file in single-directory torrent
            src_content_path = os.path.dirname(src_content_path)
            # print(f"        src_content_path {src_content_path}")

        assert os.path.dirname(src_content_path) == src_save_path

        if 0:
            src = src_save_path
            src2 = src_content_path

            # get the actual content path
            src2 = src_save_path + src_content_path[len(src_save_path):].split("/")[0]

            #if os.path.dirname(src_content_path) + "/" != src_save_path:
            if os.path.dirname(src2) + "/" != src:
                print('FIXME dirname(src2) + "/" != src')
                print("  src ", src)
                print("  src2", src2)
                # sys.exit(1)

        # FIXME torrent.info.hash is a truncated btmh hash (v2 infohash)
        # for hybrid or v2-only torrents
        # https://github.com/rmartin16/qbittorrent-api/issues/237
        # https://github.com/qbittorrent/qBittorrent/issues/18185
        torrent_id = torrent.info.hash

        debug_torrent_id = None
        # debug_torrent_id = "xxx"

        if debug_torrent_id and torrent_id != debug_torrent_id:
            continue

        btih = torrent.infohash_v1
        btmh = torrent.infohash_v2

        if btih:
            # prefer btih for hybrid torrents
            cas_subdir_parts = ["btih", btih]
        elif btmh:
            cas_subdir_parts = ["btmh", btmh]
        else:
            print(f"FIXME failed to parse infohash: torrent_id={torrent_id} btih={btih} btmh={btmh}")
            continue

        src_content_path_parts = src_content_path.split("/")

        if src_content_path_parts[-4] == "cas" and src_content_path_parts[-3] in ["btih", "btmh"]:
            # content is already stored in a CAS filesystem
            # src_content_path = f"{cas_parent_dir}/cas/btih/{btih}/{name}"
            # print(f"already CAS: {src_content_path}")
            continue

        # print("src_content_path_parts", src_content_path_parts)

        if src_content_path_parts[-3] == "cas" and src_content_path_parts[-2] == "todo":
            # content is stored in the "todo" directory of a CAS filesystem
            # src_content_path = f"{cas_parent_dir}/cas/todo/{name}"
            # print(f"TODO move to same CAS: {src_content_path}")
            dst_content_path = "/".join(src_content_path_parts[:-2] + cas_subdir_parts + src_content_path_parts[-1:])
            dst_save_path = "/".join(src_content_path_parts[:-2] + cas_subdir_parts)

        else:
            # FIXME find dst_save_path on the same filesystem
            dst_save_path = None
            print(f"TODO move to CAS: {src_content_path}")
            continue

        print(f"torrent_id={torrent_id} btih={btih} btmh={btmh}")
        print("  torrent.name    ", torrent.name)
        print("  src_save_path   ", src_save_path)
        print("  src_content_path", src_content_path)
        print("  dst_save_path   ", dst_save_path)
        print("  dst_content_path", dst_content_path)

        wait_for_check = False

        if os.path.exists(dst_save_path):
            # FIXME do a file-by-file comparison of src_save_path and dst_save_path
            # remove duplicate files from src_save_path
            # copy missing files to dst_save_path
            # handle file collisions between src_save_path and dst_save_path
            if 1:
                print("  FIXME dst_save_path exists")
                torrent.add_tags("move-to-cas-fixme")
                continue
            # FIXME this is not always true
            print("  note: dst_save_path exists. qbittorrent will check files.", dst_save_path)
            wait_for_check = True
            torrent.add_tags("move-to-cas-dst-exists")

        # if src_content_path is used by multiple torrents
        # then copy files back from dst_content_path to src_content_path
        # this is the actual issue in
        # https://github.com/qbittorrent/qBittorrent/issues/12842
        # 4.2.5 overwrites files if file names are the same
        colliding_torrents = torrents_by_src_content_path[src_content_path]
        # colliding_torrents = colliding_torrents[:]
        # this torrent no longer needs src_save_path
        for torrent2 in colliding_torrents:
            if torrent2.info.hash == torrent.info.hash:
                colliding_torrents.remove(torrent2)
                break

        # stop other torrents to avoid read errors
        colliding_torrent_was_stopped = []
        for torrent2 in colliding_torrents:
            if qbittorrentapi.TorrentState(torrent2.state).is_stopped:
                colliding_torrent_was_stopped.append(True)
            else:
                colliding_torrent_was_stopped.append(False)
                torrent2.stop()
                print(f"  torrent {torrent2.info.hash}: stopping colliding torrent {torrent2.name}")

        # move content files
        # https://github.com/rmartin16/qbittorrent-api/raw/main/src/qbittorrentapi/torrents.py
        os.makedirs(os.path.dirname(dst_save_path), exist_ok=True)
        torrent.set_location(dst_save_path)

        checking_states = (
            "checkingUP", # Torrent has finished downloading and is being checked
            "checkingDL", # Same as checkingUP, but torrent has NOT finished downloading
            "checkingResumeData", # Checking resume data on qBt startup
        )

        # only one moving state:
        # moving  Torrent is moving to another location

        def get_state():
            # TODO better. get state of one torrent
            for torrent2 in qbt_client.torrents_info():
                if torrent2.info.hash != torrent.info.hash:
                    continue
                return torrent2.state

        # TODO refactor checking and moving

        if get_state() in checking_states:
            print("  waiting: qbittorrent is checking files ", end="")
            sys.stdout.flush()
            time.sleep(2)
            # todo timeout
            while get_state() in checking_states:
                print(".", end="")
                sys.stdout.flush()
                time.sleep(2)
            print(" ok")

        if get_state() == "moving":
            print("  waiting: qbittorrent is moving files ", end="")
            sys.stdout.flush()
            time.sleep(2)
            # todo timeout
            while get_state() == "moving":
                print(".", end="")
                sys.stdout.flush()
                time.sleep(2)
            print(" ok")

        # done moving content files

        if len(colliding_torrents) >= 1:
            # at least one other torrent still needs src_save_path
            # copy content files back from dst_content_path to src_content_path
            hardlink_copy(dst_save_path, src_save_path)

        # restart other torrents
        for idx, torrent2 in enumerate(colliding_torrents):
            was_stopped = colliding_torrent_was_stopped[idx]
            if not was_stopped:
                print(f"  torrent {torrent2.info.hash}: restarting colliding torrent {torrent2.name}")
                torrent2.start()

        # move non-content files
        if len(colliding_torrents) == 0 and os.path.exists(src_content_path):
            # this is the last torrent using src_content_path
            # and src_content_path still exists
            # so there must be non-content files in src_content_path
            print(f"  moving non-content files")
            for src_dir, dirs, files in os.walk(src_content_path):
                dirs.sort()
                dst_dir = dst_content_path + src_dir[len(src_content_path):]
                os.makedirs(dst_dir, exist_ok=True)
                for filename in sorted(files):
                    src_filepath = src_dir + "/" + filename
                    dst_filepath = dst_dir + "/" + filename
                    print(f"    {dst_filepath}")
                    shutil.move(src_filepath, dst_filepath)
            print(f"  removing empty directories in {src_content_path}")
            for src_dir, dirs, files in os.walk(src_content_path, topdown=False):
                dirs.sort()
                try:
                    os.rmdir(src_dir)
                except Exception as exc:
                    print(f"    failed to remove dir {src_dir}: {exc}")

        torrent.add_tags("move-to-cas-done")

        # time.sleep(1) # help user to kill this process

        # break # debug: stop after first torrent
