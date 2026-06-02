#!/bin/bash

set -euo pipefail

git config --global --add safe.directory "$(pwd)" || true
git config --local pull.rebase false || true
git config --local push.autoSetupRemote true || true

# Check if we need to chown the django directory
if [[ -d django ]]; then
	CURRENT_USER=$(whoami)
	DJANGO_OWNER=$(stat -c '%U' django 2>/dev/null || stat -f '%Su' django 2>/dev/null)
	if [[ "$DJANGO_OWNER" != "$CURRENT_USER" ]]; then
		echo " >>> Chowning the project directory to the current user. This may take a minute."
		sudo chown -R "$CURRENT_USER" django
	else
		echo " >>> Project directory already owned by current user, skipping chown."
	fi
else
	echo " >>> django directory not found, skipping chown."
fi

echo "Installing git pre-commit hooks..."
pre-commit install --hook-type pre-commit --hook-type pre-push || echo "pre-commit install failed"

# Create version.yaml for development
if [[ -d django ]]; then
	echo " >>> Creating django/version.yaml for development..."
	GITHUB_HASH=$(git rev-parse HEAD 2>/dev/null || echo "dev")
	BUILD_DATE=$(date -u +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "unknown")
	cat >django/version.yaml <<EOF
github_hash: $GITHUB_HASH
build_date: $BUILD_DATE
EOF
	echo " >>> version.yaml created with hash: $GITHUB_HASH"
else
	echo " >>> django directory not found, skipping version.yaml creation."
fi

# Now run .devcontainer/post-create.sh
bash .devcontainer/post-create.sh

enable_local_browser_test_auth() {
	local env_file="django/.env"
	local env_example="django/.env.example"

	if [[ ! -f "$env_file" ]]; then
		if [[ ! -f "$env_example" ]]; then
			echo " >>> django/.env.example not found, so local browser auth could not be enabled."
			return 1
		fi

		cp "$env_example" "$env_file"
		echo " >>> Created django/.env from django/.env.example."
	fi

	python3 - "$env_file" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
lines = env_path.read_text(encoding="utf-8").splitlines()
setting = "ENABLE_BROWSER_TEST_AUTH="

for index, line in enumerate(lines):
    if line.startswith(setting):
        lines[index] = "ENABLE_BROWSER_TEST_AUTH=True"
        break
else:
    if lines:
        lines.append("")
    lines.extend(
        [
            "# Optional local-only fallback for VS Code integrated browser testing when Entra blocks embedded browsers.",
            "ENABLE_BROWSER_TEST_AUTH=True",
        ]
    )

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

	echo " >>> Enabled local browser test auth in django/.env. To disable, edit django/.env and set ENABLE_BROWSER_TEST_AUTH=False."
}

read -p "Enable local browser auth fallback for VS Code integrated browser testing? (Y/n): " enable_browser_test_auth
if [[ "$enable_browser_test_auth" =~ ^[Nn]$ ]]; then
	echo " >>> Leaving local browser test auth disabled. You can enable it later in django/.env."
else
	enable_local_browser_test_auth
fi

# --- Optional infrastructure tooling ---
# These are only needed for infrastructure/DevOps work, not required for app development.
# Run these functions if you need to work with infrastructure.

install_optional_infrastructure_tools() {
	local tool=${1:-all}

	case "$tool" in
	terragrunt)
		echo " >>> Installing Terragrunt..."
		TG_VERSION=v0.70.2
		curl -fsSL "https://github.com/gruntwork-io/terragrunt/releases/download/${TG_VERSION}/terragrunt_linux_amd64" -o /tmp/terragrunt
		sudo mv /tmp/terragrunt /usr/local/bin/terragrunt
		sudo chmod +x /usr/local/bin/terragrunt
		terragrunt --version
		;;
	k6)
		echo " >>> Installing k6 load testing CLI..."
		K6_VERSION=v0.45.0
		curl -fsSL "https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-amd64.tar.gz" -o /tmp/k6.tgz
		mkdir -p /tmp/k6 && tar -xzf /tmp/k6.tgz -C /tmp/k6 --strip-components=1
		sudo mv /tmp/k6/k6 /usr/local/bin/k6
		sudo chmod +x /usr/local/bin/k6
		rm -rf /tmp/k6 /tmp/k6.tgz
		k6 version
		;;

	azcopy)
		echo " >>> Installing azcopy..."
		AZCOPY_TGZ_URL="https://aka.ms/downloadazcopy-v10-linux"
		curl -fsSL "$AZCOPY_TGZ_URL" -o /tmp/azcopy.tgz
		if ! gzip -t /tmp/azcopy.tgz >/dev/null 2>&1; then
			echo "AzCopy download did not return a gzip archive. Aborting."
			file /tmp/azcopy.tgz || true
			return 1
		fi
		mkdir -p /tmp/azcopy && tar -xzf /tmp/azcopy.tgz -C /tmp/azcopy --strip-components=1
		sudo mv /tmp/azcopy/azcopy /usr/local/bin/azcopy && sudo chmod +x /usr/local/bin/azcopy
		rm -rf /tmp/azcopy /tmp/azcopy.tgz
		azcopy --version
		;;
	az-extensions)
		echo " >>> Installing Azure CLI extensions..."
		az config set extension.use_dynamic_install=yes_without_prompt || true
		for ext in dns-resolver azure-firewall amg monitor-control-service; do
			az extension add -n "$ext" || echo "Extension $ext skipped"
		done
		;;
	all)
		echo " >>> Installing all optional infrastructure tools..."
		install_optional_infrastructure_tools terragrunt
		install_optional_infrastructure_tools k6
		install_optional_infrastructure_tools azcopy
		install_optional_infrastructure_tools az-extensions
		;;
	*)
		echo "Usage: install_optional_infrastructure_tools [terragrunt|k6|azcopy|az-extensions|all]"
		return 1
		;;
	esac
}

install_optional_infrastructure_extensions() {
	if ! command -v code >/dev/null 2>&1; then
		echo "VS Code CLI not available; skipping optional infrastructure extensions."
		return 0
	fi

	echo " >>> Installing optional infrastructure VS Code extensions..."
	local extensions=(
		"ms-azuretools.vscode-azurecli"
		"ms-kubernetes-tools.vscode-kubernetes-tools"
		"ms-azuretools.vscode-docker"
		"GitHub.vscode-github-actions"
		"hashicorp.terraform"
		"redhat.vscode-yaml"
		"ms-vscode.powershell"
	)
	for ext in "${extensions[@]}"; do
		code --install-extension "$ext" >/dev/null 2>&1 || echo "Extension $ext skipped"
	done
}

echo " >>> Optional infrastructure tools available."
# Prompt user to install (shfmt is already installed in the devcontainer)
read -p "Do you want to install optional infrastructure tools (terragrunt, k6, azcopy, az-extensions)? (y/N): " install_tools
if [[ "$install_tools" =~ ^[Yy]$ ]]; then
	install_optional_infrastructure_tools all
	install_optional_infrastructure_extensions
else
	echo "Skipping optional infrastructure tools installation."
fi
