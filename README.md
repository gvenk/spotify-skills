# spotify-skills

Agent skills that work with the Spotify desktop app through its command line tool, `spotify_cli`. They are meant for AI coding agents that can run shell commands and load `SKILL.md` skills (such as Claude Code). They only work on a machine where Spotify is installed.

A product of [Million Pieces](https://millionpieces.nl).

> **Note:** these skills have only been tested on macOS. Windows and Linux are untested and may need changes (for example, the location of `spotify_cli`).

## Skills

This repository currently contains **two skills**. More may follow.

| Skill | What it does | Details |
|-------|--------------|---------|
| [`text-to-playlist`](skills/text-to-playlist/SKILL.md) | Turns any text that mentions artists (newsletter, agenda, festival line-up, article, review; URL or pasted text) into a Spotify playlist, private or public as you choose. | [docs/text-to-playlist.md](docs/text-to-playlist.md) |
| [`radio-dj`](skills/radio-dj/SKILL.md) | Plays a playlist like a radio show: researches each artist and song on the web and speaks a short intro before every track. Optional OpenAI voice. | [docs/radio-dj.md](docs/radio-dj.md) |

Example requests:

- "Make a playlist from this newsletter: https://example.com/agenda" (`text-to-playlist`)
- "DJ my playlist *Summer concerts*" (`radio-dj`)

The details of each skill (options, voices, safety and limits) are on its page in [`docs/`](docs/).

## Requirements

- The **Spotify desktop app**, installed and logged in. It ships `spotify_cli`:
  - macOS: `/Applications/Spotify.app/Contents/MacOS/spotify_cli`
  - Windows: `spotify_cli.exe`, expected on the `PATH` (**untested**)
- A shell. [`jq`](https://jqlang.github.io/jq/) is optional (`text-to-playlist` only uses it for shortcuts).
- `text-to-playlist`: a way for the agent to read a web page, only if you give a URL. Any web-fetch or browser tool works. You can also paste the text instead.
- `radio-dj`: Python 3.9 or newer, and a way for the agent to search the web.

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

**Download.** The easiest way is the zip with only the skill, from the latest release:

| Skill | Download | Unzips to |
|-------|----------|-----------|
| text-to-playlist | [text-to-playlist.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/text-to-playlist.zip) | `text-to-playlist` |
| radio-dj | [radio-dj.zip](https://github.com/gvenk/spotify-skills/releases/latest/download/radio-dj.zip) | `radio-dj` |

All versions are on the [releases page](https://github.com/gvenk/spotify-skills/releases).

If you want the newest, unreleased version, download the whole repository as [main.zip](https://github.com/gvenk/spotify-skills/archive/refs/heads/main.zip) (the skills are the folders under `skills/` inside it) or clone it with git.

In the commands below, `<skill>` is `text-to-playlist` or `radio-dj`, and `skills/<skill>` is the folder inside the repository. If you unzipped a release zip, use just `<skill>`. Copy the whole folder: `text-to-playlist` is only a `SKILL.md`, `radio-dj` is a `SKILL.md` and a `scripts/` folder.

Copy the skill folder (it contains `SKILL.md`) into your tool's skills directory. Use a link instead of a copy if you want updates from this repository to apply without copying again.

**Claude Code**

```bash
# for all your projects
cp -R skills/<skill> ~/.claude/skills/

# or for one project
cp -R skills/<skill> <your-project>/.claude/skills/
```

**Codex** ([documentation](https://learn.chatgpt.com/docs/build-skills))

```bash
# the skills folder is $CODEX_HOME/skills, usually ~/.codex/skills
cp -R skills/<skill> ~/.codex/skills/
```

You can also run `$skill-installer` inside a Codex session. Restart Codex if it does not see the new skill.

**Gemini CLI** ([documentation](https://geminicli.com/docs/cli/skills/))

```bash
# install from a local folder
gemini skills install ./skills/<skill>

# or copy it by hand
cp -R skills/<skill> ~/.gemini/skills/
```

Check that it loaded with `/skills list` in a session. Add `--scope workspace` to install it for one project only.

**Other tools.** Skills in the `SKILL.md` format are also supported by other tools, each with its own folder. Look up "agent skills" in the tool's documentation and copy the skill folder there.

The commands above follow each tool's documentation and the current layout of its skills folder, which can change. Claude Code and Codex have been tested with this repository. Gemini CLI has not.

If `spotify_cli` is not at the default path, set `SPOTIFY_CLI_PATH` to its location before starting the tool.

## Calling a skill

There are two ways to call a skill. The second works in every tool.

**1. Name it.** Most tools let you call a skill by name, followed by your request:

| Tool | How to call it |
|------|----------------|
| Claude Code | `/<skill>`, for example `/text-to-playlist` or `/radio-dj` |
| Codex (CLI and IDE extension) | `$<skill>`, or type `$` or `/skills` to pick it from a list |
| Gemini CLI | no name syntax; the model picks the skill itself (see below) |

For example: `/text-to-playlist https://example.com/agenda, public` or `$radio-dj my playlist Summer concerts, in Dutch`.

**2. Just ask.** The tool picks the skill by its description, so a normal request is enough, in any language:

- "Make a playlist from this newsletter: https://example.com/agenda"
- "Turn this line-up into a Spotify playlist, 3 tracks per artist, hidden gems, shuffled"
- Paste the text and say "playlist from this, private"
- "DJ my playlist Summer concerts"
- "Introduce every song of my Friday playlist"

In Gemini CLI the model activates the skill when your request matches its description and asks for your confirmation before it loads. In Codex a skill can also be set to run only when named (`allow_implicit_invocation: false`). If a tool does not pick up your request, name the skill.

Put the options in the same message, or leave them out and the skill asks only what it needs. Call a skill without its input (a URL or text, or a playlist), or ask for help, and it shows a short help message with examples and its options. The options of each skill are on its page in [`docs/`](docs/).

## Safety and limits

- Nothing is downloaded or installed by the skills. They run `spotify_cli` on your computer. `radio-dj` can optionally send its intro texts to OpenAI, only after you say yes (see its page).
- Text and playlist data from outside (newsletters, web pages, playlist names and descriptions) are treated as untrusted data. The agent takes facts from them and ignores any instructions inside.
- Artist and playlist names are never pasted unescaped into shell commands, and track URIs are validated before they are used.
- `text-to-playlist` only creates playlists and folders, and adds tracks. It does not play music, delete playlists, or remove folders. Playlists are private unless you choose public.
- `radio-dj` plays music, but only after you say go. It plays, pauses, changes the volume and puts the next track of the list at the front of your queue, with your own queued tracks behind it. See [its page](docs/radio-dj.md#safety-and-limits) for the details.
- `spotify_cli` cannot delete playlists. Delete test or unwanted playlists in the Spotify app.

The full lists are on the pages of [text-to-playlist](docs/text-to-playlist.md#safety-and-limits) and [radio-dj](docs/radio-dj.md#safety-and-limits).

## Repository layout

```
skills/
  text-to-playlist/
    SKILL.md
  radio-dj/
    SKILL.md
    scripts/
      radio.py
docs/
  text-to-playlist.md
  radio-dj.md
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
