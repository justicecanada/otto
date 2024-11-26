#!/bin/bash

# Default values

export CERT_SUBSCRIPTION_ID
export CERT_KEYVAULT_NAME
export CERT_NAME
export CERT_CHOICE=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --cert-choice)
        CERT_CHOICE="$2"
        shift 2
        ;;
        *)
        # Unknown option
        echo "Unknown option: $1"
        exit 1
        ;;
    esac
done

# Function to check the existing certificate
check_existing_certificate() {
    if kubectl get secret tls-secret -n otto >/dev/null 2>&1; then
        echo "Existing TLS secret found. Checking details..."
        
        # Extract certificate details
        CERT_DATA=$(kubectl get secret tls-secret -n otto -o jsonpath='{.data.tls\.crt}' | base64 -d)
        
        # Check if it's CA-signed
        if openssl x509 -in <(echo "$CERT_DATA") -noout -issuer | grep -q "Let's Encrypt"; then
            CERT_TYPE="Let's Encrypt"
        else
            CERT_TYPE="CA-signed"
        fi
        
        # Check validity
        CURRENT_DATE=$(date +%s)
        EXPIRY_DATE=$(openssl x509 -in <(echo "$CERT_DATA") -noout -enddate | cut -d= -f2)
        EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s)
        
        if [ $CURRENT_DATE -lt $EXPIRY_EPOCH ]; then
            VALIDITY="Valid"
        else
            VALIDITY="Expired"
        fi
        
        # Check if configured for NGINX
        if kubectl get ingress otto-ingress -n otto -o yaml | grep -q "tls-secret"; then
            NGINX_CONFIG="Configured for NGINX"
        else
            NGINX_CONFIG="Not configured for NGINX"
        fi
        
        # Check if URL matches SITE_URL
        CERT_CN=$(openssl x509 -in <(echo "$CERT_DATA") -noout -subject | grep -oP "CN = \K[^,]+")
        SITE_URL_STRIPPED=$(echo "$SITE_URL" | sed 's|^https://||')
        if [ "$CERT_CN" == "$SITE_URL_STRIPPED" ]; then
            URL_MATCH="Matches SITE_URL"
        else
            URL_MATCH="Does not match SITE_URL"
            echo "  Certificate CN: $CERT_CN"
            echo "  SITE_URL (stripped): $SITE_URL_STRIPPED"
        fi
        
        echo "Certificate Type: $CERT_TYPE"
        echo "Validity: $VALIDITY"
        echo "Expiry Date: $EXPIRY_DATE"
        echo "NGINX Configuration: $NGINX_CONFIG"
        echo "URL Match: $URL_MATCH"
    else
        echo "No existing TLS secret found."
    fi
}

# If CERT_CHOICE is blank, prompt the user to select an option
if [[ -z "$CERT_CHOICE" ]]; then
    echo "Checking existing certificate..."
    check_existing_certificate

    echo
    echo "Certificate options:"
    echo "  create - Generate and apply a new Let's Encrypt certificate"
    echo "  import - Import and apply a trusted SSL certificate"
    echo "  skip - Skip certificate operations"
    read -p "Enter your choice (create/import/skip): " CERT_CHOICE
fi

# Convert user input to lowercase
CERT_CHOICE=$(echo "$CERT_CHOICE" | tr '[:upper:]' '[:lower:]')

# Validate user input
case "$CERT_CHOICE" in
    create|import|skip)
        echo "Selected certificate option: $CERT_CHOICE"
        ;;
    *)
        echo "Invalid option. Please choose create, import, or skip."
        return 1
        ;;
esac