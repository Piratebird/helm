with open("src/helm/cli.py", "r") as f:
    content = f.read()

# Remove -j and -l from global_opts
content = content.replace(
    '    global_opts.add_argument("-j", "--json", action="store_true", help="Output results as JSON")\n', ""
)
content = content.replace(
    '    global_opts.add_argument("-l", "--lite", action="store_true", help="Lite mode: bypass Jackett and use public trackers")\n',
    "",
)

# Add to ui_parser
content = content.replace(
    'ui_parser.add_argument("-o", "--oneshot", action="store_true", help="One-shot mode: start stack, download, tear down")',
    'ui_parser.add_argument("-o", "--oneshot", action="store_true", help="One-shot mode: start stack, download, tear down")\n    ui_parser.add_argument("-l", "--lite", action="store_true", help="Lite mode: bypass Jackett and use public trackers")',
)

# Add to search_parser
content = content.replace(
    'search_parser.add_argument("-o", "--oneshot", action="store_true", help="One-shot mode: start stack, download, tear down")',
    'search_parser.add_argument("-o", "--oneshot", action="store_true", help="One-shot mode: start stack, download, tear down")\n    search_parser.add_argument("-l", "--lite", action="store_true", help="Lite mode: bypass Jackett and use public trackers")\n    search_parser.add_argument("-j", "--json", action="store_true", help="Output results as JSON")',
)

# Fix missing attributes
fix_attrs = """
    if not hasattr(args, 'lite'):
        args.lite = False
    if not hasattr(args, 'json'):
        args.json = False
"""
content = content.replace("    if not hasattr(args, 'oneshot'):", fix_attrs + "    if not hasattr(args, 'oneshot'):")

with open("src/helm/cli.py", "w") as f:
    f.write(content)
