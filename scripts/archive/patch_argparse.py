import re

with open("src/helm/cli.py", "r") as f:
    content = f.read()

# I will replace the entire argparse block from `parser = argparse.ArgumentParser` to `args = parser.parse_args()`

new_argparse = """        parser = argparse.ArgumentParser(
            description="Helm - Torrent automation MVP",
            formatter_class=argparse.RawTextHelpFormatter
        )

        actions = parser.add_argument_group("Actions / Modes")
        actions.add_argument("-i", "--indexers", action="store_true", help="Manage Jackett indexers")
        actions.add_argument("--logs", action="store_true", help="Print the last 20 lines of the log file")
        actions.add_argument("--paths", action="store_true", help="Print all system paths (Config, State, Downloads)")
        actions.add_argument("--bug-report", action="store_true", help="Zip the config and logs to the Desktop for reporting")

        search_opts = parser.add_argument_group("Search Options")
        search_opts.add_argument("-q", "--query", help="Search query (bypasses input prompt)", type=str)
        search_opts.add_argument("-t", "--type", help="Content type (video, games, etc.)", type=str, default="video")

        exec_opts = parser.add_argument_group("Execution Options")
        exec_opts.add_argument("-o", "--oneshot", action="store_true", help="Start docker stack, search, download, wait, and tear down")
        exec_opts.add_argument("-l", "--lite", action="store_true", help="Lite mode: Search public indexers directly without Jackett")
        exec_opts.add_argument("-a", "--auto", action="store_true", help="Automatically select the top torrent and send it")
        exec_opts.add_argument("-j", "--json", action="store_true", help="Output results as JSON and exit")

        path_opts = parser.add_argument_group("Path Overrides")
        path_opts.add_argument("--config-dir", type=str, help="Override the directory path for config.json")
        path_opts.add_argument("--state-dir", type=str, help="Override the directory path for logs and state")
        path_opts.add_argument("--dl-dir", type=str, help="Override the directory path for downloads")"""

# The regex will match from `parser = argparse.ArgumentParser...` up to right before `args = parser.parse_args()`
content = re.sub(
    r'        parser = argparse\.ArgumentParser\(.*?--dl-dir", type=str, help="Override the directory path for downloads"\n        \)',
    new_argparse,
    content,
    flags=re.DOTALL,
)

with open("src/helm/cli.py", "w") as f:
    f.write(content)
print("done")
