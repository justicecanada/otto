#!/bin/bash

# Description:
# This script runs the other scripts in the correct order.

env_file=""

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --env-file)
        env_file="$2"
        shift 2
        ;;
        *)
        shift
        ;;
    esac
done

source ./install_tools.sh
source ./load_env.sh --env-file $env_file
source ./deploy_mgmt.sh
source ./check_secrets.sh
source ./build_image.sh
source ./deploy_app.sh
source ./init_script.sh
# source ./check_velero.sh