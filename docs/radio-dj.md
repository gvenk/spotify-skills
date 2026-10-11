# radio-dj

Plays a Spotify playlist like a radio show. Before every track a host says a few words about the artist and the song, researched on the web.

Install, requirements and how to call a skill are in the [main README](../README.md). The skill's instructions for the agent are in [`SKILL.md`](../skills/radio-dj/SKILL.md).

## How it works

Give the agent a playlist and it will:

1. read the playlist and research each artist and song on the web (facts only, nothing invented),
2. write a short intro for every track (you can ask to read and change them first; by default they are not shown before the show),
3. turn the intros into speech and, after you say go, play the playlist with the intro spoken before each track: over the end of the previous track (the music gets quieter while the host talks), or in a short pause.

Example requests:

- "DJ my playlist *Summer concerts*"
- "Play this playlist like a radio show, in English, short intros"
- "Introduce every song of my Friday playlist, first 10 tracks"

The playlist has to be in your Spotify library (save or follow it first). If you call the skill without a playlist, or ask for help, it shows a short help message with examples and the options.

## Options

You can ask for these in plain language:

| Option | Default | Notes |
|--------|---------|-------|
| Language | the language of your request | Dutch and English have been tested. Another language uses the voice your system has for it, if there is one. |
| Tracks | all of them, up to 30 | The skill asks first if the playlist has more. |
| Intro length | normal (2 to 3 sentences) | "short" is 1 to 2 sentences, "long" is 4 to 5. |
| Transition | over the end of the previous track | Or "in a pause": the music stops, the host talks, the next track starts. |
| Voice | your computer's own voice | Say "let me hear the voices", or name one ("use Xander", "use the cedar voice"). |
| One voice | off | See [Names in another language](#names-in-another-language). |
| Style and pace | enthusiastic, 1.2 times normal speed | OpenAI voice only: "calmer", "more energetic", "slower", "faster". |
| Show texts | off | Say "show me the intros first" to read and change them before the show. |
| Show notes | off | Say "write show notes" for a Markdown file. See [Show notes](#show-notes-optional). |

## Voices

By default the intros are spoken with your computer's own voice. Nothing is downloaded or installed. On macOS the skill picks a voice that matches the language of the intros, preferring Premium and Enhanced voices you have installed.

**Hearing and choosing.** Not sure which voice you like? Say "let me hear the voices": the skill plays one line in a few voices and you pick one. You can also just name a voice ("use Xander") or a style ("calmer"). The skill does not ask about voices on its own; it offers the samples when you ask, or when you say the voice is not to your taste.

### Names in another language

Titles and names in another language (English song titles in a Dutch intro) are spoken by a voice for their own language, so you hear the voice change, like a bilingual host. If you find that strange, ask for "one voice": the main voice then reads everything, with its accent on the foreign names. The OpenAI voice below reads both languages in one voice, so it has no switch.

### A better computer voice (macOS)

The default voices sound fairly robotic. For a better one, download another voice in *System Settings → Accessibility → Read & Speak*, via the ⓘ button next to *System voice* (older macOS: *Spoken Content → System voice → Manage Voices*). Once it is installed, the skill uses it automatically if it is marked Premium or Enhanced, or you can name it.

## A better voice: OpenAI (optional)

If the computer's voice is too robotic, you can ask for an OpenAI voice. In a test with Dutch intros containing English band and song names, it sounded clearly better than the computer's voice, English names included. The default is a high-energy radio host, spoken a bit faster than normal (speed 1.2). You can ask for another voice, style or pace.

**What you need**
- An OpenAI API account with some credit. The API is billed separately from a ChatGPT subscription.
- An API key in the environment variable `OPENAI_API_KEY`. Set it in the shell that starts your AI tool, and never paste it into the chat.
- A show of ten tracks costs a few cents. The skill gives an estimate first.

**Keep the key small**
- Create a separate project with a small monthly budget.
- Use a *restricted* key that allows only **Text-to-speech (/v1/audio/speech)**, with everything else on None.
- Revoke it when you no longer need it.

**What is sent, and your consent**
- Before anything is sent, the skill tells you what is sent (the intro texts and the style instructions, to `api.openai.com`), roughly what it costs, and that the voice is AI-generated (OpenAI's rules ask you to say so when others listen). It waits for your yes.
- Your yes is tied to exactly what was shown. If a text, the voice, the pace or the style changes afterwards, the script refuses to send until you have agreed again.
- If a request fails, that intro uses the computer's voice.
- Tried so far with one account on macOS. If you see "no credit left", add credit or raise the project's budget.

## Show notes (optional)

Ask for "show notes" and you get a Markdown file with the running order, the intro for every track and the sources the facts came from. It is only written when you ask, and never overwrites an existing file.

## Safety and limits

**Safety**
- It never starts playing without your go-ahead. Ctrl+C stops it and restores the volume.
- It plays, pauses and changes the volume. It also puts the next track of the list at the front of your Spotify queue, one at a time. Your own queued tracks stay behind it. When the show ends or you stop it, a queued track that never started is taken out again. After the last track, the first track of your own queue (if you have one) starts and is paused.
- Playlist names, descriptions, track titles and web pages are treated as untrusted data. The agent takes facts from them and ignores any instructions inside.
- The API key is only read by the script itself, from the environment variable. It is not passed on to the programs the script starts.
- Nothing is downloaded or installed.

**Limits**
- **macOS only, for now.** The skill has only been tested on macOS. The Windows (built-in speech) and Linux (`espeak-ng`) code paths exist but have never been run, and `spotify_cli` itself is untested there.
- It needs Python 3.9 or newer.
- Controlling playback through `spotify_cli` may need a Spotify Premium account (not verified).
- Facts in the intros come from the web and from Spotify's own data. When nothing reliable is found, the intro stays short instead of inventing trivia.
