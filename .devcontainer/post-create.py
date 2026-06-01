import os
import subprocess
import sys

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

process = subprocess.Popen(
    [
        "az",
        "account",
        "show",
        "--query",
        "tenantId",
        "--output",
        "tsv",
    ],
    text=True,
    stdout=subprocess.PIPE,
)
entra_tenant_id, entra_tenant_id_stderr = process.communicate()

if entra_tenant_id_stderr:
    print("Error: An error occured trying to retrieve the tenant id. \n")
else:
    entra_tenant_id = entra_tenant_id.strip()

# Load dotenv from ../django/.env.example
load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "../django/.env.example")
)

token_credential = DefaultAzureCredential(additionally_allowed_tenants=["*"])
# Login to KeyVault using Azure credentials
client = SecretClient(
    vault_url=os.environ.get("AZURE_KEYVAULT_URL"), credential=token_credential
)

print("Writing temporary .env file...")


def get_secret(name, required=True):
    """Get secret from Key Vault. Returns empty string if not required and missing."""
    try:
        return client.get_secret(name).value
    except ResourceNotFoundError:
        if required:
            print(f"ERROR: Required secret '{name}' not found in Key Vault")
            sys.exit(1)
        return ""


# Write a file to current directory/.env
# Start with non-secret base config from .env.example
with open(
    os.path.join(os.path.dirname(__file__), "../django/.env.example"), "r"
) as example:
    example_content = example.read()

with open(os.path.join(os.path.dirname(__file__), "../django/.env"), "w") as f:
    # Write non-secret vars from .env.example first
    f.write(example_content)
    f.write("\n# Secrets from Key Vault below\n")

    # Then append secrets from Key Vault
    f.write(f"AZURE_AI_SERVICES_KEY='{get_secret('AI-SERVICES-KEY')}'\n")
    f.write(
        f"AZURE_DOCUMENT_INTELLIGENCE_KEY='{get_secret('DOCUMENT-INTELLIGENCE-KEY')}'\n"
    )
    f.write(f"AZURE_ACCOUNT_KEY='{get_secret('STORAGE-ACCOUNT-KEY')}'\n")

    f.write(f"ENTRA_CLIENT_SECRET='{get_secret('ENTRA-CLIENT-SECRET')}'\n")
    f.write(
        f"CUSTOM_TRANSLATOR_ID='{get_secret('CUSTOM-TRANSLATOR-ID', required=False)}'\n"
    )

    entra_client_id = get_secret("ENTRA-CLIENT-ID", required=False)
    if not entra_client_id:
        entra_client_id = os.environ.get("ENTRA_CLIENT_ID", "")
    f.write(f"ENTRA_CLIENT_ID='{entra_client_id}'\n")
    f.write(f"ENTRA_AUTHORITY='https://login.microsoftonline.com/{entra_tenant_id}'\n")
