# WordCounter Privacy Policy

Last updated: October 5, 2026

This policy explains what the WordCounter Discord bot ("WordCounter", "we") collects, why, and what you can do about it. WordCounter is run by Botzilla, based in Washington State, USA.

## What we store

WordCounter counts activity. It doesn't keep what people say.

**Counts, per member, per channel and per month:**
- number of words, messages, attachments (files, stickers and links) and emojis
- how many times each tracked keyword was said

**Server settings:**
- which channels and categories are tracked or ignored
- the keywords the server's admins chose to track
- which members have had their past messages analyzed

**Records that keep counts accurate:**
- when tracking was turned on or off and when counts were reset, so past messages are never counted twice
- while `/analyze_chat` is running, the ID of the last message read in each channel, so it can pick up where it left off after a restart. These are deleted when the analysis finishes.

**Identifiers:** Discord IDs for servers, channels and members, so counts can be attached to the right place and person.

## What we don't store

- **Message content.** WordCounter reads a message to count it, then discards the text. This also applies when `/analyze_chat` reads past messages.
- Attachments, images or files themselves.
- Direct messages.

## Logs and join announcements

- **Error logs** may include a member's Discord username and ID, and the name and ID of the server and channel where an error happened. We use them only to fix bugs.
- **Join and leave logs** record a server's name, ID and owner when WordCounter is added or removed.
- **Join and leave posts.** When WordCounter joins or leaves a server, a message is posted in a public channel of our [support server](https://discord.gg/prUsgFHvRS) showing that server's name, ID, icon, member count and owner's username. Anyone in the support server can see these posts. Server owners can ask us to delete them.

Logs are deleted automatically after 90 days at most.

## How we use it

Only to run the bot: leaderboards, member stats, monthly history and keyword counts inside the server where the activity happened, plus the logs and posts described above. We don't sell data or use it for ads.

## Where it's stored

WordCounter runs on [Wispbyte](https://wispbyte.com), a hosting company. The database and logs are stored on their servers. Apart from that hosting and the join and leave posts above, we don't share data with anyone.

## Who can see it

Anyone in a server can view that server's leaderboards and member stats with WordCounter's commands. Stats from one server are never shown in another.

## Keeping and deleting data

Stats and settings are kept until they're reset or deleted. They aren't deleted automatically when WordCounter is removed from a server.

- Server admins can reset a member's counts, a channel's counts, or everything for the server in `/settings` > Data & Reset Tools.
- Anyone can ask us to delete their data, and server owners can ask us to delete all of a server's data, in the [support server](https://discord.gg/prUsgFHvRS). We finish deleting it within 90 days of the request.

## Children

Discord requires users to be at least 13 (or older where local law says so). WordCounter is meant for the same audience.

## Changes

If this policy changes, we'll update the date above and announce it in the support server.

## Contact

Reach us in the [support server](https://discord.gg/prUsgFHvRS). It's the only way to contact us.
