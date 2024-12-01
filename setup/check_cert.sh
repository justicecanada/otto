#!/bin/bash

read -p "This script will attempt to create or renew the SSL certificate for the domain $DOMAIN_NAME. However, this *should* be handled by the cluster using the cert-manager. The AKS managed identity will need permission to manage the subdomain. Do you want to continue? (y/n) " answer
if [[ ! $answer =~ ^[Yy]$ ]]; then
    echo "Renewal process aborted."
    exit 0
fi

cert_path="/etc/letsencrypt/live/$DOMAIN_NAME/fullchain.pem"

check_certificate() {
    if sudo test -f "$cert_path"; then
        expiration_date=$(sudo openssl x509 -enddate -noout -in "$cert_path" | cut -d= -f2)
        expiration_days=$(( ($(date -d "$expiration_date" +%s) - $(date +%s)) / 86400 ))
        if [ $expiration_days -gt 30 ]; then
            echo "Certificate is valid for $expiration_days days."
            return 0
        fi
    fi
    echo "Certificate renewal needed."
    return 1
}

update_dns_record() {
    local txt_record=$1
    az network dns record-set txt add-record -g "$DNS_RESOURCE_GROUP" -z "$DNS_ZONE" \
        -n "_acme-challenge.$DNS_LABEL" -v "$txt_record" --subscription "$DNS_SUBSCRIPTION_ID"
}

remove_dns_record() {
    local txt_record=$1
    az network dns record-set txt remove-record -g "$DNS_RESOURCE_GROUP" -z "$DNS_ZONE" \
        -n "_acme-challenge.$DNS_LABEL" -v "$txt_record" --subscription "$DNS_SUBSCRIPTION_ID"
}

attempt_automatic_renewal() {
    certbot certonly --manual --preferred-challenges dns -d "$domain" --manual-auth-hook update_dns_record --manual-cleanup-hook remove_dns_record
}

manual_renewal() {
    certbot certonly --manual --preferred-challenges dns -d "$domain"
}

if ! check_certificate; then
    if attempt_automatic_renewal; then
        echo "Automatic renewal successful."
    else
        echo "Automatic renewal failed."
        read -p "Do you want to attempt manual renewal? (y/n) " answer
        if [[ $answer =~ ^[Yy]$ ]]; then
            manual_renewal
        else
            echo "Renewal process aborted."
        fi
    fi
fi
