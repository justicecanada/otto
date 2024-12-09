#!/bin/bash

git config --global --add safe.directory .
git config pull.rebase false
# With user input, set the username and email
echo " >>> Set your git username and email? [y/N]:"
read input
if [[ $input == "Y" || $input == "y" ]]; then
        echo "Enter your git username:"
        read username
        git config --global user.name "$username"
        echo "Enter your git email:"
        read email
        git config --global user.email "$email"
        echo "Git username and email set."
else
        echo "OK, skipping."
fi
echo " >>> Chowning the project directory to the current user. This may take a minute."
sudo chown -R $(whoami) django
# Now run .devcontainer/post-create.sh
bash .devcontainer/post-create.sh
