#!/usr/bin/env python3
"""Helper for the playlist-vennify skill: compare playlists, then queue or save the result.

Standard library only (Python 3.9+). It talks to the Spotify desktop app through its
`spotify_cli`. Comparing only reads. Adding to the queue and creating a playlist change
the user's Spotify and need the confirmation code of the exact list that was shown.

Commands:
  compare   --playlist NAME|URI|URL (2 or more) --select overlap|unique|union|atleast
            [--min N] [--only A] [--match exact|song] [--order playlist|shuffle|interleave]
            --out FILE                      read the playlists, compare, write the selection
  queue     --selection FILE --confirm CODE [--position end|front] [--max-tracks 50] [--dry-run]
  playlist  --selection FILE --confirm CODE [--name NAME] [--max-tracks 200] [--dry-run]
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")
PLAYLIST_URI = re.compile(r"spotify:playlist:[A-Za-z0-9]+")
PLAYLIST_URL = re.compile(r"https://open\.spotify\.com/(?:intl-[a-z]+/)?playlist/([A-Za-z0-9]+)(?:[?#].*)?")
LABELS = "ABCDEFGHIJ"
MAX_PLAYLISTS = len(LABELS)
PAGE = 100


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


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


# ---------------------------------------------------------------- finding and reading playlists

def resolve(ref):
    """A playlist URI, an open.spotify.com link, or the name of a playlist in the user's library -> URI."""
    ref = ref.strip()
    if not ref:
        die("A playlist name is empty.")
    if PLAYLIST_URI.fullmatch(ref):
        return ref
    m = PLAYLIST_URL.fullmatch(ref)
    if m:
        return "spotify:playlist:" + m.group(1)
    try:
        items = cli_json("library", "list", "--type", "playlist", "--limit", "2000").get("items", [])
    except CliError as e:
        die("Could not list your playlists: %s" % e, 5)
    named = [(i.get("name", ""), i["uri"]) for i in items if i.get("uri") and PLAYLIST_URI.fullmatch(i["uri"]) and i.get("name")]
    want = ref.casefold()
    hits = [(n, u) for n, u in named if n.casefold() == want]
    if not hits:
        hits = [(n, u) for n, u in named if want in n.casefold()]
    hits = list(dict.fromkeys(hits))
    if len(hits) == 1:
        return hits[0][1]
    if not hits:
        die("No playlist named %r in your library. It has to be saved or followed in Spotify first." % ref, 7)
    print(json.dumps({"ambiguous": ref, "candidates": [{"name": n, "uri": u} for n, u in hits[:20]]}, ensure_ascii=False))
    die("%d playlists match %r. Ask the user which one, and use its URI." % (len(hits), ref), 6)


def read_playlist(uri):
    try:
        d = cli_json("playlist", "get", uri)
        tracks = list(d.get("tracks", []))
        total = d.get("track_count")
        # A long playlist could come back in part: ask for the rest page by page.
        while isinstance(total, int) and len(tracks) < total and len(tracks) < 20000:
            page = cli_json("playlist", "get", uri, "--limit", str(PAGE), "--offset", str(len(tracks))).get("tracks", [])
            if not page:
                break  # tracks that are no longer available are counted but not returned
            tracks.extend(page)
    except CliError as e:
        die("Could not read %s: %s. It is probably not in your library, or private. Save or follow it in Spotify first." % (uri, e), 5)
    d["tracks"] = tracks
    return d


def load_playlists(refs):
    if not 2 <= len(refs) <= MAX_PLAYLISTS:
        die("Give between 2 and %d playlists." % MAX_PLAYLISTS)
    uris = [resolve(r) for r in refs]
    if len(set(uris)) != len(uris):
        die("The same playlist is given twice.")
    pls = []
    for k, uri in enumerate(uris):
        d = read_playlist(uri)
        seen, tracks, skipped, dups = set(), [], 0, 0
        for t in d["tracks"]:
            u = str(t.get("uri", ""))
            if not TRACK_URI.fullmatch(u):
                skipped += 1  # episodes, local files, anything that is not a track
                continue
            if u in seen:
                dups += 1
                continue
            seen.add(u)
            tracks.append({"uri": u, "name": str(t.get("name", "")), "artists": [str(a) for a in t.get("artists", [])],
                           "album": str(t.get("album", ""))})
        pls.append({"label": LABELS[k], "name": str(d.get("name") or uri), "uri": uri, "owner": d.get("owner"),
                    "tracks": tracks, "skipped_not_tracks": skipped, "duplicates_in_playlist": dups,
                    "declared_track_count": d.get("track_count")})
    return pls


# ---------------------------------------------------------------- comparing

def song_key(t):
    """artist + title without (feat. ...), [...], and '- Remastered' style endings."""
    def clean(text):
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]+", " ", text).strip()
    title = re.sub(r"\(.*?\)|\[.*?\]|\s-\s.*$", "", t["name"])
    artist = t["artists"][0] if t["artists"] else ""
    return (clean(artist), clean(title))


def group(pls, match):
    """Every distinct track (or song) once, with the labels of the playlists it is in."""
    entries = {}
    for pl in pls:
        for t in pl["tracks"]:
            k = t["uri"] if match == "exact" else song_key(t)
            e = entries.get(k)
            if e is None:
                e = entries[k] = dict(t, **{"in": [], "other_versions": []})
            if pl["label"] not in e["in"]:
                e["in"].append(pl["label"])
            if t["uri"] != e["uri"] and t["uri"] not in e["other_versions"]:
                e["other_versions"].append(t["uri"])
    return list(entries.values())


def select_entries(entries, labels, which, minimum, only):
    n = len(labels)
    if which == "overlap":
        keep = lambda e: len(e["in"]) == n
    elif which == "unique":
        if only is not None and only not in labels:
            die("--only %s: there is no such playlist. Labels are %s." % (only, ", ".join(labels)))
        keep = lambda e: len(e["in"]) == 1 and (only is None or e["in"][0] == only)
    elif which == "union":
        keep = lambda e: True
    else:  # atleast
        if not 2 <= minimum <= n:
            die("--min must be between 2 and %d for %d playlists." % (n, n))
        keep = lambda e: len(e["in"]) >= minimum
    return [e for e in entries if keep(e)]


def order_entries(chosen, how, seed):
    if how == "shuffle":
        chosen = list(chosen)
        random.Random(seed).shuffle(chosen)
        return chosen
    if how == "interleave":
        lanes = {}
        for e in chosen:
            lanes.setdefault(e["in"][0], []).append(e)
        out, lists = [], [lanes[k] for k in sorted(lanes)]
        while any(lists):
            for lane in lists:
                if lane:
                    out.append(lane.pop(0))
        return out
    return list(chosen)


def confirm_code(uris, extra=""):
    """Fingerprint of the exact list (and order) the user was shown."""
    return hashlib.sha256(json.dumps({"uris": uris, "extra": extra}).encode("utf-8")).hexdigest()[:12]


def write_private(path, data):
    if os.path.islink(path):
        die("%s is a symbolic link; refusing to write through it." % path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cmd_compare(args):
    refs = args.playlist or []
    pls = load_playlists(refs)
    labels = [p["label"] for p in pls]
    entries = group(pls, args.match)
    regions = {}
    for e in entries:
        regions["&".join(e["in"])] = regions.get("&".join(e["in"]), 0) + 1
    chosen = order_entries(select_entries(entries, labels, args.select, args.min, args.only), args.order, args.seed)
    uris = [e["uri"] for e in chosen]
    code = confirm_code(uris)
    merged = [e for e in entries if e["other_versions"]]
    selection = {
        "select": args.select, "min": args.min if args.select == "atleast" else None, "only": args.only,
        "match": args.match, "order": args.order, "confirm_code": code,
        "playlists": [{k: v for k, v in p.items() if k != "tracks"} | {"tracks": len(p["tracks"])} for p in pls],
        "regions": regions, "tracks": chosen,
    }
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    write_private(args.out, selection)
    print(json.dumps({
        "playlists": selection["playlists"], "regions": regions, "match": args.match,
        "selected": len(chosen), "select": args.select, "confirm_code": code, "selection_file": os.path.abspath(args.out),
        "songs_matched_across_versions": len(merged),
        "sample": ["%s  %s - %s" % ("&".join(e["in"]), ", ".join(e["artists"]), e["name"]) for e in chosen[:15]],
    }, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- changing the user's Spotify

def load_selection(path, confirm, limit, what):
    try:
        with open(path, encoding="utf-8") as f:
            sel = json.load(f)
    except (OSError, ValueError) as e:
        die("Cannot read the selection file: %s" % e)
    uris = [t.get("uri", "") for t in sel.get("tracks", [])]
    for u in uris:
        if not TRACK_URI.fullmatch(u):
            die("The selection contains an invalid track URI: %r" % u)
    if len(set(uris)) != len(uris):
        die("The selection contains a track twice.")
    if not uris:
        die("The selection is empty: there is nothing to %s." % what)
    if confirm != confirm_code(uris):
        die("The confirmation code does not match this list. Run `compare` again, show the user the list, "
            "ask again, and use the code from that run.", 2)
    if len(uris) > limit:
        die("%d tracks is over the limit of %d for this action. Tell the user, and raise --max-tracks only if they agree." % (len(uris), limit), 4)
    return sel, uris


def queue_entries():
    return cli_json("queue", "list").get("next_tracks", [])


def cmd_queue(args):
    sel, uris = load_selection(args.selection, args.confirm, args.max_tracks, "queue")
    before = queue_entries()
    mine = sum(1 for e in before if e.get("provider") == "queue")  # the user's own queued tracks
    if args.dry_run:
        print(json.dumps({"would_add": len(uris), "position": args.position, "already_in_queue": mine}))
        return
    added = 0
    try:
        for u in uris:
            cli("queue", "add", u)
            added += 1
    except CliError as e:
        print("error: the queue stopped accepting tracks after %d of %d: %s" % (added, len(uris), e), file=sys.stderr)
        print(json.dumps({"added": added, "requested": len(uris)}))
        sys.exit(8)
    moved = False
    if args.position == "front" and mine > 0:
        after = queue_entries()
        if [e.get("uri") for e in after[mine:mine + added]] == uris[:added]:
            for i in range(added):
                cli("queue", "move", str(mine + i), str(i))
            moved = True
        else:
            print("warning: the queue did not look as expected, so the tracks stay at the end of it.", file=sys.stderr)
    elif args.position == "front":
        moved = True  # the queue was empty: the end is the front
    print(json.dumps({"added": added, "position": "front" if moved else "end", "your_own_queued_tracks_before": mine}))


def run_batch(ops):
    """Run `library batch` with a manifest in a private temporary folder. Returns the parsed result lines."""
    d = tempfile.mkdtemp(prefix="vennify-")  # private (0700)
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


def default_name(sel):
    names = [p["name"] for p in sel["playlists"]]
    word = {"overlap": "in common", "unique": "unique", "union": "combined", "atleast": "in %s or more" % sel.get("min")}[sel["select"]]
    return ("Vennify - %s - %s" % (" + ".join(names), word))[:100]


def cmd_playlist(args):
    sel, uris = load_selection(args.selection, args.confirm, args.max_tracks, "save")
    name = (args.name or default_name(sel)).strip()[:100]
    if not name:
        die("The playlist needs a name.")
    if args.dry_run:
        print(json.dumps({"would_create": name, "private": True, "tracks": len(uris)}, ensure_ascii=False))
        return
    try:
        res = run_batch([{"op": "playlist_create", "name": name}])
        new_uri = (res[0].get("result") or {}).get("uri") if res else None
        if not new_uri or not PLAYLIST_URI.fullmatch(str(new_uri)):
            die("The playlist could not be created: %s" % (res[:1],), 8)
        description = "playlist-vennify: %s of %s" % (sel["select"], ", ".join(p["name"] for p in sel["playlists"]))
        run_batch([{"op": "playlist_add", "playlist_uri": new_uri, "track_uris": uris},
                   {"op": "playlist_update", "playlist_uri": new_uri, "description": description[:300], "public": False}])
        check = cli_json("playlist", "get", new_uri, "--no-tracks")
    except CliError as e:
        die("Spotify refused: %s" % e, 8)
    print(json.dumps({"created": name, "uri": new_uri, "private": True, "tracks_added": len(uris),
                      "track_count_in_spotify": check.get("track_count")}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("compare")
    sp.add_argument("--playlist", action="append", required=True, help="name, spotify:playlist: URI or open.spotify.com link; give 2 or more")
    sp.add_argument("--select", choices=["overlap", "unique", "union", "atleast"], required=True)
    sp.add_argument("--min", type=int, default=2, help="with atleast: in how many playlists at least")
    sp.add_argument("--only", help="with unique: only the tracks unique to this label (A, B, ...)")
    sp.add_argument("--match", choices=["exact", "song"], default="exact", help="exact = same Spotify track; song = same artist and title")
    sp.add_argument("--order", choices=["playlist", "shuffle", "interleave"], default="playlist")
    sp.add_argument("--seed", type=int, default=None, help="makes shuffle repeatable (for tests)")
    sp.add_argument("--out", required=True, help="where to write the selection (a private folder)")
    sp.set_defaults(fn=cmd_compare)

    sp = sub.add_parser("queue")
    sp.add_argument("--selection", required=True)
    sp.add_argument("--confirm", required=True, metavar="CODE", help="the confirm_code of the list the user agreed to")
    sp.add_argument("--position", choices=["end", "front"], default="end")
    sp.add_argument("--max-tracks", type=int, default=50)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_queue)

    sp = sub.add_parser("playlist")
    sp.add_argument("--selection", required=True)
    sp.add_argument("--confirm", required=True, metavar="CODE")
    sp.add_argument("--name", help="name of the new (private) playlist")
    sp.add_argument("--max-tracks", type=int, default=200)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_playlist)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
