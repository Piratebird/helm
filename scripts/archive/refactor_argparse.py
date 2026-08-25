import re
import sys

with open("src/helm/cli.py", "r") as f:
    content = f.read()

# Extract everything before parser = argparse.ArgumentParser
match = re.search(r'(    parser = argparse\.ArgumentParser\()', content)
if not match:
    print("Could not find argparse initialization")
    sys.exit(1)
start_idx = match.start()

# Extract everything after args = parser.parse_args()
match_end = re.search(r'    args = parser\.parse_args\(\)\n', content)
if not match_end:
    print("Could not find parse_args")
    sys.exit(1)
end_idx = match_end.end()

new_argparse = """    parser = argparse.ArgumentParser(
        description="Helm - Torrent automation MVP",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Global options
    global_opts = parser.add_argument_group("Global Options")
    global_opts.add_argument("--config-dir", type=str, help="Override the configuration directory")
    global_opts.add_argument("--state-dir", type=str, help="Override the state directory")
    global_opts.add_argument("--dl-dir", type=str, help="Override the downloads directory")
    global_opts.add_argument("-j", "--json", action="store_true", help="Output results as JSON")
    global_opts.add_argument("-l", "--lite", action="store_true", help="Lite mode: bypass Jackett and use public trackers")

    subparsers = parser.add_subparsers(dest="command", title="Commands", metavar="<command>")

    # UI
    ui_parser = subparsers.add_parser("ui", help="Launch the interactive terminal UI (Default if no command given)")
    ui_parser.add_argument("-o", "--oneshot", action="store_true", help="One-shot mode: start stack, download, tear down")

    # Search
    search_parser = subparsers.add_parser("search", help="Search and download via CLI flags without TUI")
    search_parser.add_argument("query", type=str, nargs="+", help="Search query")
    search_parser.add_argument("-t", "--type", type=str, default="video", help="Content type (video, games, etc.)")
    search_parser.add_argument("-a", "--auto", action="store_true", help="Automatically select and send the top torrent")
    search_parser.add_argument("-o", "--oneshot", action="store_true", help="One-shot mode: start stack, download, tear down")

    # Indexers
    idx_parser = subparsers.add_parser("indexers", help="Manage Jackett indexers interactively")

    # Utility Commands
    log_parser = subparsers.add_parser("logs", help="Tail the last 20 lines of the application log")
    path_parser = subparsers.add_parser("paths", help="Print all system data locations (Config, State, Downloads)")
    bug_parser = subparsers.add_parser("bug-report", help="Zip the config and logs to the Desktop for reporting")

    # Parse arguments (inject 'ui' if no command provided)
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1].startswith("-") and not any(c in sys.argv for c in ["search", "indexers", "logs", "paths", "bug-report", "ui"])):
        # If user passed global flags but no subcommand, append 'ui'
        # Actually, it's easier to just parse normally, and if args.command is None, default it to "ui"
        pass
        
    args = parser.parse_args()
    
    if not args.command:
        args.command = "ui"
        
    # Standardize args to not break old logic
    if args.command == "search":
        args.query = " ".join(args.query)
    else:
        args.query = None
        args.type = "video"
        args.auto = False
        
    if args.command != "ui" and args.command != "search":
        args.oneshot = False
        
    args.indexers = (args.command == "indexers")
    args.logs = (args.command == "logs")
    args.paths = (args.command == "paths")
    args.bug_report = (args.command == "bug-report")
"""

content = content[:start_idx] + new_argparse + content[end_idx:]

with open("src/helm/cli.py", "w") as f:
    f.write(content)
print("done")
