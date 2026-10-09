# spotify-skills

Agent skills that work with the Spotify desktop app through its command line tool, `spotify_cli`. They are meant for AI coding agents that can run shell commands and load `SKILL.md` skills (such as Claude Code). They only work on a machine where Spotify is installed.

## Skills

| Skill | What it does |
|-------|--------------|
| [`newsletter-to-playlist`](skills/newsletter-to-playlist/SKILL.md) | Turns a music newsletter or venue agenda (URL or pasted text) into a private Spotify playlist. |

### newsletter-to-playlist

Give the agent a URL or paste the text, and it will:

1. read the newsletter and extract the artists,
2. find each artist on Spotify and pick their most played tracks, from both recent and older releases,
3. create a private playlist, optionally inside a folder, and verify the result.

Example requests:

- "Make a playlist from this newsletter: https://example.com/agenda"
- "Agenda to playlist, 3 tracks per artist, call it *Summer concerts*"
- "Playlist from this agenda, put it in my *Newsletters* folder"

Options you can ask for in plain language:

| Option | Default | Notes |
|--------|---------|-------|
| Tracks per artist | 2 recent + 2 older | Each group can be 0 to 5. |
| Playlist name | generated as `<Source> - <Theme> - <Month Year>` | A name you give is used exactly as written. |
| Folder | none (top level) | An existing folder is used; a new one is created if the name does not exist. |

## Requirements

- The **Spotify desktop app**, installed and logged in. It ships `spotify_cli`:
  - macOS: `/Applications/Spotify.app/Contents/MacOS/spotify_cli`
  - Windows: `spotify_cli.exe`, expected on the `PATH` (**untested**)
- A shell, and [`jq`](https://jqlang.github.io/jq/).
- A way for the agent to read a web page, only if you give a URL. Any web-fetch or browser tool works. You can also paste the text instead.

The skills were written and tested on macOS only.

## Install

Copy the skill folder into your agent's skills directory. For Claude Code:

```bash
# for all your projects
cp -R skills/newsletter-to-playlist ~/.claude/skills/

# or for one project
cp -R skills/newsletter-to-playlist <your-project>/.claude/skills/
```

Other agents have their own skill or instruction locations. See their documentation.

If `spotify_cli` is not at the default path, set `SPOTIFY_CLI_PATH` to its location before starting the agent.

## Safety and limits

- Playlists are created **private**. The skill never passes `--public`.
- Newsletter text is treated as untrusted data. The agent extracts artist names only and ignores any instructions inside the content.
- Artist and playlist names are never pasted unescaped into shell commands, and track URIs are validated before they are added.
- It only creates playlists and folders, and adds tracks. It does not play music, delete playlists, or remove folders.
- `spotify_cli` cannot delete playlists. Delete test or unwanted playlists in the Spotify app.
- Artists are matched by name, then by the artist behind the release. Artists that share a name on Spotify can still be mixed up, so check the result. Artists that cannot be found are listed in the final report.

## Repository layout

```
skills/
  newsletter-to-playlist/
    SKILL.md
README.md
```

## License

[MIT](LICENSE)
