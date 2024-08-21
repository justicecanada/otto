output "db_hostname" {
  value = azurerm_cosmosdb_postgresql_cluster.djangodb.servers[0].fqdn
  description = "The hostname of the Cosmos DB for PostgreSQL"
}
