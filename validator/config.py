import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    VALIDATOR_WALLET_NAME = os.getenv("VALIDATOR_WALLET_NAME", "default")
    VALIDATOR_HOTKEY_NAME = os.getenv("VALIDATOR_HOTKEY_NAME", "default")
    VALIDATOR_PORT = int(os.getenv("VALIDATOR_PORT", 8081))
    NETUID = int(os.getenv("NETUID", "42"))
    SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK", "finney")
    SUBTENSOR_ADDRESS = os.getenv(
        "SUBTENSOR_ADDRESS", "wss://entrypoint-finney.opentensor.ai:443"
    )
    MINER_WHITELIST = os.getenv("MINER_WHITELIST", "").split(",")
    API_KEY = os.getenv("API_KEY", None)
