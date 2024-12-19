terraform {
  source = "."
}

generate "backend" {
  path      = "backend.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<EOF
terraform {
  backend "local" {}
}
EOF
}

remote_state {
  backend = "local"
  config = {
    # These will be filled in by command-line arguments or environment variables
  }
}

# Leave the inputs block empty since we're using TF_VAR environment variables
inputs = {}
