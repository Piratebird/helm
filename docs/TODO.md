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

### 1. User Interface & Experience
- [ ] **Scrumptious TUI Interface:** Rewrite the CLI interface using a modern framework (like `Textual` or `Rich`) to replace raw `termios` handling.
- [ ] **"Zero Setup" Lite Mode:** 
  - [ ] Implement a fallback scraper inside Python for 2-3 hardcoded public indexers.
  - [ ] Allow users to search and get Magnet links instantly *without* Docker or Jackett installed.

### 2. Tracker Management
- [ ] **Prowlarr Integration (Optional Mode):** 
  - [ ] Give users a choice during setup between Jackett and Prowlarr.
  - [ ] Implement API handlers for Prowlarr so users who prefer modern tracker syncing can use it seamlessly.
- [ ] **Improve Jackett Interaction:** 
  - [ ] Allow adding/removing indexers directly from the CLI.
  - [ ] Bundle preconfigured tracker JSONs to skip Jackett's manual web setup.

### 3. Distribution & Installation
- [ ] **Publish to PyPI (`pip install helm-torrent`):**
  - [ ] Restructure directory to standard Python package layout.
  - [ ] Implement `helm init` to automatically bootstrap the Docker environment on first run.
- [ ] **Fully Automated Setup Script:**
  - [ ] Add a "skip-prompts" flag to `setup.sh` that auto-generates passwords and pulls Jackett API keys completely silently.
  - [ ] Provide a curl one-liner for instant terminal installation.
