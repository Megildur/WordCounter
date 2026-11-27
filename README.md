# WordCounter

<p align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1551875701748277299&permissions=277295131712&scope=bot+applications.commands&integration_type=0">
    <img src="https://img.shields.io/badge/Invite%20Bot-Discord%20App-B85A24?style=for-the-badge&logo=discord&logoColor=white" alt="Invite Bot" />
  </a>
  &nbsp;
  <a href="https://discord.gg/prUsgFHvRS">
    <img src="https://img.shields.io/badge/Support%20Server-Join%20Community-1D335B?style=for-the-badge&logo=discord&logoColor=white" alt="Support Server" />
  </a>
  &nbsp;
  <a href="https://wordcounter.dev">
    <img src="https://img.shields.io/badge/Website-wordcounter.dev-1D335B?style=for-the-badge" alt="Website" />
  </a>
  &nbsp;
  <a href="https://github.com/Megildur/WordCounter/releases/tag/v4.1.4">
    <img src="https://img.shields.io/badge/Release-v4.1.4-8C96AC?style=for-the-badge" alt="Release v4.1.4" />
  </a>
</p>

A Discord bot that counts **words**, **messages**, **attachments** (including stickers and links), **emojis** (Unicode and custom) and **custom keywords** in your server. It keeps monthly history per channel and can count messages sent before it joined.

## Links

| | |
| :--- | :--- |
| Invite WordCounter | [Add the bot to a server](https://discord.com/oauth2/authorize?client_id=1551875701748277299&permissions=277295131712&scope=bot+applications.commands&integration_type=0) |
| Website and dashboard | [wordcounter.dev](https://wordcounter.dev) |
| Support server | [discord.gg/prUsgFHvRS](https://discord.gg/prUsgFHvRS) |
| Support email | [support@wordcounter.dev](mailto:support@wordcounter.dev) |
| Source and releases | [Megildur/WordCounter](https://github.com/Megildur/WordCounter) |
| Privacy Policy | [wordcounter.dev/privacy](https://wordcounter.dev/privacy) |
| Terms of Service | [wordcounter.dev/terms](https://wordcounter.dev/terms) |

## What it does

- **Live tracking.** Every message in a tracked channel is counted as it's sent, and counts are corrected when a message that was counted is edited or deleted. A message from before tracking, from a gap while it was off, or from before a reset changes the counts only if `/analyze_chat` counted it, because its counts were never in the totals (after a reset, only if the analysis ran after it).
- **Monthly history.** Everything is grouped by month and channel, both for live tracking and for past messages.
- **Member stats** (`/stats user`). Totals at the top, then a month-by-month view you can page through, with a per-channel breakdown.
- **Leaderboards** (`/leaderboard [channel]`). Switch between words, messages, attachments, emojis, keywords and swears. Pick a channel to see its top members and monthly history.
- **Past messages** (`/analyze_chat`). Counts messages that weren't counted live:
  - WordCounter records when tracking is turned on and off, so messages already counted live are skipped and nothing is counted twice. Gaps when tracking was off are filled in, and messages wiped by a reset are counted again.
  - `single_user` uses Discord's message search to find one member's messages, usually in a few minutes. If search isn't available for the server, it reads channel history instead.
  - `whole_server` reads every tracked channel, voice chat and thread (active and archived) once, 100 messages per request, and credits each message to its author. Channels the bot can't read are listed at the end so you know what was left out.
  - Channel reads save a checkpoint every minute. If the bot restarts mid-run, it picks up where it stopped on its own.
  - Each member is analyzed once. To redo it, **Allow re-analysis** in `/settings` clears that member's counts first, so the next run rebuilds their full history without counting anything twice.
- **Website** ([wordcounter.dev](https://wordcounter.dev)). Log in with Discord to see your own stats in every server you share with WordCounter. In servers where you have Manage Server, you also get the leaderboards, every member's stats, the same settings and resets as `/settings`, and the swear jar's settings. The dashboard also lists the servers you manage that don't have WordCounter yet, each with a button to add it.
- **Settings** (`/settings`). Track the whole server with an ignore list, or only the channels and categories you pick. Manage keywords, and reset all counts for a member, a channel or both, or reset the whole server: its stats and settings, its swear jar, or both, after you pick which.
- **A hello when it joins.** WordCounter posts a short welcome with its banner in the server's system channel (or the first channel where it can attach a picture): how to turn counting on, `/help`, and Botzilla and its game from the same lab. Nobody is pinged.
- **Swear jar** (`/swearjar`). Off until an admin turns it on in `/swearjar settings`. Every swear costs, and debt is only paid back by not swearing:
  - Swears are fined by tier: mild (1 unit), explicit (4) and slurs or the server's own words (10). A message is fined for at most its 3 priciest swears. Fines show as money ($0.25 a unit by default) or the server's own tokens.
  - The default list covers English swears and slurs, and Polish ones (with their word forms) for servers that turn Polish on. English is on by default and Polish is off; staff switch each language in `/swearjar settings` or on the dashboard, which shows one language's words at a time. Matching ignores case and accents and catches leetspeak (`sh!t`), stretched letters (`fuuuck`), spaced letters (`f u c k`) and starred words (`f*ck`). Staff can add, remove or move words.
  - WordCounter reacts with 🫙 and posts nothing. In delete mode it deletes the message instead and shows a notice for 5 seconds saying why and what the fine was. Deleting or editing a message never refunds a fine.
  - More than 3 fined messages in a minute doubles fines for 10 minutes (reaction 🚫).
  - A clean active day (5 or more messages, no fines, UTC) pays back 1 unit, with bonuses at 7 days (+3) and every 30 days (+10). Members who never swear build up a wallet.
  - Optional roles: Potty Mouth for the week's most fined member (moves every Monday, UTC) and a clean streak role at 14 days. A server goal posts once when the jar collects enough.
  - It listens wherever tracking does, with exceptions per channel and a switch for age-restricted channels. `/analyze_chat swears` adds past swears to the Swears leaderboard without fining anyone.

## Commands

| Command | Who can use it | What it does |
| :--- | :--- | :--- |
| `/help [ephemeral]` | Everyone | A guide to every command, opening on a pixel-art banner, with invite links. Needs Attach Files where it's used. |
| `/advertisement` | Everyone | Posts a card about WordCounter with its pixel-art poster and invite links. Needs Attach Files where it's used. |
| `/leaderboard [channel]` | Everyone | Leaderboards for words, messages, attachments, emojis, keywords and swears, with the top three on a pixel-art podium. Needs Attach Files. |
| `/stats user [member]` | Everyone | Your stats card, or a member's (their words on WordCounter's flip counter), totals and monthly and per-channel history. Needs Attach Files. |
| `/keyword list` | Everyone | The keywords this server tracks. |
| `/settings` | Manage Server | Tracking mode, channels, keywords and data resets. |
| `/analyze_chat single_user <member>` | Manage Server | Counts one member's past messages. |
| `/analyze_chat whole_server` | Manage Server | Counts past messages for every member not analyzed yet. |
| `/analyze_chat swears` | Manage Server | Adds past swears to the Swears leaderboard. Nobody is fined. |
| `/swearjar wallet [member]` | Everyone | A member's swear jar wallet or debt, clean streak and swears, with their jar drawn full of coins or IOU slips. Needs Attach Files. |
| `/swearjar top` | Everyone | Who swears most, owes most, saved most and has the longest clean streak. |
| `/swearjar goal` | Everyone | What the swear jar is saving up for. |
| `/swearjar settings` | Manage Server | Turn the swear jar on, and set fines, words, channels, roles and a goal. |
| `/owner delete_data <User/Server> <id>` | Administrator | Find everything stored under a user's or server's ID in every table, and delete it once you confirm. |
| `/owner export_data <User/Server> <id>` | Administrator | Post a text file in the channel with every row stored under the ID, table by table. |
| `/owner stats_panel [channel] [remove]` | Administrator | Post WordCounter's live stats in a channel, or move the panel there if this server already has one. The panel is a pixel-art card with the numbers on it and the same numbers in text under it: servers, unique users (each person once, bots never counted, the same count the home page shows), slash commands, words and messages counted, latency and how long WordCounter has been up since it last started. It is edited every 5 minutes, posted again if its message is deleted, and forgotten if its channel is deleted (one deleted while WordCounter was offline is noticed once it's back) or WordCounter leaves the server. Use either `channel` or `remove`, not both. With no options it says where the panel is, and `remove` deletes it. |
| `/owner kept_data <User/Server> <id>` | Administrator | After a deletion, post in the channel what WordCounter still keeps under the ID (a ban from WordCounter) and why, quoting the Privacy Policy's "What we keep after a deletion request" and the GDPR articles it relies on. Refuses until everything a deletion removes is gone. |
| `/owner blacklist <Add/Remove/List> [kind] [id] [reason]` | Administrator | Ban a user or server from WordCounter, lift a ban, or list them. A banned user's messages aren't counted, and their commands, buttons and dashboard answer that they're banned. In servers where the swear jar removes swearing, it still removes theirs, without a fine. Adding a ban deletes their data once you confirm. |

Right-click menus:
- **User Stats**: right-click a member, then Apps > User Stats.
- **Message Word Count**: right-click a message, then Apps > Message Word Count.
- **Swear Jar**: right-click a message, then Apps > Swear Jar to see its fine.

## Self-hosting

### Requirements
- Python 3.10 or newer
- A MySQL 8 (or MariaDB 10.5+) database for WordCounter, separate from Botzilla's
- A [Discord application](https://discord.com/developers/applications) with the **Message Content**, **Server Members**, and **Presence** intents turned on (Presence is used for the home-page developer card)

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
   BASE_URL=https://wordcounter.dev
   WEB_PORT=8080
   DISCORD_CLIENT_SECRET=your_client_secret_here
   SESSION_SECRET=a_long_random_string
   MYSQL_HOST=your_mysql_host
   MYSQL_PORT=3306
   MYSQL_USER=wordcounter
   MYSQL_PASSWORD=your_mysql_password
   MYSQL_DB=wordcounter
   ```
   - `API_TOKEN`: your bot token.
   - `BOT_SERVER`: support server invite shown in help and error messages.
   - `SUPPORT_EMAIL`: optional, the support address shown next to it on the website, in help and in error messages. It is `support@wordcounter.dev` if it isn't set.
   - `BOT_WEBHOOK_URL`: optional webhook that gets a message when the bot joins or leaves a server.
   - `ALLOWED_GUILDS`: servers where the `/owner` commands (administrators only) are available.
   - `BASE_URL`: the public address of the website, without a trailing slash.
   - `WEB_PORT`: the port the website listens on. If it isn't set, `SERVER_PORT` is used, then `8080`. `WEB_HOST` defaults to `0.0.0.0`.
   - `DISCORD_CLIENT_SECRET`: from **OAuth2** in the Developer Portal. Without it the website still runs, but logging in is turned off.
   - `SESSION_SECRET` and `SESSION_SECRET_PREVIOUS`: `SESSION_SECRET` (at least 32 random characters) signs login cookies, CSRF tokens and OAuth states via isolated HMAC subkeys. Set `SESSION_SECRET_PREVIOUS` to the old value while rotating keys so active sessions stay valid without logging people out.
   - `ACTIVITY_FLUSH_SECONDS`: how long the counts from new messages wait in memory so they are written to the database together, `2` if it isn't set. `0` writes every message at once. A crash or a kill can lose the counts from the last few seconds.
   - `CLIENT_IP_HEADER` and `TRUSTED_PROXY_CIDRS`: optional, turning on limiting requests for each visitor's address. Give `CLIENT_IP_HEADER` `cloudflare` behind Cloudflare: it reads `CF-Connecting-IP` on connections from Cloudflare's published address ranges or carrying a verified enforcing `X-Origin-Secret` (for NATted origins), and otherwise limits nothing by address, because the connecting address can be one shared by every visitor. Give it `peer` if nothing stands in front of the website, or a custom header name paired with `TRUSTED_PROXY_CIDRS` (comma-separated trusted proxy CIDRs). Without `CLIENT_IP_HEADER` nothing is limited by address.
   - `ORIGIN_SECRET` and `ORIGIN_SECRET_MODE`: optional, for a website behind Cloudflare whose server address can also be reached directly. Add a Cloudflare request-header rule (Rules > Transform Rules > Modify Request Header) that sets `X-Origin-Secret` to a long random value on every request to your domain, and give the same value to `ORIGIN_SECRET`. When `ORIGIN_SECRET` is set, `ORIGIN_SECRET_MODE` defaults to `enforce` (requests without the secret get a bare 403). Set `ORIGIN_SECRET_MODE=log` explicitly while testing a new rule; switching to `enforce` before the rule exists takes the website down for everyone. The start-up log says which state is in force (off, logging or enforcing), and while enforcing it notes the first few refusals and then how many were refused, at most every 10 minutes.
   - `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`: the MySQL database WordCounter keeps everything in (the same names Botzilla uses). Give it its own database; WordCounter creates its tables on first start. `MYSQL_PORT` defaults to 3306 and `MYSQL_DB` to `wordcounter`.

5. Moving from SQLite: WordCounter used to keep its data in `Data/WordCounter.db`. The first time it starts on MySQL with that file in place, it copies every table into MySQL, checks each one's row count and records that it's done, then starts. If anything doesn't copy, it stops with the reason instead of starting on half the data; restart it to try again (rows are replaced, never counted twice).

6. Run the bot:
   ```bash
   python main.py
   ```

7. Invite the bot to a server in `ALLOWED_GUILDS`. In the Developer Portal, under **OAuth2 > URL Generator**, pick the `bot` and `applications.commands` scopes and these permissions (integer `277295131712`):
   - View Channels, Read Message History, Send Messages, Send Messages in Threads, Connect (for reading tracked voice channel chats in `/analyze_chat`)
   - Embed Links, Attach Files, Add Reactions, Use Slash Commands
   - Manage Messages (swear jar delete mode), Manage Roles (swear jar roles), Mention Everyone (swear jar goal pings)

   ```text
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=277295131712&scope=bot+applications.commands&integration_type=0
   ```

   `/analyze_chat whole_server` can read private archived threads only if the bot has **Manage Threads** or was added to those threads.

8. Set up website logins. In the Developer Portal, under **OAuth2 > Redirects**, add `BASE_URL` followed by `/callback`, for example:
   ```text
   https://wordcounter.dev/callback
   ```
   The website starts with the bot and asks Discord who you are and which servers you are in (the `identify` and `guilds` scopes). The server list is only used to show the servers you manage on the dashboard, and is kept in memory for an hour.

9. Sync slash commands. In a server listed in `ALLOWED_GUILDS`, send:
   ```text
   !wcquicksync
   ```
   then run `/owner sync sync_type:Guild` (or `Global` to publish everywhere).

## Support

Questions, bug reports and ideas go in the [support server](https://discord.gg/prUsgFHvRS), or by email to [support@wordcounter.dev](mailto:support@wordcounter.dev).
