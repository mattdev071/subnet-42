from dotenv import load_dotenv

import os
import asyncio
import uvicorn
from typing import Optional, Any
import datetime

from fiber.chain import chain_utils, interface
from fiber.chain.metagraph import Metagraph
from fiber.miner.server import factory_app
from fiber.networking.models import NodeWithFernet as Node
from fiber.logging_utils import get_logger


from fastapi import FastAPI

from validator.config import Config
from validator.http_client import HttpClientManager
from validator.background_tasks import BackgroundTasks
from validator.api_routes import ValidatorAPI
from validator.network_operations import (
    make_non_streamed_get,
    make_non_streamed_post,
)
from validator.metagraph import MetagraphManager
from validator.node_manager import NodeManager
from validator.nats import MinersNATSPublisher
from validator.weights import WeightsManager
from validator.scorer import NodeDataScorer

from validator.telemetry_storage import TelemetryStorage

from validator.routing_table import RoutingTable

logger = get_logger(__name__)

BLOCKS_PER_WEIGHT_SETTING = 100
BLOCK_TIME_SECONDS = 12
TIME_PER_WEIGHT_SETTING = BLOCKS_PER_WEIGHT_SETTING * BLOCK_TIME_SECONDS
WEIGHTS_LOOP_CADENCE_SECONDS = (
    TIME_PER_WEIGHT_SETTING / 2
)  # half of a weight setting period

SYNC_LOOP_CADENCE_SECONDS = 120


class Validator:
    def __init__(self):
        """Initialize validator"""
        # Explicitly get environment variables
        self.config = Config()
        self.http_client_manager = HttpClientManager()

        self.keypair = chain_utils.load_hotkey_keypair(
            self.config.VALIDATOR_WALLET_NAME, self.config.VALIDATOR_HOTKEY_NAME
        )

        self.netuid = int(os.getenv("NETUID", "42"))

        self.subtensor_network = os.getenv("SUBTENSOR_NETWORK")
        self.subtensor_address = os.getenv("SUBTENSOR_ADDRESS")

        self.server: Optional[factory_app] = None
        self.app: Optional[FastAPI] = None

        self.substrate = interface.get_substrate(
            subtensor_network=self.subtensor_network,
            subtensor_address=self.subtensor_address,
        )

        self.routing_table = RoutingTable()

        # Add flag to coordinate routing table updates with NATS publishing
        self.routing_table_updating = False

        self.metagraph = Metagraph(netuid=self.netuid, substrate=self.substrate)
        self.metagraph.sync_nodes()

        self.node_manager = NodeManager(validator=self)
        self.telemetry_storage = TelemetryStorage()
        self.scorer = NodeDataScorer(validator=self)
        self.weights_manager = WeightsManager(validator=self)
        self.background_tasks = BackgroundTasks(validator=self)
        self.metagraph_manager = MetagraphManager(validator=self)
        self.NATSPublisher = MinersNATSPublisher(validator=self)

        self.routes = ValidatorAPI(validator=self)

    async def start(self) -> None:
        """Start the validator service"""
        try:
            await self.http_client_manager.start()
            self.app = factory_app(debug=False)

            # Start background tasks

            asyncio.create_task(
                self.background_tasks.sync_loop(SYNC_LOOP_CADENCE_SECONDS)
            )
            asyncio.create_task(
                self.background_tasks.set_weights_loop(WEIGHTS_LOOP_CADENCE_SECONDS)
            )

            # 1 hour
            asyncio.create_task(self.background_tasks.update_tee(60 * 60))

            # Start telemetry collection in its own task
            asyncio.create_task(self.background_tasks.telemetry_loop(60 * 10))

            # Start process monitoring cleanup task
            asyncio.create_task(self.background_tasks.monitor_cleanup_loop())

        except Exception as e:
            logger.error(f"Failed to start validator: {str(e)}")
            raise

        try:
            config = uvicorn.Config(
                self.routes.app,
                host="0.0.0.0",
                port=self.config.VALIDATOR_PORT,
                lifespan="on",
            )
            server = uvicorn.Server(config)
            await server.serve()
        except Exception as e:
            logger.error(f"Failed to start validator api: {str(e)}")
            raise

    def node(self) -> Optional[Node]:
        try:
            nodes = self.metagraph.nodes
            node = nodes[self.keypair.ss58_address]
            return node
        except Exception as e:
            logger.error(f"Failed to get node from metagraph: {e}")
            return None

    async def make_non_streamed_get(self, node: Node, endpoint: str) -> Optional[Any]:
        return await make_non_streamed_get(
            httpx_client=self.http_client_manager.client,
            node=node,
            endpoint=endpoint,
            connected_nodes=self.node_manager.connected_nodes,
            validator_ss58_address=self.keypair.ss58_address,
        )

    async def make_non_streamed_post(
        self, node: Node, endpoint: str, payload: Any
    ) -> Optional[Any]:
        return await make_non_streamed_post(
            httpx_client=self.http_client_manager.client,
            node=node,
            endpoint=endpoint,
            payload=payload,
            connected_nodes=self.node_manager.connected_nodes,
            validator_ss58_address=self.keypair.ss58_address,
            keypair=self.keypair,
        )

    async def stop(self) -> None:
        """Cleanup validator resources and shutdown gracefully.

        Closes:
        - HTTP client connections
        - Server instances
        """
        await self.http_client_manager.stop()
        if self.server:
            await self.server.stop()

    def connected_nodes(self):
        return self.routing_table.get_all_addresses()

    def healthcheck(self):
        try:
            info = {
                "ss58_address": str(self.keypair.ss58_address),
                "uid": str(self.metagraph.nodes[self.keypair.ss58_address].node_id),
                "ip": str(self.metagraph.nodes[self.keypair.ss58_address].ip),
                "port": str(self.metagraph.nodes[self.keypair.ss58_address].port),
                "netuid": str(self.config.NETUID),
                "subtensor_network": str(self.config.SUBTENSOR_NETWORK),
                "subtensor_address": str(self.config.SUBTENSOR_ADDRESS),
            }
            return info
        except Exception as e:
            logger.error(f"Failed to get validator info: {str(e)}")
            return None

    def dashboard(self):
        """Return a simple HTML dashboard for the validator."""
        try:
            # Get basic validator info
            info = self.healthcheck()

            # Get worker registry stats
            worker_count = len(self.routing_table.get_all_worker_registrations())

            # Get error stats from the last 24 hours
            error_count_24h = self.node_manager.errors_storage.get_error_count(hours=24)

            # Get uptime (approximate)
            import time
            import math

            start_time = os.getenv("START_TIME", str(int(time.time())))
            uptime_seconds = int(time.time()) - int(start_time)
            uptime_days = math.floor(uptime_seconds / (60 * 60 * 24))

            # Read the HTML template
            try:
                with open("static/index.html", "r") as f:
                    template = f.read()
            except FileNotFoundError:
                logger.error("Dashboard template not found")
                return "Dashboard template not found"

            # Replace template variables with actual values
            replace_dict = {
                "{{ss58_address}}": info.get("ss58_address", "N/A"),
                "{{uid}}": info.get("uid", "N/A"),
                "{{ip}}": info.get("ip", "N/A"),
                "{{port}}": info.get("port", "N/A"),
                "{{subtensor_network}}": info.get("subtensor_network", "N/A"),
                "{{netuid}}": info.get("netuid", "N/A"),
                "{{worker_count}}": str(worker_count),
                "{{error_count_24h}}": str(error_count_24h),
                "{{network}}": info.get("subtensor_network", "N/A").upper(),
                "{{uptime_days}}": str(uptime_days),
                "{{current_year}}": str(datetime.datetime.now().year),
            }

            for key, value in replace_dict.items():
                template = template.replace(key, value)

            return template

        except Exception as e:
            logger.error(f"Failed to generate dashboard: {str(e)}")
            return f"""
            <html>
                <body>
                    <h1>Dashboard Error</h1>
                    <p>Failed to load dashboard: {str(e)}</p>
                </body>
            </html>
            """

    def dashboard_data(self):
        """Return a JSON object with dashboard data for API calls."""
        try:
            # Get basic validator info
            info = self.healthcheck()

            # Get worker registry stats
            worker_count = len(self.routing_table.get_all_worker_registrations())

            # Get error stats from the last 24 hours
            error_count_24h = self.node_manager.errors_storage.get_error_count(hours=24)

            # Get uptime (approximate)
            import time
            import math

            start_time = os.getenv("START_TIME", str(int(time.time())))
            uptime_seconds = int(time.time()) - int(start_time)
            uptime_days = math.floor(uptime_seconds / (60 * 60 * 24))

            # Return JSON data
            return {
                "ss58_address": info.get("ss58_address", "N/A"),
                "uid": info.get("uid", "N/A"),
                "ip": info.get("ip", "N/A"),
                "port": info.get("port", "N/A"),
                "subtensor_network": info.get("subtensor_network", "N/A"),
                "netuid": info.get("netuid", "N/A"),
                "worker_count": worker_count,
                "error_count_24h": error_count_24h,
                "uptime_days": uptime_days,
                "network": info.get("subtensor_network", "N/A").upper(),
                "current_year": datetime.datetime.now().year,
            }

        except Exception as e:
            logger.error(f"Failed to generate dashboard data: {str(e)}")
            return {"error": str(e)}

    async def get_score_simulation_data(self):
        """Calculate simulated scores based on recently fetched telemetry data."""
        logger.info("Starting score simulation based on recent telemetry...")
        try:
            # 1. Fetch the latest telemetry data for reachable nodes
            telemetry = self.telemetry_storage.get_all_telemetry()

            data_to_score = self.weights_manager._get_delta_node_data(telemetry)

            logger.info(f"Data to score: {data_to_score}")
            # 2. Calculate weights (scores) using the WeightsManager
            logger.info("Calculating weights using WeightsManager...")
            uids, scores = await self.weights_manager.calculate_weights(
                data_to_score, simulation=True
            )

            logger.info(f"Weights calculated for {len(uids)} UIDs.")

            if uids is None or scores is None or len(uids) != len(scores):
                logger.error("Mismatch or None returned from calculate_weights.")
                return {"scores": []}  # Return empty if calculation failed

            # 3. Map UIDs back to hotkeys using the metagraph
            uid_to_hotkey = {
                node.node_id: node.hotkey for node in self.metagraph.nodes.values()
            }

            # 4. Format the scores for the API response - directly use raw scores from calculate_weights
            formatted_scores = [
                {"hotkey": uid_to_hotkey.get(int(uid)), "score": float(score)}
                for uid, score in zip(uids, scores)
                if int(uid) in uid_to_hotkey
            ]

            logger.info(
                f"Score simulation complete. Returning {len(formatted_scores)} scores."
            )
            return {"scores": formatted_scores}

        except Exception as e:
            logger.error(f"Error during score simulation: {str(e)}", exc_info=True)
            # Raise the exception to see the full traceback in logs
            raise
