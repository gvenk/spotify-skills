#!/usr/bin/env python3
"""Helper for the playlist-watch skill: see what changed in Spotify playlists since you last looked.

Standard library only (Python 3.9+). It talks to the Spotify desktop app through its `spotify_cli`.
It keeps a snapshot of every watched playlist in a data folder of its own (see `status`). A change is a
track that was added or removed since the snapshot you last confirmed.

Commands:
  watch   [--add NAME|URI|URL ...] [--all-library on|off]   choose what is watched (no options: show the list)
  check   --out FILE                          read the watched playlists, report what changed (changes nothing)
  seen    --selection FILE --confirm CODE [--playlist REF ...]   accept the changes: the snapshot moves forward
  merge   --selection FILE [--from REF ...] --to queue|playlist [--target REF | --new-name NAME]
          [--mode append|overwrite] [--confirm CODE] [--confirm-overwrite URI]
                                              plan (default) or do (with --confirm) a merge of the added tracks
  forget  REF | --all [--include-backups] [--yes]    stop watching, delete local data
  status                                      where the data lives and what is watched
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")
PLAYLIST_URI = re.compile(r"spotify:playlist:[A-Za-z0-9]+")
PLAYLIST_URL = re.compile(r"https://open\.spotify\.com/(?:intl-[a-z]+/)?playlist/([A-Za-z0-9]+)(?:[?#].*)?")
PAGE = 100
RETRY_WAIT = 1.0
TRANSIENT = re.compile(r"connect|timed? ?out", re.I)
MAX_OVERWRITE = 2000

NOTICE = """This folder belongs to the playlist-watch skill (a helper for Spotify playlists).

It holds:
  watch.json     the list of playlists you watch
  snapshots/     the track lists of those playlists as of the last time you confirmed them
  backups/       the contents of a playlist just before an overwrite, so you can restore it by hand

It contains no passwords, keys or Spotify login. Nothing here is sent anywhere.
To remove everything: ask the skill to `forget --all`, or delete this folder.
"""


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


BIDI_CONTROLS = set("‪‫‬‭‮⁦⁧⁨⁩")


def clean_text(value, limit=300):
    """Untrusted text (a playlist or track name) made safe to print and to write back to Spotify:
    control characters and text-direction overrides are removed, whitespace becomes single spaces,
    and the length is capped."""
    kept = []
    for ch in str(value):
        if ch.isspace():
            kept.append(" ")
        elif unicodedata.category(ch) != "Cc" and ch not in BIDI_CONTROLS:
            kept.append(ch)
    return " ".join("".join(kept).split())[:limit]


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- talking to spotify_cli

def spotify_cli():
    path = os.environ.get("SPOTIFY_CLI_PATH")
    if path:
        return path
    if sys.platform == "darwin":
        return "/Applications/Spotify.app/Contents/MacOS/spotify_cli"
    return "spotify_cli.exe" if os.name == "nt" else "spotify_cli"


class CliError(Exception):
    pass


def cli(*args):
    """Run spotify_cli with an argument list (never through a shell). Returns stdout."""
    try:
        r = subprocess.run([spotify_cli()] + list(args), capture_output=True, text=True)
    except FileNotFoundError:
        die("spotify_cli was not found. Install the Spotify desktop app, or set SPOTIFY_CLI_PATH.", 3)
    if r.returncode != 0:
        raise CliError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def cli_json(*args):
    out = cli(*args, "--format", "json")
    try:
        return json.loads(out)
    except ValueError:
        raise CliError("unexpected answer: " + out.strip()[:200])


def read_json_cli(*args):
    """Like cli_json, for reading playlists: a failed connection to the Spotify app is tried once more
    (it sometimes drops a single call). The CLI says the same for a private playlist, so that costs one extra try."""
    try:
        return cli_json(*args)
    except CliError as e:
        if not TRANSIENT.search(str(e)):
            raise
        time.sleep(RETRY_WAIT)
        return cli_json(*args)


def library_playlists():
    """Every playlist in the user's library, also those inside folders (nested folders too).
    `library list` only returns the top level, so the folder hierarchy is read as well."""
    try:
        items = cli_json("library", "list", "--type", "playlist", "--limit", "2000").get("items", [])
    except CliError as e:
        die("Could not list your playlists: %s" % e, 5)
    try:
        items = items + cli_json("folder", "list", "--recursive").get("items", [])
    except CliError:
        pass  # without the folder list we still have the top level
    out = []
    for i in items:
        uri = str(i.get("uri", ""))
        if i.get("type", "playlist") == "playlist" and PLAYLIST_URI.fullmatch(uri):
            out.append((clean_text(i.get("name", "")), uri))
    return list(dict.fromkeys(out))


def resolve(ref, library=None):
    """A playlist URI, an open.spotify.com link, or the name of a playlist in the user's library -> URI."""
    ref = ref.strip()
    if not ref:
        die("A playlist name is empty.")
    if PLAYLIST_URI.fullmatch(ref):
        return ref
    m = PLAYLIST_URL.fullmatch(ref)
    if m:
        return "spotify:playlist:" + m.group(1)
    named = library if library is not None else library_playlists()
    want = ref.casefold()
    hits = [(n, u) for n, u in named if n.casefold() == want] or [(n, u) for n, u in named if want in n.casefold()]
    hits = list(dict.fromkeys(hits))
    if len(hits) == 1:
        return hits[0][1]
    if not hits:
        die("No playlist named %r in your library. Use its link instead, if it is a public playlist." % ref, 7)
    say({"ambiguous": ref, "candidates": [{"name": n, "uri": u} for n, u in hits[:20]]})
    die("%d playlists match %r. Ask the user which one, and use its URI." % (len(hits), ref), 6)


def clean_track(t):
    u = str(t.get("uri", ""))
    if not TRACK_URI.fullmatch(u):
        return None
    return {"uri": u, "name": clean_text(t.get("name", "")), "artists": [clean_text(a) for a in t.get("artists", [])][:10],
            "album": clean_text(t.get("album", ""))}


def clean_tracks(raw):
    """Tracks only (no episodes, no local files), each once, text cleaned. Returns (tracks, skipped, duplicates)."""
    seen, out, skipped, dups = set(), [], 0, 0
    for t in raw:
        c = clean_track(t) if isinstance(t, dict) else None
        if c is None:
            skipped += 1
        elif c["uri"] in seen:
            dups += 1
        else:
            seen.add(c["uri"])
            out.append(c)
    return out, skipped, dups


def read_live(uri):
    """The playlist as it is in Spotify now. Raises CliError when it cannot be read."""
    d = read_json_cli("playlist", "get", uri)
    tracks = list(d.get("tracks", []))
    total = d.get("track_count")
    while isinstance(total, int) and len(tracks) < total and len(tracks) < 20000:
        page = read_json_cli("playlist", "get", uri, "--limit", str(PAGE), "--offset", str(len(tracks))).get("tracks", [])
        if not page:
            break  # tracks that are no longer available are counted but not returned
        tracks.extend(page)
    clean, skipped, dups = clean_tracks(tracks)
    return {"uri": uri, "name": clean_text(d.get("name") or uri), "description": clean_text(d.get("description") or ""),
            "owner": clean_text(d.get("owner") or ""), "is_owned": bool(d.get("is_owned")),
            "tracks": clean, "skipped_not_tracks": skipped, "duplicates_in_playlist": dups}


# ---------------------------------------------------------------- the data folder

def data_dir():
    env = os.environ.get("PLAYLIST_WATCH_DIR")
    if env:
        if not os.path.isabs(env):
            die("PLAYLIST_WATCH_DIR must be an absolute path.", 9)
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = xdg if xdg and os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "playlist-watch")


def write_file(path, text):
    """Write atomically (a temporary file in the same folder, then a rename), readable by the owner only."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json(path, data):
    write_file(path, json.dumps(data, indent=2, ensure_ascii=False))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        die("Cannot read %s: %s. If it is damaged, `forget` that playlist, or delete the file." % (path, e), 9)


def ensure_data():
    """Create the data folder (private) with its notice. Refuses symbolic links."""
    d = data_dir()
    for p in (d, os.path.join(d, "snapshots"), os.path.join(d, "backups")):
        if os.path.islink(p):
            die("%s is a symbolic link; refusing to use it." % p, 9)
        os.makedirs(p, mode=0o700, exist_ok=True)
    notice = os.path.join(d, "README.txt")
    if not os.path.exists(notice):
        write_file(notice, NOTICE)
    return d


def load_watch():
    w = read_json(os.path.join(data_dir(), "watch.json"), {})
    if not isinstance(w, dict):
        die("watch.json is not what it should be. Delete it, or run `forget --all`.", 9)

    def uris(key):
        return [u for u in w.get(key, []) if isinstance(u, str) and PLAYLIST_URI.fullmatch(u)]
    selected = [{"uri": x["uri"], "name": clean_text(x.get("name", ""))} for x in w.get("selected", [])
                if isinstance(x, dict) and PLAYLIST_URI.fullmatch(str(x.get("uri", "")))]
    return {"version": 1, "all_library": bool(w.get("all_library")), "selected": selected,
            "targets": uris("targets"), "excluded": uris("excluded")}


def save_watch(w):
    write_json(os.path.join(ensure_data(), "watch.json"), w)


def snap_path(uri):
    return os.path.join(data_dir(), "snapshots", uri.split(":")[2] + ".json")


def load_snapshot(uri):
    s = read_json(snap_path(uri))
    if s is None:
        return None
    if not isinstance(s, dict) or not isinstance(s.get("tracks"), list) or s.get("uri") != uri:
        die("The snapshot for %s is damaged. `forget` that playlist, or delete %s." % (uri, snap_path(uri)), 9)
    tracks, _, _ = clean_tracks(s["tracks"])
    return {"uri": uri, "name": clean_text(s.get("name", "")), "description": clean_text(s.get("description", "")),
            "tracks": tracks, "confirmed_at": str(s.get("confirmed_at", ""))[:20], "archived": bool(s.get("archived"))}


def all_snapshots():
    folder = os.path.join(data_dir(), "snapshots")
    out = []
    if os.path.isdir(folder):
        for fn in sorted(os.listdir(folder)):
            m = re.fullmatch(r"([A-Za-z0-9]+)\.json", fn)
            if m:
                s = load_snapshot("spotify:playlist:" + m.group(1))
                if s:
                    out.append(s)
    return out


def save_snapshot(live, archived=False):
    ensure_data()
    write_json(snap_path(live["uri"]), {"uri": live["uri"], "name": live["name"], "description": live.get("description", ""),
                                        "track_count": len(live["tracks"]), "tracks": live["tracks"],
                                        "confirmed_at": now(), "archived": archived})


def delete_snapshot(uri):
    try:
        os.remove(snap_path(uri))
        return True
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------- what is watched, and what changed

def watched_uris(w, library):
    """Selected playlists, then (in all-library mode) every library playlist that is not a merge target or excluded."""
    skip = set(w["targets"]) | set(w["excluded"])
    order = [x["uri"] for x in w["selected"]]
    if w["all_library"]:
        order += [u for _, u in library if u not in skip]
    return list(dict.fromkeys(order))


def diff(old, new):
    old_uris, new_uris = {t["uri"] for t in old}, {t["uri"] for t in new}
    added = [t for t in new if t["uri"] not in old_uris]
    removed = [t for t in old if t["uri"] not in new_uris]
    reordered = not added and not removed and [t["uri"] for t in old] != [t["uri"] for t in new]
    return added, removed, reordered


def uris_hash(tracks):
    return hashlib.sha256("\n".join(t["uri"] for t in tracks).encode("utf-8")).hexdigest()[:16]


def check_code(entries):
    rows = sorted([e["uri"], e["status"], uris_hash(e["live"]["tracks"]) if e.get("live") else ""] for e in entries)
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()[:12]


def private_dir_for(path):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def cmd_check(args):
    ensure_data()
    w = load_watch()
    library = library_playlists() if w["all_library"] else []
    uris = watched_uris(w, library)
    names = {u: n for n, u in library}
    entries = []
    for i, uri in enumerate(uris, 1):
        print("reading %d of %d" % (i, len(uris)), file=sys.stderr)
        snap = load_snapshot(uri)
        try:
            live = read_live(uri)
        except CliError as e:
            entries.append({"uri": uri, "name": (snap or {}).get("name") or names.get(uri) or uri, "status": "unreadable", "error": clean_text(str(e), 200)})
            continue
        e = {"uri": uri, "name": live["name"], "owner": live["owner"], "live": live, "added": [], "removed": []}
        if snap is None:
            e["status"] = "new"
        else:
            added, removed, reordered = diff(snap["tracks"], live["tracks"])
            e.update(added=added, removed=removed, order_changed=reordered)
            if snap["name"] != live["name"]:
                e["renamed_from"] = snap["name"]
            if snap["description"] != live["description"]:
                e["description_changed"] = True
            changed = added or removed or reordered or "renamed_from" in e or "description_changed" in e
            e["status"] = "changed" if changed else "unchanged"
            e["confirmed_at"] = snap["confirmed_at"]
        if e["status"] == "unchanged":
            e.pop("live")
        entries.append(e)
    if w["all_library"]:
        watched = set(uris)
        for s in all_snapshots():
            if s["uri"] not in watched and not s["archived"] and s["uri"] not in w["excluded"] and s["uri"] not in w["targets"]:
                entries.append({"uri": s["uri"], "name": s["name"], "status": "left_library", "confirmed_at": s["confirmed_at"]})
    code = check_code(entries)
    sel = {"version": 1, "created": now(), "confirm_code": code, "playlists": entries}
    private_dir_for(args.out)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sel, f, indent=2, ensure_ascii=False)

    def brief(e):
        b = {"name": e["name"], "uri": e["uri"], "status": e["status"]}
        if e["status"] in ("changed", "new"):
            b["added"] = len(e["added"])
            b["removed"] = len(e["removed"])
            b["added_sample"] = ["%s - %s" % (", ".join(t["artists"]), t["name"]) for t in e["added"][:10]]
            b["removed_sample"] = ["%s - %s" % (", ".join(t["artists"]), t["name"]) for t in e["removed"][:10]]
            for k in ("renamed_from", "description_changed", "order_changed", "confirmed_at"):
                if e.get(k):
                    b[k] = e[k]
        elif e["status"] == "unreadable":
            b["error"] = e["error"]
        elif e["status"] == "left_library":
            b["last_confirmed"] = e["confirmed_at"]
        return b
    counts = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    say({"watched": len(uris), "counts": counts, "confirm_code": code, "selection_file": os.path.abspath(args.out),
         "playlists": [brief(e) for e in entries if e["status"] != "unchanged"]})


def load_check(path, confirm=None):
    sel = read_json(path)
    if sel is None:
        die("Cannot read the selection file: %s" % path)
    entries = []
    for e in sel.get("playlists", []) if isinstance(sel, dict) else []:
        if not isinstance(e, dict) or not PLAYLIST_URI.fullmatch(str(e.get("uri", ""))):
            die("The selection file has an invalid playlist entry.")
        e["name"] = clean_text(e.get("name", ""))
        if isinstance(e.get("live"), dict):
            e["live"]["tracks"], _, _ = clean_tracks(e["live"].get("tracks", []))
            e["live"]["name"] = clean_text(e["live"].get("name", ""))
            e["live"]["description"] = clean_text(e["live"].get("description", ""))
            e["live"]["uri"] = e["uri"]
        for k in ("added", "removed"):
            e[k], _, _ = clean_tracks(e.get(k, []))
        entries.append(e)
    if confirm is not None and confirm != check_code(entries):
        die("The confirmation code does not match this check. Run `check` again, show the user the result, "
            "ask again, and use the code from that run.", 2)
    return entries


def pick(entries, refs):
    if not refs:
        return list(entries)
    out = []
    for ref in refs:
        ref = ref.strip()
        m = PLAYLIST_URL.fullmatch(ref)
        key = "spotify:playlist:" + m.group(1) if m else ref
        hits = [e for e in entries if e["uri"] == key] or [e for e in entries if e["name"].casefold() == ref.casefold()]
        if not hits:
            hits = [e for e in entries if ref.casefold() in e["name"].casefold()]
        if not hits:
            die("%r is not in this check." % ref, 7)
        if len(hits) > 1:
            say({"ambiguous": ref, "candidates": [{"name": e["name"], "uri": e["uri"]} for e in hits[:20]]})
            die("%d playlists in this check match %r. Use the URI." % (len(hits), ref), 6)
        if hits[0] not in out:
            out.append(hits[0])
    return out


def accept(e):
    """Move the snapshot of one checked playlist forward. Returns what happened."""
    if e["status"] in ("changed", "new") and e.get("live"):
        save_snapshot(e["live"])
        return "baseline moved"
    if e["status"] == "left_library":
        snap = load_snapshot(e["uri"])
        if snap:
            save_snapshot(snap, archived=True)
        return "archived"
    return "nothing to do" if e["status"] == "unchanged" else "skipped (%s)" % e["status"]


def cmd_seen(args):
    entries = load_check(args.selection, args.confirm)
    ensure_data()
    chosen = pick(entries, args.playlist)
    say({"seen": [{"name": e["name"], "uri": e["uri"], "result": accept(e)} for e in chosen]})


# ---------------------------------------------------------------- watch / forget / status

def cmd_watch(args):
    ensure_data()
    w = load_watch()
    out = {"baselines_taken": [], "unreadable": [], "notes": []}
    library = None
    if args.all_library:
        on = args.all_library == "on"
        if on and not w["all_library"]:
            w["all_library"] = True
            save_watch(w)
            library = library_playlists()
            todo = [u for u in watched_uris(w, library) if load_snapshot(u) is None]
            for i, uri in enumerate(todo, 1):
                print("baseline %d of %d" % (i, len(todo)), file=sys.stderr)
                try:
                    save_snapshot(read_live(uri))
                    out["baselines_taken"].append(uri)
                except CliError as e:
                    out["unreadable"].append({"uri": uri, "error": clean_text(str(e), 200)})
        elif not on and w["all_library"]:
            w["all_library"] = False
            keep = {x["uri"] for x in w["selected"]}
            gone = [s["uri"] for s in all_snapshots() if s["uri"] not in keep]
            for u in gone:
                delete_snapshot(u)
            save_watch(w)
            out["notes"].append("all-library switched off; %d snapshots of other playlists deleted" % len(gone))
        else:
            out["notes"].append("all-library was already %s" % args.all_library)
    for ref in args.add or []:
        uri = resolve(ref, library)
        if uri in w["targets"]:
            die("%s is a playlist the skill merges into; it cannot also be watched." % uri, 4)
        if any(x["uri"] == uri for x in w["selected"]):
            out["notes"].append("%s was already watched" % uri)
            continue
        try:
            live = read_live(uri)
        except CliError as e:
            die("Could not read %s: %s. It is probably private. Only public playlists and playlists in your library can be watched." % (uri, e), 5)
        if load_snapshot(uri) is None:
            save_snapshot(live)
            out["baselines_taken"].append(uri)
        w["selected"].append({"uri": uri, "name": live["name"]})
        w["excluded"] = [u for u in w["excluded"] if u != uri]
        save_watch(w)
    say(dict(out, **watch_summary(w)))


def watch_summary(w):
    return {"data_folder": data_dir(), "all_library": w["all_library"],
            "selected": [{"name": x["name"], "uri": x["uri"]} for x in w["selected"]],
            "excluded": len(w["excluded"]), "merge_targets": len(w["targets"])}


def cmd_status(args):
    w = load_watch()
    snaps = all_snapshots()
    backups = []
    folder = os.path.join(data_dir(), "backups")
    if os.path.isdir(folder):
        backups = sorted(f for f in os.listdir(folder) if f.endswith(".json"))
    say(dict(watch_summary(w), exists=os.path.isdir(data_dir()),
             snapshots=[{"name": s["name"], "uri": s["uri"], "tracks": len(s["tracks"]), "confirmed_at": s["confirmed_at"],
                         "archived": s["archived"]} for s in snapshots_sorted(snaps)],
             backups=backups))


def snapshots_sorted(snaps):
    return sorted(snaps, key=lambda s: s["name"].casefold())


def cmd_forget(args):
    d = data_dir()
    if args.all:
        files = []
        for sub, pattern in (("snapshots", r"[A-Za-z0-9]+\.json"), ("backups", r"[0-9TZ-]+-[A-Za-z0-9]+\.json")):
            folder = os.path.join(d, sub)
            if sub == "backups" and not args.include_backups:
                continue
            if os.path.isdir(folder):
                files += [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if re.fullmatch(pattern, f)]
        files += [os.path.join(d, f) for f in ("watch.json", "README.txt") if os.path.exists(os.path.join(d, f))]
        keep_backups = not args.include_backups and os.path.isdir(os.path.join(d, "backups")) and os.listdir(os.path.join(d, "backups"))
        if not args.yes:
            say({"would_delete": len(files), "folder": d, "backups_kept": bool(keep_backups),
                 "next": "run again with --yes to delete these files" + ("; add --include-backups to delete the backups too" if keep_backups else "")})
            return
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass
        for sub in ("snapshots", "backups", ""):
            try:
                os.rmdir(os.path.join(d, sub) if sub else d)
            except OSError:
                pass
        say({"deleted": len(files), "folder": d, "backups_kept": bool(keep_backups)})
        return
    if not args.ref:
        die("Name a playlist to forget, or use --all.")
    w = load_watch()
    ref = args.ref.strip()
    m = PLAYLIST_URL.fullmatch(ref)
    uri = ref if PLAYLIST_URI.fullmatch(ref) else ("spotify:playlist:" + m.group(1) if m else None)
    if uri is None:
        known = {x["uri"]: x["name"] for x in w["selected"]}
        known.update({s["uri"]: s["name"] for s in all_snapshots()})
        hits = [u for u, n in known.items() if n.casefold() == ref.casefold()] or [u for u, n in known.items() if ref.casefold() in n.casefold()]
        if len(hits) > 1:
            say({"ambiguous": ref, "candidates": [{"name": known[u], "uri": u} for u in hits[:20]]})
            die("%d watched playlists match %r. Use the URI." % (len(hits), ref), 6)
        if not hits:
            die("%r is not a watched playlist." % ref, 7)
        uri = hits[0]
    was_selected = any(x["uri"] == uri for x in w["selected"])
    had_snapshot = delete_snapshot(uri)
    w["selected"] = [x for x in w["selected"] if x["uri"] != uri]
    if w["all_library"] and uri not in w["excluded"]:
        w["excluded"].append(uri)
    if not (was_selected or had_snapshot or w["all_library"]):
        die("%s is not a watched playlist." % uri, 7)
    save_watch(w)
    say({"forgotten": uri, "was_selected": was_selected, "snapshot_deleted": had_snapshot,
         "excluded_from_all_library": uri in w["excluded"]})


# ---------------------------------------------------------------- merging the added tracks

def confirm_code(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def run_batch(ops):
    d = tempfile.mkdtemp(prefix="playlist-watch-")  # private (0700)
    path = os.path.join(d, "batch.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ops": ops}, f, ensure_ascii=False)
        out = cli("library", "batch", "--from-file", path, "--stop-on-error", "--format", "json")
    finally:
        try:
            os.remove(path)
            os.rmdir(d)
        except OSError:
            pass
    results = []
    for line in out.splitlines():
        try:
            results.append(json.loads(line))
        except ValueError:
            continue
    return results


def add_in_chunks(playlist_uri, uris):
    for i in range(0, len(uris), PAGE):
        res = run_batch([{"op": "playlist_add", "playlist_uri": playlist_uri, "track_uris": uris[i:i + PAGE]}])
        if not res or not res[0].get("ok", True):
            raise CliError("playlist_add failed: %s" % (res[:1],))


def cmd_merge(args):
    entries = load_check(args.selection)  # the merge code below ties the user's yes to the exact tracks
    w = load_watch()
    chosen = pick(entries, args.source)
    sources = [e for e in chosen if e["status"] == "changed" and e["added"]]
    if args.source and len(sources) != len(chosen):
        die("A playlist you named has no added tracks in this check.")
    if not sources:
        die("There are no added tracks in this check, so there is nothing to merge.")
    tracks, seen = [], set()
    for e in sources:
        for t in e["added"]:
            if t["uri"] not in seen:
                seen.add(t["uri"])
                tracks.append(t)
    plan = {"to": args.to, "from": sorted(e["uri"] for e in sources)}
    target = None
    skipped_existing = will_remove = 0
    if args.to == "queue":
        if args.target or args.new_name or args.mode != "append":
            die("The queue takes added tracks at its end only: no --target, --new-name or --mode.")
        limit = args.max_tracks or 50
    else:
        if bool(args.target) == bool(args.new_name):
            die("Give exactly one of --target (an existing playlist you own) or --new-name (a new private playlist).")
        limit = args.max_tracks or 200
        if args.new_name:
            if args.mode == "overwrite":
                die("A new playlist has nothing to overwrite: use --mode append.")
            name = args.new_name.strip()[:100]
            if not name:
                die("The playlist needs a name.")
            plan.update(new_name=name, mode="append")
        else:
            turi = resolve(args.target)
            if turi in {x["uri"] for x in w["selected"]}:
                die("%s is on your watch list, so merging into it would show up as a change. Stop watching it first (`forget`), or pick another target." % turi, 4)
            if turi in plan["from"]:
                die("The target is one of the playlists the tracks come from.", 4)
            try:
                target = read_live(turi)
            except CliError as e:
                die("Could not read the target %s: %s" % (turi, e), 5)
            if not target["is_owned"]:
                die("%s is not your playlist, so it cannot be written to." % turi, 4)
            plan.update(target=turi, mode=args.mode)
            if args.mode == "append":
                have = {t["uri"] for t in target["tracks"]}
                skipped_existing = sum(1 for t in tracks if t["uri"] in have)
                tracks = [t for t in tracks if t["uri"] not in have]
            else:
                will_remove = len(target["tracks"])
                if will_remove > MAX_OVERWRITE:
                    die("Overwriting a playlist of %d tracks is not supported (limit %d)." % (will_remove, MAX_OVERWRITE), 4)
    plan["tracks"] = [t["uri"] for t in tracks]
    code = confirm_code(plan)
    summary = {"to": args.to, "mode": plan.get("mode"), "target": plan.get("target"), "new_name": plan.get("new_name"),
               "from": [{"name": e["name"], "added": len(e["added"])} for e in sources], "tracks_to_add": len(tracks),
               "skipped_already_in_target": skipped_existing, "will_remove_from_target": will_remove,
               "target_name": target["name"] if target else None, "confirm_code": code,
               "sample": ["%s - %s" % (", ".join(t["artists"]), t["name"]) for t in tracks[:40]]}
    if not args.confirm:
        say(dict(summary, executed=False))
        return
    if args.confirm != code:
        die("The confirmation code does not match this merge. Run `merge` without --confirm again, show the user the plan, "
            "ask again, and use the code from that run.", 2)
    if not tracks:
        die("Nothing to add: every track is already in the target.")
    if len(tracks) > limit:
        die("%d tracks is over the limit of %d for this action. Tell the user, and raise --max-tracks only if they agree." % (len(tracks), limit), 4)
    if args.mode == "overwrite" and args.to == "playlist" and args.confirm_overwrite != plan["target"]:
        die("An overwrite needs --confirm-overwrite %s, after the user has said yes to replacing %d tracks in %r." % (plan["target"], will_remove, target["name"]), 2)
    ensure_data()
    uris = plan["tracks"]
    result = {}
    if args.to == "queue":
        added = 0
        try:
            for u in uris:
                cli("queue", "add", u)
                added += 1
        except CliError as e:
            print("error: the queue stopped accepting tracks after %d of %d: %s" % (added, len(uris), e), file=sys.stderr)
            say({"added": added, "requested": len(uris)})
            sys.exit(8)
        result = {"added_to_queue": added}
    elif args.new_name:
        try:
            res = run_batch([{"op": "playlist_create", "name": plan["new_name"]}])
            new_uri = (res[0].get("result") or {}).get("uri") if res else None
            if not new_uri or not PLAYLIST_URI.fullmatch(str(new_uri)):
                die("The playlist could not be created: %s" % (res[:1],), 8)
            description = clean_text("playlist-watch: new tracks from " + ", ".join(clean_text(e["name"], 60) for e in sources), 300)
            add_in_chunks(new_uri, uris)
            run_batch([{"op": "playlist_update", "playlist_uri": new_uri, "description": description, "public": False}])
            count = cli_json("playlist", "get", new_uri, "--no-tracks").get("track_count")
        except CliError as e:
            die("Spotify refused: %s" % e, 8)
        result = {"created": plan["new_name"], "uri": new_uri, "private": True, "tracks_added": len(uris), "track_count_in_spotify": count}
        register_target(w, new_uri)
    else:
        backup = None
        if args.mode == "overwrite":
            backup = os.path.join(data_dir(), "backups", "%s-%s.json" % (now().replace(":", ""), plan["target"].split(":")[2]))
            write_json(backup, {"uri": target["uri"], "name": target["name"], "saved_at": now(), "tracks": target["tracks"]})
        try:
            if args.mode == "overwrite" and target["tracks"]:
                n = len(target["tracks"])
                res = run_batch([{"op": "playlist_remove", "playlist_uri": plan["target"], "positions": list(range(n))}])
                if not res or not res[0].get("ok", True):
                    die("Spotify refused to empty the playlist: %s" % (res[:1],), 8)
            add_in_chunks(plan["target"], uris)
            count = cli_json("playlist", "get", plan["target"], "--no-tracks").get("track_count")
        except CliError as e:
            die("Spotify refused: %s.%s" % (e, " The previous contents are saved in %s." % backup if backup else ""), 8)
        result = {"target": plan["target"], "mode": args.mode, "tracks_added": len(uris), "track_count_in_spotify": count,
                  "backup": backup, "skipped_already_in_target": skipped_existing}
        register_target(w, plan["target"])
    result["baseline_moved_for"] = [e["name"] for e in sources if accept(e) == "baseline moved"]
    say(result)


def register_target(w, uri):
    """A playlist we merge into is not watched: its own edits must not show up as changes."""
    if uri not in w["targets"]:
        w["targets"].append(uri)
    delete_snapshot(uri)
    save_watch(w)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("watch")
    sp.add_argument("--add", action="append", metavar="PLAYLIST", help="name, spotify:playlist: URI or open.spotify.com link")
    sp.add_argument("--all-library", choices=["on", "off"])
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("check")
    sp.add_argument("--out", required=True, help="where to write the selection (a private folder)")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("seen")
    sp.add_argument("--selection", required=True)
    sp.add_argument("--confirm", required=True, metavar="CODE", help="the confirm_code of the check the user saw")
    sp.add_argument("--playlist", action="append", metavar="PLAYLIST", help="only these playlists of the check (default: all)")
    sp.set_defaults(fn=cmd_seen)

    sp = sub.add_parser("merge")
    sp.add_argument("--selection", required=True)
    sp.add_argument("--from", dest="source", action="append", metavar="PLAYLIST", help="only the added tracks of these playlists (default: all that changed)")
    sp.add_argument("--to", choices=["queue", "playlist"], required=True)
    sp.add_argument("--target", help="an existing playlist you own")
    sp.add_argument("--new-name", help="create a new private playlist with this name")
    sp.add_argument("--mode", choices=["append", "overwrite"], default="append")
    sp.add_argument("--confirm", metavar="CODE", help="the confirm_code of the plan the user agreed to (without it: only a plan)")
    sp.add_argument("--confirm-overwrite", metavar="URI", help="for an overwrite: the target's URI, after the user said yes to replacing it")
    sp.add_argument("--max-tracks", type=int, default=0, help="limit (default 50 for the queue, 200 for a playlist)")
    sp.set_defaults(fn=cmd_merge)

    sp = sub.add_parser("forget")
    sp.add_argument("ref", nargs="?", help="a watched playlist")
    sp.add_argument("--all", action="store_true", help="delete all local data")
    sp.add_argument("--include-backups", action="store_true")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_forget)

    sp = sub.add_parser("status")
    sp.set_defaults(fn=cmd_status)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
