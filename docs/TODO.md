# Helm Roadmap & Tasks

## Completed Tasks
- [x] Create a Bash installer (`setup.sh`)
  - [x] Automate installation of Docker containers for Jackett, Flaresolverr, and qBittorrent
  - [x] Implement Permanent / Ephemeral run modes
- [x] Implement interactive user configuration
  - [x] Config wizard gathers all required info through input prompts
  - [x] Store state gracefully in `config.json`
- [x] Fix exception handling in `core/rss_fetcher.py`
- [x] Implement proper qBittorrent login flow
  - [x] Ensure the user is actually authenticated via requests session
- [x] Integrate VPN Support (Gluetun for secure torrenting)
- [x] Automate Docker image publishing (GitHub Container Registry via GitHub Actions)

## Planned / Upcoming Tasks

### 0. Architecture & Systems
- [x] **Global Logging System:** Implemented a centralized Python `logging` architecture that writes structured logs to `~/.local/state/helm/logs/helm.log` (or Windows equivalent) for easy debugging of background tasks and failed plugin fetches.
- [x] **Advanced Configuration Management:** Expanded `ConfigManager` to support schema validations, OS-native paths (XDG standards), and seamless migrations when updating to newer Helm versions.

### 1. User Interface & Experience
- [ ] **Scrumptious TUI Interface:** Rewrite the CLI interface using a modern framework (like `Textual` or `Rich`) to replace raw `termios` handling.
- [x] **"Zero Setup" Lite Mode:** 
  - [x] Implement a fallback scraper inside Python for 2-3 hardcoded public indexers.
  - [x] Allow users to search and get Magnet links instantly *without* Docker or Jackett installed.

### 2. Tracker Management
- [ ] **Prowlarr Integration (Optional Mode):** 
  - [ ] Give users a choice during setup between Jackett and Prowlarr.
  - [ ] Implement API handlers for Prowlarr so users who prefer modern tracker syncing can use it seamlessly.
- [x] **Improve Jackett Interaction:** 
  - [x] Allow adding/removing indexers directly from the CLI.
  - [ ] Bundle preconfigured tracker JSONs to skip Jackett's manual web setup.

### 3. Distribution & Installation
- [x] **Publish to PyPI (`pip install helm-torrent`):**
  - [x] Restructure directory to standard Python package layout.
  - [x] Implement `helm init` to automatically bootstrap the Docker environment on first run.
- [x] **Fully Automated Setup Script:**
  - [x] Add a "skip-prompts" flag to `setup.sh` that auto-generates passwords and pulls Jackett API keys completely silently.
  - [x] Provide a curl one-liner for instant terminal installation.

### 4. Headless & Server Mode
- [ ] **Daemon Background Processes:** Add a `--daemon` flag to run operations silently in the background.
- [ ] **Watch Directory (`helm watch <dir>`):** Automatically parse and download any `.torrent` or magnet files dropped into a specified folder.
- [ ] **HTTP Server (`helm serve` / `helm files`):** Spin up a lightweight web server to accept magnets remotely and stream finished downloads.
- [ ] **Session Attach (`helm attach`):** Allow reattaching to a running TUI session across SSH disconnects.

### 5. AI & Automation
- [ ] **AI Torrent Agent (Natural Language):** Integrate an LLM (Gemini/OpenAI) so users can type natural language commands (e,g. *"Download the LOTR Extended Editions in 4K"*). The AI will autonomously map the intent to search queries, evaluate the seeders/sizes of the results, pick the optimal magnet, and send it to qBittorrent.
