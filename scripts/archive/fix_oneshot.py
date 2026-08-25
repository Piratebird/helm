import re

with open("src/helm/cli.py", "r") as f:
    content = f.read()

new_logic = """    if not args.command:
        args.command = "ui"

    if not hasattr(args, 'oneshot'):
        args.oneshot = False

    if args.command == "search":
        args.query = " ".join(args.query)
    else:
        args.query = None
        args.type = "video"
        args.auto = False
"""

content = re.sub(
    r'    if not args.command:\n        args.command = "ui"\n        \n    # Standardize args to not break old logic\n    if args.command == "search":\n        args.query = " ".join\(args.query\)\n    else:\n        args.query = None\n        args.type = "video"\n        args.auto = False',
    new_logic,
    content,
)

with open("src/helm/cli.py", "w") as f:
    f.write(content)
