#!/bin/bash

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

# Function to list Azure subscriptions
list_subscriptions() {
    az account list --query "[].{name:name, id:id}" -o table
}

# Function to list Key Vaults in a subscription
list_key_vaults() {
    local subscription_id=$1
    az keyvault list --subscription $subscription_id --query "[].{name:name, resourceGroup:resourceGroup}" -o table
}

# Function to list certificates in a Key Vault
list_certificates() {
    local keyvault_name=$1
    az keyvault certificate list --vault-name $keyvault_name --query "[].{name:name}" -o table
}

# Main script
echo "Checking existing certificate..."
check_existing_certificate

echo
echo "Do you want to:"
echo "1) Use a CA-signed certificate from Azure Key Vault"
echo "2) Generate a new Let's Encrypt certificate"
echo "3) Skip certificate creation"
read -p "Enter your choice (1 or 3): " choice

if [ "$choice" == "1" ]; then
    echo "Listing available Azure subscriptions..."
    list_subscriptions
    read -p "Enter the subscription ID: " subscription_id
    
    echo "Listing Key Vaults in the selected subscription..."
    list_key_vaults $subscription_id
    read -p "Enter the Key Vault name: " keyvault_name
    
    echo "Listing certificates in the selected Key Vault..."
    list_certificates $keyvault_name
    read -p "Enter the certificate name: " cert_name
    
    # Here you would add the logic to retrieve the certificate from Key Vault
    # and create the Kubernetes secret
    echo "Retrieving certificate $cert_name from Key Vault $keyvault_name..."


    # Retrieve the certificate in base64 format
    cert_data=$(az keyvault certificate show --vault-name $keyvault_name --name $cert_name --query "cer" -o tsv)

    # Create a properly formatted PEM certificate
    echo "-----BEGIN CERTIFICATE-----" > temp_cert.pem
    echo "$cert_data" | fold -w 64 >> temp_cert.pem
    echo "-----END CERTIFICATE-----" >> temp_cert.pem

    # Retrieve the private key
    key_data=$(az keyvault secret show --vault-name $keyvault_name --name $cert_name --query "value" -o tsv)

    # Ensure the key is in PEM format
    if [[ $key_data != -----BEGIN*PRIVATE*KEY----- ]]; then
    echo "-----BEGIN PRIVATE KEY-----" > temp_key.pem
    echo "$key_data" | fold -w 64 >> temp_key.pem
    echo "-----END PRIVATE KEY-----" >> temp_key.pem
    else
    echo "$key_data" > temp_key.pem
    fi
    
    # Delete the existing Kubernetes secret if it exists
    kubectl delete secret tls-secret -n otto --ignore-not-found

    # Create a Kubernetes TLS secret using the certificate data
    kubectl create secret tls tls-secret -n otto --cert=temp_cert.pem --key=temp_key.pem --dry-run=client -o yaml | kubectl apply -f -
    
    # Clean up temporary files
    rm temp_cert.pem temp_key.pem

    # Update the Ingress to use the new secret
    kubectl patch ingress otto-ingress -n otto --type=json \
    -p='[{"op": "replace", "path": "/spec/tls/0/secretName", "value": "tls-secret"}]'

    echo "Certificate applied to Kubernetes secret 'tls-secret' and Ingress updated"
    
    
elif [ "$choice" == "2" ]; then
    echo "Proceeding with Let's Encrypt certificate generation..."
    # Add your Let's Encrypt certificate generation logic here
    
else
    echo "Invalid choice. Exiting."
    exit 1
fi
