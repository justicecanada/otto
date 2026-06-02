#!/bin/bash
set -euo pipefail

# Optional interactive bootstrap script. Infrastructure tooling (az, kubectl, azcopy) is now baked into the devcontainer image.
# You can suppress all prompts by exporting OTTO_NON_INTERACTIVE=1 before invoking.

configure_gpg() {
	# Optional: only does permission hardening if a user has mounted ~/.gnupg.
	# This should be harmless for users who don't use GPG.
	if ! command -v gpg >/dev/null 2>&1; then
		return 0
	fi
	if [ ! -d ~/.gnupg ]; then
		return 0
	fi

	chmod 700 ~/.gnupg 2>/dev/null || true
	# Typical GnuPG expects 600 on keybox and config files; do best-effort only.
	find ~/.gnupg -maxdepth 1 -type f -exec chmod 600 {} \; 2>/dev/null || true
	# If gpg-agent is available, try to launch (non-fatal).
	gpgconf --launch gpg-agent >/dev/null 2>&1 || true
}

configure_git() {
	# Check if git user.name and user.email are already configured
	if git config --global user.name >/dev/null 2>&1 && git config --global user.email >/dev/null 2>&1; then
		echo "Git is already configured."
		return 0
	fi

	if [[ "${OTTO_NON_INTERACTIVE:-}" == "1" ]]; then
		echo "Git not configured and running non-interactively. Skipping git configuration."
		return 1
	fi

	echo ""
	echo "Git user.name and/or user.email not configured in the container."
	if prompt "Configure git user settings?" "Y"; then
		read -r -p "Git user name: " git_name || true
		read -r -p "Git user email: " git_email || true
		if [[ -n "$git_name" && -n "$git_email" ]]; then
			git config --global user.name "$git_name"
			git config --global user.email "$git_email"
			echo "Git configured: $git_name <$git_email>"
		else
			echo "Skipping git configuration (incomplete input)."
		fi
	fi
}

# Optional GPG setup (safe no-op if not configured)
configure_gpg

prompt() {
	local question="$1"
	shift
	local default="${1:-N}"
	if [[ "${OTTO_NON_INTERACTIVE:-}" == "1" ]]; then
		return 1
	fi
	if [[ "$default" == "Y" ]]; then
		read -r -p "${question} [Y/n]: " ans || true
		if [[ -z "$ans" || $ans == "y" || $ans == "Y" ]]; then return 0; else return 1; fi
	else
		read -r -p "${question} [y/N]: " ans || true
		if [[ $ans == "y" || $ans == "Y" ]]; then return 0; else return 1; fi
	fi
}

# Configure git
configure_git

ensure_azure_login() {
	# Returns 0 if logged in, 1 if skipped/failed
	if az account show >/dev/null 2>&1; then
		return 0
	fi

	if [[ "${OTTO_AZ_SKIP_LOGIN:-}" == "1" ]]; then
		echo "OTTO_AZ_SKIP_LOGIN=1 set; skipping Azure login."
		return 1
	fi

	if [[ "${OTTO_NON_INTERACTIVE:-}" == "1" ]]; then
		echo "Not logged in and non-interactive mode enabled; skipping Azure login. (Run 'az login' later then rerun)."
		return 1
	fi

	echo "Azure CLI not logged in. Choose authentication method:"
	echo "  [L] Login (opens browser)"
	echo "  [D] Device code"
	echo "  [S] Skip"
	read -r -p "Selection (L/D/S) [L]: " auth_choice || true
	auth_choice=${auth_choice:-L}
	case "$auth_choice" in
	L | l)
		echo "Running: az login"
		if ! az login >/dev/null 2>&1; then
			echo "Login failed."
			return 1
		fi
		;;
	D | d)
		echo "Running: az login --use-device-code"
		if ! az login --use-device-code >/dev/null 2>&1; then
			echo "Device code login failed."
			return 1
		fi
		;;
	*)
		echo "Skipping Azure login."
		return 1
		;;
	esac
	return 0
}

generate_env() {

	# Check if .env exists and has AZURE_KEYVAULT_URL
	local env_has_keyvault=0
	if [[ -f django/.env ]]; then
		if grep -q '^AZURE_KEYVAULT_URL=' django/.env 2>/dev/null; then
			env_has_keyvault=1
		fi
	fi

	# If .env doesn't exist OR exists but lacks AZURE_KEYVAULT_URL, recreate from .env.example
	if [[ ! -f django/.env ]] || [[ $env_has_keyvault -eq 0 ]]; then
		if [[ -f django/.env.example ]]; then
			if [[ -f django/.env ]]; then
				echo "django/.env exists but missing AZURE_KEYVAULT_URL; overwriting from .env.example..."
			else
				echo "django/.env not found; creating from .env.example..."
			fi
			cp django/.env.example django/.env
		else
			echo "Warning: django/.env.example not found. Cannot generate .env."
			return 0
		fi
	fi

	# Try to get AZURE_KEYVAULT_URL from environment or django/.env
	if [[ -z "${AZURE_KEYVAULT_URL:-}" ]]; then
		if [[ -f django/.env ]]; then
			AZURE_KEYVAULT_URL=$(grep '^AZURE_KEYVAULT_URL=' django/.env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | xargs || true)
			export AZURE_KEYVAULT_URL
		fi
	fi

	if [[ -z "${AZURE_KEYVAULT_URL:-}" ]]; then
		echo "Warning: AZURE_KEYVAULT_URL not set in environment or .env. Skipping Key Vault secret fetch."
		echo "You can manually add secrets to django/.env or set AZURE_KEYVAULT_URL and re-run."
		return 0
	fi

	echo "Fetching secrets from Azure Key Vault (${AZURE_KEYVAULT_URL}) and appending to django/.env..."
	if ! ensure_azure_login; then
		echo "Warning: Secret fetch skipped (not logged into Azure)"
		return 0
	fi

	if python .devcontainer/post-create.py; then
		echo ""
		echo "✓ Secrets appended to django/.env successfully!"
	else
		echo "Warning: Secret fetch failed (Key Vault may not exist yet or secrets are missing)."
	fi
}
initial_data() {
	(cd django && bash initial_setup.sh)
}

setup_frontend_vendor() {
	if ! command -v npm >/dev/null 2>&1; then
		echo "npm not found; skipping frontend vendor setup."
		return 1
	fi

	echo "Installing npm workspace dependencies..."
	npm install

	echo "Syncing frontend vendor assets..."
	npm run vendor:sync
}

set_admin_user() {
	if [[ "${OTTO_NON_INTERACTIVE:-}" == "1" ]]; then
		echo "Non-interactive mode: skipping admin user setup."
		return 0
	fi

	# Try to get email from git config
	local git_email
	git_email="$(git config --global user.email 2>/dev/null || true)"

	# If git email is a justice.gc.ca email, use it directly
	if [[ -n "$git_email" && "$git_email" == *"@justice.gc.ca" ]]; then
		echo "Setting admin user from git config: $git_email"
		python /workspace/django/manage.py set_admin_user "$git_email" || echo "Warning: Failed to set admin user"
		return 0
	fi

	echo "Enter the 'firstname.lastname' part of your Justice email (e.g., john.doe for john.doe@justice.gc.ca):"
	read -r user_input || true

	if [[ -z "$user_input" ]]; then
		echo "No input provided, skipping admin user setup."
		return 0
	fi

	local email="${user_input}@justice.gc.ca"
	echo "Setting admin user: $email"
	python /workspace/django/manage.py set_admin_user "$email" || echo "Warning: Failed to set admin user"
}

echo "Otto post-create (optional steps)"

prompt "Generate django/.env from Azure Key Vault?" "Y" && generate_env || echo "Skipping env generation"

# Always source .env if it exists (whether just generated or pre-existing)
if [[ -f django/.env ]]; then
	set -a
	# shellcheck disable=SC1091
	source django/.env
	set +a
fi

prompt "Run initial app data setup (reset & load)?" && initial_data || echo "Skipping initial data load"
prompt "Set yourself as admin user?" "Y" && set_admin_user || echo "Skipping admin user setup"
prompt "Install npm deps and sync frontend vendor assets?" "Y" && setup_frontend_vendor || echo "Skipping npm install + vendor sync"

echo "Setup complete. You can run the server with: python django/manage.py runserver"
