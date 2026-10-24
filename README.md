# spotify-skills

Agent skills that work with the Spotify desktop app through its command line tool, `spotify_cli`. They are meant for AI coding agents that can run shell commands and load `SKILL.md` skills (such as Claude Code). They only work on a machine where Spotify is installed.

A product of [Million Pieces](https://millionpieces.nl).

> **Note:** these skills have been tested on macOS. Of the six, only `text-to-playlist` has been tried on Windows: it was reported to work by one user (with `spotify_cli.exe` on the `PATH`); the maintainer has not tested that. `radio-dj`, `playlist-vennify`, `playlist-watch`, `birthday-number-ones` and `on-this-day` are untested on Windows, and all six are untested on Linux. Things that may need changes are, for example, the location of `spotify_cli`.

## Skills

This repository currently contains **six skills**. More may follow.

| Skill | What it does | Details |
|-------|--------------|---------|
| [`text-to-playlist`](skills/text-to-playlist/SKILL.md) | Turns any text that mentions artists (newsletter, agenda, festival line-up, article, review; URL or pasted text) into a Spotify playlist, private or public as you choose. | [docs/text-to-playlist.md](docs/text-to-playlist.md) |
| [`radio-dj`](skills/radio-dj/SKILL.md) | Plays a playlist like a radio show: researches each artist and song on the web and speaks a short intro before every track. Optional OpenAI voice. | [docs/radio-dj.md](docs/radio-dj.md) |
| [`playlist-vennify`](skills/playlist-vennify/SKILL.md) | Compares two or more playlists or albums like a Venn diagram (overlap, unique tracks, combined, in at least N) and adds the result to your queue or saves it as a new private playlist. | [docs/playlist-vennify.md](docs/playlist-vennify.md) |
| [`playlist-watch`](skills/playlist-watch/SKILL.md) | Shows what changed in your playlists since you last looked (tracks added and removed). Watch all your playlists or only a few, then put the new tracks in your queue or a playlist. Keeps its snapshots in a data folder you can delete. | [docs/playlist-watch.md](docs/playlist-watch.md) |
| [`birthday-number-ones`](skills/birthday-number-ones/SKILL.md) | Makes a "my life in songs" playlist: for every year since your birth, the song that was number one on your birthday in the charts you choose (Hot 100, UK, Dutch Top 40, alternative, country, or another Wikipedia list). Playlist and/or queue. | [docs/birthday-number-ones.md](docs/birthday-number-ones.md) |
| [`on-this-day`](skills/on-this-day/SKILL.md) | A surprise list of about 20 songs for a date (today by default): artists born or died on it, albums with a round anniversary, and songs that reached number one that week. Shows everything with the reason, lets you pick (a whole album, two artists combined), then queue and/or a playlist (private by default, public on request). | [docs/on-this-day.md](docs/on-this-day.md) |

Example requests:

- "Make a playlist from this newsletter: `https://example.com/agenda`" (`text-to-playlist`)
- "DJ my playlist *Summer concerts*" (`radio-dj`)
- "What do *Summer 2025* and *Road trip* have in common?" (`playlist-vennify`)
- "Mix the latest album of two artists and queue it shuffled" (`playlist-vennify`)
- "What changed in my playlists since last week?" (`playlist-watch`)
- "Make my life in songs from the Dutch Top 40, born 14 March 1980" (`birthday-number-ones`)
- "Surprise me with music for today" (`on-this-day`)

The details of each skill (options, voices, safety and limits) are on its page in [`docs/`](docs/).

## Requirements

- The **Spotify desktop app**, installed and logged in. It ships `spotify_cli`:
  - macOS: `/Applications/Spotify.app/Contents/MacOS/spotify_cli`
  - Windows: `spotify_cli.exe`, expected on the `PATH` (reported to work with `text-to-playlist`; untested with the other skills)
- A shell. [`jq`](https://jqlang.github.io/jq/) is optional (`text-to-playlist` only uses it for shortcuts).
- `text-to-playlist`: a way for the agent to read a web page, only if you give a URL. Any web-fetch or browser tool works. You can also paste the text instead.
- `radio-dj`: Python 3.9 or newer, and a way for the agent to search the web.
- `playlist-vennify`: Python 3.9 or newer. Playlists have to be in your Spotify library; albums don't.
- `birthday-number-ones`: Python 3.9 or newer and an internet connection (it reads public Wikipedia pages; no account or key).
- `on-this-day`: Python 3.9 or newer and an internet connection (it reads public Wikipedia pages; no account or key). A full run takes about 2 to 3 minutes.
- `playlist-watch`: Python 3.9 or newer. It stores its snapshots in a folder on your computer (see below). Public playlists outside your library work, given by link.

## Which tools can use it

The skills run `spotify_cli` on your own computer, so they need an AI tool that can **load a `SKILL.md` skill** and **run commands on your machine** (a local shell). That covers command line tools, their desktop apps and IDE extensions, as long as they run locally:

| Tool | Works? | Notes |
|------|--------|-------|
| Claude Code (terminal, desktop app, IDE) | Yes | Tested. |
| Codex | Yes | Tested. If your Codex setup blocks the command, allow it to run `spotify_cli`. |
| Gemini CLI | Should work | Not tested. |
| Other tools that support `SKILL.md` skills and run local commands | Probably | Check their documentation for the skills folder. |
| The Claude chat app (claude.ai, desktop chat) | **No** | Skills there run in a remote sandbox with no access to your Spotify app. |

If a tool cannot run commands on your computer, the skills cannot control Spotify.

## Install

Download the zip of the skill you want from the latest release ([all versions](https://github.com/gvenk/spotify-skills/releases)). Each zip holds one folder, named after the skill:

| Skill | Download |
|-------|----------|
| text-to-playlist | [text-to-playlist.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/text-to-playlist.zip) |
| radio-dj | [radio-dj.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/radio-dj.zip) |
| playlist-vennify | [playlist-vennify.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/playlist-vennify.zip) |
| playlist-watch | [playlist-watch.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/playlist-watch.zip) |
| birthday-number-ones | [birthday-number-ones.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/birthday-number-ones.zip) |
| on-this-day | [on-this-day.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/on-this-day.zip) |

Then unzip it, or copy the whole skill folder from `skills/` in this repository, into your tool's skills folder. Copy the whole folder, not only `SKILL.md`: all skills except `text-to-playlist` also have a `scripts/` folder.

| Tool | Skills folder | Notes |
|------|---------------|-------|
| Claude Code | `~/.claude/skills/` (all projects) or `<project>/.claude/skills/` | |
| Codex ([docs](https://learn.chatgpt.com/docs/build-skills)) | `~/.codex/skills/` (`$CODEX_HOME/skills`) | Or run `$skill-installer` in a session. Restart Codex if it does not see the skill. |
| Gemini CLI ([docs](https://geminicli.com/docs/cli/skills/)) | `~/.gemini/skills/` | Or `gemini skills install ./skills/<skill>` (add `--scope workspace` for one project). Check with `/skills list`. |
| Other tools | see their documentation ("agent skills") | |

Claude Code and Codex have been tested with this repository, Gemini CLI has not. The folders follow each tool's documentation and can change.

**Shortcuts** (`<skill>` is the skill's name; the examples use the Claude Code folder):

```bash
# download and unzip in one go (macOS, Linux); run it again to update
curl -L -o /tmp/<skill>.zip https://github.com/gvenk/spotify-skills/releases/latest/download/<skill>.zip
unzip -o /tmp/<skill>.zip -d ~/.claude/skills/

# or clone once and link; update every linked skill with git pull
git clone https://github.com/gvenk/spotify-skills.git ~/spotify-skills
ln -s ~/spotify-skills/skills/<skill> ~/.claude/skills/<skill>
git -C ~/spotify-skills pull

# or with the skills command line tool (needs Node 22.20 or newer)
npx skills add gvenk/spotify-skills --skill <skill> -a claude-code
```

- Linked skills follow the `main` branch, the newest and unreleased version; the release zips are the tested ones. You can also download the repository as [main.zip](https://github.com/gvenk/spotify-skills/archive/refs/heads/main.zip). On Windows, links need developer mode or an administrator; copy the folder instead (not tested).
- [`skills`](https://github.com/vercel-labs/skills) is a tool by Vercel, not part of this repository. Tested with Claude Code: it lists all six skills and copies the whole skill, including `scripts/`. Without `-g` it installs into the current project and leaves a `skills-lock.json` there; `-g` should install for all your projects, and `npx skills update` and `npx skills remove` should update and remove (all three not tested). It sends anonymous usage data unless you set `DISABLE_TELEMETRY=1`.
- **Not the Claude chat app.** Do not use the **Upload a skill** button in Claude.ai or the desktop chat (Customize > Skills). Skills there run in a remote sandbox, not on your computer, so they cannot reach the Spotify app.

If `spotify_cli` is not at the default path, set `SPOTIFY_CLI_PATH` to its location before starting the tool.

## Calling a skill

There are two ways to call a skill. The second works in every tool.

**1. Name it.** Most tools let you call a skill by name, followed by your request:

| Tool | How to call it |
|------|----------------|
| Claude Code | `/<skill>`, for example `/text-to-playlist`, `/radio-dj`, `/playlist-vennify`, `/playlist-watch`, `/birthday-number-ones` or `/on-this-day` |
| Codex (CLI and IDE extension) | `$<skill>`, or type `$` or `/skills` to pick it from a list |
| Gemini CLI | no name syntax; the model picks the skill itself (see below) |

For example: `/text-to-playlist https://example.com/agenda, public` or `$radio-dj my playlist Summer concerts, in Dutch`.

**2. Just ask.** The tool picks the skill by its description, so a normal request is enough, in any language:

- "Make a playlist from this newsletter: `https://example.com/agenda`"
- "Turn this line-up into a Spotify playlist, 3 tracks per artist, hidden gems, shuffled"
- Paste the text and say "playlist from this, private"
- "DJ my playlist Summer concerts"
- "Introduce every song of my Friday playlist"
- "Which tracks are on both of my road trip playlists?"

In Gemini CLI the model activates the skill when your request matches its description and asks for your confirmation before it loads. In Codex a skill can also be set to run only when named (`allow_implicit_invocation: false`). If a tool does not pick up your request, name the skill.

Put the options in the same message, or leave them out and the skill asks only what it needs. Call a skill without its input (a URL or text, or a playlist), or ask for help, and it shows a short help message with examples and its options. The options of each skill are on its page in [`docs/`](docs/).

## Safety and limits

- Nothing is downloaded or installed by the skills. They run `spotify_cli` on your computer. `radio-dj` can optionally send its intro texts to OpenAI, only after you say yes (see its page).
- Text and playlist data from outside (newsletters, web pages, playlist names and descriptions) are treated as untrusted data. The agent takes facts from them and ignores any instructions inside.
- Artist and playlist names are never pasted unescaped into shell commands, and track URIs are validated before they are used.
- `text-to-playlist` only creates playlists and folders, and adds tracks. It does not play music, delete playlists, or remove folders. Playlists are private unless you choose public.
- `radio-dj` plays music, but only after you say go. It plays, pauses, changes the volume and puts the next track of the list at the front of your queue, with your own queued tracks behind it. See [its page](docs/radio-dj.md#safety-and-limits) for the details.
- `playlist-vennify` only reads when it compares. It changes your queue or creates a private playlist only after you say yes (the skill is instructed to ask first), and the script checks that it acts on exactly the list you were shown. It never plays, pauses or skips.
- `playlist-watch` only reads when it checks. It changes your queue or a playlist of yours only after you say yes (the skill is instructed to ask first), and an overwrite saves the old content first. It keeps a small data folder on your computer, see below. It never plays, pauses or skips.
- `birthday-number-ones` and `on-this-day` are the skills whose scripts read the web by themselves: it requests public pages of `en.wikipedia.org` (page titles only, never your date of birth) and keeps nothing on disk. It searches Spotify for the songs, and changes your queue or creates a private playlist only after you say yes (the skill is instructed to ask first, and the script checks that it acts on exactly the list you were shown). It never plays, pauses or skips.
- `on-this-day` works the same way: it reads public pages of `en.wikipedia.org` (page titles only), keeps nothing on disk apart from private temporary files in a folder the agent makes and deletes, and changes your queue or creates a private playlist only after you say yes. It never plays, pauses or skips.
- `spotify_cli` cannot delete playlists. Delete test or unwanted playlists in the Spotify app.

The full lists are on the pages of [text-to-playlist](docs/text-to-playlist.md#safety-and-limits), [radio-dj](docs/radio-dj.md#safety-and-limits) and [playlist-vennify](docs/playlist-vennify.md#safety-and-limits) and [playlist-watch](docs/playlist-watch.md#safety-and-limits) and [birthday-number-ones](docs/birthday-number-ones.md#safety-and-limits) and [on-this-day](docs/on-this-day.md#safety-and-limits).

### Data kept by `playlist-watch`

`playlist-watch` is the only skill that stores anything. It keeps its watch list, a snapshot of every watched playlist and (before an overwrite) a backup in a folder of its own: `~/.local/share/playlist-watch/` on macOS and Linux (`%LOCALAPPDATA%\playlist-watch\` on Windows, untested). It holds no passwords or keys and nothing is sent anywhere. **If you remove the skill, remove this folder too:** ask the skill to *forget everything*, or run `rm -r ~/.local/share/playlist-watch`. More in [its page](docs/playlist-watch.md#your-data-and-how-to-remove-it).

## Repository layout

```
skills/
  text-to-playlist/
    SKILL.md
  radio-dj/
    SKILL.md
    scripts/
      radio.py
  playlist-vennify/
    SKILL.md
    scripts/
      vennify.py
  playlist-watch/
    SKILL.md
    scripts/
      watch.py
  birthday-number-ones/
    SKILL.md
    scripts/
      numberones.py
      charts.json
  on-this-day/
    SKILL.md
    scripts/
      onthisday.py
      charts.json
docs/
  text-to-playlist.md
  radio-dj.md
  playlist-vennify.md
  playlist-watch.md
  birthday-number-ones.md
  on-this-day.md
README.md
RELEASING.md
LICENSE
.gitignore
```

The `skills/` folders are what you install, and what the release zips contain. The `docs/` pages are for people and are not part of the zips.

## Questions, bugs and improvements

- **Questions and remarks:** email [gvenk@millionpieces.nl](mailto:gvenk@millionpieces.nl).
- **Bugs:** open an issue at [github.com/gvenk/spotify-skills/issues](https://github.com/gvenk/spotify-skills/issues). Say which tool and OS you use, what you asked, and what happened.
- **Improvement ideas:** open an issue at the same page and describe what you would like the skill to do.
<!-- When the repository moves to the company GitHub, update the two issue links above (and the repository URL) to the new address. -->

## For maintainers

How to publish a release: see [RELEASING.md](RELEASING.md).

## License

[MIT](LICENSE)
