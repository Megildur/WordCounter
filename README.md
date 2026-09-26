# 📊 WordCounter

<p align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1551875701748277299&integration_type=0">
    <img src="https://img.shields.io/badge/Invite%20Bot-Discord%20App-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Invite Bot" />
  </a>
  &nbsp;
  <a href="https://discord.gg/Hr595Zk2tr">
    <img src="https://img.shields.io/badge/Support%20Server-Join%20Community-57F287?style=for-the-badge&logo=discord&logoColor=white" alt="Support Server" />
  </a>
  &nbsp;
  <a href="https://github.com/Megildur/WordCounter/releases/tag/v1.0.7">
    <img src="https://img.shields.io/badge/Release-v1.0.7-blue?style=for-the-badge" alt="Release v1.0.7" />
  </a>
</p>

A feature-rich Discord bot designed to track **words**, **messages**, **attachments**, **media links**, and **custom keywords** across your Discord server with real-time analytics, monthly history breakdowns, and retroactive past-message analysis.

---

## 🔗 Quick Links & Invites

| Link | Description | URL |
| :--- | :--- | :--- |
| 🤖 **Invite WordCounter** | Add the bot directly to your Discord server | [**Add Bot to Server**](https://discord.com/oauth2/authorize?client_id=1551875701748277299&integration_type=0) |
| 💬 **Discord Support Server** | Get help, report bugs, and suggest features | [**Join Community Server**](https://discord.gg/Hr595Zk2tr) |
| 📦 **GitHub Repository** | View source code and release notes | [**Megildur/WordCounter**](https://github.com/Megildur/WordCounter) |

---

## ✨ Key Features

- **⚡ Real-Time Tracking**: Counts words, messages, attachments, media links, and custom keywords instantly on `on_message`, with accurate updates on edits and deletions.
- **📅 Monthly & Yearly Channel Analytics**: All stats are grouped by month, year, and channel from newest to oldest for both live tracking and historical message sweeps.
- **👤 Interactive User Stats (`/stats user`)**:
  - Displays overall server totals (words, messages, attachments, keywords) at the top.
  - Interactive month-by-month timeline underneath with dropdown navigation (`Month / Year...`) and `◀️ Newer` / `Older ▶️` buttons.
  - Shows per-channel activity breakdowns for the selected month.
- **🏆 Unified Leaderboards (`/leaderboard [channel]`)**:
  - Switch interactively between **Words**, **Messages**, **Attachments**, and **Keywords** using buttons.
  - Optional `channel` filter to view top 10 contributors in a specific channel plus a complete chronological monthly history breakdown (newest to oldest).
- **🔍 Retroactive Chat Deep-Sweep (`/analyze_chat`)**:
  - Scan messages sent before the bot joined the server via Discord's Guild Search API.
  - Supports single-user analysis or whole-server sweeping with live progress tracking, dynamic ETA estimation, skipped-user accounting, and 5.0s anti-ratelimit pacing.
- **⚙️ Interactive Settings Dashboard (`/settings`)**:
  - Switch between **Whole Server Mode** (with ignore lists) or **Specific Channels/Categories Mode**.
  - Add, edit, or remove tracked keywords with instant regex matching.
  - Reset individual user stats, specific channels, or perform a complete server wipe with re-analysis unlock tools.
- **🎨 Modern Components V2 UI**: Clean, containerized Discord layout views with brand-colored embeds and responsive controls.

---

## 📋 Slash Commands Reference

| Command | Scope | Description |
| :--- | :--- | :--- |
| `/help [ephemeral]` | Everyone | Comprehensive command guide, tips, and invite links. |
| `/advertisement` | Everyone | Quick feature summary card with community and invite links. |
| `/leaderboard [channel]` | Everyone | Interactive leaderboard for Words, Messages, Attachments, and Keywords. When filtering by channel, shows top 10 plus channel monthly history. |
| `/stats user <member>` | Everyone | Member stats with overall totals and monthly channel history navigation. |
| `/keyword list` | Everyone | Lists all custom keywords currently watched in the server. |
| `/settings` | Admins (`Manage Server`) | Interactive control dashboard for tracking rules, channels, keywords, and data resets. |
| `/analyze_chat single_user <member>` | Admins (`Manage Server`) | Retroactively scans a member's chat history before the bot joined and groups by month, year, and channel. |
| `/analyze_chat whole_server` | Admins (`Manage Server`) | Sweeps chat history for all eligible non-bot server members with live progress and dynamically scaling ETA. |

### Context Menus
- **User Stats** *(Right-click User -> Apps -> User Stats)*: Opens the interactive user statistics menu.
- **Message Word Count** *(Right-click Message -> Apps -> Message Word Count)*: Shows word count of that specific message.

---

## 🚀 Setup & Self-Hosting

### Prerequisites
- Python 3.10+
- A registered [Discord Bot Application](https://discord.com/developers/applications) with:
  - **Message Content Intent** enabled
  - **Server Members Intent** enabled

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Megildur/WordCounter.git
   cd WordCounter
   ```

2. **Set up a virtual environment**:
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install discord.py aiosqlite
   ```

4. **Environment Variables**:
   Create a `.env` file (or set environment variables) in the root directory:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   BOT_SERVER=https://discord.gg/Hr595Zk2tr
   ```

5. **Run the bot**:
   ```bash
   python main.py
   ```

---

## 🛡️ Support & Community

Need assistance, found an issue, or want to suggest new features?
- **Join our Discord**: [https://discord.gg/Hr595Zk2tr](https://discord.gg/Hr595Zk2tr)
- **Invite WordCounter**: [https://discord.com/oauth2/authorize?client_id=1551875701748277299&integration_type=0](https://discord.com/oauth2/authorize?client_id=1551875701748277299&integration_type=0)
