# WordCounter Privacy Policy

**Effective October 5, 2026 · Last revised October 5, 2026**

> **The short version.** WordCounter stores counts, not conversations. It keeps how many words, messages, attachments, emojis and keywords each member sends in each channel, month by month, plus your server's tracking settings. It reads messages to count them, then throws the text away. It never keeps message content, files or direct messages.

**On this page**

1. [Introduction](#01-introduction)
2. [Information we do not collect](#02-information-we-do-not-collect)
3. [Information we collect and store](#03-information-we-collect-and-store)
4. [Processed, not stored](#04-processed-not-stored)
5. [Third-party services](#05-third-party-services)
6. [Who can see your data](#06-who-can-see-your-data)
7. [Data storage and security](#07-data-storage-and-security)
8. [Your rights and deletion](#08-your-rights-and-deletion)
9. [Children's privacy](#09-childrens-privacy)
10. [Changes to this Privacy Policy](#10-changes-to-this-privacy-policy)
11. [Contact us](#11-contact-us)

## 01. Introduction

This Privacy Policy explains what information **WordCounter** ("we", "us", or "our") collects when you use the WordCounter Discord application, why, and what you can do about it. WordCounter is run by Botzilla, based in Washington State, USA. It is a separate bot from Botzilla, with its own data and its own policy.

## 02. Information we do not collect

WordCounter does not collect or store:

- **The text of your messages.** WordCounter reads each message in a tracked channel so it can count it, then discards the text. The same applies when `/analyze_chat` reads past messages. Only the resulting numbers are kept.
- **Attachments.** Files, images and stickers are counted, never saved.
- **Direct messages.** WordCounter doesn't read or count DMs.
- **Personal details.** We don't ask for or store your real name, email address, phone number or passwords.
- **Payment details.** WordCounter is free and never handles card or bank details.

## 03. Information we collect and store

We store what WordCounter needs to keep counting accurately after a restart, and nothing for advertising or profiling.

- **Discord IDs.** Server, channel and member IDs. These are the numbers Discord uses to tell accounts and channels apart. We use them to attach each count to the right place and person.
- **Counts per member, per channel and per month.** The number of words, messages, attachments (files, stickers and links) and emojis, and how many times each tracked keyword was said.
- **Server settings.** Which channels and categories are tracked or ignored, the keywords the server's admins chose to track, and which members have had their past messages analyzed.
- **Records that keep counts accurate.** When tracking was turned on or off and when counts were reset, so past messages are never counted twice. While `/analyze_chat` is running, the ID of the last message read in each channel, so it can pick up where it left off after a restart. These checkpoints are deleted when the analysis finishes.
- **Logs.**
  - **Error logs** may include a member's Discord username and ID, and the name and ID of the server and channel where an error happened. We use them only to fix bugs.
  - **Join logs** record a server's name, ID and owner when WordCounter is added, and its name and ID when it's removed.
  - Log files are deleted automatically after 90 days at most.
- **Join and leave posts.** When WordCounter joins or leaves a server, a message is posted in a public channel of our [support server](https://discord.gg/prUsgFHvRS) showing that server's name, ID, icon, member count and owner's username. Anyone in the support server can see these posts. They aren't deleted automatically. Server owners can ask us to remove the ones about their server.

The planned Swear Jar feature will count swearing per member in the same way. This policy will be updated before it launches.

## 04. Processed, not stored

These actions use your data once and keep only numbers:

- **Live counting:** each new, edited or deleted message is read, counted or recounted, and its text discarded.
- **Past-message analysis (`/analyze_chat`):** past messages are read the same way, either through Discord's message search for one member or by reading channel history for the whole server.
- **Message Word Count (right-click app):** the message you pick is counted and the result shown to you. Nothing is saved.

## 05. Third-party services

WordCounter talks only to Discord, to read messages and send replies. It doesn't send your data to any other API.

WordCounter runs on [Wispbyte](https://wispbyte.com), a hosting company. The database and logs are stored on their servers. Apart from that hosting and the join and leave posts described in section 03, we don't share data with anyone, and we never sell it or use it for ads.

## 06. Who can see your data

Anyone in a server can view that server's leaderboards and member stats with WordCounter's commands. Stats from one server are never shown in another.

## 07. Data storage and security

Counts and settings live in a database file on the bot's host that only the bot can reach. No system is perfectly secure, but we keep access limited to the bot and the developer.

## 08. Your rights and deletion

Stats and settings are kept until they're reset or deleted. They aren't deleted automatically when WordCounter is removed from a server.

- **Server admins:** reset a member's counts, a channel's counts, or everything for the server in `/settings` > Data & Reset Tools.
- **Re-analysis:** **Allow re-analysis** in `/settings` clears one member's counts so their history can be rebuilt.
- **Everything else:** anyone can ask us to delete their data, and server owners can ask us to delete all of a server's data, in our [support server](https://discord.gg/prUsgFHvRS). We finish deleting it within 90 days of the request.

## 09. Children's privacy

In line with Discord's Terms of Service, WordCounter is not meant for anyone under 13 (or under the minimum legal age for digital consent where you live). We do not knowingly collect data from children under 13.

## 10. Changes to this Privacy Policy

We may revise this Privacy Policy when we add features or the law requires it. When we do, we'll change the "Last revised" date at the top and announce it in the support server.

## 11. Contact us

For privacy questions or deletion requests, join our [Discord support server](https://discord.gg/prUsgFHvRS). It's the only way to contact us.
