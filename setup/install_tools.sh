#!/bin/bash

# Description:
# This script performs the following steps:
# 1. Checks if Terraform is installed and installs it if needed
# 2. Checks if Terragrunt is installed and installs it if needed
# 3. Ensures the installed tools are accessible in the current session

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to update PATH
update_path() {
    if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
        export PATH="$HOME/bin:$PATH"
        echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
        echo "Added $HOME/bin to PATH."
    fi
}

# Function to install Terraform
install_terraform() {
    echo "Installing Terraform..."
    
    # Get the latest Terraform version
    terraform_version=$(curl -s https://api.github.com/repos/hashicorp/terraform/releases/latest | grep tag_name | cut -d '"' -f 4)
    terraform_binary="$HOME/bin/terraform"
    
    # Ensure the bin directory exists
    mkdir -p "$HOME/bin"
    
    # Download and unzip the Terraform binary
    wget https://releases.hashicorp.com/terraform/${terraform_version#v}/terraform_${terraform_version#v}_linux_amd64.zip -O terraform.zip
    unzip -qo terraform.zip -d temp_terraform
    
    # Move the terraform binary to the correct location
    mv temp_terraform/terraform "$terraform_binary"
    chmod +x "$terraform_binary"
    
    # Remove temporary files
    rm -rf temp_terraform terraform.zip
    
    # Update PATH
    update_path
    
    # Output the Terraform version to confirm installation
    echo "Terraform installation complete."
    "$terraform_binary" --version
}

# Function to install Terragrunt
install_terragrunt() {
    echo "Installing Terragrunt..."
    
    # Get the latest Terragrunt version
    terragrunt_version=$(curl -s https://api.github.com/repos/gruntwork-io/terragrunt/releases/latest | grep tag_name | cut -d '"' -f 4)
    terragrunt_binary="$HOME/bin/terragrunt"
    
    # Ensure the bin directory exists
    mkdir -p "$HOME/bin"
    
    # Download the Terragrunt binary
    wget https://github.com/gruntwork-io/terragrunt/releases/download/${terragrunt_version}/terragrunt_linux_amd64 -O "$terragrunt_binary"
    
    # Make the binary executable
    chmod +x "$terragrunt_binary"
    
    # Update PATH
    update_path
    
    # Output the Terragrunt version to confirm installation
    echo "Terragrunt installation complete."
    "$terragrunt_binary" --version
}

# Main execution
main() {
    # Update PATH at the beginning to ensure it's set for the current session
    update_path

    # Check and install Terraform if needed
    if ! command_exists terraform; then
        install_terraform
    else
        echo "Terraform is already installed."
        terraform --version
    fi

    # Check and install Terragrunt if needed
    if ! command_exists terragrunt; then
        install_terragrunt
    else
        echo "Terragrunt is already installed."
        terragrunt --version
    fi

    # Final PATH update and notification
    update_path
    echo "Installation complete. Please run 'source ~/.bashrc' or start a new shell session if the commands are not immediately available."
}

# Run the main function
main
