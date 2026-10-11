#!/usr/bin/env python3
"""Helper for the radio-dj skill: speech synthesis and the playback loop.

Standard library only (Python 3.9+). Speech comes from the operating system's own
voice by default, so nothing is downloaded or installed. Optionally a cloud voice
(OpenAI) can be used: that sends the intro text to OpenAI and needs --accept-cloud
and the key in the environment variable OPENAI_API_KEY. Tested on macOS only.

Commands:
  synth    --script FILE --out-dir DIR [--language nl|en] [--os-voice NAME] [--one-voice]
           [--engine os|openai --accept-cloud [--tts-voice NAME] [--tts-model NAME]]
  estimate --script FILE [--engine openai]   what a cloud voice would send and roughly cost
  voices   [--language nl]                   list the voices that can be chosen
  sample   --out-dir DIR [--language nl] [--engine os|openai] [--voice NAME ...] [--play]
           make (and play) one short line in several voices, to choose from
           (text may contain {en:English name} to speak that part with an English voice)
  probe                                      print the raw now-playing JSON
  run      --tracks FILE [--mode duck|gap] [--duck-level 0.5] [--preview SECONDS]
                         [--volume 1.0] [--device NAME]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")  # used with fullmatch (a "$" would accept a trailing newline)


def refuse_symlink(path):
    """Do not write through a link that someone may have planted in a shared folder."""
    if os.path.islink(path):
        die("%s is a symbolic link; refusing to write through it. Use a new private folder." % path)


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def child_env(**extra):
    """Environment for the programs this script starts (say, afplay, spotify_cli, ...).

    OPENAI_API_KEY is only for this process: none of those programs needs it."""
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    env.update(extra)
    return env


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- synthesis

MAC_VOICE_PREFERENCE = {"en": ["Samantha", "Daniel", "Karen", "Moira"], "nl": ["Xander", "Claire", "Ellen"]}


def mac_voice_candidates(language):
    """Installed macOS voices for the language, best first, so Dutch system settings
    do not make `say` read English text with a Dutch voice."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True, env=child_env()).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    voices = []  # (name, locale)
    for line in out.splitlines():
        m = re.match(r"^(.*?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if m:
            voices.append((m.group(1).strip(), m.group(2)))
    names = list(dict.fromkeys(n for n, loc in voices if loc.startswith(language + "_")))  # `say` lists some twice

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
    return sorted((n for n in names if n not in multi), key=rank) + multi


def mac_voice(language):
    candidates = mac_voice_candidates(language)
    return candidates[0] if candidates else None


WINDOWS_CULTURE = {"en": "en-US", "nl": "nl-NL"}


def os_synth(text, out_path, language=None, voice=None):
    """Synthesize with the operating system voice. Returns True on success."""
    try:
        if sys.platform == "darwin":
            cmd = ["say", "-o", out_path, "--data-format=LEI16@22050"]
            voice = voice or os.environ.get("RADIO_DJ_OS_VOICE") or (mac_voice(language) if language else None)
            if voice:
                cmd += ["-v", voice]
            subprocess.run(cmd + ["--", text], check=True, env=child_env())
        elif os.name == "nt":
            # Text and paths travel in environment variables, never inside the command line.
            env = child_env(RADIO_TEXT=text, RADIO_OUT=out_path,
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
            subprocess.run(cmd + ["--", text], check=True, env=child_env())
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


# ---------------------------------------------------------------- cloud voice (OpenAI)

OPENAI_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "marin"
OPENAI_SPEED = 1.2  # a little brisker than normal speech, like a radio host
# Planning numbers for `estimate` only, not a price list. Check the provider's pricing page.
EST_CHARS_PER_MINUTE = 900
EST_USD_PER_MINUTE = 0.015
MAX_CHARS_PER_REQUEST = 3000
MAX_RESPONSE_BYTES = 40 * 1024 * 1024  # longer than any intro; protects memory from a misbehaving server
LANGUAGE_NAMES = {"nl": "Dutch", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian"}


def plain_text(text):
    """Intro text for a cloud voice: the {xx:name} tags are dropped, one multilingual voice reads it all."""
    return " ".join(SEGMENT.sub(lambda m: m.group(2), text).replace("{", " ").replace("}", " ").split())


def openai_url():
    override = os.environ.get("RADIO_DJ_OPENAI_URL")  # for tests only
    if not override:
        return OPENAI_URL
    if os.environ.get("RADIO_DJ_TEST") != "1":
        die("RADIO_DJ_OPENAI_URL is only for tests and needs RADIO_DJ_TEST=1.")
    if urllib.parse.urlparse(override).hostname not in ("127.0.0.1", "localhost", "::1"):
        die("RADIO_DJ_OPENAI_URL may only point to this computer (it exists for tests).")
    return override


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # never forward the key to another address


def error_code(e):
    """The error code from an OpenAI error answer (for example insufficient_quota), never the message."""
    try:
        err = json.loads(e.read(4000).decode("utf-8", "replace")).get("error", {})
        code = str(err.get("code") or err.get("type") or "")
    except (ValueError, AttributeError, OSError):
        return ""
    return code if re.match(r"^[A-Za-z0-9_.-]{1,60}$", code) else ""


def fix_wav_sizes(data):
    """OpenAI streams its wav: the header can hold a placeholder size (0xFFFFFFFF). Write the real sizes."""
    i = data.find(b"data")
    if data[:4] != b"RIFF" or i < 12 or len(data) < i + 8:
        return data
    real = len(data) - (i + 8)
    real -= real % 2  # whole 16-bit samples
    return data[:4] + struct.pack("<I", i + 8 + real - 8) + data[8:i + 4] + struct.pack("<I", real) + data[i + 8:i + 8 + real]


def openai_synth(text, out_path, voice, model, instructions, key, speed=None):
    """Ask OpenAI for speech as a wav file. Returns True on success. The key is never printed."""
    payload = {"model": model, "input": text, "voice": voice, "response_format": "wav", "instructions": instructions}
    if speed:
        payload["speed"] = speed
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(openai_url(), data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    delay, data = 2.0, None
    for attempt in range(4):
        try:
            with opener.open(req, timeout=90) as resp:
                data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                print("OpenAI speech failed: the answer was unexpectedly large", file=sys.stderr)
                return False
            break
        except urllib.error.HTTPError as e:
            code = error_code(e)
            no_credit = any(w in code for w in ("quota", "credit", "budget", "billing"))  # retrying cannot help
            if e.code in (429, 500, 502, 503, 504) and attempt < 3 and not no_credit:
                try:
                    wait = float(e.headers.get("Retry-After") or delay)
                except ValueError:
                    wait = delay
                time.sleep(min(wait, 30))
                delay *= 2
                continue
            reason = {401: "the key was rejected (check OPENAI_API_KEY)",
                      403: "the key is not allowed to use this model or voice (check its permissions)",
                      429: "rate limit or quota reached"}.get(e.code, "HTTP %d" % e.code)
            if no_credit:
                reason = "no credit left on the account, or the project's budget is used up (add credit or raise the budget)"
            print("OpenAI speech failed: %s%s" % (reason, " [%s]" % code if code else ""), file=sys.stderr)
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            print("OpenAI speech failed: no connection (%s)" % e.__class__.__name__, file=sys.stderr)
            return False
    if not data or data[:4] != b"RIFF":
        print("OpenAI speech failed: the answer was not a wav file", file=sys.stderr)
        return False
    with open(out_path, "wb") as f:
        f.write(fix_wav_sizes(data))
    try:
        wav_seconds(out_path)
    except (wave.Error, EOFError):
        os.remove(out_path)
        print("OpenAI speech failed: the wav file is damaged", file=sys.stderr)
        return False
    return True


def check_speed(speed):
    if speed is not None and not 0.5 <= speed <= 2.0:
        die("--tts-speed must be between 0.5 and 2.0 (1.0 is normal).")


def default_instructions(language):
    name = LANGUAGE_NAMES.get(language or "", language or "the language of the text")
    return ("Speak as a high-energy, upbeat radio host introducing a song you love: lively and excited, "
            "with a quick, bouncy tempo, bright pitch and punchy emphasis, never flat or slow. Speak %s. "
            "Names and titles in another language are pronounced the way speakers of that language say them." % name)


def consent_hash(texts, language, args):
    """Fingerprint of everything that would be sent, so a yes covers exactly what was shown."""
    payload = {"engine": "openai", "model": args.tts_model, "voice": args.tts_voice, "speed": args.tts_speed,
               "instructions": args.tts_instructions or default_instructions(language), "texts": texts}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def cmd_estimate(args):
    with open(args.script, encoding="utf-8") as f:
        items = json.load(f)
    texts = [plain_text(str(i["text"])) for i in items]
    chars = sum(len(t) for t in texts)
    minutes = chars / float(EST_CHARS_PER_MINUTE)
    print(json.dumps({
        "engine": args.engine, "model": args.tts_model, "voice": args.tts_voice, "speed": args.tts_speed,
        "style_instructions_sent": args.tts_instructions or default_instructions(args.language),
        "sends_to": "api.openai.com", "sends": "the intro texts and the style instructions above (no Spotify account data)",
        "consent_code": consent_hash(texts, args.language, args),
        "intros": len(items), "characters": chars, "max_characters": args.max_chars,
        "over_max": chars > args.max_chars,
        "approx_minutes_of_speech": round(minutes, 1),
        "approx_cost_usd": round(minutes * EST_USD_PER_MINUTE, 3),
        "note": "A rough estimate from planning numbers. Check the provider's current pricing.",
        "requires": "the key in the environment variable OPENAI_API_KEY, and --accept-cloud <consent_code> on synth, "
                    "run with the same options as this estimate (language, voice, speed, style)",
        "ai_voice": "The provider's rules require telling listeners that the voice is AI-generated.",
    }, indent=2))


def cmd_synth(args):
    with open(args.script, encoding="utf-8") as f:
        items = json.load(f)  # [{"id": "01", "text": "..."}]
    private_dir(args.out_dir)
    key = None
    check_speed(args.tts_speed)
    if args.engine == "openai":
        if not args.accept_cloud:
            die("Refusing to send text to a cloud service without --accept-cloud <consent_code>. Run `estimate` with "
                "the same options, tell the user what is sent and what it costs, ask for a yes, and pass the "
                "consent_code from the estimate.", 2)
        texts = [plain_text(str(i["text"])) for i in items]
        if args.accept_cloud != consent_hash(texts, args.language, args):
            die("The consent code does not match what would be sent now (texts, voice, speed or style changed). "
                "Run `estimate` again with these options and ask the user again.", 2)
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            die("OPENAI_API_KEY is not set. Set it in the shell that starts the agent (never paste it in the chat).", 3)
        total = sum(len(t) for t in texts)
        if total > args.max_chars:
            die("The intros have %d characters, over the limit of %d (--max-chars). Shorten them or raise the limit "
                "after telling the user the cost." % (total, args.max_chars), 4)
    result = []
    for item in items:
        iid = str(item["id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", iid):
            die("Bad intro id %r" % iid)
        out = os.path.join(args.out_dir, iid + ".wav")
        refuse_symlink(out)
        raw, ok, label = str(item["text"]), False, "ok "
        if args.engine == "openai":
            text = plain_text(raw)
            if text and len(text) <= MAX_CHARS_PER_REQUEST:
                ok = openai_synth(text, out, args.tts_voice, args.tts_model,
                                  args.tts_instructions or default_instructions(args.language), key, args.tts_speed)
            if not ok and not args.no_fallback:
                ok, label = synth_text(raw, out, args.language, args.os_voice, args.one_voice), "FALLBACK (computer voice)"
        else:
            ok = synth_text(raw, out, args.language, args.os_voice, args.one_voice)
        result.append({"id": iid, "file": out if ok else None})
        print("%s %s" % (label if ok else "FAIL", iid))
    refuse_symlink(os.path.join(args.out_dir, "index.json"))
    with open(os.path.join(args.out_dir, "index.json"), "w") as f:
        json.dump(result, f, indent=2)
    if not all(r["file"] for r in result):
        sys.exit(5)


# ---------------------------------------------------------------- voice samples

SAMPLE_TEXT = {
    "nl": "Welkom bij de show! Hier komt een gloednieuw nummer van Florence Road.",
    "en": "Welcome to the show! Here comes a brand new song from Florence Road.",
}
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar"]
SAMPLE_CLOUD_DEFAULT = ["marin", "cedar", "coral"]


def cmd_voices(args):
    language = args.language or "en"
    if sys.platform == "darwin":
        os_voices = mac_voice_candidates(language)
    else:
        os_voices = []  # Windows and Linux: the system voice list is not read yet
    print(json.dumps({
        "language": language,
        "computer_voices": os_voices,
        "computer_voice_note": "Best first. Use the exact name with --os-voice." if os_voices else
                               "Not listed on this system; the default voice for the language is used.",
        "cloud_voices_openai": OPENAI_VOICES,
        "cloud_voice_note": "Needs --engine openai --accept-cloud. OpenAI recommends marin and cedar.",
    }, indent=2, ensure_ascii=False))


def cmd_sample(args):
    language = args.language or "en"
    text = args.text or SAMPLE_TEXT.get(language, SAMPLE_TEXT["en"])
    if len(text) > 300:
        die("Keep a voice sample under 300 characters.")
    key = None
    check_speed(args.tts_speed)
    if args.engine == "openai":
        if not args.accept_cloud:
            die("Refusing to send text to a cloud service without --accept-cloud. Tell the user what is sent "
                "(the sample sentence, to api.openai.com) and ask for a yes.", 2)
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            die("OPENAI_API_KEY is not set. Set it in the shell that starts the agent (never paste it in the chat).", 3)
        voices = args.voice or SAMPLE_CLOUD_DEFAULT
        for v in voices:
            if v not in OPENAI_VOICES:
                die("Unknown OpenAI voice %r. Choose from: %s" % (v, ", ".join(OPENAI_VOICES)))
    else:
        voices = args.voice or (mac_voice_candidates(language)[:3] if sys.platform == "darwin" else [None])
        for v in voices:
            if v is not None and len(v) > 80:
                die("Voice name too long.")
    private_dir(args.out_dir)
    made = []
    for n, v in enumerate(voices, 1):
        out = os.path.join(args.out_dir, "sample-%d.wav" % n)
        refuse_symlink(out)
        if args.engine == "openai":
            ok = openai_synth(text, out, v, args.tts_model, args.tts_instructions or default_instructions(language), key, args.tts_speed)
        else:
            ok = os_synth(text, out, language, v)
        label = v or "default voice"
        print("%s %d. %s%s" % ("ok " if ok else "FAIL", n, label, "" if ok else " (could not be made)"))
        if ok:
            made.append((label, out))
    if args.play:
        for label, path in made:
            print("Playing: %s" % label, flush=True)
            play_wav(path)
            time.sleep(0.6)
    if not made:
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
    r = subprocess.run([spotify_cli()] + list(cli_args), capture_output=True, text=True, env=child_env())
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


def put_first(uri):
    """`queue add` appends to the end of the user's own queue, so anything already queued would play
    first. Move the next track of the show to the front; the user's tracks stay behind it."""
    try:
        q = json.loads(cli("queue", "list", "--format", "json", check=False) or "{}")
    except ValueError:
        return
    spots = [k for k, e in enumerate(q.get("next_tracks", [])) if e.get("provider") == "queue" and e.get("uri") == uri]
    if spots and spots[-1] > 0:
        cli("queue", "move", str(spots[-1]), "0", check=False)


def wav_seconds(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def play_wav(path):
    path = os.path.abspath(path)
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], check=False, env=child_env())
    elif os.name == "nt":
        env = child_env(RADIO_WAV=path)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(New-Object System.Media.SoundPlayer $env:RADIO_WAV).PlaySync()"], check=False, env=env)
    else:
        for exe, extra in (("paplay", []), ("aplay", ["-q"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"])):
            if shutil.which(exe):
                subprocess.run([exe] + extra + [path], check=False, env=child_env())
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
        if not isinstance(t.get("uri"), str) or not TRACK_URI.fullmatch(t["uri"]):
            die("Invalid track URI: %r" % t["uri"])
        if t["uri"] in seen:
            die("Track %r is in the list twice. Remove duplicates before the run." % t["uri"])
        seen.add(t["uri"])
        if not isinstance(t.get("duration_ms"), (int, float)) or t["duration_ms"] <= 0:
            die("Track %r has no duration_ms. Get it with `spotify_cli lookup <uri> --fields duration`." % t["uri"])
        if t.get("intro"):
            t["intro"] = os.path.abspath(str(t["intro"]))  # absolute: a path starting with "-" can never look like an option
            if not os.path.isfile(t["intro"]):
                die("The intro file %r does not exist." % t["intro"])
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

    stopped = [False]
    pending = [None]  # the track this script put in the queue and that has not started yet

    def shutdown(*_):
        stopped[0] = True
        raise SystemExit(0)  # unwinds through the try/finally below, which restores the volume

    def clear_pending_queue():
        """Take the track this script queued, if it never started, out of the user's queue again."""
        uri = pending[0]
        pending[0] = None
        if not uri:
            return
        try:
            q = json.loads(cli("queue", "list", "--format", "json", check=False) or "{}")
        except ValueError:
            return
        for pos, item in enumerate(q.get("next_tracks", [])):
            if item.get("provider") == "queue" and item.get("uri") == uri:
                cli("queue", "remove", str(pos), check=False)
                return

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
            put_first(tracks[k + 1]["uri"])
            queued[0] = k + 1
            pending[0] = tracks[k + 1]["uri"]
        return (tracks[k]["duration_ms"] / 1000.0 - args.preview) if (args.preview and tracks[k]["duration_ms"] / 1000.0 > args.preview + 5) else 0.0

    def play_show():
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
                    if uri == pending[0]:
                        pending[0] = None  # the queued track has started: it is no longer ours to clean up
                    if i == n - 1 and j <= i and elapsed >= tracks[i]["duration_ms"] / 1000.0 - 3:
                        # The last track ran to its end and Spotify's autoplay started a track from the list again.
                        cli("pause", check=False)
                        break
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

    try:
        play_show()
    finally:
        # Also on Ctrl+C, a termination signal or an error: the volume must not stay lowered.
        set_volume(restore)
        if stopped[0]:
            cli("pause", check=False)
            print("\nStopped. Volume restored.")
        clear_pending_queue()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_cloud_args(sp):
        sp.add_argument("--tts-voice", default=OPENAI_VOICE)
        sp.add_argument("--tts-model", default=OPENAI_MODEL)
        sp.add_argument("--tts-instructions", help="speaking style for the cloud voice")
        sp.add_argument("--tts-speed", type=float, default=OPENAI_SPEED, help="speaking speed for the cloud voice, 0.5 to 2.0 (1.0 = normal, default %.1f)" % OPENAI_SPEED)
        sp.add_argument("--max-chars", type=int, default=6000, help="most characters to send for one show")

    sp = sub.add_parser("synth")
    sp.add_argument("--script", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--language", help="two-letter language code (nl, en, ...); picks a voice for it")
    sp.add_argument("--os-voice", help="exact voice name from your system (macOS: `say -v ?`)")
    sp.add_argument("--one-voice", action="store_true", help="read {xx:name} parts with the main voice too (no voice switching)")
    sp.add_argument("--engine", choices=["os", "openai"], default="os")
    add_cloud_args(sp)
    sp.add_argument("--accept-cloud", metavar="CONSENT_CODE", help="the user agreed to send exactly what `estimate` showed; pass its consent_code")
    sp.add_argument("--no-fallback", action="store_true", help="do not fall back to the computer voice when the cloud voice fails")
    sp.set_defaults(fn=cmd_synth)
    sp = sub.add_parser("voices")
    sp.add_argument("--language", help="two-letter language code (default en)")
    sp.set_defaults(fn=cmd_voices)
    sp = sub.add_parser("sample")
    sp.add_argument("--language", help="two-letter language code (default en)")
    sp.add_argument("--engine", choices=["os", "openai"], default="os")
    sp.add_argument("--voice", nargs="+", help="voices to try (default: the best computer voices, or marin cedar coral)")
    sp.add_argument("--text", help="sentence to speak (default: a short radio line)")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--play", action="store_true", help="play the samples one after the other")
    sp.add_argument("--accept-cloud", action="store_true", help="the user agreed to send the sample sentence to the cloud service")
    sp.add_argument("--tts-model", default=OPENAI_MODEL)
    sp.add_argument("--tts-instructions", help="speaking style for the cloud voice")
    sp.add_argument("--tts-speed", type=float, default=OPENAI_SPEED, help="speaking speed for the cloud voice, 0.5 to 2.0 (1.0 = normal, default %.1f)" % OPENAI_SPEED)
    sp.set_defaults(fn=cmd_sample)
    sp = sub.add_parser("estimate")
    sp.add_argument("--script", required=True)
    sp.add_argument("--language", help="two-letter language code (the style instructions name it)")
    sp.add_argument("--engine", choices=["openai"], default="openai")
    add_cloud_args(sp)
    sp.set_defaults(fn=cmd_estimate)
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
