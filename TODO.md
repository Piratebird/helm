- [ ] Create a Bash installer
  - [ ] Automate installation of Docker containers for Jackett and Flaresolverr
  - [ ] Optionally register them as systemd services based on user choice

- [ ] Replace qBittorrent Web UI automation
  - [ ] Use the libtorrent library for terminal-based automation
  - [ ] Potentially remove reliance on qBittorrent entirely

- [ ] Implement interactive user configuration
  - [ ] Gather all required info through input prompts
  - [ ] Remove the need for a .env file

- [ ] Fix exception handling in core/rss_fetcher.py

- [ ] Implement proper qBittorrent login flow
  - [ ] Ensure the user is actually authenticated
  - [ ] Replace current temporary solution

- [ ] Add a TUI interface
  - [ ] Consider using Textual or alternative TUI frameworks

- [ ] Improve indexer management
  - [ ] Include more default indexers
  - [ ] Allow adding and removing indexers
  - [ ] Improve overall interaction with the Jackett API
