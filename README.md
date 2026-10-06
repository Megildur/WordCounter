# WordCounter

<p align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1551875701748277299&permissions=1126177200925776&scope=bot+applications.commands&integration_type=0">
    <img src="https://img.shields.io/badge/Invite%20Bot-Discord%20App-B62402?style=for-the-badge&logo=discord&logoColor=white" alt="Invite Bot" />
  </a>
  &nbsp;
  <a href="https://discord.gg/prUsgFHvRS">
    <img src="https://img.shields.io/badge/Support%20Server-Join%20Community-D26B42?style=for-the-badge&logo=discord&logoColor=white" alt="Support Server" />
  </a>
  &nbsp;
  <a href="https://wordcounter.wisp.uno">
    <img src="https://img.shields.io/badge/Website-wordcounter.wisp.uno-D26B42?style=for-the-badge" alt="Website" />
  </a>
  &nbsp;
  <a href="https://github.com/Megildur/WordCounter/releases/tag/v1.1.0">
    <img src="https://img.shields.io/badge/Release-v1.1.0-908C90?style=for-the-badge" alt="Release v1.1.0" />
  </a>
</p>

A Discord bot that counts **words**, **messages**, **attachments** (including stickers and links), **emojis** (Unicode and custom) and **custom keywords** in your server. It keeps monthly history per channel and can count messages sent before it joined.

## Links

| | |
| :--- | :--- |
| Invite WordCounter | [Add the bot to a server](https://discord.com/oauth2/authorize?client_id=1551875701748277299&permissions=1126177200925776&scope=bot+applications.commands&integration_type=0) |
| Website and dashboard | [wordcounter.wisp.uno](https://wordcounter.wisp.uno) |
| Support server | [discord.gg/prUsgFHvRS](https://discord.gg/prUsgFHvRS) |
| Source and releases | [Megildur/WordCounter](https://github.com/Megildur/WordCounter) |
| Privacy Policy | [wordcounter.wisp.uno/privacy](https://wordcounter.wisp.uno/privacy) |
| Terms of Service | [wordcounter.wisp.uno/terms](https://wordcounter.wisp.uno/terms) |

## What it does

- **Live tracking.** Every message in a tracked channel is counted as it's sent, and counts are corrected when messages are edited or deleted.
- **Monthly history.** Everything is grouped by month and channel, both for live tracking and for past messages.
- **Member stats** (`/stats user`). Totals at the top, then a month-by-month view you can page through, with a per-channel breakdown.
- **Leaderboards** (`/leaderboard [channel]`). Switch between words, messages, attachments, emojis and keywords. Pick a channel to see its top members and monthly history.
- **Past messages** (`/analyze_chat`). Counts messages that weren't counted live:
  - WordCounter records when tracking is turned on and off, so messages already counted live are skipped and nothing is counted twice. Gaps when tracking was off are filled in, and messages wiped by a reset are counted again.
  - `single_user` uses Discord's message search to find one member's messages, usually in a few minutes. If search isn't available for the server, it reads channel history instead.
  - `whole_server` reads every tracked channel, voice chat and thread (active and archived) once, 100 messages per request, and credits each message to its author. Channels the bot can't read are listed at the end so you know what was left out.
  - Channel reads save a checkpoint every minute. If the bot restarts mid-run, it picks up where it stopped on its own.
  - Each member is analyzed once. To redo it, **Allow re-analysis** in `/settings` clears that member's counts first, so the next run rebuilds their full history without counting anything twice.
- **Website** ([wordcounter.wisp.uno](https://wordcounter.wisp.uno)). Log in with Discord to see your own stats in every server you share with WordCounter. In servers where you have Manage Server, you also get the leaderboards, every member's stats, and the same settings and resets as `/settings`.
- **Settings** (`/settings`). Track the whole server with an ignore list, or only the channels and categories you pick. Manage keywords, and reset all counts for a member, a channel, both, or the whole server.
- **Coming soon: Swear Jar.** Track swearing in chat and see who owes the jar the most.

## Commands

| Command | Who can use it | What it does |
| :--- | :--- | :--- |
| `/help [ephemeral]` | Everyone | Command guide and invite links. |
| `/advertisement` | Everyone | Posts a card about WordCounter with invite links. |
| `/leaderboard [channel]` | Everyone | Leaderboards for words, messages, attachments, emojis and keywords. |
| `/stats user <member>` | Everyone | A member's totals with monthly and per-channel history. |
| `/keyword list` | Everyone | The keywords this server tracks. |
| `/settings` | Manage Server | Tracking mode, channels, keywords and data resets. |
| `/analyze_chat single_user <member>` | Manage Server | Counts one member's past messages. |
| `/analyze_chat whole_server` | Manage Server | Counts past messages for every member not analyzed yet. |

Right-click menus:
- **User Stats**: right-click a member, then Apps > User Stats.
- **Message Word Count**: right-click a message, then Apps > Message Word Count.

## Self-hosting

### Requirements
- Python 3.10 or newer
- A [Discord application](https://discord.com/developers/applications) with the **Message Content** and **Server Members** intents turned on

### Install

1. Clone the repository:
   ```bash
   git clone https://github.com/Megildur/WordCounter.git
   cd WordCounter
   ```

2. Create a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a `.env` file in the project root:
   ```env
   API_TOKEN=your_bot_token_here
   BOT_SERVER=https://discord.gg/prUsgFHvRS
   BOT_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ALLOWED_GUILDS=123456789012345678,987654321098765432
   BASE_URL=https://wordcounter.wisp.uno
   WEB_PORT=8080
   DISCORD_CLIENT_SECRET=your_client_secret_here
   SESSION_SECRET=a_long_random_string
   ```
   - `API_TOKEN`: your bot token.
   - `BOT_SERVER`: support server invite shown in help and error messages.
   - `BOT_WEBHOOK_URL`: optional webhook that gets a message when the bot joins or leaves a server.
   - `ALLOWED_GUILDS`: servers where the owner-only `/owner` commands are available.
   - `BASE_URL`: the public address of the website, without a trailing slash.
   - `WEB_PORT`: the port the website listens on. If it isn't set, `SERVER_PORT` is used, then `8080`. `WEB_HOST` defaults to `0.0.0.0`.
   - `DISCORD_CLIENT_SECRET`: from **OAuth2** in the Developer Portal. Without it the website still runs, but logging in is turned off.
   - `SESSION_SECRET`: signs login cookies. Changing it logs everyone out.

5. Run the bot:
   ```bash
   python main.py
   ```

6. Invite the bot to a server in `ALLOWED_GUILDS`. In the Developer Portal, under **OAuth2 > URL Generator**, pick the `bot` and `applications.commands` scopes and these permissions (integer `1126177200925776`):
   - View Channels, Read Message History, Send Messages, Send Messages in Threads
   - Embed Links, Attach Files, Add Reactions, Use Slash Commands, Use External Apps
   - Manage Channels, Manage Messages, Manage Roles, Mention Everyone (for the upcoming Swear Jar)

   ```text
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=1126177200925776&scope=bot+applications.commands&integration_type=0
   ```

   `/analyze_chat whole_server` can read private archived threads only if the bot has **Manage Threads** or was added to those threads.

7. Set up website logins. In the Developer Portal, under **OAuth2 > Redirects**, add `BASE_URL` followed by `/callback`, for example:
   ```text
   https://wordcounter.wisp.uno/callback
   ```
   The website starts with the bot and only asks Discord who you are (the `identify` scope).

8. Sync slash commands. In a server listed in `ALLOWED_GUILDS`, send:
   ```text
   !wcquicksync
   ```
   then run `/owner sync sync_type:Guild` (or `Global` to publish everywhere).

## Support

Questions, bug reports and ideas go in the [support server](https://discord.gg/prUsgFHvRS).
