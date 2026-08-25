with open("src/helm/cli.py", "r") as f:
    content = f.read()

# Let's see the current lines
lines = content.split("\n")
for i, line in enumerate(lines[:50]):
    if "parser = argparse.ArgumentParser" in line:
        print(f"Line {i}: {line!r}")
