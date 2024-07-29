import os

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

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
    f.write(f"AZURE_OPENAI_KEY='{client.get_secret('OPENAI-SERVICE-KEY').value}'\n")
    f.write(
        f"AZURE_COGNITIVE_SERVICE_KEY='{client.get_secret('COGNITIVE-SERVICE-KEY').value}'\n"
    )
    f.write(f"AZURE_ACCOUNT_KEY='{client.get_secret('STORAGE-KEY').value}'\n")
    f.write(f"ENTRA_CLIENT_ID='{client.get_secret('ENTRA-CLIENT-ID').value}'\n")
    f.write(f"ENTRA_CLIENT_SECRET='{client.get_secret('ENTRA-CLIENT-SECRET').value}'\n")
    f.write(f"ENTRA_AUTHORITY='{client.get_secret('ENTRA-AUTHORITY').value}'\n")
