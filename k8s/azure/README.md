# Otto Setup Guide

This guide will walk you through the process of setting up and deploying the Otto infrastructure using Docker and Terraform.

## Building the Docker Image

First, we need to build a Docker image that contains all the necessary tools and dependencies for our setup process. This image will be based on the Dockerfile in the current directory.

```
docker build -t otto-setup:latest .
```

This command builds a Docker image tagged as 'otto-setup:latest'. The '.' at the end specifies that the Dockerfile is in the current directory.

## Running the Docker Container

Next, we'll run a container based on the image we just built. We'll mount the local 'scripts' directory into the container and start an interactive bash shell.

```
docker run -it -v ${PWD}/scripts:/scripts otto-setup:latest /bin/bash
```

This command does the following:
- `-it` runs the container interactively
- `-v ${PWD}/scripts:/scripts` mounts the local 'scripts' directory to '/scripts' in the container
- `otto-setup:latest` specifies the image to use
- `/bin/bash` starts a bash shell in the container

## Logging into Azure

Before we can deploy resources to Azure, we need to authenticate. The following command will start the Azure login process:

```
az login
```

This will open a browser window where you can enter your Azure credentials. Once logged in, you'll be able to interact with your Azure resources.

## Initializing Terraform and Creating Resources

Now we're ready to use Terraform to create our infrastructure. We'll do this in three steps:

1. Initialize Terraform:

```
terraform init
```

This command initializes Terraform, downloads necessary providers, and sets up the backend.

2. Plan the infrastructure changes:

```
timestamp=$(date +%Y%m%d%H%M%S)
terraform plan -out=tfplan-${timestamp} -var-file=dev.tfvars
```

This command creates an execution plan, showing you what changes Terraform will make to your infrastructure. The `-var-file=dev.tfvars` flag specifies a file containing variable values for your development environment.

3. Apply the changes:

```
terraform apply tfplan-${timestamp}
```

This command applies the changes required to reach the desired state of the infrastructure. Terraform will show you the execution plan and ask for confirmation before making any changes.

Alternatively, you can combine the plan and apply steps into a single command:

```
terraform apply -var-file=dev.tfvars
```

This command applies the changes required to reach the desired state of the infrastructure. The `-var-file=dev.tfvars` flag specifies a file containing variable values for your development environment.

## Getting AKS Cluster Credentials

After Terraform has successfully created the infrastructure, we need to get credentials for the AKS cluster. We'll use the values from `dev.tfvars` for the resource group and cluster name.

```
# Get outputs from Terraform
resource_group=$(terraform output -raw resource_group_name)
aks_cluster_name=$(terraform output -raw aks_cluster_name)

# Get AKS cluster credentials
az aks get-credentials --resource-group "${resource_group}" --name "${aks_cluster_name}" --overwrite-existing
```

This command does the following:
- `source dev.tfvars` loads the variables from the `dev.tfvars` file into the current shell.
- `az aks get-credentials` retrieves the credentials for the specified AKS cluster and sets the context for kubectl commands.

By following these steps, you'll have built a Docker image with all necessary tools, started a container with access to your scripts, logged into Azure, used Terraform to plan and create your infrastructure, and obtained credentials for your AKS cluster.

---

## Creating the NGINX Ingress Controller for AKS Cluster

Now, let's set up an NGINX Ingress Controller for our Azure Kubernetes Service (AKS) cluster. This controller will manage external access to your services in the cluster.

1. **Add the NGINX Helm Repository**:

```
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
```

This command adds the official Helm repository for the NGINX Ingress Controller. This repository contains the Helm chart that we'll use to install the Ingress Controller.

2. **Update Helm Repositories**:

```
helm repo update
```

This command updates the list of charts in all your Helm repositories to ensure you have the latest versions.

3. **Install the NGINX Ingress Controller**:

```
helm install ingress-nginx ingress-nginx/ingress-nginx --create-namespace --namespace "ingress-basic" --set controller.service.annotations."service\.beta\.kubernetes\.io/azure-load-balancer-health-probe-request-path"=/healthz --set controller.service.externalTrafficPolicy=Local
```

This command installs the NGINX Ingress Controller into a new Kubernetes namespace. Here’s what each part does:
- `helm install ingress-nginx ingress-nginx/ingress-nginx` installs the Ingress NGINX chart.
- `--create-namespace` creates the namespace if it doesn't already exist.
- `--namespace "ingress-basic"` specifies the namespace where the Ingress Controller will be installed.
- `--set controller.service.annotations."service\.beta\.kubernetes\.io/azure-load-balancer-health-probe-request-path"=/healthz` sets a health probe path for the Azure load balancer.
- `--set controller.service.externalTrafficPolicy=Local` configures the traffic policy to ensure that client IP addresses are preserved.

At this point, you may need to add DNS records for the Ingress Controller, but that will be a separate step.

## Installing Cert-Manager

Cert-manager is a Kubernetes add-on to automate the management and issuance of TLS certificates. We'll use it to automatically provision certificates for our Ingress resources.

1. **Add the Jetstack Helm Repository**:

```
helm repo add jetstack https://charts.jetstack.io --force-update
```

This command adds the official Helm repository for Jetstack, which includes the cert-manager chart. The `--force-update` flag ensures that any existing repository with the same name is updated.

2. **Install Cert-Manager**:

```
helm install \
  cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.15.0 \
  --set crds.enabled=true
```

This command installs cert-manager into a new Kubernetes namespace. Here’s what each part does:
- `helm install cert-manager jetstack/cert-manager` installs the cert-manager chart.
- `--namespace cert-manager` specifies the namespace where cert-manager will be installed.
- `--create-namespace` creates the namespace if it doesn't already exist.
- `--version v1.15.0` specifies the version of cert-manager to install.
- `--set crds.enabled=true` ensures that the necessary Custom Resource Definitions (CRDs) are installed.

For more detailed instructions and examples, you can refer to the [cert-manager documentation](https://cert-manager.io/docs/tutorials/getting-started-aks-letsencrypt/) and [this tutorial on securing your AKS ingress with Let's Encrypt and cert-manager](https://mrdevops.medium.com/secure-your-aks-ingress-with-letsencrypt-and-cert-manager-97a698418cf3).
