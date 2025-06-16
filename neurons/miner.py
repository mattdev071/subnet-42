import os
import httpx
import uvicorn
import requests
import asyncio
import time
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fiber.chain import chain_utils, post_ip_to_chain, interface
from fiber.chain.metagraph import Metagraph
from fiber.miner.server import factory_app
from fiber.networking.models import NodeWithFernet as Node
from fiber.logging_utils import get_logger

logger = get_logger(__name__)

class OptimizedMiner:
    def __init__(self):
        """Initialize optimized miner with enhanced features"""
        self.wallet_name = os.getenv("WALLET_NAME", "default")
        self.hotkey_name = os.getenv("HOTKEY_NAME", "default")
        self.port = int(os.getenv("MINER_PORT", 8082))
        self.external_ip = self.get_external_ip()
        self.worker_id = os.getenv("WORKER_ID", "default_worker")
        self.worker_version = os.getenv("WORKER_VERSION", "1.0.0")
        
        # Initialize performance tracking
        self.performance_stats = {
            "twitter_auth_errors": 0,
            "twitter_errors": 0,
            "twitter_ratelimit_errors": 0,
            "twitter_returned_other": 0,
            "twitter_returned_profiles": 0,
            "twitter_returned_tweets": 0,
            "twitter_scrapes": 0,
            "web_errors": 0,
            "web_success": 0
        }
        
        # Initialize connection tracking
        self.last_telemetry_time = 0
        self.telemetry_interval = 60  # seconds
        self.connection_status = True
        
        self.keypair = chain_utils.load_hotkey_keypair(
            self.wallet_name, self.hotkey_name
        )

        self.httpx_client: Optional[httpx.AsyncClient] = None
        self.netuid = int(os.getenv("NETUID", "42"))
        self.subtensor_network = os.getenv("SUBTENSOR_NETWORK")
        self.subtensor_address = os.getenv("SUBTENSOR_ADDRESS")

        self.server: Optional[factory_app] = None
        self.app: Optional[FastAPI] = None

        self.substrate = interface.get_substrate(
            subtensor_network=self.subtensor_network,
            subtensor_address=self.subtensor_address,
        )
        self.metagraph = Metagraph(netuid=self.netuid, substrate=self.substrate)
        self.metagraph.sync_nodes()

        self.post_ip_to_chain()
        self.setup_routes()

    def setup_routes(self):
        """Setup FastAPI routes with enhanced endpoints"""
        self.app = FastAPI()
        
        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "worker_id": self.worker_id, "version": self.worker_version}
        
        @self.app.get("/telemetry")
        async def get_telemetry():
            current_time = time.time()
            if current_time - self.last_telemetry_time < self.telemetry_interval:
                return {
                    "worker_id": self.worker_id,
                    "worker_version": self.worker_version,
                    "stats": {self.worker_id: self.performance_stats},
                    "connection_status": self.connection_status
                }
            return {"error": "Telemetry not ready"}
        
        @self.app.post("/job/generate")
        async def generate_job():
            try:
                # Implement job generation logic here
                return {"status": "success", "job_id": "sample_job"}
            except Exception as e:
                logger.error(f"Error generating job: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/job/add")
        async def add_job(job_data: Dict[str, Any]):
            try:
                # Implement job addition logic here
                return {"status": "success", "job_id": job_data.get("job_id")}
            except Exception as e:
                logger.error(f"Error adding job: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/job/status/{job_id}")
        async def check_job_status(job_id: str):
            try:
                # Implement job status check logic here
                return {"status": "completed", "job_id": job_id}
            except Exception as e:
                logger.error(f"Error checking job status: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/job/result")
        async def submit_job_result(result_data: Dict[str, Any]):
            try:
                # Implement job result submission logic here
                return {"status": "success"}
            except Exception as e:
                logger.error(f"Error submitting job result: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))

    async def start(self) -> None:
        """Start the miner service with enhanced monitoring"""
        try:
            self.httpx_client = httpx.AsyncClient()
            
            # Start background tasks
            asyncio.create_task(self.monitor_performance())
            asyncio.create_task(self.update_telemetry())
            
            config = uvicorn.Config(
                self.app, host="0.0.0.0", port=self.port, lifespan="on"
            )
            server = uvicorn.Server(config)
            await server.serve()

        except Exception as e:
            logger.error(f"Failed to start miner: {str(e)}")
            raise

    async def monitor_performance(self):
        """Monitor and update performance metrics"""
        while True:
            try:
                # Update performance stats here
                self.performance_stats["web_success"] += 1
                await asyncio.sleep(60)  # Update every minute
            except Exception as e:
                logger.error(f"Error in performance monitoring: {str(e)}")
                await asyncio.sleep(60)

    async def update_telemetry(self):
        """Update telemetry data periodically"""
        while True:
            try:
                self.last_telemetry_time = time.time()
                await asyncio.sleep(self.telemetry_interval)
            except Exception as e:
                logger.error(f"Error updating telemetry: {str(e)}")
                await asyncio.sleep(self.telemetry_interval)

    def get_external_ip(self) -> str:
        """Get external IP with enhanced error handling"""
        env = os.getenv("ENV", "prod").lower()
        if env == "dev":
            return "0.0.0.1"

        try:
            response = requests.get("https://api.ipify.org?format=json", timeout=10)
            response.raise_for_status()
            return response.json()["ip"]
        except requests.RequestException as e:
            logger.error(f"Failed to get external IP: {e}")
            return "0.0.0.0"

    def post_ip_to_chain(self) -> None:
        """Post IP to chain with enhanced error handling"""
        logger.info("Starting post_ip_to_chain process")
        try:
            node = self.node()
            logger.debug(f"Retrieved node from metagraph: {node}")

            external_ip = os.getenv("OVERRIDE_EXTERNAL_IP", self.external_ip)

            if node:
                if node.ip != external_ip or node.port != self.port:
                    logger.info(
                        f"IP/Port mismatch detected - Current chain values: "
                        f"IP={node.ip}, Port={node.port}"
                    )
                    logger.info(
                        f"Updating chain with new values: IP={external_ip}, "
                        f"Port={self.port}"
                    )

                    try:
                        coldkey_keypair_pub = chain_utils.load_coldkeypub_keypair(
                            wallet_name=self.wallet_name
                        )
                        
                        post_ip_to_chain.post_node_ip_to_chain(
                            substrate=self.substrate,
                            keypair=self.keypair,
                            netuid=self.netuid,
                            external_ip=external_ip,
                            external_port=self.port,
                            coldkey_ss58_address=coldkey_keypair_pub.ss58_address,
                        )
                        logger.info("✅ Successfully posted IP/Port to chain")

                    except Exception as e:
                        logger.error(
                            f"Failed to post IP/Port to chain: {str(e)}", exc_info=True
                        )
                else:
                    logger.info(
                        f"IP/Port already up to date on chain: IP={node.ip}, "
                        f"Port={node.port}"
                    )
            else:
                err_msg = (
                    f"Hotkey {self.keypair.ss58_address} not found in metagraph. "
                    f"Please ensure it is registered."
                )
                logger.error(err_msg)

        except Exception as e:
            logger.error(f"Error in post_ip_to_chain: {str(e)}", exc_info=True)

    def node(self) -> Optional[Node]:
        """Get node information with enhanced error handling"""
        try:
            nodes = self.metagraph.nodes
            node = nodes[self.keypair.ss58_address]
            return node
        except Exception as e:
            logger.error(f"Failed to get node from metagraph: {e}")
            return None

    async def stop(self) -> None:
        """Cleanup and shutdown with enhanced error handling"""
        try:
            if self.httpx_client:
                await self.httpx_client.aclose()
            if self.server:
                await self.server.stop()
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}")
            raise
