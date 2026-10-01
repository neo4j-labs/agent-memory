# Use AuraDB for an example

Use a dedicated, empty AuraDB instance for the example's synthetic data. The Python SDK calls this the `bolt` backend: Aura hosts Neo4j and accepts encrypted Bolt connections. Local embedding or extraction models still run on your computer; the database connection requires network access.

## Create and configure the instance

1. Sign in to the [Aura console](https://console.neo4j.io/) and create a dedicated instance named `agent-memory-example`. **AuraDB Free** is the starting point for small exercises without additional database-feature requirements. If the application requires more capacity or optional analytics, establish its supported Aura configuration first; a larger database tier alone does not establish GDS availability. Do not load a sample dataset. Aura permits one Free instance per account; do not delete an existing database containing other work to make room. See [instance creation](https://neo4j.com/docs/aura/getting-started/create-instance/) for account and tier requirements.
2. Download the generated credentials outside the repository and wait for the instance to show **Running**. Copy its connection settings into a POSIX shell:

```bash
unset MEMORY_API_KEY  # Also remove this selector from private .env files below
export NAM_BACKEND=bolt
export NEO4J_URI='neo4j+s://<instance-id>.databases.neo4j.io'
export NEO4J_USERNAME='neo4j'
export NEO4J_PASSWORD='replace-with-the-generated-password'
export NEO4J_DATABASE='neo4j'
```

3. In Aura **Query**, select this instance and database, connect with the same credentials, and run:

```cypher
RETURN 1 AS connected;
MATCH (n) RETURN count(n) AS node_count;
```

Expect `connected = 1` and `node_count = 0`. This check is for the newly created example instance. See [Aura connection instructions](https://neo4j.com/docs/aura/getting-started/connect-instance/) if the connection fails.

Keep these variables exported while following the example's installation and run commands. Some full applications use `NEO4J_USER` instead of `NEO4J_USERNAME`, or separate credentials for a domain graph; their README gives the required mapping. When a README asks for a private `.env` file, replace its template's local connection values with these Aura values. Remove any `MEMORY_API_KEY` entry from the private `.env` files that the example loads: `unset` alone is insufficient because dotenv can load the key again and select NAMS. Keep `NAM_BACKEND=bolt` for this Aura run and replace any conflicting backend selector in those private files. Never commit credentials.

Start each example with an empty instance. Vector indexes from another embedding model can be incompatible even when dimensions match. Large datasets or optional graph algorithms can also have requirements beyond AuraDB Free; check the example before loading more than its starter dataset.

## Clean up this example

Stop the example application. In Aura, select only the `agent-memory-example` instance created for this exercise, use its trashcan action, enter its exact name and confirm **Destroy**. Verify that the instance disappears. This removes its data and snapshots; see [instance deletion](https://neo4j.com/docs/aura/managing-instances/instance-actions/#_delete_an_instance).

Remove the downloaded credentials and any private configuration created for that deleted instance, then clear its variables:

```bash
unset NEO4J_URI NEO4J_USERNAME NEO4J_PASSWORD NEO4J_DATABASE NAM_BACKEND
```

Also clear application-specific aliases you set, such as `NEO4J_USER` or `NEWS_GRAPH_*`. Keep the repository and installed dependencies for the next example.

## Local alternative: Docker

For a key-free local run of the repository's standalone and v0.2 examples, use the throwaway Neo4j from `docker-compose.test.yml` instead of Aura. The `make example-*` targets (and `make examples`) start it when `NEO4J_URI` is set neither in your shell nor in `examples/.env`, then export `NEO4J_URI=bolt://localhost:7687`, `NEO4J_USERNAME=neo4j` and `NEO4J_PASSWORD=test-password` for the script:

```bash
unset NEO4J_URI MEMORY_API_KEY
make example-basic
```

To run a script directly, start the container with `make neo4j-start` and export the same three values. The container needs ports 7474 and 7687 free. Stop it with `make neo4j-stop`, or remove it and its data with `make neo4j-clean`.
