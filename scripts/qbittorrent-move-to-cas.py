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

# py-tree-sitter with support for binary strings
# https://github.com/tree-sitter/py-tree-sitter/pull/415
import tree_sitter

from typing import Optional, Tuple

# https://github.com/Samasaur1/tree-sitter-bencode
# https://github.com/Samasaur1/tree-sitter-bencode/pull/2
# note: to actually use the bencode parser
# you will need
# https://github.com/tree-sitter/py-tree-sitter/pull/415
# git submodule add https://github.com/Samasaur1/tree-sitter-bencode lib/tree-sitter-bencode
BENCODE_SRC_DIR = "lib/tree-sitter-bencode"
BENCODE_LIB = "lib/tree-sitter-bencode/bencode.so"
# BENCODE_NAME = "bencode"

# helper functions to build and load a tree-sitter parser
# https://stackoverflow.com/a/79769385/10440128
# https://github.com/tree-sitter/py-tree-sitter/issues/327

import re
import subprocess
import shlex
from ctypes import c_void_p, CDLL, PYFUNCTYPE, pythonapi, py_object, c_char_p

def build_tree_sitter_language(lib_path: str, source_path: str):
    if os.path.exists(lib_path):
        return
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)
    args = ["tree-sitter", "generate", "--abi", str(tree_sitter.LANGUAGE_VERSION)]
    # print(">", shlex.join(args)) # debug
    subprocess.run(args, cwd=source_path, check=True)
    args = ["tree-sitter", "build"]
    # TODO https://github.com/tree-sitter/tree-sitter/issues/4933
    # args = ["tree-sitter", "build", "--no-config"]
    # print(">", shlex.join(args)) # debug
    subprocess.run(args, cwd=source_path, check=True)
    if not os.path.exists(lib_path):
        raise RuntimeError(f"failed to build {lib_path}")


def load_tree_sitter_language(lib_path: str, name: str = None):
    "load a tree-sitter language from its parser.so file"
    if name is None:
        name = os.path.basename(lib_path)
        name = re.sub(r"\.(so|dylib|dll)$", "", name)
    # note: there is no tree_sitter.__version__
    # https://github.com/tree-sitter/py-tree-sitter/issues/413
    # but maybe we can use tree_sitter.LANGUAGE_VERSION
    # to switch these branches without try/except blocks
    excs = []
    # Strategy A: the "traditional" constructor Language(lib_path, name)
    # if ? <= tree_sitter.LANGUAGE_VERSION <= ?:
    try:
        lang = tree_sitter.Language(lib_path, name)
        # print("load_tree_sitter_language: strategy A")
        return lang
    except Exception as exc:
        excs.append(exc)
    # Strategy B: some packaged languages expose a module like 'tree_sitter_<name>'
    # that exposes a function language() which returns a pointer/handle we can
    # pass to Language(...)
    # if ? <= tree_sitter.LANGUAGE_VERSION <= ?:
    try:
        module_name = f"tree_sitter_{name}"
        lang_mod = __import__(module_name)
        if hasattr(lang_mod, "language"):
            ptr = lang_mod.language()
            # In newer py-tree-sitter, Language(ptr) expects the raw pointer
            lang = tree_sitter.Language(ptr)
            # print("load_tree_sitter_language: strategy B")
            return lang
    except Exception as exc:
        excs.append(exc)
    # Strategy C: load the .so with ctypes and call the exported symbol
    # tree_sitter_<name>() to obtain a TSLanguage* pointer, then pass it into
    # Language(ptr).
    # if ? <= tree_sitter.LANGUAGE_VERSION:
    try:
        cdll = CDLL(os.path.abspath(lib_path))
        func_name = f"tree_sitter_{name}"
        if not hasattr(cdll, func_name):
            # sometimes grammar authors compile with an alternate exported name
            alt = func_name + "_language"
            if hasattr(cdll, alt):
                func_name = alt
        func = getattr(cdll, func_name)
        func.restype = c_void_p
        ptr = func()
        PyCapsule_New = PYFUNCTYPE(py_object, c_void_p, c_char_p, c_void_p)(("PyCapsule_New", pythonapi))
        ptr = PyCapsule_New(ptr, b"tree_sitter.Language", None)
        lang = tree_sitter.Language(ptr)
        # print("load_tree_sitter_language: strategy C")
        return lang
    except Exception as exc:
        excs.append(exc)
    raise RuntimeError(f"Failed to load tree-sitter language from {lib_path}: {excs}")


def create_tree_sitter_parser(language):
    excs = []
    try:
        parser = tree_sitter.Parser(language)
        return parser
    except Exception as exc:
        excs.append(exc)
    try:
        parser = tree_sitter.Parser()
        parser.set_language(language)
        return parser
    except Exception as exc:
        excs.append(exc)
    raise RuntimeError(f"Failed to create tree-sitter parser from {language}: {excs}")



import re
import hashlib
from typing import Optional, Tuple

# infodict keys present in v1/v2 torrent files
_V1_KEYS = (b"length", b"files", b"pieces")
_V2_KEYS = (b"file tree", b"pieces root", b"meta version")

def _build_key_regex(v1_keys, v2_keys) -> re.Pattern:
    """
    Construct a regex like
    rb"(?:6:length|5:files|6:pieces|9:file tree|11:pieces root|12:meta version)"
    """
    all_keys = v1_keys + v2_keys
    parts = []
    for k in sorted(all_keys):
        parts.append(rb"%d:%s" % (len(k), k))
    pattern = rb"(?:%s)" % b"|".join(parts)
    return re.compile(pattern)

_KEY_RE = _build_key_regex(_V1_KEYS, _V2_KEYS)

def get_infohashes_of_torrent_file(
        torrent_file_path: str,
        bencode_parser,
        bencode_language,
        hexdigest=True
    ) -> Tuple[bytes, Optional[bytes]]:
    with open(torrent_file_path, "rb") as f:
        bencode_bytes = f.read()
    tree = bencode_parser.parse(bencode_bytes, encoding=None)
    root = tree.root_node
    # print_tree(tree, bencode_bytes)
    info_node = None
    root_dictionary = root.children[0]
    assert root_dictionary.type == "dictionary", f"unexpected root_dictionary.type {root_dictionary.type} in torrent file {torrent_file_path}"
    debug = False
    if debug:
        print(f"root node type {root.type}")
        print(f"root_dictionary node type {root_dictionary.type}")
    for i, n in enumerate(root_dictionary.children):
        if debug: print(f"n node type {n.type}")
        if n.type != "string": continue
        n_bytes = bencode_bytes[n.start_byte:n.end_byte]
        if debug: print(f"n node bytes {n_bytes}")
        if n_bytes != b"4:info": continue
        if debug: print(f"found info dict key at {n.start_byte}:{n.end_byte}")
        info_node = root_dictionary.children[i+1]
        assert info_node.type == "dictionary", f"unexpected info_node.type {info_node.type} in torrent file {torrent_file_path}"
        if debug:
            info_bytes = bencode_bytes[info_node.start_byte:info_node.end_byte]
            print(f"found info dict value at {info_node.start_byte}:{info_node.end_byte}: {info_bytes[:20]}...{info_bytes[-20:]}")
        break
    assert info_node, f"not found info_node in torrent file {torrent_file_path}"
    info_bytes = bencode_bytes[info_node.start_byte:info_node.end_byte]
    # heuristic detection of torrent version
    key_matches = _KEY_RE.findall(info_bytes)
    has_v1_keys = any(m.split(b":", 1)[1] in _V1_KEYS for m in key_matches)
    has_v2_keys = any(m.split(b":", 1)[1] in _V2_KEYS for m in key_matches)
    # compute hashes conditionally
    h1 = hashlib.sha1(info_bytes).digest() if has_v1_keys else None
    h2 = hashlib.sha256(info_bytes).digest() if has_v2_keys else None
    if hexdigest:
        if h1: h1 = h1.hex()
        if h2: h2 = h2.hex()
    return h1, h2



# https://github.com/tree-sitter/py-tree-sitter/blob/master/examples/walk_tree.py
from tree_sitter import Language, Parser, Tree, Node
def print_tree(tree: Tree, source: bytes) -> None:
    cursor = tree.walk()
    visited_children = False
    depth = 0
    while True:
        if not visited_children:
            # yield cursor.node
            node = cursor.node
            node_source = source[node.start_byte:min(node.end_byte, (node.start_byte + 100))]
            print((depth * "  ") + node.type + ": " + repr(node_source))
            if cursor.goto_first_child():
                depth += 1
            else:
                visited_children = True
        elif cursor.goto_next_sibling():
            visited_children = False
        elif cursor.goto_parent():
            depth -= 1
        else:
            break



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

    build_tree_sitter_language(BENCODE_LIB, BENCODE_SRC_DIR)
    bencode_language = load_tree_sitter_language(BENCODE_LIB)
    bencode_parser = create_tree_sitter_parser(bencode_language)

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

        # no. torrent.info.hash is a truncated btmh hash (v2 infohash)
        # for hybrid or v2-only torrents
        # https://github.com/rmartin16/qbittorrent-api/issues/237
        # https://github.com/qbittorrent/qBittorrent/issues/18185
        r'''
        if len(torrent.info.hash) == 40:
            btih = torrent.info.hash
            btmh = None
            cas_subdir_parts = ["btih", btih]
        elif len(torrent.info.hash) == 64:
            btmh = torrent.info.hash
            btih = None
            cas_subdir_parts = ["btmh", btmh]
        else:
            print(f"FIXME unknown torrent.info.hash {torrent.info.hash}")
            continue
        '''

        # TODO also implement this for remote qbittorrent instances
        # api/v2/torrents/export?hash={torrent_id}
        # https://qbittorrent-api.readthedocs.io/en/latest/apidoc/torrents.html#qbittorrentapi.torrents.TorrentsAPIMixIn.torrents_export

        # get the actual infohashes from the .torrent file
        torrent_file_path = os.path.expanduser(f"~/.local/share/qBittorrent/BT_backup/{torrent_id}.torrent")
        # no. torf does not support v2 torrents https://github.com/rndusr/torf/issues/55
        # torf_torrent = torf.Torrent.read(torrent_file_path)

        btih, btmh = get_infohashes_of_torrent_file(torrent_file_path, bencode_parser, bencode_language)

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
