import re

with open("src/helm/cli.py", "r") as f:
    content = f.read()

# Replace get_logger("") at the top of main()
content = content.replace('get_logger("") # Initialize root logger for all background imports\n', "")

# Replace args.config_path with args.paths
content = content.replace("--config-path", "--paths")
content = content.replace(
    "print the path to the configuration file", "Print all system paths (Config, State, Downloads)"
)

# Replace args.logs logic
old_logs_logic = """        if args.logs:
            from helm.core.config_manager import get_log_dir
            log_file = os.path.join(get_log_dir(), "helm.log")
            print(f"Log file is located at: {log_file}\\n")
            if os.path.exists(log_file):
                print("--- Last 20 lines ---")
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        for line in lines[-20:]:
                            sys.stdout.write(line)
                        print("\\n---------------------")
                except Exception as e:
                    print(f"Could not read log file: {e}")
            else:
                print("Log file does not exist yet.")
            sys.exit(0)

        if args.config_path:
            from helm.core.config_manager import get_config_file
            print(f"Configuration file is located at: {get_config_file()}")
            sys.exit(0)"""

# I will use a regex to replace it because exact match might fail due to spaces
content = re.sub(
    r"        if args\.logs:.*?sys\.exit\(0\).*?if args\.config_path:.*?sys\.exit\(0\)", "", content, flags=re.DOTALL
)

# Let's insert the new logic right after `args = parser.parse_args()`
new_logic = """        if args.config_dir:
            os.environ["HELM_CONFIG_DIR"] = os.path.abspath(args.config_dir)
        if args.state_dir:
            os.environ["HELM_STATE_DIR"] = os.path.abspath(args.state_dir)
        if hasattr(args, 'dl_dir') and args.dl_dir:
            os.environ["HELM_DL_DIR"] = os.path.abspath(args.dl_dir)

        get_logger("") # Initialize root logger after directory overrides

        if args.paths:
            from helm.core.config_manager import get_config_dir, get_log_dir, get_dl_dir
            print(f"\\033[1m\\033[36mHelm Data Locations:\\033[0m")
            print(f"  Configuration: {get_config_dir()}")
            print(f"  State/Logs:    {get_log_dir()}")
            print(f"  Downloads:     {get_dl_dir()}")
            sys.exit(0)

        if args.logs:
            from helm.core.config_manager import get_log_dir
            log_file = os.path.join(get_log_dir(), "helm.log")
            if os.path.exists(log_file):
                print("--- Last 20 lines ---")
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        for line in lines[-20:]:
                            sys.stdout.write(line)
                        print("\\n---------------------")
                except Exception as e:
                    print(f"Could not read log file: {e}")
            else:
                print("Log file does not exist yet.")
            sys.exit(0)"""

content = content.replace("args = parser.parse_args()", f"args = parser.parse_args()\n\n{new_logic}")

# Add the new arguments to argparse
argparse_additions = """        parser.add_argument(
            "--config-dir", type=str, help="Override the directory path for config.json"
        )
        parser.add_argument(
            "--state-dir", type=str, help="Override the directory path for logs and state"
        )
        parser.add_argument(
            "--dl-dir", type=str, help="Override the directory path for downloads"
        )"""

content = content.replace(
    "        args = parser.parse_args()", f"{argparse_additions}\n        args = parser.parse_args()"
)


with open("src/helm/cli.py", "w") as f:
    f.write(content)
print("Done patching cli.py")
