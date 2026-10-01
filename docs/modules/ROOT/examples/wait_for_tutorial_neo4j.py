"""Check the exported Aura connection, waiting at most three minutes."""

import asyncio
import sys

from aura_connection import AuraConfigurationError, aura_config
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import DriverError, Neo4jError, ServiceUnavailable, SessionExpired


async def wait_until_ready(timeout=180):
    if timeout <= 0:
        raise ValueError("Readiness timeout must be positive")
    config = aura_config()

    async def probe(driver):
        while True:
            try:
                await driver.verify_connectivity()
                records, _, _ = await driver.execute_query(
                    "RETURN 1 AS ready", database_=config["database"], routing_="r"
                )
                if len(records) != 1 or records[0]["ready"] != 1:
                    raise RuntimeError("Unexpected readiness query result")
                return
            except (ServiceUnavailable, SessionExpired):
                await asyncio.sleep(2)

    async with AsyncGraphDatabase.driver(
        config["uri"],
        auth=(config["username"], config["password"]),
        connection_timeout=min(10, timeout),
        connection_acquisition_timeout=min(10, timeout),
    ) as driver:
        await asyncio.wait_for(probe(driver), timeout=timeout)


def main():
    try:
        asyncio.run(wait_until_ready())
    except AuraConfigurationError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except (asyncio.TimeoutError, DriverError, Neo4jError) as error:
        # Driver exceptions can include connection details. Report the category
        # and next action without echoing credentials or a raw server message.
        print(
            f"Neo4j Aura readiness failed ({type(error).__name__}). "
            "Check that the instance is Running, the exported NEO4J_* values "
            "match its credentials, and your network allows the connection.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print("Verified: Neo4j Aura answered the readiness query")


if __name__ == "__main__":
    main()
