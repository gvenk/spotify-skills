# spotify-skills

Agent skills that work with the Spotify desktop app through its command line tool, `spotify_cli`. They are meant for AI coding agents that can run shell commands and load `SKILL.md` skills (such as Claude Code). They only work on a machine where Spotify is installed.

A product of [Million Pieces](https://millionpieces.nl).

> **Note:** these skills have only been tested on macOS. Windows and Linux are untested and may need changes (for example, the location of `spotify_cli`).

## Skills

This repository currently contains **two skills**. More may follow.

| Skill | What it does |
|-------|--------------|
| [`text-to-playlist`](skills/text-to-playlist/SKILL.md) | Turns any text that mentions artists (newsletter, agenda, festival line-up, article, review; URL or pasted text) into a Spotify playlist, private or public as you choose. |
| [`radio-dj`](skills/radio-dj/SKILL.md) | Plays a playlist like a radio show: researches each artist and song on the web and speaks a short intro before every track. |

### text-to-playlist

Give the agent a URL or paste the text, and it will:

1. read the text and extract the artists the text is about (artists mentioned only as history or comparison, such as acts from past editions, are left out),
2. find each artist on Spotify and pick tracks from both recent and older releases: the most played ones by default, or lesser-known "deep cuts" if you ask (reissues and remasters don't count as "recent"),
3. create a playlist (it asks whether it should be private or public, unless you said so), optionally inside a folder, and verify the result.

Example requests:

- "Make a playlist from this newsletter: https://example.com/agenda"
- "Agenda to playlist, 3 tracks per artist, call it *Summer concerts*"
- "Playlist from this agenda, put it in my *Newsletters* folder"
- "Make a playlist with exactly the songs in this list of winners" (see *Named songs* below)
- "Playlist from this line-up, shuffled"
- "Make a public playlist with the hidden gems of the artists in this review"
- "Playlist from this album chart, 2 deep cuts per album" (see *Album lists* below)
- "Playlist from this top 100, 1 track per album, in the order of the chart"

If you call the skill without a URL or text, or ask for help, it shows a short help message with examples and the options, and asks for input.

Options you can ask for in plain language:

| Option | Default | Notes |
|--------|---------|-------|
| Tracks per artist | 2 recent + 2 older | Each group can be 0 to 5. "3 per artist" means 2 recent + 1 older. |
| Tracks per album | 2 | For texts that list albums (see *Album lists*). 0 to 5. |
| Playlist name | generated as `<Source> - <Theme> - <Month Year>` | A name you give is used exactly as written. |
| Selection style | hits (most played) | Say "deep cuts", "hidden gems" or "not just the hits" for less common songs, or "mix" for one hit and one deep cut per group. Doesn't apply to named songs. |
| Track order | in the order of the text | Say "shuffled" or "random order" to mix the tracks. This sets the order of the tracks in the playlist, not playback shuffle in Spotify. |
| Visibility | asked if not mentioned (private if it can't ask) | Say "public" or "private" in your request to skip the question. |
| Folder | none (top level) | An existing folder is used; a new one is created if the name does not exist. |
| Named songs | asked if the text names songs | If the text pairs artists with specific songs (winners, setlist, tracklist, a single chart), you can get exactly those songs instead of each artist's top tracks. |
| Album lists | asked if the text lists albums | For an album chart or best-albums list, you can get tracks from each listed album (the most played, or deep cuts) instead of the artist's top tracks. |

**Same-name artists.** Spotify can have several artists with the same name. The skill picks the one whose releases match the text. If the text gives no hint, it takes an artist only when they have at least 10 times the monthly listeners of every other match, and says in its report that this was a guess. Otherwise it skips the artist and lists them in the report.

**Large texts.** More than 40 artists makes the skill ask before it continues. If the settings would give more than 200 tracks, it tells you up front and uses fewer (for example 1 track per album instead of 2) to stay under 200. You can overrule that by saying how many you want.

### radio-dj

Give the agent a playlist and it will:

1. read the playlist and research each artist and song on the web (facts only, nothing invented),
2. write a short intro for every track and show them to you for approval,
3. turn the intros into speech and, after you say go, play the playlist with the intro spoken before each track: over the end of the previous track (the music gets quieter while the host talks), or in a short pause.

Example requests:

- "DJ my playlist *Summer concerts*"
- "Play this playlist like a radio show, in English, short intros"
- "Introduce every song of my Friday playlist, first 10 tracks"

**Voice.** The intros are spoken with your computer's own voice. Nothing is downloaded or installed. On macOS the skill picks a voice that matches the language of the intros, preferring Premium and Enhanced voices you have installed. The default voices sound fairly robotic; for a better one, download a Premium or Enhanced voice in *System Settings → Accessibility → Spoken Content → System voice → Manage Voices*.

**Limits.**
- **macOS only, for now.** The skill has only been tested on macOS. The Windows (built-in speech) and Linux (`espeak-ng`) code paths exist but have never been run, and `spotify_cli` itself is untested there.
- Needs Python 3.9 or newer.
- Controlling playback through `spotify_cli` may need a Spotify Premium account.
- The skill only plays, pauses and changes the volume, and it never starts playing without your go-ahead. Ctrl+C stops it and restores the volume.
- Facts in the intros come from the web and from Spotify's own data. When nothing reliable is found, the intro stays short instead of inventing trivia.

## Requirements

- The **Spotify desktop app**, installed and logged in. It ships `spotify_cli`:
  - macOS: `/Applications/Spotify.app/Contents/MacOS/spotify_cli`
  - Windows: `spotify_cli.exe`, expected on the `PATH` (**untested**)
- A shell. [`jq`](https://jqlang.github.io/jq/) is optional (the skill only uses it for shortcuts).
- For `radio-dj` only: Python 3.9 or newer, and a way for the agent to search the web.
- A way for the agent to read a web page, only if you give a URL. Any web-fetch or browser tool works. You can also paste the text instead.

## Which tools can use it

The skill runs `spotify_cli` on your own computer, so it needs an AI tool that can **load a `SKILL.md` skill** and **run commands on your machine** (a local shell). That covers command line tools, their desktop apps and IDE extensions, as long as they run locally:

| Tool | Works? | Notes |
|------|--------|-------|
| Claude Code (terminal, desktop app, IDE) | Yes | Tested. |
| Codex | Yes | Tested. If your Codex setup blocks the command, allow it to run `spotify_cli`. |
| Gemini CLI | Should work | Not tested. |
| Other tools that support `SKILL.md` skills and run local commands | Probably | Check their documentation for the skills folder. |
| The Claude chat app (claude.ai, desktop chat) | **No** | Skills there run in a remote sandbox with no access to your Spotify app. |

If a tool cannot run commands on your computer, the skill cannot control Spotify.

## Install

**Download.** The easiest way is the zip with only the skill, from the latest release:

| Skill | Download | Unzips to |
|-------|----------|-----------|
| text-to-playlist | [text-to-playlist.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/text-to-playlist.zip) | `text-to-playlist` |
| radio-dj | [radio-dj.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/radio-dj.zip) | `radio-dj` |

All versions are on the [releases page](https://github.com/gvenk/spotify-skills/releases).

If you want the newest, unreleased version, download the whole repository as [main.zip](https://github.com/gvenk/spotify-skills/archive/refs/heads/main.zip) (the skills are the folders under `skills/` inside it) or clone it with git.

The commands below use `text-to-playlist`. **To install `radio-dj`, use `radio-dj` instead** (for example `cp -R skills/radio-dj ~/.claude/skills/`). In the commands, `skills/<name>` is the folder inside the repository. If you unzipped a release zip, use just `<name>`. `radio-dj` is a folder with `SKILL.md` and a `scripts/` folder, so copy the whole folder.

Copy the skill folder (it contains `SKILL.md`) into your tool's skills directory. Use a link instead of a copy if you want updates from this repository to apply without copying again.

**Claude Code**

```bash
# for all your projects
cp -R skills/text-to-playlist ~/.claude/skills/

# or for one project
cp -R skills/text-to-playlist <your-project>/.claude/skills/
```

**Codex** ([documentation](https://learn.chatgpt.com/docs/build-skills))

```bash
# the skills folder is $CODEX_HOME/skills, usually ~/.codex/skills
cp -R skills/text-to-playlist ~/.codex/skills/
```

You can also run `$skill-installer` inside a Codex session. Restart Codex if it does not see the new skill.

**Gemini CLI** ([documentation](https://geminicli.com/docs/cli/skills/))

```bash
# install from a local folder
gemini skills install ./skills/text-to-playlist

# or copy it by hand
cp -R skills/text-to-playlist ~/.gemini/skills/
```

Check that it loaded with `/skills list` in a session. Add `--scope workspace` to install it for one project only.

**Other tools.** Skills in the `SKILL.md` format are also supported by other tools, each with its own folder. Look up "agent skills" in the tool's documentation and copy the `text-to-playlist` folder there.

The commands above follow each tool's documentation and the current layout of its skills folder, which can change. Claude Code and Codex have been tested with this repository. Gemini CLI has not.

If `spotify_cli` is not at the default path, set `SPOTIFY_CLI_PATH` to its location before starting the tool.

## Calling the skill

There are two ways to call the skill. The second works in every tool.

**1. Name it.** Most tools let you call a skill by name, followed by your request:

| Tool | How to call it | Example |
|------|----------------|---------|
| Claude Code | `/text-to-playlist` | `/text-to-playlist https://example.com/agenda, public` |
| Codex (CLI and IDE extension) | `$text-to-playlist`, or type `$` or `/skills` to pick it from a list | `$text-to-playlist https://example.com/agenda, public` |

For `radio-dj` the names are `/radio-dj` and `$radio-dj`, for example `/radio-dj my playlist Summer concerts, in Dutch`.
| Gemini CLI | no name syntax; the model picks the skill itself (see below) | |

**2. Just ask.** The tool picks the skill by its description, so a normal request is enough, in any language:

- "Make a playlist from this newsletter: https://example.com/agenda"
- "Turn this line-up into a Spotify playlist, 3 tracks per artist, hidden gems, shuffled"
- Paste the text and say "playlist from this, private"

In Gemini CLI the model activates the skill when your request matches its description and asks for your confirmation before it loads. In Codex a skill can also be set to run only when named (`allow_implicit_invocation: false`). If a tool does not pick up your request, name the skill.

Put the options in the same message ("public", "shuffled", "deep cuts", "3 per artist", "call it Summer concerts", "in my Newsletters folder"), or leave them out and the skill asks only what it needs. Call the skill without a URL or text, or ask for help, and it shows a short help message with examples.

## Safety and limits

- Playlists are **private** unless you choose public. If your request doesn't say, the skill asks, and falls back to private when it can't ask.
- Newsletter text is treated as untrusted data. The agent extracts artist names only and ignores any instructions inside the content.
- Artist and playlist names are never pasted unescaped into shell commands, and track URIs are validated before they are added.
- `text-to-playlist` only creates playlists and folders, and adds tracks. It does not play music, delete playlists, or remove folders.
- `radio-dj` plays music, but only after you say go. It changes only play, pause and volume, and downloads or installs nothing.
- `spotify_cli` cannot delete playlists. Delete test or unwanted playlists in the Spotify app.
- Artists are matched by name, then by the artist behind the release. Artists that share a name on Spotify can still be mixed up, so check the result. Artists that cannot be found are listed in the final report.

## Repository layout

```
skills/
  text-to-playlist/
    SKILL.md
  radio-dj/
    SKILL.md
    scripts/
      radio.py
README.md
RELEASING.md
LICENSE
.gitignore
```

## Questions, bugs and improvements

- **Questions and remarks:** email [gvenk@millionpieces.nl](mailto:gvenk@millionpieces.nl).
- **Bugs:** open an issue at [github.com/gvenk/spotify-skills/issues](https://github.com/gvenk/spotify-skills/issues). Say which tool and OS you use, what you asked, and what happened.
- **Improvement ideas:** open an issue at the same page and describe what you would like the skill to do.
<!-- When the repository moves to the company GitHub, update the two issue links above (and the repository URL) to the new address. -->

## For maintainers

How to publish a release: see [RELEASING.md](RELEASING.md).

## License

[MIT](LICENSE)
