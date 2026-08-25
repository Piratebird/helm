
with open("setup.sh", "r") as f:
    content = f.read()

# Inside docker-compose.yml, replace "$HELM_STATE" with __HELM_STATE__ so we can sed it later
# Actually, the easiest way is just to add a block after EOF that does sed on docker-compose.yml

sed_block = """
sed -i "s|\\\"\\$HELM_STATE\\\"|$HELM_STATE|g" docker-compose.yml
sed -i "s|\\\"\\$HELM_CONFIG\\\"|$HELM_CONFIG|g" docker-compose.yml
sed -i "s|\\\"\\$HELM_DL\\\"|$HELM_DL|g" docker-compose.yml
"""

content = content.replace("EOF\nfi\n\ncat << EOF > \"$HELM_STATE\"/.env.docker", f"EOF\nfi\n{sed_block}\ncat << EOF > \"$HELM_STATE\"/.env.docker")

with open("setup.sh", "w") as f:
    f.write(content)
