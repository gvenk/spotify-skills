#!/usr/bin/env python3
"""Helper for the radio-dj skill: speech synthesis and the playback loop.

Standard library only (Python 3.9+). Speech comes from the operating system's own
voice, so nothing is downloaded or installed. Tested on macOS only.

Commands:
  synth    --script FILE --out-dir DIR [--language nl|en] [--os-voice NAME] [--one-voice]
           (text may contain {en:English name} to speak that part with an English voice)
  probe                                      print the raw now-playing JSON
  run      --tracks FILE [--mode duck|gap] [--duck-level 0.5] [--preview SECONDS]
                         [--volume 1.0] [--device NAME]
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import wave

TRACK_URI = re.compile(r"^spotify:track:[A-Za-z0-9]{22}$")


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- synthesis

MAC_VOICE_PREFERENCE = {"en": ["Samantha", "Daniel", "Karen", "Moira"], "nl": ["Xander", "Claire", "Ellen"]}


def mac_voice(language):
    """Name of an installed macOS voice for the language, so Dutch system settings
    do not make `say` read English text with a Dutch voice. None = system default."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    voices = []  # (name, locale)
    for line in out.splitlines():
        m = re.match(r"^(.*?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if m:
            voices.append((m.group(1).strip(), m.group(2)))
    names = [n for n, loc in voices if loc.startswith(language + "_")]

    def rank(n):
        quality = 0 if "premium" in n.lower() else 1 if "enhanced" in n.lower() else 2
        base = n.split(" (")[0]
        prefs = MAC_VOICE_PREFERENCE.get(language, [])
        return (quality, prefs.index(base) if base in prefs else len(prefs))

    # On a non-English system `say -v ?` lists "Xander (Nederlands (Nederland))": match on the base name.
    # Premium and Enhanced downloads win over the compact voices; multi-language entries
    # like "Eddy (Engels (VK))" come last.
    multi = [n for n in names if n.split(" (")[0] not in MAC_VOICE_PREFERENCE.get(language, []) and "(" in n
             and "premium" not in n.lower() and "enhanced" not in n.lower()]
    ordered = sorted((n for n in names if n not in multi), key=rank) + multi
    return ordered[0] if ordered else None


WINDOWS_CULTURE = {"en": "en-US", "nl": "nl-NL"}


def os_synth(text, out_path, language=None, voice=None):
    """Synthesize with the operating system voice. Returns True on success."""
    try:
        if sys.platform == "darwin":
            cmd = ["say", "-o", out_path, "--data-format=LEI16@22050"]
            voice = voice or os.environ.get("RADIO_DJ_OS_VOICE") or (mac_voice(language) if language else None)
            if voice:
                cmd += ["-v", voice]
            subprocess.run(cmd + ["--", text], check=True)
        elif os.name == "nt":
            # Text and paths travel in environment variables, never inside the command line.
            env = dict(os.environ, RADIO_TEXT=text, RADIO_OUT=out_path,
                       RADIO_CULTURE=WINDOWS_CULTURE.get(language or "", language or ""), RADIO_VOICE=voice or "")
            ps = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "try { if ($env:RADIO_VOICE) { $s.SelectVoice($env:RADIO_VOICE) } "
                  "elseif ($env:RADIO_CULTURE) { $s.SelectVoiceByHints('NotSet', 'NotSet', 0, "
                  "[System.Globalization.CultureInfo]::GetCultureInfo($env:RADIO_CULTURE)) } } "
                  "catch { [Console]::Error.WriteLine('No installed Windows voice for this language; using the default voice.') }; "
                  "$s.SetOutputToWaveFile($env:RADIO_OUT); $s.Speak($env:RADIO_TEXT); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, env=env)
        else:
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            if not exe:
                print("No speech engine found. Install espeak-ng (for example `sudo apt install espeak-ng`).", file=sys.stderr)
                return False
            cmd = [exe, "-w", out_path]
            voice = voice or language  # espeak voice names: "nl", "en"
            if voice:
                cmd += ["-v", voice]
            subprocess.run(cmd + ["--", text], check=True)
        return os.path.exists(out_path)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


SEGMENT = re.compile(r"\{([a-z]{2}):([^{}]*)\}")


def split_segments(text, default_language):
    """Split "Dutch text {en:English name} more Dutch" into [(language, text), ...].

    A single-language voice mangles names from another language, so tagged parts
    are spoken by a voice for their own language."""
    parts, pos = [], 0
    for m in SEGMENT.finditer(text):
        parts.append((default_language, text[pos:m.start()]))
        parts.append((m.group(1), m.group(2)))
        pos = m.end()
    parts.append((default_language, text[pos:]))
    cleaned = []
    for lang, chunk in parts:
        chunk = " ".join(chunk.replace("{", " ").replace("}", " ").split())
        if chunk:
            cleaned.append((lang, chunk))
    return cleaned


def join_wavs(paths, out_path, gap_s=0.08):
    """Concatenate wav files that share one format, with a short pause between them."""
    params, frames = None, []
    for p in paths:
        with wave.open(p, "rb") as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != params[:3]:
                return False  # different formats cannot be joined here
            frames.append(w.readframes(w.getnframes()))
    silence = b"\x00" * int(params.framerate * gap_s) * params.nchannels * params.sampwidth
    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        out.writeframes(silence.join(frames))
    return True


def synth_text(text, out_path, language, voice, one_voice=False):
    if one_voice:
        # No voice switching: the tags are dropped and everything is read by the main voice.
        text = SEGMENT.sub(lambda m: m.group(2), text)
    segments = split_segments(text, language)
    if not segments:
        return False
    if len(segments) == 1:
        return os_synth(segments[0][1], out_path, segments[0][0], voice if segments[0][0] == language else None)
    pieces = []
    for n, (lang, chunk) in enumerate(segments):
        piece = "%s.part%d.wav" % (out_path, n)
        # An explicit --os-voice applies to the main language only.
        if not os_synth(chunk, piece, lang, voice if lang == language else None):
            for p in pieces:
                os.remove(p)
            return False
        pieces.append(piece)
    ok = join_wavs(pieces, out_path)
    for p in pieces:
        os.remove(p)
    return ok


def cmd_synth(args):
    with open(args.script, encoding="utf-8") as f:
        items = json.load(f)  # [{"id": "01", "text": "..."}]
    os.makedirs(args.out_dir, exist_ok=True)
    result = []
    for item in items:
        iid = str(item["id"])
        if not re.match(r"^[A-Za-z0-9_-]+$", iid):
            die("Bad intro id %r" % iid)
        out = os.path.join(args.out_dir, iid + ".wav")
        ok = synth_text(str(item["text"]), out, args.language, args.os_voice, args.one_voice)
        result.append({"id": iid, "file": out if ok else None})
        print("%s %s" % ("ok " if ok else "FAIL", iid))
    with open(os.path.join(args.out_dir, "index.json"), "w") as f:
        json.dump(result, f, indent=2)
    if not all(r["file"] for r in result):
        sys.exit(5)


# ---------------------------------------------------------------- playback

def spotify_cli():
    path = os.environ.get("SPOTIFY_CLI_PATH")
    if path:
        return path
    if sys.platform == "darwin":
        return "/Applications/Spotify.app/Contents/MacOS/spotify_cli"
    return "spotify_cli.exe" if os.name == "nt" else "spotify_cli"


def cli(*cli_args, check=True):
    r = subprocess.run([spotify_cli()] + list(cli_args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("spotify_cli %s failed: %s" % (" ".join(cli_args), (r.stderr or r.stdout).strip()))
    return r.stdout


def now_playing():
    out = cli("now-playing", "--format", "json", check=False)
    try:
        return json.loads(out)
    except ValueError:
        return None


def read_state(np):
    """(uri, is_playing) from `now-playing --format json`, or None.

    The CLI reports only the track and whether it plays, not the position:
    {"currently_playing": {"description", "uri", "context_description", "is_playing"}}
    """
    cur = np.get("currently_playing") if isinstance(np, dict) else None
    if not isinstance(cur, dict):
        return None
    return cur.get("uri"), bool(cur.get("is_playing"))


def wav_seconds(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def play_wav(path):
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], check=False)
    elif os.name == "nt":
        env = dict(os.environ, RADIO_WAV=path)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(New-Object System.Media.SoundPlayer $env:RADIO_WAV).PlaySync()"], check=False, env=env)
    else:
        for exe, extra in (("paplay", []), ("aplay", ["-q"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"])):
            if shutil.which(exe):
                subprocess.run([exe] + extra + [path], check=False)
                return
        print("No audio player found (paplay, aplay, ffplay).", file=sys.stderr)


def cmd_probe(_args):
    np = now_playing()
    print(json.dumps(np, indent=2))
    print("parsed state (uri, is_playing):", read_state(np))


def cmd_run(args):
    with open(args.tracks, encoding="utf-8") as f:
        tracks = json.load(f)  # [{"uri","title","artist","duration_ms","intro": "path.wav" | null}]
    seen = set()
    for t in tracks:
        if not TRACK_URI.match(t["uri"]):
            die("Invalid track URI: %r" % t["uri"])
        if t["uri"] in seen:
            die("Track %r is in the list twice. Remove duplicates before the run." % t["uri"])
        seen.add(t["uri"])
        if not isinstance(t.get("duration_ms"), (int, float)) or t["duration_ms"] <= 0:
            die("Track %r has no duration_ms. Get it with `spotify_cli lookup <uri> --fields duration`." % t["uri"])
        t["_intro_s"] = wav_seconds(t["intro"]) if t.get("intro") else 0.0
    if not tracks:
        die("No tracks to play.")
    n = len(tracks)
    index_of = {t["uri"]: k for k, t in enumerate(tracks)}
    restore = args.volume
    current = [restore]

    def set_volume(level):
        cli("volume", "%.2f" % level, check=False)

    def fade(target, steps=4, seconds=0.6):
        """Move the volume to target in a few steps, like a radio fader."""
        start = current[0]
        for k in range(1, steps + 1):
            set_volume(start + (target - start) * k / steps)
            time.sleep(seconds / steps)
        current[0] = target

    def shutdown(*_):
        set_volume(restore)
        cli("pause", check=False)
        print("\nStopped. Volume restored.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    def intro_over_music(t):
        fade(args.duck_level)
        print("Intro: %s - %s" % (t["artist"], t["title"]))
        play_wav(t["intro"])
        fade(restore)

    # Spotify's own queue does the advancing, so a skip, a jump to the end and a natural
    # end all show up as a change of track. (`now-playing` reports pause, skip and end alike:
    # the same track, "not playing".) The script times the intros by the clock only.
    spoken = [not t.get("intro") for t in tracks]
    queued = [0]  # index of the last track put in the queue (or being played)

    def begin(k, first=False):
        """Bookkeeping when track k starts: seek for preview, queue the next track."""
        if args.preview and tracks[k]["duration_ms"] / 1000.0 > args.preview + 5:
            time.sleep(1.0)
            cli("seek", str(int(tracks[k]["duration_ms"] - args.preview * 1000)), check=False)
        if k + 1 < n and queued[0] < k + 1:
            cli("queue", "add", tracks[k + 1]["uri"])
            queued[0] = k + 1
        return (tracks[k]["duration_ms"] / 1000.0 - args.preview) if (args.preview and tracks[k]["duration_ms"] / 1000.0 > args.preview + 5) else 0.0

    set_volume(restore)
    # The intro of the first track goes before any music.
    if tracks[0].get("intro"):
        print("Intro: %s - %s" % (tracks[0]["artist"], tracks[0]["title"]))
        play_wav(tracks[0]["intro"])
    spoken[0] = True
    print("Playing 1/%d: %s - %s" % (n, tracks[0]["artist"], tracks[0]["title"]))
    cli(*(["play", tracks[0]["uri"]] + (["--device", args.device] if args.device else [])))
    queued[0] = 0
    i = 0
    elapsed = begin(0)
    last, idle_since = time.time(), None
    predicted_end = last + tracks[0]["duration_ms"] / 1000.0 - elapsed

    while True:
        time.sleep(1.0)
        now = time.time()
        state = read_state(now_playing())
        if state is None:
            continue
        uri, playing = state

        if uri != tracks[i]["uri"]:
            if uri in index_of:
                # The track changed: natural end, skip, jump to the end, or the user picked another one.
                j = index_of[uri]
                for k in range(min(i, j), j):
                    spoken[k] = True  # skipped past; their intros no longer apply
                i = j
                print("Playing %d/%d: %s - %s" % (i + 1, n, tracks[i]["artist"], tracks[i]["title"]))
                idle_since = None
                if not spoken[i]:
                    spoken[i] = True
                    intro_over_music(tracks[i])  # the track started before its intro: talk over the start
                elapsed = begin(i)
                # An intro that blocked us may have spanned the change: count the time the track already ran.
                elapsed = max(elapsed, min(max(now - predicted_end, 0.0), 8.0))
                last = time.time()
                predicted_end = last + tracks[i]["duration_ms"] / 1000.0 - elapsed
                continue
            # Something outside the playlist is playing.
            if i == n - 1 and elapsed >= tracks[i]["duration_ms"] / 1000.0 - 3:
                cli("pause", check=False)  # Spotify's autoplay after the last track: keep the silence
            else:
                print("Another track started outside the playlist; stopping the show.")
            break

        if playing:
            elapsed += now - last
            idle_since = None
        else:
            if idle_since is None:
                idle_since = now
            # After the last track Spotify just reports "not playing", like a pause. The clock says
            # whether it ran to its end; a skip on the last track is caught by the shorter wait.
            if i == n - 1 and elapsed >= tracks[i]["duration_ms"] / 1000.0 - 3:
                break
            if now - idle_since > (10 if i == n - 1 else 3 * 3600):
                break
        last = now

        remaining = tracks[i]["duration_ms"] / 1000.0 - elapsed
        if playing:
            predicted_end = now + remaining
        if i == n - 1:
            if playing and remaining <= -2:
                break
            continue
        nxt = tracks[i + 1]
        if playing and not spoken[i + 1]:
            if args.mode == "duck" and remaining <= nxt["_intro_s"] + 1.7:
                spoken[i + 1] = True
                intro_over_music(nxt)
                last = time.time()
            elif args.mode == "gap" and remaining <= 1.5:
                spoken[i + 1] = True
                cli("pause", check=False)
                print("Intro: %s - %s" % (nxt["artist"], nxt["title"]))
                play_wav(nxt["intro"])
                cli("next", check=False)  # starts the queued track, also from a pause
                last = time.time()

    print("Done.")
    set_volume(restore)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("synth")
    sp.add_argument("--script", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--language", help="two-letter language code (nl, en, ...); picks a voice for it")
    sp.add_argument("--os-voice", help="exact voice name from your system (macOS: `say -v ?`)")
    sp.add_argument("--one-voice", action="store_true", help="read {xx:name} parts with the main voice too (no voice switching)")
    sp.set_defaults(fn=cmd_synth)
    sp = sub.add_parser("probe"); sp.set_defaults(fn=cmd_probe)
    sp = sub.add_parser("run")
    sp.add_argument("--tracks", required=True)
    sp.add_argument("--mode", choices=["duck", "gap"], default="duck")
    sp.add_argument("--duck-level", type=float, default=0.5, help="music level (0.0-1.0) while the host talks")
    sp.add_argument("--preview", type=float, default=0, help="test aid: start each track this many seconds before its end")
    sp.add_argument("--volume", type=float, default=1.0, help="level (0.0-1.0) to restore after ducking")
    sp.add_argument("--device", help="device name or id from `spotify_cli devices list`")
    sp.set_defaults(fn=cmd_run)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
