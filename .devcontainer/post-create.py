import os
import subprocess

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

process = subprocess.Popen(
    [
        "az",
        "ad",
        "app",
        "list",
        "--display-name",
        "Otto (Sandbox)",
        "--query",
        "[].{appId:appId}",
        "--output",
        "tsv",
    ],
    text=True,
    stdout=subprocess.PIPE,
)
entra_client_id, entra_client_id_stderr = process.communicate()

if entra_client_id_stderr:
    print("Error: An error occured trying to retrieve the client id. \n")
else:
    entra_client_id = entra_client_id.strip()

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

# Write a file to current directory/.env
with open(os.path.join(os.path.dirname(__file__), "../django/.env"), "w") as f:
    f.write(f"AZURE_OPENAI_KEY='{client.get_secret('OPENAI-CANADA-EAST-KEY').value}'\n")
    f.write(
        f"AZURE_COGNITIVE_SERVICE_KEY='{client.get_secret('COGNITIVE-SERVICE-KEY').value}'\n"
    )
    f.write(f"AZURE_ACCOUNT_KEY='{client.get_secret('STORAGE-KEY').value}'\n")
    f.write(f"ENTRA_CLIENT_SECRET='{client.get_secret('ENTRA-CLIENT-SECRET').value}'\n")
    f.write(f"ENTRA_CLIENT_ID='{entra_client_id}'\n")
    f.write(f"ENTRA_AUTHORITY='https://login.microsoftonline.com/{entra_tenant_id}'\n")
    f.write(
        f"AZURE_TRANSLATOR_KEY='{client.get_secret('AZURE-TRANSLATOR-KEY').value}'\n"
    )
