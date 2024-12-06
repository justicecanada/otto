#!/bin/bash

if ! command -v az &> /dev/null
then
        curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
fi
az logout
az login
python .devcontainer/post-create.py
echo " >>> RESET app data, load corporate data, etc? [y/N]:"
read input
if [[ $input == "Y" || $input == "y" ]]; then
# Set the working directory to /django
        cd django
        bash initial_setup.sh
else
        echo "OK, skipping. Run 'bash .devcontainer/post-create.sh' again if you change your mind."
fi
echo " >>> Configure kubectl, zsh? [y/N]:"
read input
if [[ $input == "Y" || $input == "y" ]]; then
        sudo az aks install-cli
        az account set --subscription 86ca3d9f-ad5e-4c04-8dea-af9a9802e459
        # Set azure environment variables in zshrc
        echo "export AZURE_DEFAULTS_GROUP=OttoSANDBOXRg" >> ~/.zshrc
        echo "export AZURE_DEFAULTS_LOCATION=canadacentral" >> ~/.zshrc
        az aks get-credentials --resource-group OttoSANDBOXRg --name jus-sandbox-otto-aks --overwrite-existing
        kubelogin convert-kubeconfig -l azurecli
        source <(kubectl completion zsh)  # set up autocomplete in zsh into the current shell
        alias k="kubectl"
        echo '[[ $commands[kubectl] ]] && source <(kubectl completion zsh)' >> ~/.zshrc # add autocomplete permanently to your zsh shell
        echo 'alias k="kubectl"' >> ~/.zshrc
        kubectl config set-context --current --namespace=otto
        echo "Setting up helm..."
        curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg > /dev/null
        sudo apt-get install apt-transport-https --yes
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list
        sudo apt-get update
        sudo apt-get install helm
        echo "Installing cert manager CLI..."
        mkdir cmctl-tmp
        curl -fsSL -o cmctl-tmp/cmctl.tar.gz https://github.com/cert-manager/cmctl/releases/download/v2.1.0-alpha.0/cmctl_linux_amd64.tar.gz
        tar -xzf cmctl-tmp/cmctl.tar.gz -C cmctl-tmp
        chmod +x cmctl-tmp/cmctl
        sudo mv cmctl-tmp/cmctl /usr/local/bin
        rm -rf cmctl-tmp
        echo "kubectl is now aliased to 'k' and autocompletion is enabled in zsh, with default k8s namespace set to 'otto'. You may need to open a new terminal for changes to take effect."
        echo "Helm and cert-manager have been installed."
        echo "Running 'kubectl get deployments' (otto namespace)"
        kubectl get deployments
        echo "Done! Recommend changing your default terminal profile in VS Code to zsh."
else
        echo "OK, skipping. Run 'bash .devcontainer/post-create.sh' again if you change your mind."
fi
echo " >>> Install k6 for load testing? [y/N]:"
read input
if [[ $input == "Y" || $input == "y" ]]; then
        echo "Installing k6..."
        # https://grafana.com/docs/k6/latest/set-up/install-k6/
        sudo gpg -k
        sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
        echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
        sudo apt-get update
        sudo apt-get install k6
        echo "k6 installed. Run 'k6 --version' to verify."
else
        echo "OK, skipping. Run 'bash .devcontainer/post-create.sh' again if you change your mind."
fi
echo " >>> SETUP COMPLETE! You can now run the server with 'python django/manage.py runserver'."
