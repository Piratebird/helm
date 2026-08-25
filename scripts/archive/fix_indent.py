
with open("src/helm/cli.py", "r") as f:
    content = f.read()

content = content.replace("                parser = argparse.ArgumentParser(", "    parser = argparse.ArgumentParser(")
content = content.replace("            description=\"Helm - Torrent automation MVP\",", "        description=\"Helm - Torrent automation MVP\",")
content = content.replace("            formatter_class=argparse.RawTextHelpFormatter", "        formatter_class=argparse.RawTextHelpFormatter")
content = content.replace("        )\n\n        actions = ", "    )\n\n    actions = ")

content = content.replace("        actions.add_argument", "    actions.add_argument")
content = content.replace("        search_opts = ", "    search_opts = ")
content = content.replace("        search_opts.add_argument", "    search_opts.add_argument")
content = content.replace("        exec_opts = ", "    exec_opts = ")
content = content.replace("        exec_opts.add_argument", "    exec_opts.add_argument")
content = content.replace("        path_opts = ", "    path_opts = ")
content = content.replace("        path_opts.add_argument", "    path_opts.add_argument")
content = content.replace("        args = parser.parse_args()", "    args = parser.parse_args()")

with open("src/helm/cli.py", "w") as f:
    f.write(content)
